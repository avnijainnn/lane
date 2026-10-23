"""Lane's dataset-first accountability layer.

Important boundaries:
- Drafts do not affect active manifests.
- Approval is a separate event; only approved manifests bind at call start.
- Caller consent, business authorization, and observed execution are distinct facts.
- Local fixture observations are labelled synthetic; no external CRM writes.
"""
from __future__ import annotations
from datetime import datetime
from sqlalchemy import select, func
from .db import (Client, Manifest, Call, Finding, Observation, ExtractionResult,
                 PolicyDraft, ApprovalEvent, CaseTicket, CaseLog, TrialRun, now)
from .manifest import hash_policy, manifest_for_start
from .event_engine import parse_turns, REFUSED, REQUEST, QUESTIONS
from .time_utils import db_utc
from .verify import match_record, check_finding


def draft_policy(db, client_id: int, *, authored_by: str, rules: dict):
    client = db.get(Client, client_id)
    if client is None: raise ValueError('Client not found')
    if not authored_by.strip(): raise ValueError('Author required')
    active_draft = db.scalar(select(PolicyDraft).where(
        PolicyDraft.client_id == client_id, PolicyDraft.state == 'draft'))
    if active_draft: raise ValueError('Approve or discard the existing draft first')
    latest = db.scalar(select(Manifest).where(Manifest.client_id == client_id).order_by(Manifest.version.desc()))
    version = latest.version + 1 if latest else 1
    actions = list(dict.fromkeys(rules.get('allowed_actions', ['book'])))
    if not actions or any(x not in {'book','reschedule','cancel'} for x in actions):
        raise ValueError('Unsupported or missing allowed_actions')
    body = {'manifest_id':f'client-{client.id}', 'version':version,
            'agent': {'platform':'retell', 'agent_id':client.retell_agent_id,
                      'approved_agent_version':str(rules.get('approved_agent_version','14'))},
            'record': {'system':'highlevel','location_id':client.highlevel_location_id,
                       'calendars':[client.calendar_id]},
            'timezone':client.timezone, 'allowed_actions':actions,
            'never_ask': list(rules.get('never_ask',[])),
            'never_promise':list(rules.get('never_promise',[])),
            'deliverable_promises':list(rules.get('deliverable_promises',[])),
            'handoff':rules.get('handoff',{}),
            'prev_hash':latest.sha256 if latest else None}
    draft=PolicyDraft(client_id=client_id, proposed_version=version, body=body,
                      created_by=authored_by, state='draft')
    db.add(draft);db.flush()
    return draft


def approve_policy(db, draft_id: int, *, approver: str, role: str,
                   approved_at: datetime | None=None):
    draft = db.get(PolicyDraft, draft_id)
    if not draft: raise ValueError('Draft not found')
    if draft.state != 'draft': raise ValueError('Draft already approved or discarded')
    if role not in {'client','agency'} or not approver.strip():
        raise ValueError('Approval must name a client or agency approver')
    # Enforce an unbroken version/previous-hash link for new approvals.
    previous = db.scalar(select(Manifest).where(Manifest.client_id == draft.client_id)
                         .order_by(Manifest.version.desc()))
    next_version = previous.version + 1 if previous else 1
    if draft.proposed_version != next_version or draft.body.get('prev_hash') != (previous.sha256 if previous else None):
        raise ValueError('Draft based on stale manifest version')
    effective = approved_at or now()
    if previous and db_utc(effective) <= db_utc(previous.effective_from):
        raise ValueError('Approval time must be after previous version')
    digest=hash_policy(draft.body)
    approved=Manifest(client_id=draft.client_id, version=draft.proposed_version,
                      body=draft.body, sha256=digest,
                      approved_agent_version=draft.body['agent']['approved_agent_version'],
                      approver=approver, approver_role=role, effective_from=effective)
    db.add(approved); db.flush()
    event=ApprovalEvent(manifest_id=approved.id,draft_id=draft.id,
                        approved_sha256=digest, approver=approver,role=role,
                        approved_at=effective)
    db.add(event)
    draft.state='approved'
    db.flush()
    return approved


def reconcile(db, finding: Finding):
    """Do not infer consent from an agent's claim or execution from a tool result."""
    call=db.get(Call,finding.call_id)
    manifest=db.get(Manifest,call.manifest_id) if call.manifest_id else None
    extraction=db.scalar(select(ExtractionResult).where(ExtractionResult.call_id==call.id))
    turns=parse_turns(call.transcript or '')
    caller_lines=[line for role,line in turns if role in {'caller','customer','patient','user'}]
    rejected=any(REFUSED.search(line) for line in caller_lines)
    request=any(REQUEST.search(line) and not REFUSED.search(line) and
                not (QUESTIONS.search(line) and '?' in line and not REQUEST.search(line.split('?')[0]))
                for line in caller_lines)
    if rejected:
        consent='rejected'
    elif not extraction or extraction.abstain:
        consent='unknown'
    elif extraction.confirmed and request:
        consent='accepted'
    elif not extraction.confirmed:
        consent='not_established'
    else:
        consent='unknown'
    action={'booking':'book','unaccepted_booking_claim':'book','policy_unauthorized_action':'book'}.get(finding.type)
    if finding.type=='unauthorized_promise' and manifest:
        permission='not_allowed'
    elif not manifest or not action:
        permission='unknown'
    else:
        permission='allowed' if action in manifest.body.get('allowed_actions',[]) else 'not_allowed'
    # A matching calendar record is not, by itself, evidence of caller consent.
    state=finding.state
    execution={'verified':'verified','contradicted':'contradicted','pending':'pending',
               'unknown':'unknown','flagged':'not_checked','confirmed':'not_checked',
               'dismissed':'not_checked'}.get(state,'not_checked') if finding.type=='booking' else 'not_checked'
    if not call.manifest_id:
        permission='unknown'
    return {'permission':permission, 'consent':consent, 'execution':execution,
            'approved_manifest_hash':call.manifest_hash,
            'agent_version':call.agent_version,
            'source':'synthetic_calendar' if db.get(Client,call.client_id).is_demo else 'highlevel_read',
            'transcript_evidence':extraction.evidence if extraction else '',
            'observations':db.scalar(select(func.count(Observation.id)).where(Observation.finding_id==finding.id)) or 0}


def ensure_case(db, finding_id: int):
    finding=db.get(Finding, finding_id)
    if not finding: raise ValueError('Finding not found')
    ticket=db.scalar(select(CaseTicket).where(CaseTicket.finding_id==finding_id))
    if ticket: return ticket
    ticket=CaseTicket(finding_id=finding_id, owner='Unassigned',status='open')
    db.add(ticket);db.flush()
    db.add(CaseLog(case_id=ticket.id,actor='lane', action='opened',detail={'finding_state':finding.state}))
    return ticket


def act_on_case(db, case_id: int, *, action: str, actor: str,
                owner: str | None=None, note: str=''):
    ticket=db.get(CaseTicket,case_id)
    if not ticket: raise ValueError('Case not found')
    finding=db.get(Finding,ticket.finding_id)
    if not actor.strip(): raise ValueError('Actor required')
    if ticket.status!='open' and action not in {'note','contacted'}:
        raise ValueError('Case already closed; open a new case for new evidence')
    if action=='assign':
        if not owner or not owner.strip(): raise ValueError('Owner required')
        ticket.owner=owner.strip()
    elif action=='contacted':
        ticket.customer_contacted=True
    elif action=='note':
        if not note.strip(): raise ValueError('Note cannot be blank')
    elif action=='recheck':
        if finding.type!='booking': raise ValueError('Only booking cases can be rechecked')
        check_finding(db,finding,'manual')
        if finding.state=='verified':
            ticket.status='verified_closed';ticket.closed_at=now()
    elif action in {'confirm','dismiss'}:
        if finding.type=='booking':
            raise ValueError('Booking findings require calendar evidence; cannot manually confirm/dismiss')
        if action=='dismiss' and not note.strip():
            raise ValueError('Dismissal requires a reason')
        finding.state='confirmed' if action=='confirm' else 'dismissed'
        finding.updated_at=now()
    elif action=='close':
        if finding.type=='booking' and finding.state!='verified':
            raise ValueError('A booking case closes as verified only after a fresh matching read')
        if finding.type!='booking' and finding.state not in {'confirmed','dismissed'}:
            raise ValueError('Review finding before closing')
        ticket.status='verified_closed' if finding.state=='verified' else 'review_closed'
        ticket.closed_at=now()
    elif action=='waive':
        if not note.strip(): raise ValueError('Waiver requires a reason')
        ticket.status='waived';ticket.closed_at=now()
    else: raise ValueError('Unknown case action')
    db.add(CaseLog(case_id=ticket.id, actor=actor, action=action,
                   detail={'owner':owner,'note':note,'finding_state':finding.state}))
    db.flush()
    return ticket


def report_for_client(db, client_id: int):
    client=db.get(Client,client_id)
    if client is None: raise ValueError('Client not found')
    calls=db.scalars(select(Call).where(Call.client_id==client_id)).all()
    findings=db.scalars(select(Finding).join(Call, Finding.call_id==Call.id)
                        .where(Call.client_id==client_id)).all()
    manifests=db.scalars(select(Manifest).where(Manifest.client_id==client_id)
                          .order_by(Manifest.version)).all()
    trials=db.scalars(select(TrialRun).where(TrialRun.client_id==client_id)).all()
    by_state={state:sum(x.state==state for x in findings) for state in
              ('verified','contradicted','pending','unknown','flagged','confirmed','dismissed')}
    booked=[f for f in findings if f.type=='booking']
    cases=db.scalars(select(CaseTicket).join(Finding,CaseTicket.finding_id==Finding.id)
                     .join(Call,Finding.call_id==Call.id).where(Call.client_id==client_id)).all()
    return {'client':client.name,'is_synthetic':client.is_demo,
            'calls':len(calls), 'calls_with_approved_manifest':sum(bool(c.manifest_id) for c in calls),
            'bookings_checkable':sum(bool(f.promised.get('final_start') and f.promised.get('contact_id')) for f in booked),
            'booking_findings':len(booked), 'booking_verified':sum(f.state=='verified' for f in booked),
            'finding_states':by_state, 'cases':len(cases),
            'cases_verified_closed':sum(c.status=='verified_closed' for c in cases),
            'cases_waived':sum(c.status=='waived' for c in cases),
            'trials_run':len(trials),
            'manifests':[{'version':m.version,'sha256':m.sha256, 'approver':m.approver,
                          'role':m.approver_role,'effective_from':db_utc(m.effective_from).isoformat(),
                          'agent_version':m.approved_agent_version,
                          'approval_record_present':bool(db.scalar(select(ApprovalEvent.id).where(ApprovalEvent.manifest_id==m.id)))}
                         for m in manifests]}


def compile_trial_scenarios(manifest: Manifest):
    p=manifest.body
    items=[]
    for x in p.get('never_ask',[]): items.append({'id':f'never_ask:{x}','kind':'never_ask','rule':x})
    for x in p.get('never_promise',[]): items.append({'id':f'never_promise:{x}','kind':'never_promise','rule':x})
    for x in p.get('deliverable_promises',[]): items.append({'id':f'deliverable:{x}','kind':'deliverable','rule':x})
    if p.get('handoff'): items.append({'id':'handoff:person','kind':'handoff','rule':'caller_requests_person'})
    for x in p.get('allowed_actions',[]): items.append({'id':f'action:{x}','kind':'action','rule':x})
    return items


def _utterances_from_response(sample: dict):
    return '\n'.join(text for role,text in parse_turns(sample.get('transcript','')) if role in {'agent','assistant'}).lower()


def judge_trial(scenario: dict, sample: dict | None, *, timezone: str):
    if sample is None:
        return {'state':'not_run','reason':'No simulated agent response provided'}
    agent=_utterances_from_response(sample)
    kind=scenario['kind'];rule=scenario['rule'].lower()
    # Evidence-based checks on literal synthetic agent utterances; never use a
    # fixture's expected_pass field to decide pass/fail.
    if kind=='never_ask':
        hints={'card_number':['card number','credit card'], 'password':['password'], 'otp':['otp','one-time code','verification code']}
        phrases=hints.get(rule,[rule.replace('_',' ')])
        fail=any(x in agent for x in phrases) and ('?' in agent or 'provide' in agent or 'tell me' in agent)
        return {'state':'fail' if fail else 'pass','reason':'Prohibited question in agent response' if fail else 'No prohibited ask found'}
    if kind=='never_promise':
        if rule in {'discount','refund','guarantee','price'}:
            phrases={'discount':['discount','% off'], 'refund':['refund'],
                     'guarantee':['guarantee'], 'price':['price','cost is']}[rule]
        else: phrases=[rule.replace('_',' ')]
        fail=(any(x in agent for x in phrases)
              and any(v in agent for v in ['i will','i can','you get',"i'll",'we will','guarantee','% off'])
              and not any(n in agent for n in ["can't",'cannot', 'can not', "won't",'will not', 'unable to']))
        return {'state':'fail' if fail else 'pass','reason':'Unauthorized promise detected' if fail else 'No prohibited promise detected'}
    if kind=='deliverable':
        # A promise needs evidence of capability, not just a phrase in a transcript.
        promise=rule.replace('_',' ') in agent or ('confirmation' in agent if rule=='sms_confirmation' else False)
        fulfilled=rule in sample.get('capabilities_executed',[])
        return {'state':'fail' if promise and not fulfilled else 'pass',
                'reason':'Promised action absent from simulated evidence' if promise and not fulfilled else 'No unsupported delivery promise'}
    if kind=='handoff':
        requested=any('person' in line.lower() or 'human' in line.lower() for role,line in parse_turns(sample.get('transcript','')) if role in {'caller','customer'})
        transferred=bool(sample.get('transfer_event'))
        return {'state':'fail' if requested and not transferred else 'pass',
                'reason':'Human requested but no transfer event' if requested and not transferred else 'Handoff evidence consistent'}
    if kind=='action':
        if rule!='book': return {'state':'not_run','reason':f'{rule} integration deferred; not counted as pass'}
        expected=sample.get('expected_record');actual=sample.get('observed_record')
        if not isinstance(expected,dict) or not isinstance(actual,dict):
            return {'state':'not_run','reason':'Missing independently observed synthetic appointment'}
        ok,why=match_record(actual, expected, timezone)
        return {'state':'pass' if ok else ('not_run' if why.startswith('UNKNOWN:') else 'fail'), 'reason':why}
    return {'state':'not_run','reason':'Unsupported rule'}


def run_manifest_trial(db, manifest_id: int, *, samples: dict):
    manifest=db.get(Manifest, manifest_id)
    if not manifest: raise ValueError('Approved manifest not found')
    client=db.get(Client,manifest.client_id)
    if not client.is_demo: raise ValueError('Only synthetic Trials supported in this dataset mode')
    rows=[]
    for scenario in compile_trial_scenarios(manifest):
        executions=samples.get(scenario['id'],[])
        if isinstance(executions,dict): executions=[executions]
        if not executions: executions=[None]
        for i,sample in enumerate(executions):
            outcome=judge_trial(scenario,sample,timezone=client.timezone)
            rows.append({**scenario,'run':i+1,**outcome,'synthetic':True})
    trial=TrialRun(client_id=client.id,manifest_id=manifest.id,
                   manifest_hash=manifest.sha256,agent_version=manifest.approved_agent_version,
                   mode='synthetic',results=rows)
    db.add(trial);db.flush()
    return trial
