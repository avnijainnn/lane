"""Install a linked synthetic agency workspace; no real external API reads/writes."""
import argparse, json
from datetime import timedelta
from pathlib import Path
from sqlalchemy import select
from app.db import (init_db,SessionLocal,Client,Manifest,Call,Finding,DemoAppointment,
                    CalendarSnapshot,PolicyDraft,TrialRun,now)
from app.accountability import draft_policy,approve_policy,ensure_case,run_manifest_trial
from app.main import store_call
from app.pipeline import process_call
from app.verify import check_finding

ROOT=Path(__file__).resolve().parents[1]

def seed_world(*, source=ROOT/'datasets/agency_world.json'):
    data=json.loads(Path(source).read_text(encoding='utf-8'))
    samples=json.loads((ROOT/'datasets/trial_responses.json').read_text(encoding='utf-8'))
    init_db()
    with SessionLocal() as db:
        clients={}
        for spec in data['clients']:
            client=db.scalar(select(Client).where(Client.retell_agent_id==spec['agent_id']))
            if not client:
                client=Client(name=spec['name'],retell_agent_id=spec['agent_id'],
                              highlevel_location_id=spec['location'],timezone=spec['timezone'],
                              calendar_id=spec['calendar'],is_demo=True)
                db.add(client);db.flush()
            elif not client.is_demo: raise ValueError('Refusing to alter non-demo client')
            clients[spec['key']]=client
            manifest=db.scalar(select(Manifest).where(Manifest.client_id==client.id).order_by(Manifest.version.desc()))
            if not manifest:
                draft=draft_policy(db,client.id,authored_by='Agency setup (SYNTHETIC)',rules=spec['policy'])
                manifest=approve_policy(db,draft.id,approver=('Client owner (SYNTHETIC)' if spec['key']=='harbor' else 'Agency manager (SYNTHETIC)'),
                                        role=('client' if spec['key']=='harbor' else 'agency'),approved_at=now()-timedelta(hours=1))
            if spec['key']=='bright' and not db.scalar(select(PolicyDraft).where(
                    PolicyDraft.client_id==client.id, PolicyDraft.state=='draft')):
                draft_policy(db,client.id,authored_by='Agency setup (SYNTHETIC)',
                             rules={**spec['policy'],'approved_agent_version':'15','never_promise':['discount','refund','guarantee']})
        db.flush()
        seeded=[]
        for row in data['calls']:
            client=clients[row['client']]
            existing=db.scalar(select(Call).where(Call.retell_call_id==row['id']))
            if existing:
                seeded.append(row['id']);continue
            latest=db.scalar(select(Manifest).where(Manifest.client_id==client.id).order_by(Manifest.version.desc()))
            started=now()-timedelta(minutes=5)
            payload={'event':'call_ended','call':{
                'call_id':row['id'], 'agent_id':client.retell_agent_id,
                'agent_version':int(row['agent_version']),
                'start_timestamp':int(started.timestamp()*1000),
                'end_timestamp':int((started+timedelta(minutes=2)).timestamp()*1000),
                'transcript':row['transcript'],
                'transcript_with_tool_calls':[{'tool_name':'create_appointment',
                    'arguments':{'contactId':row['contact'],'calendarId':client.calendar_id},
                    'result':{'eventId':row['appointment']}}]}}
            received=store_call(db,payload,demo=True)
            call=db.get(Call,received['id'])
            process_call(db,call,extraction_backend='rules')
            db.flush()
            finding=db.scalar(select(Finding).where(Finding.call_id==call.id,Finding.type=='booking'))
            if finding:
                for event in row['timeline']:
                    record=event['record']
                    record=({**record,'id':row['appointment'], 'contactId':row['contact'],
                             'calendarId':client.calendar_id} if record else None)
                    db.add(CalendarSnapshot(scenario=row['id'],finding_id=finding.id,
                                            minute=event['minute'],record=record))
                db.flush()
                for event in row['timeline']:
                    advance_calendar(db,finding.id,event['minute'])
                if not row['timeline']:
                    check_finding(db,finding,'60m')
                if finding.state in {'contradicted','pending','unknown'}:
                    ensure_case(db,finding.id)
            # Drift finding can coexist with the booking finding.
            drift=db.scalar(select(Finding).where(Finding.call_id==call.id,Finding.type=='agent_drift'))
            if drift: ensure_case(db,drift.id)
            policy=db.scalar(select(Finding).where(Finding.call_id==call.id,Finding.type=='policy_unauthorized_action'))
            if policy: ensure_case(db,policy.id)
            seeded.append(row['id'])
        for key, client in clients.items():
            existing=db.scalar(select(Manifest).where(Manifest.client_id==client.id).order_by(Manifest.version.desc()))
            if not db.scalar(select(TrialRun).where(TrialRun.manifest_id==existing.id)):
                run_manifest_trial(db,existing.id,samples=samples.get(key,{}))
        db.commit()
        return {'clients':len(clients),'calls':len(seeded),'scenario_ids':seeded,
                'kind':'synthetic_only'}


def advance_calendar(db, finding_id: int, minute: int):
    """Apply one time-indexed *synthetic* record, then run real Lane matching logic."""
    row=db.scalar(select(CalendarSnapshot).where(CalendarSnapshot.finding_id==finding_id,
                                                 CalendarSnapshot.minute==minute))
    if row is None: raise ValueError('Calendar snapshot not found')
    finding=db.get(Finding,finding_id)
    call=db.get(Call,finding.call_id)
    client=db.get(Client,call.client_id)
    if not client.is_demo: raise ValueError('Never mutate a real calendar')
    appointment_id=finding.promised.get('appointment_id')
    if not appointment_id: raise ValueError('Appointment ID missing')
    appointment=db.get(DemoAppointment,appointment_id)
    if appointment and appointment.client_id!=client.id: raise ValueError('Client isolation violation')
    if row.record is None:
        if appointment: db.delete(appointment)
    elif appointment: appointment.record=dict(row.record)
    else: db.add(DemoAppointment(id=appointment_id,client_id=client.id,record=dict(row.record)))
    db.flush()
    finding=check_finding(db,finding,'60m' if minute>=60 else f'{minute}m')
    return finding

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--dataset',default=str(ROOT/'datasets/agency_world.json'))
    args=p.parse_args(); print(json.dumps(seed_world(source=args.dataset),indent=2))
