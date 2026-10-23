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
from app.db import Base, engine, SessionLocal, Client, Call, Finding, DemoAppointment, ExtractionResult, DatasetEvaluation
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


def test_offline_dataset_import_reuses_live_verifier(client):
    from scripts.import_dataset import import_cases
    result = import_cases('datasets/booking_cases.jsonl')
    assert len(result) == 10
    assert all(r[3] == 'PASS' for r in result)
    with SessionLocal() as db:
        drift = db.scalars(select(Finding).where(Finding.type == 'agent_drift')).all()
        assert len(drift) == 1
        obs = db.scalars(select(Finding).where(Finding.state == 'contradicted')).all()
        assert len(obs) == 4
        delayed_call = db.scalar(select(Call).where(Call.retell_call_id == 'dataset_extracted_v2_rules_delayed_write'))
        delayed = db.scalar(select(Finding).where(Finding.call_id == delayed_call.id))
        assert delayed.state == 'verified'


def test_dataset_labels_hidden_and_extraction_stored(client):
    from scripts.import_dataset import import_cases
    import_cases('datasets/booking_cases.jsonl', prefix='independent_test')
    with SessionLocal() as db:
        extracted = db.scalars(select(ExtractionResult)).all()
        evaluations = db.scalars(select(DatasetEvaluation)).all()
        assert len(extracted) == len(evaluations) == 10
        assert all(row.backend == 'rules' for row in extracted)
        assert all(row.extraction_correct for row in evaluations)
        assert all(row.state_correct for row in evaluations)
        correction_call = db.scalar(select(Call).where(Call.retell_call_id == 'independent_test_rules_corrected_time'))
        value = db.scalar(select(ExtractionResult).where(ExtractionResult.call_id == correction_call.id))
        assert value.confirmed and not value.abstain
        assert value.final_start.endswith('16:00:00+00:00')
        assert client.get('/evaluation',auth=('lane','test-dashboard-password')).status_code == 200


def test_extractor_does_not_read_gold_label(monkeypatch):
    from scripts.evaluate_extraction import evaluate
    from tempfile import TemporaryDirectory
    import json
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / 'bench.jsonl'
        # This deliberately puts a false label against the clearly stated time.
        # Correct extractor MUST fail the label, not copy it.
        case = {'id':'adversarial_label',
            'transcript':'Caller: Book November 3, 2026 at 10 AM.\nAgent: Confirmed.',
            'timezone':'America/Chicago', 'call_started_at':'2026-10-22T15:00:00Z',
            'commitment':{'confirmed':True,'final_start':'2026-11-03T09:00:00-06:00'}}
        path.write_text(json.dumps(case)+'\n',encoding='utf-8')
        results = evaluate(str(path),output=str(Path(tmp)/'out.json'))
        assert results['metrics']['coverage'] == 1
        assert results['metrics']['extraction_correct'] == 0
        assert results['cases'][0]['prediction']['final_start'].endswith('16:00:00+00:00')


def test_offline_hard_cases_record_nontrivial_errors():
    from scripts.evaluate_extraction import evaluate
    from tempfile import TemporaryDirectory
    with TemporaryDirectory() as d:
        x = evaluate('datasets/extraction_challenge.jsonl',output=str(Path(d)/'report.json'))
    assert x['metrics']['total'] >= 6
    assert x['metrics']['abstained'] > 0
    assert x['metrics']['extraction_correct'] < x['metrics']['total']


def test_dataset_reimport_scopes_appointment_and_contact_ids(client):
    """A different prefix must coexist with older fixture records in one database."""
    from scripts.import_dataset import import_cases
    from app.db import DemoAppointment
    first = import_cases('datasets/booking_cases.jsonl', prefix='run_one')
    second = import_cases('datasets/booking_cases.jsonl', prefix='run_two')
    repeat = import_cases('datasets/booking_cases.jsonl', prefix='run_two')
    assert all(row[3] == 'PASS' for row in first)
    assert all(row[3] == 'PASS' for row in second)
    assert all(row[3] == 'SKIP' for row in repeat)
    with SessionLocal() as db:
        assert len(db.scalars(select(Call).where(Call.retell_call_id.like('run_%'))).all()) == 20
        correct_rows = db.scalars(select(DemoAppointment)).all()
        assert len(correct_rows) == 14  # Seven stored appointment records per run
        assert len({r.id for r in correct_rows}) == len(correct_rows)
        # Mock contacts must be isolated too, avoiding cross-run by_contact matches.
        contacts = [r.record['contactId'] for r in correct_rows if 'contactId' in r.record]
        assert len(set(contacts)) == 14


def test_unaccepted_booking_claim_flagged_not_verified(client, monkeypatch):
    """Caller approval is separate from agent assertion and calendar state."""
    from datetime import timedelta
    from app.db import now
    from app.main import store_call
    from app.pipeline import process_call
    from app.extract import Commitment
    client.post('/api/demo/seed', headers=headers()).raise_for_status()
    with SessionLocal() as db:
        start = now() - timedelta(minutes=4)
        payload={'event':'call_ended','call':{
            'call_id':'unaccepted_claim_test', 'agent_id':'agent_demo_lane',
            'agent_version':14,
            'start_timestamp':int(start.timestamp()*1000),
            'end_timestamp':int((start+timedelta(minutes=2)).timestamp()*1000),
            'transcript':('Caller: Is November 5 at 11 AM free?\n'
                          'Agent: Yes, your appointment is booked.\n'
                          'Caller: No! I was just asking, do not book it.'),
            'transcript_with_tool_calls':[]}}
        saved=store_call(db,payload,demo=True)
        call=db.get(Call,saved['id'])
        monkeypatch.setattr('app.pipeline.extract_commitment', lambda *args, **kwargs:
                            Commitment(confirmed=False,final_start=None,abstain=False,
                                       reason='Caller explicitly rejected booking'))
        process_call(db,call,extraction_backend='groq_events')
        db.commit()
        types=[(f.type,f.state) for f in db.scalars(select(Finding).where(Finding.call_id==call.id))]
        assert ('unaccepted_booking_claim','flagged') in types
        assert all(t!='booking' for t,state in types)
        assert call.coverage=='no_confirmed_booking'
