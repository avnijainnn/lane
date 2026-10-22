import hmac, json, os, secrets
from datetime import timedelta, datetime, timezone
from zoneinfo import ZoneInfo
from contextlib import asynccontextmanager
from typing import Annotated
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials, HTTPBearer, HTTPAuthorizationCredentials
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from .db import init_db, SessionLocal, Client, Manifest, Call, Finding, DemoAppointment, Observation, ExtractionResult, DatasetEvaluation, now
from .manifest import hash_policy, manifest_for_start
from .redact import text as redact_text, obj as redact_obj
from .time_utils import to_utc
from .pipeline import process_call
from .public_report import public_sgd_view
from .verify import check_finding, process_due_jobs

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(title='Lane Phase 0', docs_url=None, redoc_url=None, lifespan=lifespan)

@app.middleware('http')
async def form_origin_guard(request: Request, call_next):
    # Browser form writes must come from our own origin. A user-supplied host
    # alone is not a trust anchor; constrain allowed origins explicitly in live mode.
    if request.method in {'POST', 'PUT', 'PATCH', 'DELETE'} and request.url.path != '/webhooks/retell':
        content_type = request.headers.get('content-type', '').lower()
        if content_type.startswith(('application/x-www-form-urlencoded', 'multipart/form-data')):
            if os.getenv('DEMO_MODE','false').lower() != 'true':
                allowed = {x.strip().rstrip('/') for x in os.getenv('LANE_ALLOWED_ORIGINS','').split(',') if x.strip()}
                origin = request.headers.get('origin', '').rstrip('/')
                if not allowed or origin not in allowed:
                    return HTMLResponse('Browser form denied: configure LANE_ALLOWED_ORIGINS and send an allowed Origin', status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Cache-Control'] = 'no-store'
    return response

templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), 'templates'))
basic = HTTPBasic(auto_error=False)
bearer = HTTPBearer(auto_error=False)


def db_session():
    with SessionLocal() as db:
        yield db


def require_dashboard(credentials: HTTPBasicCredentials | None = Depends(basic)):
    u = os.getenv('DASHBOARD_USER', '')
    p = os.getenv('DASHBOARD_PASSWORD', '')
    if not (u and p and credentials and hmac.compare_digest(credentials.username, u) and hmac.compare_digest(credentials.password, p)):
        raise HTTPException(401, 'Login required', headers={'WWW-Authenticate':'Basic realm="Lane"'})
    return credentials.username


def require_token(creds: HTTPAuthorizationCredentials | None = Depends(bearer)):
    token = os.getenv('APP_TOKEN', '')
    if not token or not creds or not hmac.compare_digest(creds.credentials, token):
        raise HTTPException(401, 'Bad API token')
    return True


def demo_only():
    if os.getenv('DEMO_MODE', 'false').lower() != 'true':
        raise HTTPException(404, 'Demo disabled')

class ClientIn(BaseModel):
    name: str
    retell_agent_id: str
    highlevel_location_id: str
    timezone: str = 'America/Chicago'
    calendar_id: str

class ManifestIn(BaseModel):
    approved_agent_version: str
    approver: str
    approver_role: str
    never_ask: list[str] = Field(default_factory=lambda:['card_number','password','otp'])
    never_promise: list[str] = Field(default_factory=lambda:['price','discount','refund','guarantee'])
    deliverable_promises: list[str] = Field(default_factory=lambda:['sms_confirmation'])
    allowed_actions: list[str] = Field(default_factory=lambda:['book'])

@app.get('/health')
def health():
    return {'status': 'ok', 'note': 'Web process only; see /api/operations for worker and CRM coverage'}

@app.get('/api/operations', dependencies=[Depends(require_token)])
def operations(db: Session=Depends(db_session)):
    from .db import WorkerHeartbeat, Job
    from .time_utils import db_utc
    hb = db.get(WorkerHeartbeat,'pipeline')
    age = (now() - db_utc(hb.last_seen_at)).total_seconds() if hb else None
    open_calls = db.scalar(select(func.count(Call.id)).where(Call.processed.is_(False))) or 0
    due_jobs = db.scalar(select(func.count(Job.id)).where(Job.done.is_(False), Job.run_at<=now())) or 0
    live_clients = db.scalars(select(Client).where(Client.is_demo.is_(False))).all()
    return {'web': 'ok', 'worker': ('healthy' if age is not None and age <= 30 else 'stale_or_missing'),
            'worker_age_seconds':round(age, 1) if age is not None else None,
            'unprocessed_calls':open_calls,'due_calendar_checks':due_jobs,
            'live_clients':len(live_clients),
            'highlevel_token_configured':bool(os.getenv('HIGHLEVEL_TOKEN')),
            'client_coverage':'NOT_TESTED: live credentials and per-client calendar scope are not validated by this endpoint',
            'note':'For demo the worker is optional; for live use start a separate worker terminal.'} 

@app.post('/api/clients', dependencies=[Depends(require_token)])
def add_client(item: ClientIn, db: Session = Depends(db_session)):
    if os.getenv('DEMO_MODE','false').lower() != 'true' and os.getenv('LANE_ALLOW_SINGLE_WORKSPACE_LIVE') != 'yes':
        raise HTTPException(403, 'Prototype lacks tenant-safe authentication; live client registration disabled by default')
    if db.scalar(select(Client).where(Client.retell_agent_id == item.retell_agent_id)):
        raise HTTPException(409, 'Agent already registered')
    try: ZoneInfo(item.timezone)
    except Exception: raise HTTPException(400,'Unknown IANA timezone')
    client = Client(**item.model_dump())
    db.add(client)
    db.commit()
    return {'id':client.id, 'name':client.name}

@app.post('/api/clients/{client_id}/manifests', dependencies=[Depends(require_token)])
def add_manifest(client_id: int, item: ManifestIn, db: Session = Depends(db_session)):
    client = db.get(Client, client_id)
    if not client: raise HTTPException(404, 'Client not found')
    if item.approver_role not in ('agency','client'):
        raise HTTPException(400, 'role must be client or agency')
    # Deprecated write path is draft-only. It MUST NOT silently activate a policy.
    from .accountability import draft_policy
    try:
        draft = draft_policy(db, client_id, authored_by=item.approver, rules={
            'approved_agent_version': item.approved_agent_version,
            'never_ask': item.never_ask,
            'never_promise': item.never_promise,
            'deliverable_promises': item.deliverable_promises,
            'allowed_actions': item.allowed_actions,
        })
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    db.commit()
    return {'draft_id': draft.id, 'proposed_version': draft.proposed_version,
            'status': 'draft', 'active': False,
            'notice': 'Approval event required: POST /api/accountability/drafts/{draft_id}/approve'}


def signed_retell(body: bytes, signature: str | None) -> bool:
    secret = os.getenv('RETELL_API_KEY', '')
    if not signature or not secret: return False
    try:
        from retell import Retell
        return bool(Retell(api_key=secret).verify(body.decode('utf-8'), secret, signature))
    except Exception:
        return False


def store_call(db, payload: dict, *, demo=False):
    event_type = payload.get('event')
    if event_type != 'call_ended':
        return {'status':'ignored', 'event':event_type}
    call_data = payload.get('call') or {}
    call_id = call_data.get('call_id')
    agent_id = call_data.get('agent_id')
    if not call_id or not agent_id:
        raise HTTPException(422, 'Missing call or agent ID')
    client = db.scalar(select(Client).where(Client.retell_agent_id == agent_id))
    if not client or client.is_demo != demo:
        return {'status':'ignored','reason':'Unknown agent'}
    existing = db.scalar(select(Call).where(Call.retell_call_id == call_id))
    if existing: return {'status':'duplicate','id':existing.id}
    start_ms = call_data.get('start_timestamp')
    end_ms = call_data.get('end_timestamp')
    if not start_ms:
        # Must NOT bind to an arbitrary current manifest if start time was not provided.
        started_at = now()
        manifest = None
    else:
        started_at = to_utc(start_ms, client.timezone)
        manifest = manifest_for_start(db, client.id, started_at)
    ended_at = to_utc(end_ms, client.timezone) if end_ms else now()
    raw_transcript = call_data.get('transcript') or ''
    trace = call_data.get('transcript_with_tool_calls') or []
    if isinstance(raw_transcript, list):
        raw_transcript = '\n'.join(f"{t.get('role','?')}: {t.get('content','')}" for t in raw_transcript if isinstance(t, dict))
    if not isinstance(raw_transcript, str): raw_transcript = ''
    call = Call(client_id=client.id, retell_call_id=str(call_id),
                started_at=started_at, ended_at=ended_at,
                agent_version=str(call_data.get('agent_version') if call_data.get('agent_version') is not None else 'unknown'),
                transcript=redact_text(raw_transcript),tool_trace=redact_obj(trace),
                manifest_id=manifest.id if manifest else None,
                manifest_hash=manifest.sha256 if manifest else None,
                coverage='awaiting_processing' if manifest else 'unknown_manifest')
    db.add(call); db.commit()
    return {'status':'ingested','id':call.id,'bound_manifest':manifest.version if manifest else None}

@app.post('/webhooks/retell')
async def retell_webhook(request: Request, db: Session = Depends(db_session)):
    body = await request.body()
    if len(body) > 2_000_000: raise HTTPException(413, 'Webhook too large')
    if not signed_retell(body, request.headers.get('x-retell-signature')):
        raise HTTPException(401, 'Invalid Retell signature')
    try: payload = json.loads(body)
    except ValueError: raise HTTPException(400, 'Invalid JSON')
    # Ack quickly. The worker performs LLM extraction and independent verification.
    return store_call(db, payload)

@app.get('/')
def agency_home(_=Depends(require_dashboard)):
    return RedirectResponse('/accountability', status_code=303)

@app.get('/findings', response_class=HTMLResponse)
def dashboard(request: Request, _=Depends(require_dashboard), db:Session=Depends(db_session)):
    rows = db.execute(select(Finding, Call, Client).join(Call, Finding.call_id == Call.id).join(Client, Call.client_id == Client.id).order_by(Finding.id.desc())).all()
    return templates.TemplateResponse(request, 'dashboard.html', {'rows':rows,'demo_mode':os.getenv('DEMO_MODE','false').lower()=='true'})

@app.get('/findings/{finding_id}', response_class=HTMLResponse)
def detail(finding_id:int, request:Request, _=Depends(require_dashboard), db:Session=Depends(db_session)):
    f = db.get(Finding, finding_id)
    if not f: raise HTTPException(404)
    call = db.get(Call, f.call_id); client = db.get(Client,call.client_id)
    obs = db.scalars(select(Observation).where(Observation.finding_id == f.id).order_by(Observation.id.desc())).all()
    extracted = db.scalar(select(ExtractionResult).where(ExtractionResult.call_id == call.id))
    return templates.TemplateResponse(request,'finding.html',{
        'f':f, 'call':call, 'client':client, 'obs':obs,
        'demo_mode':client.is_demo, 'can_simulate_fix':client.is_demo and call.retell_call_id.startswith('demo_correction'),
        'extracted':extracted,
    })

@app.get('/evaluation', response_class=HTMLResponse)
def evaluation_dashboard(request:Request, _=Depends(require_dashboard), db:Session=Depends(db_session)):
    values = db.execute(
        select(DatasetEvaluation, Call, ExtractionResult)
        .join(Call, DatasetEvaluation.call_id == Call.id)
        .outerjoin(ExtractionResult, ExtractionResult.call_id == Call.id)
        .order_by(DatasetEvaluation.id.desc())
    ).all()
    total = len(values)
    covered = sum(bool(extraction and not extraction.abstain) for _,_,extraction in values)
    exact = sum(int(item.extraction_correct) for item,_,_ in values)
    matches = sum(int(item.state_correct) for item,_,_ in values)
    return templates.TemplateResponse(request, 'evaluation.html', {
        'rows':values, 'total':total, 'covered':covered, 'exact':exact,
        'matches':matches,
    })

@app.get('/comparison', response_class=HTMLResponse)
def comparison_dashboard(request: Request, _=Depends(require_dashboard)):
    # Only the known local report is readable; no user-supplied file paths.
    path = Path(__file__).resolve().parents[1] / 'datasets' / 'reports' / 'extraction_challenge.comparison.json'
    results = None
    if path.is_file():
        try:
            report_data = json.loads(path.read_text(encoding='utf-8'))
            if report_data.get('kind') == 'lane_transcript_extraction_comparison':
                results = report_data
        except (ValueError, OSError):
            pass
    root = Path(__file__).resolve().parents[1] / 'datasets' / 'reports'
    public_reports = []
    # Read only a few configured report names; do not accept arbitrary user paths.
    for name in ('sgd_groq_events_recovery.json','sgd_rules_same100.json','sgd_rules_1000.json',
                 'sgd_gemini_events.json','sgd_gemini.json',
                 'sgd_new_relevant_gemini.json','sgd_new_relevant_rules.json',
                 'sgd_gemini_events_full.json','sgd_full_gemini_100.json'):
        path = root / name
        if not path.is_file() or path.stat().st_size > 8_000_000:
            continue
        try:
            item=json.loads(path.read_text(encoding='utf-8'))
            if item.get('kind') == 'unlabelled_public_corpus_stress':
                public_reports.append({'name':name,'backend':item.get('backend'),
                   'evaluated':item.get('evaluated_n',0),'errors':item.get('provider_error_n',0),
                   'remaining':item.get('unprocessed_n',0),'requested':item.get('requested_n',0),
                   'abstentions':item.get('abstentions',0)})
        except (ValueError,OSError):
            pass
    sgd = public_sgd_view(Path(__file__).resolve().parents[1])
    return templates.TemplateResponse(request, 'comparison.html', {'report': results,'public_reports':public_reports,'sgd':sgd})

@app.post('/findings/{finding_id}/recheck')
def recheck_form(finding_id:int, _=Depends(require_dashboard), db:Session=Depends(db_session)):
    f = db.get(Finding, finding_id)
    if not f: raise HTTPException(404)
    check_finding(db,f,'manual'); db.commit()
    return RedirectResponse(f'/findings/{finding_id}', status_code=303)

@app.post('/api/findings/{finding_id}/recheck', dependencies=[Depends(require_token)])
def recheck_api(finding_id:int, db:Session=Depends(db_session)):
    f = db.get(Finding, finding_id)
    if not f: raise HTTPException(404)
    if f.type != 'booking': raise HTTPException(400,'Not a booking')
    check_finding(db,f,'manual'); db.commit()
    return {'id':f.id,'state':f.state,'reason':f.reason,'observed':f.observed}

@app.get('/api/findings', dependencies=[Depends(require_token)])
def findings_api(db:Session=Depends(db_session)):
    return [{'id':f.id,'type':f.type,'state':f.state,'reason':f.reason, 'call_id':f.call_id, 'manifest_hash':f.manifest_hash} for f in db.scalars(select(Finding).order_by(Finding.id.desc())).all()]

@app.post('/api/demo/seed', dependencies=[Depends(require_token)])
def seed_demo(db:Session=Depends(db_session)):
    demo_only()
    client = db.scalar(select(Client).where(Client.retell_agent_id == 'agent_demo_lane'))
    if client:
        existing = db.scalar(select(Finding).join(Call,Finding.call_id == Call.id).where(
            Call.client_id == client.id, Call.retell_call_id == 'demo_correction_extracted_v2'
        ).order_by(Finding.id.desc()))
        if existing: return {'finding_id':existing.id,'detail_url':f'/findings/{existing.id}', 'note':'Demo already seeded'}
    else:
        client = Client(name='Bright Smile Dental (SYNTHETIC)',retell_agent_id='agent_demo_lane',
                        highlevel_location_id='loc_demo',timezone='America/Chicago',calendar_id='cal_demo',is_demo=True)
        db.add(client);db.flush()
    latest = db.scalar(select(Manifest).where(Manifest.client_id == client.id))
    if not latest:
        body = {'manifest_id':f'client-{client.id}', 'version':1,
                'agent':{'platform':'retell','agent_id':client.retell_agent_id,'approved_agent_version':'14'},
                'record':{'system':'highlevel','location_id':'loc_demo','calendars':['cal_demo']},
                'timezone':client.timezone,'allowed_actions':['book'],'deliverable_promises':['sms_confirmation'],
                'never_promise':['discount','refund'],'never_ask':['card_number','password','otp']}
        latest = Manifest(client_id=client.id,version=1,body=body,sha256=hash_policy(body),
                          approved_agent_version='14',approver='Demo operator',approver_role='agency',
                          effective_from=now()-timedelta(days=1))
        db.add(latest);db.flush()
    local_day = (now().astimezone(ZoneInfo(client.timezone)) + timedelta(days=2)).date()
    at_nine = datetime.combine(local_day,datetime.min.time().replace(hour=9),tzinfo=ZoneInfo(client.timezone)).isoformat()
    at_ten = datetime.combine(local_day,datetime.min.time().replace(hour=10),tzinfo=ZoneInfo(client.timezone)).isoformat()
    record = {'id':'evt_demo_wrong','contactId':'contact_demo','calendarId':'cal_demo',
              'startTime':at_nine,'status':'booked','title':'Cleaning appointment'}
    old = db.get(DemoAppointment,'evt_demo_wrong')
    if old: old.record=record
    else: db.add(DemoAppointment(id='evt_demo_wrong',client_id=client.id,record=record))
    db.flush()
    call_data = {'event':'call_ended','call':{
        'call_id':'demo_correction_extracted_v2','agent_id':'agent_demo_lane','agent_version':14,
        'start_timestamp':int((now()-timedelta(minutes=5)).timestamp()*1000),
        'end_timestamp':int((now()-timedelta(minutes=3)).timestamp()*1000),
        'transcript':(f'Caller: Book {local_day.strftime("%B")} {local_day.day}, {local_day.year} at 9 AM. '
                      f'Actually, not 9, make it 10 AM.\n'
                      f'Agent: You are confirmed for 10 AM on {local_day.strftime("%B")} {local_day.day}.'),
        'transcript_with_tool_calls':[{'tool_name':'create_appointment','arguments':{'contactId':'contact_demo','calendarId':'cal_demo','startTime':at_nine},
                                       'result':{'eventId':'evt_demo_wrong'}}]}}
    # Transcript is parsed by the selected extractor; no labelled time is injected.
    outcome = store_call(db,call_data,demo=True)
    call = db.get(Call,outcome['id'])
    process_call(db, call, extraction_backend=os.getenv('DEMO_EXTRACTION_BACKEND', 'rules'))
    db.commit()
    finding = db.scalar(select(Finding).where(Finding.call_id==call.id,Finding.type=='booking'))
    if not finding:
        raise HTTPException(422, 'Demo extractor abstained. Use DEMO_EXTRACTION_BACKEND=rules or configure a working Groq/OpenAI key.')
    check_finding(db,finding,'2m')
    db.commit()
    return {'finding_id':finding.id,'detail_url':f'/findings/{finding.id}', 'note':'SYNTHETIC - no external HighLevel data'}

@app.post('/api/demo/fix/{finding_id}', dependencies=[Depends(require_token)])
def fix_demo(finding_id:int, db:Session=Depends(db_session)):
    demo_only()
    f = db.get(Finding,finding_id)
    if not f or f.type != 'booking': raise HTTPException(404)
    call = db.get(Call,f.call_id)
    client = db.get(Client,call.client_id)
    if not client.is_demo: raise HTTPException(403, 'Demo fix cannot modify live CRM')
    record = db.get(DemoAppointment,f.promised['appointment_id'])
    if not record: raise HTTPException(404, 'Demo record not found')
    record.record = dict(record.record, startTime=f.promised['final_start'])
    db.commit()
    return {'status':'synthetic_calendar_updated','finding_id':f.id,'next':'Click Re-check to verify'}

@app.post('/demo/seed')
def browser_demo_seed(_=Depends(require_dashboard), db:Session=Depends(db_session)):
    result = seed_demo(db)
    return RedirectResponse(result['detail_url'], status_code=303)

@app.post('/demo/fix/{finding_id}')
def browser_demo_fix(finding_id:int, _=Depends(require_dashboard), db:Session=Depends(db_session)):
    fix_demo(finding_id,db)
    return RedirectResponse(f'/findings/{finding_id}', status_code=303)

# The Phase-0 dataset-first accountability features are additive to the original
# webhook, verifier, extraction benchmark and findings routes.
from .accountability_routes import mount as _mount_accountability
_mount_accountability(app, templates, require_dashboard, require_token, db_session)
