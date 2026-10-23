"""Single-workspace pilot UI. Not a multi-tenant production authorization layer."""
import json
from pathlib import Path
from fastapi import Depends, HTTPException, Form, Request
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from .db import (Client,Manifest,PolicyDraft,ApprovalEvent,Call,Finding,CaseTicket,CaseLog,
                 TrialRun,CalendarSnapshot,DemoAppointment,now)
from .accountability import (draft_policy,approve_policy,reconcile,ensure_case,
                             act_on_case,report_for_client,compile_trial_scenarios,run_manifest_trial)

ROOT=Path(__file__).resolve().parents[1]

class DraftInput(BaseModel):
    created_by: str = 'Agency delivery lead'
    rules: dict = Field(default_factory=dict)

class ApprovalInput(BaseModel):
    approver: str
    role: str

class ActionInput(BaseModel):
    action: str
    actor: str
    owner: str|None=None
    note: str=''


def mount(app, templates, require_dashboard, require_token, db_session):

    @app.get('/accountability')
    def overview(request:Request, _=Depends(require_dashboard),db:Session=Depends(db_session)):
        clients=db.scalars(select(Client).order_by(Client.name)).all()
        rows=[(c,report_for_client(db,c.id)) for c in clients]
        return templates.TemplateResponse(request,'accountability.html',{'rows':rows,'synthetic':True})

    @app.get('/accountability/clients/{client_id}')
    def client_page(client_id:int,request:Request,_=Depends(require_dashboard),db:Session=Depends(db_session)):
        client=db.get(Client,client_id)
        if not client: raise HTTPException(404)
        drafts=db.scalars(select(PolicyDraft).where(PolicyDraft.client_id==client_id).order_by(PolicyDraft.id.desc())).all()
        manifests=db.scalars(select(Manifest).where(Manifest.client_id==client_id).order_by(Manifest.version.desc())).all()
        report=report_for_client(db,client_id)
        trials=db.scalars(select(TrialRun).where(TrialRun.client_id==client_id).order_by(TrialRun.id.desc())).all()
        return templates.TemplateResponse(request,'accountability_client.html',
            {'client':client,'drafts':drafts,'manifests':manifests,'report':report,'trials':trials})

    @app.post('/api/accountability/clients/{client_id}/draft',dependencies=[Depends(require_token)])
    def draft_api(client_id:int,item:DraftInput,db:Session=Depends(db_session)):
        try: draft=draft_policy(db,client_id,authored_by=item.created_by,rules=item.rules)
        except ValueError as exc: raise HTTPException(400,str(exc))
        db.commit()
        return {'draft_id':draft.id,'version':draft.proposed_version,'status':draft.state,
                'notice':'Draft is NOT active until approved'}

    @app.post('/accountability/clients/{client_id}/draft')
    def draft_form(client_id:int, authored_by:str=Form(...),rules_json:str=Form(...),
                   _=Depends(require_dashboard),db:Session=Depends(db_session)):
        try:
            rules=json.loads(rules_json)
            draft_policy(db,client_id,authored_by=authored_by,rules=rules)
            db.commit()
        except (ValueError,TypeError) as exc: raise HTTPException(400,str(exc))
        return RedirectResponse(f'/accountability/clients/{client_id}',status_code=303)

    @app.post('/api/accountability/drafts/{draft_id}/approve',dependencies=[Depends(require_token)])
    def approve_api(draft_id:int,item:ApprovalInput,db:Session=Depends(db_session)):
        try: manifest=approve_policy(db,draft_id,approver=item.approver,role=item.role)
        except ValueError as exc: raise HTTPException(400,str(exc))
        db.commit()
        return {'version':manifest.version,'sha256':manifest.sha256,'approved_by':manifest.approver,
                'role':manifest.approver_role}

    @app.post('/accountability/drafts/{draft_id}/approve')
    def approve_form(draft_id:int,approver:str=Form(...),role:str=Form(...),
                     _=Depends(require_dashboard),db:Session=Depends(db_session)):
        try: manifest=approve_policy(db,draft_id,approver=approver,role=role)
        except ValueError as exc: raise HTTPException(400,str(exc))
        db.commit()
        return RedirectResponse(f'/accountability/clients/{manifest.client_id}',status_code=303)

    @app.get('/accountability/queue')
    def queue_page(request:Request,_=Depends(require_dashboard),db:Session=Depends(db_session)):
        findings=db.scalars(select(Finding).order_by(Finding.id.desc())).all()
        rows=[]
        for f in findings:
            c=db.get(Call,f.call_id);client=db.get(Client,c.client_id)
            ticket=db.scalar(select(CaseTicket).where(CaseTicket.finding_id==f.id))
            # Cases created on demand in the queue do not silently mutate verification facts.
            if not ticket and f.state in {'contradicted','unknown','flagged','pending'}:
                ticket=ensure_case(db,f.id)
            if ticket:rows.append({'ticket':ticket,'finding':f,'client':client,
                                   'facts':reconcile(db,f)})
        db.commit()
        return templates.TemplateResponse(request,'accountability_queue.html',{'rows':rows})

    @app.get('/accountability/cases/{case_id}')
    def case_detail(case_id:int,request:Request,_=Depends(require_dashboard),db:Session=Depends(db_session)):
        ticket=db.get(CaseTicket,case_id)
        if not ticket: raise HTTPException(404)
        finding=db.get(Finding,ticket.finding_id)
        call=db.get(Call,finding.call_id);client=db.get(Client,call.client_id)
        logs=db.scalars(select(CaseLog).where(CaseLog.case_id==case_id).order_by(CaseLog.id)).all()
        return templates.TemplateResponse(request,'accountability_case.html',
            {'ticket':ticket,'finding':finding,'call':call,'client':client,'logs':logs,
             'facts':reconcile(db,finding)})

    @app.post('/api/accountability/cases/{case_id}/action',dependencies=[Depends(require_token)])
    def action_api(case_id:int,item:ActionInput,db:Session=Depends(db_session)):
        try: ticket=act_on_case(db,case_id,**item.model_dump())
        except ValueError as exc: raise HTTPException(400,str(exc))
        db.commit()
        return {'case_id':ticket.id,'status':ticket.status,'owner':ticket.owner}

    @app.post('/accountability/cases/{case_id}/action')
    def action_form(case_id:int, action:str=Form(...), actor:str=Form(...),
                    owner:str=Form(''),note:str=Form(''),
                    _=Depends(require_dashboard),db:Session=Depends(db_session)):
        try: act_on_case(db,case_id,action=action,actor=actor,owner=owner,note=note)
        except ValueError as exc: raise HTTPException(400,str(exc))
        db.commit()
        return RedirectResponse(f'/accountability/cases/{case_id}',status_code=303)

    @app.post('/accountability/cases/{case_id}/simulate-repair')
    def simulated_repair(case_id:int,_=Depends(require_dashboard),db:Session=Depends(db_session)):
        ticket=db.get(CaseTicket,case_id)
        if not ticket: raise HTTPException(404)
        finding=db.get(Finding,ticket.finding_id)
        call=db.get(Call,finding.call_id)
        client=db.get(Client,call.client_id)
        if not client.is_demo or finding.type!='booking': raise HTTPException(403,'Only synthetic booking records')
        p=finding.promised
        if not all(p.get(k) for k in ('appointment_id','contact_id','calendar_id','final_start')):
            raise HTTPException(400,'Cannot fabricate missing evidence')
        record=db.get(DemoAppointment,p['appointment_id'])
        new={'id':p['appointment_id'],'contactId':p['contact_id'],
             'calendarId':p['calendar_id'],'startTime':p['final_start'],'status':'booked'}
        if record and record.client_id!=client.id: raise HTTPException(403,'Client mismatch')
        if record: record.record=new
        else:db.add(DemoAppointment(id=p['appointment_id'],client_id=client.id,record=new))
        db.add(CaseLog(case_id=ticket.id,actor='Demo operator',action='synthetic_calendar_changed',
                       detail={'record':new,'warning':'synthetic only; not yet rechecked'}))
        db.commit()
        return RedirectResponse(f'/accountability/cases/{case_id}',status_code=303)

    @app.get('/accountability/reconcile/{finding_id}')
    def reconciliation_page(finding_id:int,request:Request,_=Depends(require_dashboard),db:Session=Depends(db_session)):
        f=db.get(Finding,finding_id)
        if not f: raise HTTPException(404)
        call=db.get(Call,f.call_id); client=db.get(Client,call.client_id)
        return templates.TemplateResponse(request,'accountability_reconcile.html',
                 {'finding':f,'call':call,'client':client,'facts':reconcile(db,f)})

    @app.get('/accountability/reports/{client_id}')
    def report_page(client_id:int,request:Request,_=Depends(require_dashboard),db:Session=Depends(db_session)):
        try: data=report_for_client(db,client_id)
        except ValueError: raise HTTPException(404)
        return templates.TemplateResponse(request,'accountability_report.html',{'report':data,'client_id':client_id})

    @app.get('/accountability/export/{client_id}')
    def report_json(client_id:int,_=Depends(require_dashboard),db:Session=Depends(db_session)):
        try: data=report_for_client(db,client_id)
        except ValueError: raise HTTPException(404)
        return JSONResponse(data,headers={'Content-Disposition':f'attachment; filename="lane_client_{client_id}_synthetic.json"'})

    @app.get('/accountability/trials/{manifest_id}')
    def trial_page(manifest_id:int,request:Request,_=Depends(require_dashboard),db:Session=Depends(db_session)):
        manifest=db.get(Manifest,manifest_id)
        if not manifest: raise HTTPException(404)
        runs=db.scalars(select(TrialRun).where(TrialRun.manifest_id==manifest_id).order_by(TrialRun.id.desc())).all()
        return templates.TemplateResponse(request,'accountability_trials.html',
              {'manifest':manifest,'client':db.get(Client,manifest.client_id),
               'scenarios':compile_trial_scenarios(manifest),'runs':runs})

    @app.post('/accountability/trials/{manifest_id}/run')
    def trial_form(manifest_id:int,_=Depends(require_dashboard),db:Session=Depends(db_session)):
        manifest=db.get(Manifest,manifest_id)
        if not manifest: raise HTTPException(404)
        client=db.get(Client,manifest.client_id)
        samples=json.loads((ROOT/'datasets/trial_responses.json').read_text(encoding='utf-8'))
        key='bright' if client.retell_agent_id=='agent_world_bright' else 'harbor' if client.retell_agent_id=='agent_world_harbor' else None
        if not key:raise HTTPException(400,'No synthetic trial responses for this client')
        try: run_manifest_trial(db,manifest.id,samples=samples[key])
        except ValueError as exc:raise HTTPException(400,str(exc))
        db.commit()
        return RedirectResponse(f'/accountability/trials/{manifest_id}',status_code=303)

    @app.post('/api/accountability/demo/seed',dependencies=[Depends(require_token)])
    def seed_api():
        from .main import demo_only
        demo_only()
        from scripts.seed_agency_world import seed_world
        return seed_world()

    @app.post('/accountability/demo/seed')
    def seed_form(_=Depends(require_dashboard)):
        from .main import demo_only
        demo_only()
        from scripts.seed_agency_world import seed_world
        seed_world()
        return RedirectResponse('/accountability',status_code=303)
