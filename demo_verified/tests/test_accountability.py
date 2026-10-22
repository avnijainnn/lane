import os
from pathlib import Path
os.environ['DATABASE_URL']='sqlite:///./lane_phase0_pytest.db'
os.environ['DEMO_MODE']='true'
os.environ['APP_TOKEN']='test-api-token'
os.environ['DASHBOARD_USER']='lane'
os.environ['DASHBOARD_PASSWORD']='test-dashboard-password'
import pytest
from datetime import timedelta
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.main import app
from app.db import (Base,engine,SessionLocal,Client,Manifest,PolicyDraft,ApprovalEvent,Call,
                    Finding,Observation,CaseTicket,CaseLog,TrialRun,CalendarSnapshot,DemoAppointment,now)
from app.manifest import manifest_for_start,hash_policy
from app.accountability import (draft_policy,approve_policy,report_for_client,
                                reconcile,act_on_case,judge_trial,compile_trial_scenarios,
                                run_manifest_trial)
from scripts.seed_agency_world import seed_world,advance_calendar

@pytest.fixture()
def world():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(app) as c:
        yield c
    Base.metadata.drop_all(engine)

def login():return ('lane','test-dashboard-password')
def token():return {'Authorization':'Bearer test-api-token'}


def test_entire_world_and_timed_observations(world):
    seeded=seed_world()
    assert seeded['clients']==3 and seeded['calls']==6
    with SessionLocal() as d:
        assert len(d.scalars(select(Client)).all())==3
        assert len(d.scalars(select(Call)).all())==6
        rows={r.retell_call_id:r for r in d.scalars(select(Call)).all()}
        assert rows['world_availability_only'].coverage=='no_confirmed_booking'
        assert rows['world_wrong_time'].manifest_hash
        bad=d.scalar(select(Finding).where(Finding.call_id==rows['world_wrong_time'].id,Finding.type=='booking'))
        delayed=d.scalar(select(Finding).where(Finding.call_id==rows['world_delayed_write'].id))
        drift=d.scalar(select(Finding).where(Finding.call_id==rows['world_agent_drift'].id,Finding.type=='agent_drift'))
        assert bad.state=='contradicted'
        assert delayed.state=='verified'
        assert drift.state=='flagged'
        assert len(d.scalars(select(Observation).where(Observation.finding_id==bad.id)).all())==3
        assert len(d.scalars(select(Observation).where(Observation.finding_id==delayed.id)).all())==2
        assert reconcile(d,bad)['permission']=='allowed'
        assert reconcile(d,bad)['consent']=='accepted'
        assert reconcile(d,bad)['execution']=='contradicted'
        assert db_approved(d,rows['world_wrong_time'])


def db_approved(db,call):
    m=db.get(Manifest,call.manifest_id)
    return bool(m and m.sha256==call.manifest_hash)


def test_seed_idempotent_and_draft_inactive(world):
    seed_world(); seed_world()
    with SessionLocal() as d:
        assert len(d.scalars(select(Call)).all())==6
        assert len(d.scalars(select(TrialRun)).all())==3
        bright=d.scalar(select(Client).where(Client.retell_agent_id=='agent_world_bright'))
        drafts=d.scalars(select(PolicyDraft).where(PolicyDraft.client_id==bright.id)).all()
        assert len([x for x in drafts if x.state=='draft'])==1
        assert manifest_for_start(d,bright.id,now()).version==1
        assert len(d.scalars(select(ApprovalEvent)).all())==3


def test_approval_hash_and_old_call_binding(world):
    seed_world()
    with SessionLocal() as d:
        bright=d.scalar(select(Client).where(Client.retell_agent_id=='agent_world_bright'))
        call=d.scalar(select(Call).where(Call.retell_call_id=='world_wrong_time'))
        old_hash=call.manifest_hash
        draft=d.scalar(select(PolicyDraft).where(PolicyDraft.client_id==bright.id,PolicyDraft.state=='draft'))
        assert draft.body['prev_hash']==old_hash
        approved=approve_policy(d,draft.id,approver='Clinic owner (SYNTHETIC)',role='client')
        d.commit()
        assert approved.sha256==hash_policy(draft.body)
        assert approved.version==2
        assert manifest_for_start(d,bright.id,call.started_at).sha256==old_hash
        assert call.manifest_hash==old_hash
        assert manifest_for_start(d,bright.id,now()).version==2
        event=d.scalar(select(ApprovalEvent).where(ApprovalEvent.manifest_id==approved.id))
        assert event.approved_sha256==approved.sha256 and event.role=='client'
        with pytest.raises(ValueError,match='already approved'):
            approve_policy(d,draft.id,approver='Another person',role='client')


def test_bad_approver_rejected(world):
    seed_world()
    with SessionLocal() as d:
        draft=d.scalar(select(PolicyDraft).where(PolicyDraft.state=='draft'))
        with pytest.raises(ValueError,match='client or agency'):
            approve_policy(d,draft.id,approver='x',role='admin')
        assert draft.state=='draft'


def test_reject_duplicate_active_draft(world):
    seed_world()
    with SessionLocal() as d:
        client=d.scalar(select(Client).where(Client.retell_agent_id=='agent_world_bright'))
        with pytest.raises(ValueError,match='existing draft'):
            draft_policy(d,client.id,authored_by='x',rules={'allowed_actions':['book']})


def test_resolution_must_recheck_after_synthetic_fix(world):
    seed_world()
    with SessionLocal() as d:
        call=d.scalar(select(Call).where(Call.retell_call_id=='world_wrong_time'))
        f=d.scalar(select(Finding).where(Finding.call_id==call.id,Finding.type=='booking'))
        ticket=d.scalar(select(CaseTicket).where(CaseTicket.finding_id==f.id))
        with pytest.raises(ValueError,match='fresh matching read'):
            act_on_case(d,ticket.id,action='close',actor='Ops')
        act_on_case(d,ticket.id,action='assign',actor='Ops',owner='Taylor')
        assert ticket.owner=='Taylor'
        assert ticket.status=='open'
        assert f.state=='contradicted'
        d.commit()
    result=world.post(f'/accountability/cases/{ticket.id}/simulate-repair',auth=login(),follow_redirects=False)
    assert result.status_code==303
    with SessionLocal() as d:
        f=d.get(Finding,f.id);ticket=d.get(CaseTicket,ticket.id)
        assert f.state=='contradicted' and ticket.status=='open'
        act_on_case(d,ticket.id,action='recheck',actor='Taylor')
        d.commit()
        assert f.state=='verified' and ticket.status=='verified_closed'
        logs=d.scalars(select(CaseLog).where(CaseLog.case_id==ticket.id)).all()
        assert {'opened','assign','synthetic_calendar_changed','recheck'} <= {x.action for x in logs}


def test_case_contact_waiver_never_counts_verified(world):
    seed_world()
    with SessionLocal() as d:
        case=d.scalar(select(CaseTicket).order_by(CaseTicket.id))
        act_on_case(d,case.id,action='contacted',actor='Ops')
        with pytest.raises(ValueError,match='requires a reason'):
            act_on_case(d,case.id,action='waive',actor='Ops')
        act_on_case(d,case.id,action='waive',actor='Ops',note='Caller unreachable after two attempts')
        d.commit()
        assert case.status=='waived' and case.customer_contacted
        call=d.get(Call,d.get(Finding,case.finding_id).call_id)
        report=report_for_client(d,call.client_id)
        assert report['cases_waived']>=1
        assert report['cases_verified_closed']==0


def test_report_denominators_and_manifest_approvers(world):
    seed_world()
    with SessionLocal() as d:
        c=d.scalar(select(Client).where(Client.retell_agent_id=='agent_world_bright'))
        r=report_for_client(d,c.id)
        assert r['calls']==3
        assert r['calls_with_approved_manifest']==3
        assert r['booking_findings']==3
        assert r['booking_verified']==2
        assert r['finding_states']['contradicted']==1
        assert r['finding_states']['flagged']==1
        assert r['manifests'][0]['role']=='agency'
        assert r['is_synthetic']


def test_trials_generated_from_approved_manifest(world):
    seed_world()
    with SessionLocal() as d:
        c=d.scalar(select(Client).where(Client.retell_agent_id=='agent_world_bright'))
        m=d.scalar(select(Manifest).where(Manifest.client_id==c.id))
        cases=compile_trial_scenarios(m)
        assert {'never_ask:card_number','never_ask:otp','never_promise:discount',
                'never_promise:refund','deliverable:sms_confirmation','handoff:person','action:book'} == {x['id'] for x in cases}
        runs=d.scalars(select(TrialRun).where(TrialRun.manifest_id==m.id)).all()
        assert runs[0].manifest_hash==m.sha256
        assert any(x['state']=='fail' for x in runs[0].results)
        assert all(x['synthetic'] for x in runs[0].results)
        assert len(runs[0].results)>len(cases) # repeat/flakiness cases


def test_trials_missing_response_does_not_pass(world):
    seed_world()
    with SessionLocal() as d:
        m=d.scalar(select(Manifest))
        trial=run_manifest_trial(d,m.id,samples={})
        assert all(row['state']=='not_run' for row in trial.results)
        assert all(row['state']!='pass' for row in trial.results)


def test_no_trusting_user_supplied_verdicts():
    scenario={'id':'action:book','kind':'action','rule':'book'}
    a={'expected_pass':True,'expected_record':{'id':'e','contact_id':'c','calendar_id':'cal','final_start':'2026-11-04T10:00:00-06:00'},
       'observed_record':{'id':'e','contactId':'c','calendarId':'cal','startTime':'2026-11-04T09:00:00-06:00','status':'booked'}}
    assert judge_trial(scenario,a,timezone='America/Chicago')['state']=='fail'


def test_api_routes_auth_and_synthetic_demo(world):
    assert world.get('/accountability').status_code==401
    assert world.post('/api/accountability/demo/seed').status_code==401
    response=world.post('/api/accountability/demo/seed',headers=token())
    assert response.status_code==200, response.text
    for p in ['/accountability','/accountability/queue','/accountability/clients/1',
              '/accountability/cases/1','/accountability/reconcile/1',
              '/accountability/trials/1','/accountability/reports/1','/accountability/export/1']:
        r=world.get(p,auth=login())
        assert r.status_code==200,(p,r.text[:250])
    assert world.get('/accountability/export/1',auth=login()).json()['is_synthetic']
    assert world.get('/comparison',auth=login()).status_code==200


def test_api_approval_and_case_audit(world):
    seed_world()
    with SessionLocal() as d:
        draft=d.scalar(select(PolicyDraft).where(PolicyDraft.state=='draft'))
    resp=world.post(f'/api/accountability/drafts/{draft.id}/approve',headers=token(),
                    json={'approver':'Dr. Sample','role':'client'})
    assert resp.status_code==200, resp.text
    with SessionLocal() as d:
        assert d.get(PolicyDraft,draft.id).state=='approved'
        case=d.scalar(select(CaseTicket))
        case_id=case.id
    r=world.post(f'/api/accountability/cases/{case_id}/action',headers=token(),
                 json={'action':'assign','actor':'Ops','owner':'Morgan'})
    assert r.status_code==200 and r.json()['owner']=='Morgan'


def test_fixture_time_snapshots_are_ordered(world):
    seed_world()
    with SessionLocal() as d:
        c=d.scalar(select(Call).where(Call.retell_call_id=='world_delayed_write'))
        f=d.scalar(select(Finding).where(Finding.call_id==c.id))
        snapshots=d.scalars(select(CalendarSnapshot).where(CalendarSnapshot.finding_id==f.id)
                            .order_by(CalendarSnapshot.minute)).all()
        assert [s.minute for s in snapshots]==[2,15]
        assert snapshots[0].record is None
        assert snapshots[1].record['status']=='booked'
        assert all(o.source=='demo_simulator' for o in d.scalars(select(Observation).where(Observation.finding_id==f.id)))


def test_out_of_scope_synthetic_repair_never_changes_other_client(world):
    seed_world()
    with SessionLocal() as d:
        a=d.scalar(select(Call).where(Call.retell_call_id=='world_wrong_time'))
        f=d.scalar(select(Finding).where(Finding.call_id==a.id))
        wrong=d.get(DemoAppointment,'evt_world_c')
        before=dict(wrong.record)
        ticket=d.scalar(select(CaseTicket).where(CaseTicket.finding_id==f.id))
    world.post(f'/accountability/cases/{ticket.id}/simulate-repair',auth=login())
    with SessionLocal() as d:
        assert d.get(DemoAppointment,'evt_world_c').record==before


def test_three_way_reconciliation_detects_execution_without_authorization(world):
    seed_world()
    with SessionLocal() as d:
        call=d.scalar(select(Call).where(Call.retell_call_id=='world_unauthorized_booking'))
        assert call is not None
        booking=d.scalar(select(Finding).where(Finding.call_id==call.id,Finding.type=='booking'))
        policy=d.scalar(select(Finding).where(Finding.call_id==call.id,Finding.type=='policy_unauthorized_action'))
        assert booking.state=='verified'
        assert policy.state=='flagged'
        facts=reconcile(d,booking)
        assert facts['execution']=='verified'
        assert facts['consent']=='accepted'
        assert facts['permission']=='not_allowed'
        ticket=d.scalar(select(CaseTicket).where(CaseTicket.finding_id==policy.id))
        assert ticket.status=='open'
        act_on_case(d,ticket.id,action='confirm',actor='Agency reviewer')
        act_on_case(d,ticket.id,action='close',actor='Agency reviewer')
        d.commit()
        assert ticket.status=='review_closed' and policy.state=='confirmed'


def test_policy_finding_dismissal_requires_reason_and_keeps_evidence(world):
    seed_world()
    with SessionLocal() as d:
        call=d.scalar(select(Call).where(Call.retell_call_id=='world_unauthorized_booking'))
        f=d.scalar(select(Finding).where(Finding.call_id==call.id,Finding.type=='policy_unauthorized_action'))
        ticket=d.scalar(select(CaseTicket).where(CaseTicket.finding_id==f.id))
        before_hash=f.manifest_hash
        with pytest.raises(ValueError,match='reason'):
            act_on_case(d,ticket.id,action='dismiss',actor='Ops')
        act_on_case(d,ticket.id,action='dismiss',actor='Ops',note='Reviewed and disputed policy interpretation')
        d.commit()
        assert f.state=='dismissed'
        assert f.manifest_hash==before_hash
        assert any(x.action=='dismiss' for x in d.scalars(select(CaseLog).where(CaseLog.case_id==ticket.id)))
