"""Run full Phase-0 dataset regression in an isolated *temporary* SQLite DB.

Outputs report of scripted synthetic cases, not model accuracy/customer evidence.
Never alters the user's SQLite DB. Live API keys are not used.
"""
import argparse, json, os, subprocess, sys, tempfile
from datetime import timedelta
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def run_internal(path):
    # Imports happen after DATABASE_URL points at isolated temporary DB.
    from sqlalchemy import select
    from app.db import SessionLocal, Client,Call,Finding,Manifest,PolicyDraft,ApprovalEvent,CaseTicket,Observation,DemoAppointment,TrialRun,now
    from app.main import store_call
    from app.pipeline import process_call
    from app.accountability import reconcile,report_for_client,act_on_case,compile_trial_scenarios
    from app.verify import check_finding
    from scripts.seed_agency_world import seed_world
    seed_world()
    cases=[json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]
    results=[]
    with SessionLocal() as db:
        clients={name:db.scalar(select(Client).where(Client.retell_agent_id==f'agent_world_{name}')) for name in {'bright','cedar'}}
        for row in cases:
            client=clients[row['client']]
            started=now()-timedelta(minutes=3)
            payload={'event':'call_ended','call':{
                'call_id':row['id'],'agent_id':client.retell_agent_id,
                'agent_version':int(row['agent_version']),
                'start_timestamp':int(started.timestamp()*1000),
                'end_timestamp':int((started+timedelta(minutes=1)).timestamp()*1000),
                'transcript':row['transcript'],
                'transcript_with_tool_calls':[{'tool_name':'create_appointment',
                    'arguments':{'contactId':row['contact_id'],'calendarId':client.calendar_id},
                    'result':{'eventId':row['appointment_id']}}]}}
            receipt=store_call(db,payload,demo=True)
            call=db.get(Call,receipt['id'])
            # The extractor receives only transcript, timezone and call start.
            process_call(db,call,extraction_backend='rules')
            db.flush()
            finding=db.scalar(select(Finding).where(Finding.call_id==call.id,Finding.type=='booking'))
            if finding:
                for snapshot in row['calendar_events']:
                    rec=snapshot['record']
                    old=db.get(DemoAppointment,row['appointment_id'])
                    if rec is None:
                        if old:db.delete(old)
                    else:
                        new={**rec,'id':row['appointment_id'],
                             'contactId':rec.get('contactId',row['contact_id']),
                             'calendarId':rec.get('calendarId',client.calendar_id)}
                        if old:old.record=new
                        else:db.add(DemoAppointment(id=row['appointment_id'],client_id=client.id,record=new))
                    db.flush()
                    check_finding(db,finding,'60m' if snapshot['minute']>=60 else f"{snapshot['minute']}m")
            drift=bool(db.scalar(select(Finding.id).where(Finding.call_id==call.id,Finding.type=='agent_drift')))
            policy=bool(db.scalar(select(Finding.id).where(Finding.call_id==call.id,Finding.type=='policy_unauthorized_action')))
            facts=reconcile(db,finding) if finding else {'consent':'not_established','permission':'allowed'}
            observed=finding.state if finding else 'none'
            correct=(observed==row['expected_booking_state'] and
                     facts['consent']==row['expected_consent'] and
                     facts['permission']==row['expected_permission'] and
                     policy==row['expected_policy_flag'] and drift==row['expected_agent_drift'])
            results.append({'id':row['id'],'category':row['category'],
                            'booking_state':observed,'expected_state':row['expected_booking_state'],
                            'permission':facts['permission'],'consent':facts['consent'],
                            'drift':drift,'policy_flag':policy,'pass':correct})
        db.commit()
        # Test the other four workflows against the same isolated workspace.
        bright=clients['bright']
        active=db.scalar(select(Manifest).where(Manifest.client_id==bright.id))
        draft=db.scalar(select(PolicyDraft).where(PolicyDraft.client_id==bright.id,PolicyDraft.state=='draft'))
        approvals=db.scalars(select(ApprovalEvent)).all()
        historical_binding=all(c.manifest_hash==db.get(Manifest,c.manifest_id).sha256
                               for c in db.scalars(select(Call).where(Call.manifest_id.is_not(None))))
        draft_inactive=(draft is not None and draft.proposed_version==2 and active.version==1)
        case=db.scalar(select(CaseTicket).join(Finding,CaseTicket.finding_id==Finding.id)
                       .join(Call,Finding.call_id==Call.id).where(Call.retell_call_id=='world_wrong_time'))
        f=db.get(Finding,case.finding_id)
        owner_before=case.owner
        act_on_case(db,case.id,action='assign',actor='Synthetic benchmark',owner='Synthetic owner')
        record=db.get(DemoAppointment,f.promised['appointment_id'])
        old=dict(record.record)
        record.record={**old,'startTime':f.promised['final_start']}
        db.flush()
        act_on_case(db,case.id,action='recheck',actor='Synthetic owner')
        resolution_ok=f.state=='verified' and case.status=='verified_closed'
        report=report_for_client(db,bright.id)
        report_ok=report['booking_verified']>=2 and report['cases_verified_closed']>=1
        trials=db.scalars(select(TrialRun)).all()
        trial_ok=bool(trials) and all(t.manifest_hash==db.get(Manifest,t.manifest_id).sha256 for t in trials)
        db.commit()
    return {'kind':'synthetic_accountability_regression','source':str(path),'total':len(results),
            'passed':sum(r['pass'] for r in results),
            'failed':[r for r in results if not r['pass']],
            'checks':{'draft_inactive':draft_inactive,'approvals_recorded':len(approvals)==3,
                      'historical_policy_binding':historical_binding,'verified_case_resolution':resolution_ok,
                      'report_denominator_and_fix':report_ok,'manifest_derived_trials':trial_ok},
            'breakdown':{category:{'total':sum(r['category']==category for r in results),
                                   'passed':sum(r['category']==category and r['pass'] for r in results)}
                         for category in sorted({r['category'] for r in results})},
            'notice':'Synthetic scripted fixtures, not real-call accuracy or verified HighLevel writes.'}

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--internal',action='store_true')
    p.add_argument('--dataset',default=str(ROOT/'datasets/accountability_regression_72.jsonl'))
    p.add_argument('--output',default=str(ROOT/'datasets/reports/accountability_regression.json'))
    args=p.parse_args()
    if args.internal:
        result=run_internal(args.dataset)
        print(json.dumps(result))
        return
    with tempfile.TemporaryDirectory(prefix='lane-accountability-') as temp:
        env=os.environ.copy()
        env['DATABASE_URL']='sqlite:///'+str(Path(temp)/'isolated.db').replace('\\','/')
        env['DEMO_MODE']='true'
        env['DEMO_EXTRACTION_BACKEND']='rules'
        command=[sys.executable,'-m','scripts.evaluate_accountability','--internal','--dataset',str(Path(args.dataset).resolve())]
        result=subprocess.run(command,cwd=ROOT,env=env,text=True,capture_output=True)
        if result.returncode:
            print(result.stdout);print(result.stderr,file=sys.stderr)
            raise SystemExit(result.returncode)
        report=json.loads(result.stdout.strip().splitlines()[-1])
    target=Path(args.output);target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(f"Scripted synthetic scenarios matching expected results: {report['passed']}/{report['total']}")
    for k,v in report['checks'].items():print(f"{k}: {'PASS' if v else 'FAIL'}")
    print('Report:',target)
    print(report['notice'])
    if report['passed']!=report['total'] or not all(report['checks'].values()):raise SystemExit(1)

if __name__=='__main__':main()
