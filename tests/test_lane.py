import os
from pathlib import Path
# SQLite is used only for deterministic tests; the runnable app uses Postgres.
os.environ['DATABASE_URL'] = 'sqlite:///./lane_phase0_pytest.db'
os.environ['DEMO_MODE'] = 'true'
os.environ['APP_TOKEN'] = 'test-api-token'
os.environ['DASHBOARD_USER'] = 'lane'
os.environ['DASHBOARD_PASSWORD'] = 'test-dashboard-password'
from fastapi.testclient import TestClient
from app.main import app
from app.db import Base, engine, SessionLocal, Client, Call, Finding, DemoAppointment
from app.verify import match_record, check_finding, process_due_jobs
from app.time_utils import to_utc, AmbiguousTime
from app.manifest import hash_policy
from sqlalchemy import select
import pytest

@pytest.fixture()
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(app) as c:
        yield c
    Base.metadata.drop_all(engine)


def headers(): return {'Authorization':'Bearer test-api-token'}


def test_demo_wrong_then_fix_then_recheck(client):
    resp = client.post('/api/demo/seed', headers=headers())
    assert resp.status_code == 200, resp.text
    fid = resp.json()['finding_id']
    with SessionLocal() as db:
        f = db.get(Finding, fid)
        assert f.state == 'pending'
        assert f.promised['final_start'] != f.observed['records'][0]['startTime']
        assert f.manifest_hash
        assert len(db.scalars(select(Call)).all()) == 1
    client.post(f'/api/demo/fix/{fid}',headers=headers()).raise_for_status()
    result = client.post(f'/api/findings/{fid}/recheck',headers=headers()).json()
    assert result['state'] == 'verified'
    with SessionLocal() as db:
        assert len(db.get(Finding,fid).observed['records']) == 1
    # A second seed is idempotent.
    assert client.post('/api/demo/seed', headers=headers()).json()['finding_id'] == fid


def test_bearer_required_and_webhook_unsigned_rejected(client):
    assert client.get('/api/findings').status_code == 401
    assert client.post('/webhooks/retell', json={'event':'call_ended'}).status_code == 401
    assert client.get('/').status_code == 401


def test_strict_match():
    want = {'contact_id':'c1','calendar_id':'cal1','appointment_id':'a1','final_start':'2026-11-01T10:00:00-06:00'}
    got = {'id':'a1','contactId':'c1','calendarId':'cal1','status':'booked','startTime':'2026-11-01T10:00:00-06:00'}
    assert match_record(got,want,'America/Chicago')[0]
    assert not match_record(dict(got,startTime='2026-11-01T09:00:00-06:00'),want,'America/Chicago')[0]
    assert not match_record(dict(got,contactId='c2'),want,'America/Chicago')[0]
    assert not match_record(dict(got,id='a2'),want,'America/Chicago')[0]
    assert match_record(dict(got,status=None),want,'America/Chicago')[1].startswith('UNKNOWN:')


def test_dst_ambiguous_and_offsets():
    with pytest.raises(AmbiguousTime):
        to_utc('2026-11-01T01:30:00','America/Chicago')
    assert to_utc('2026-11-01T01:30:00-05:00','America/Chicago') != to_utc('2026-11-01T01:30:00-06:00','America/Chicago')


def test_policy_hash_canonical():
    assert hash_policy({'a':1,'b':2}) == hash_policy({'b':2,'a':1})
    assert hash_policy({'a':1}) != hash_policy({'a':2})


def test_final_recheck_mismatch_contradicts(client):
    fid = client.post('/api/demo/seed', headers=headers()).json()['finding_id']
    with SessionLocal() as db:
        f = db.get(Finding,fid)
        check_finding(db,f,'60m')
        db.commit()
        assert f.state == 'contradicted'


def test_policy_check_is_deterministic():
    from app.policy import Labels, unauthorized_promises
    labels = Labels.model_validate({'abstain':False,'labels':[
       {'kind':'discount','exact_quote':'I will give you 20% off','is_commitment':True},
       {'kind':'refund','exact_quote':'Can you refund me?','is_commitment':False}]})
    result = unauthorized_promises(labels, ['discount','refund'])
    assert len(result) == 1 and result[0].kind == 'discount'
    assert unauthorized_promises(Labels(abstain=True), ['discount']) == []

def test_call_start_before_manifest_has_no_fallback(client):
    from app.main import store_call
    from datetime import timedelta
    from app.db import now
    client.post('/api/demo/seed',headers=headers())
    with SessionLocal() as db:
        old_start = int((now()-timedelta(days=2)).timestamp()*1000)
        payload = {'event':'call_ended','call':{'call_id':'past_demo_no_manifest',
            'agent_id':'agent_demo_lane','agent_version':14,
            'start_timestamp':old_start,'end_timestamp':old_start+30000,
            'transcript':'Agent: You are booked.'}}
        result = store_call(db,payload,demo=True)
        assert result['bound_manifest'] is None
        row=db.get(Call,result['id'])
        assert row.manifest_hash is None
        assert store_call(db,payload,demo=True)['status']=='duplicate'


def test_recheck_does_not_downgrade_contradicted_state(client):
    fid = client.post('/api/demo/seed',headers=headers()).json()['finding_id']
    with SessionLocal() as db:
        f = db.get(Finding, fid)
        check_finding(db,f,'60m')
        assert f.state == 'contradicted'
        check_finding(db,f,'manual')
        assert f.state == 'contradicted'


def test_missing_highlevel_token_cannot_verify():
    from app.highlevel import HighLevel, CRMError
    from app.db import Client
    import os
    from unittest.mock import patch
    live = Client(name='Live',retell_agent_id='agent_live',highlevel_location_id='loc1',
                  timezone='UTC',calendar_id='cal1',is_demo=False)
    with patch.dict(os.environ, {'HIGHLEVEL_TOKEN':''}):
        with pytest.raises(CRMError):
            HighLevel(live,None).by_id('appointment_id')
