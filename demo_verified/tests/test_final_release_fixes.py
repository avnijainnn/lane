"""Regression tests for the consolidated Lane correctness and Gemini release."""
import os
os.environ['DATABASE_URL'] = 'sqlite:///./lane_phase0_pytest.db'
os.environ['DEMO_MODE'] = 'true'
os.environ['APP_TOKEN'] = 'test-api-token'
os.environ['DASHBOARD_USER'] = 'lane'
os.environ['DASHBOARD_PASSWORD'] = 'test-dashboard-password'
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import json
import pytest
from sqlalchemy import select, func
from fastapi.testclient import TestClient
from app.main import app
from app.db import (Base, engine, SessionLocal, Client, Manifest, Finding,
                    Call, CaseTicket, ExtractionResult, PolicyDraft, ApprovalEvent,
                    WorkerHeartbeat, now)
from app.highlevel import HighLevel
from app.verify import match_record, check_finding
from app.accountability import reconcile, report_for_client
from app.manifest import hash_policy, manifest_for_start
from scripts.seed_agency_world import seed_world
from app.extract import extract_commitment

AUTH=('lane','test-dashboard-password')
TOKEN={'Authorization':'Bearer test-api-token'}

@pytest.fixture
def live():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(app) as c:
        yield c
    Base.metadata.drop_all(engine)


def test_exact_time_differs_even_one_minute():
    expected={'contact_id':'contact1','calendar_id':'cal1', 'final_start':'2026-11-01T11:00:00-06:00'}
    record={'id':'a1','contactId':'contact1','calendarId':'cal1','startTime':'2026-11-01T11:01:00-06:00','status':'booked'}
    assert match_record(record,expected,'America/Chicago')[0] is False
    assert match_record(dict(record,startTime=expected['final_start']),expected,'America/Chicago')[0]


def test_highlevel_contact_lookup_preserves_provenance_without_contactid(monkeypatch):
    client=SimpleNamespace(is_demo=False)
    h=HighLevel(client,None)
    monkeypatch.setattr(h,'_get',lambda path:{'events':[{'id':'a1','calendarId':'cal1',
        'status':'booked','startTime':'2026-11-01 11:00:00'}]})
    rows=h.by_contact('contact1')
    assert rows[0]['contactId']=='contact1'
    assert rows[0]['_lane_contact_lookup'] is True


def test_by_id_without_contactid_needs_contact_query(live,monkeypatch):
    from app import highlevel
    seed_world()
    with SessionLocal() as d:
        f=d.scalar(select(Finding).where(Finding.type=='booking'))
        f.state='pending'
        p=f.promised
        monkeypatch.setattr(highlevel.HighLevel,'by_id',lambda self, appt:{'id':appt,'calendarId':p['calendar_id'],
             'startTime':p['final_start'],'status':'booked'})
        monkeypatch.setattr(highlevel.HighLevel,'by_contact',lambda self,contact:[])
        monkeypatch.setattr(highlevel.HighLevel,'validate_contact_location',lambda self,contact:True)
        check_finding(d,f,'manual')
        assert f.state != 'verified'
        monkeypatch.setattr(highlevel.HighLevel,'by_contact',lambda self,contact:[{
             'id':p['appointment_id'],'contactId':p['contact_id'],
             'calendarId':p['calendar_id'],'startTime':p['final_start'],'status':'booked'}])
        check_finding(d,f,'manual')
        assert f.state == 'verified'


def test_refusal_then_new_request_can_be_accepted(live):
    seed_world()
    with SessionLocal() as d:
        f=d.scalar(select(Finding).where(Finding.type=='booking'))
        call=d.get(Call,f.call_id)
        extraction=d.scalar(select(ExtractionResult).where(ExtractionResult.call_id==call.id))
        extraction.confirmed=True
        extraction.abstain=False
        call.transcript=("Caller: Don't book it.\nAgent: Okay.\n"
            "Caller: Actually book November 3 at 11 am for me.\n"
            "Agent: Your appointment is booked for November 3 at 11 am.\nCaller: Yes.")
        assert reconcile(d,f)['consent']=='accepted'
        call.transcript += '\nCaller: Never mind, do not book it.'
        assert reconcile(d,f)['consent']=='rejected'


def test_legacy_post_only_creates_draft(live):
    result=live.post('/api/clients',json={'name':'New dental','retell_agent_id':'agent_n',
       'highlevel_location_id':'loc','calendar_id':'cal','timezone':'America/Chicago'},headers=TOKEN)
    assert result.status_code==200, result.text
    client_id=result.json()['id']
    resp=live.post(f'/api/clients/{client_id}/manifests',json={'approved_agent_version':'14',
       'approver':'Unverified name','approver_role':'client'},headers=TOKEN)
    assert resp.status_code==200,resp.text
    assert resp.json()['active'] is False
    with SessionLocal() as d:
        assert d.scalar(select(Manifest).where(Manifest.client_id==client_id)) is None
        draft=d.scalar(select(PolicyDraft).where(PolicyDraft.client_id==client_id))
        assert draft.state=='draft'
        assert manifest_for_start(d,client_id,now()) is None
    approved=live.post(f"/api/accountability/drafts/{resp.json()['draft_id']}/approve",
        json={'approver':'Demo consent','role':'client'},headers=TOKEN)
    assert approved.status_code==200,approved.text
    with SessionLocal() as d:
        m=manifest_for_start(d,client_id,now())
        assert m is not None
        assert d.scalar(select(ApprovalEvent).where(ApprovalEvent.manifest_id==m.id)).approved_sha256==m.sha256


def test_legacy_manifest_does_not_bind_live_calls(live):
    with SessionLocal() as d:
        c=Client(name='live unapproved',retell_agent_id='l1',highlevel_location_id='l',calendar_id='c',is_demo=False)
        d.add(c);d.flush()
        body={'version':1}
        m=Manifest(client_id=c.id,version=1,body=body,sha256=hash_policy(body),
                   approved_agent_version='1',approver='typed name',approver_role='client',
                   effective_from=now()-timedelta(minutes=1))
        d.add(m);d.commit()
        assert manifest_for_start(d,c.id,now()) is None


def test_queue_get_has_no_side_effects(live):
    seed_world()
    with SessionLocal() as d:
        f=d.scalar(select(Finding).where(Finding.type=='booking'))
        d.execute(__import__('sqlalchemy').delete(CaseTicket).where(CaseTicket.finding_id==f.id))
        d.commit()
        baseline=d.scalar(select(func.count(CaseTicket.id)))
    response=live.get('/accountability/queue',auth=AUTH)
    assert response.status_code==200
    with SessionLocal() as d:
        assert d.scalar(select(func.count(CaseTicket.id))) == baseline


def test_reporting_does_not_count_unapproved_legacy(live):
    seed_world()
    with SessionLocal() as d:
        c=d.scalar(select(Client).where(Client.retell_agent_id=='agent_world_bright'))
        before=report_for_client(d,c.id)['calls_with_approved_manifest']
        call=d.scalar(select(Call).where(Call.client_id==c.id))
        original=call.manifest_id
        unapproved=Manifest(client_id=c.id,version=77,body={'new':True},sha256='a'*64,
                    approved_agent_version='88',approver='fake',approver_role='client',effective_from=now())
        d.add(unapproved);d.flush()
        call.manifest_id=unapproved.id
        call.manifest_hash=unapproved.sha256
        assert report_for_client(d,c.id)['calls_with_approved_manifest']==before-1
        call.manifest_id=original


def test_worker_health_detects_missing_and_live(live):
    assert live.get('/api/operations',headers=TOKEN).json()['worker']=='stale_or_missing'
    with SessionLocal() as d:
        d.add(WorkerHeartbeat(name='pipeline',last_seen_at=now(),last_processed=3));d.commit()
    assert live.get('/api/operations',headers=TOKEN).json()['worker']=='healthy'
    assert live.get('/').status_code==401
    assert live.get('/',auth=AUTH,follow_redirects=False).headers['location']=='/accountability'


def test_non_demo_form_requires_explicit_origin(live,monkeypatch):
    monkeypatch.setenv('DEMO_MODE','false')
    monkeypatch.setenv('LANE_ALLOWED_ORIGINS','http://localhost:8001')
    resp=live.post('/accountability/demo/seed',auth=AUTH,data={'action':'seed'})
    assert resp.status_code==403
    resp=live.post('/accountability/demo/seed',auth=AUTH,data={'action':'seed'},headers={'Origin':'http://localhost:8001'})
    assert resp.status_code==404 # authenticated allowed origin, but demo seeding disabled


def test_gemini_response_validated_and_does_not_log_key(monkeypatch):
    import httpx
    monkeypatch.setenv('GEMINI_API_KEY','private-test-key')
    monkeypatch.setenv('GEMINI_MODEL','gemini-3.5-flash-lite')
    def mock_post(url,**kwargs):
        assert url.endswith('gemini-3.5-flash-lite:generateContent')
        assert kwargs['headers']['x-goog-api-key']=='private-test-key'
        assert kwargs['json']['generationConfig']['responseMimeType']=='application/json'
        return SimpleNamespace(status_code=200,json=lambda:{'candidates':[{'content':{'parts':[{'text':json.dumps({
             'confirmed':False,'final_start':None,'evidence':'None', 'abstain':False,'reason':'Not booked'})}]}}]})
    monkeypatch.setattr(httpx,'post',mock_post)
    r=extract_commitment('Caller: Are you available?\nAgent: No.', 'America/Chicago', backend='gemini',
       reference_time=datetime(2026,10,22,tzinfo=timezone.utc))
    assert r.abstain is False and r.confirmed is False
    def error_post(url,**kwargs):
        return SimpleNamespace(status_code=429)
    monkeypatch.setattr(httpx,'post',error_post)
    r=extract_commitment('Caller: Can I book?', 'America/Chicago',backend='gemini')
    assert 'http_status=429' in r.reason and 'private-test-key' not in r.reason


def test_redaction_email_phone():
    from app.redact import text
    red=text('email me at user@example.com and call 415-555-1234')
    assert 'user@example.com' not in red and '415-555-1234' not in red


def test_gemini_events_path(monkeypatch):
    import httpx
    monkeypatch.setenv('GEMINI_API_KEY','fake-key')
    def mock_post(url,**kwargs):
        return SimpleNamespace(status_code=200,json=lambda:{'candidates':[{'content':{'parts':[{'text':'{"events": []}'}]}}]})
    monkeypatch.setattr(httpx,'post',mock_post)
    r=extract_commitment('Caller: What are your hours?\nAgent: Open 9 to 5.',
                         'America/Chicago',backend='gemini_events',
                         reference_time=datetime(2026,10,22,tzinfo=timezone.utc))
    assert not r.confirmed
    assert not r.reason.startswith('Extractor unavailable/invalid:')


def test_comparison_recognizes_gemini_without_api(live,tmp_path):
    from scripts.compare_extractors import compare
    from app.extract import Commitment
    path=tmp_path/'out.json'
    result=compare('datasets/extraction_challenge.jsonl',
                   backends=('gemini',),extractor=lambda *a,**k:Commitment(
                       confirmed=False,abstain=False,reason='No booking'),output=path)
    assert result['backends']['gemini']['model']
    assert result['backends']['gemini']['metrics']['total']==8
