import os
from sqlalchemy import select
from .db import Call, Finding, Client, Manifest, ExtractionResult
from .extract import extract_commitment, tool_refs
from .policy import label_agent_promises, unauthorized_promises
from .verify import create_booking_finding
from .time_utils import to_utc, AmbiguousTime


def process_call(db, call: Call, *, extraction_backend=None):
    if call.processed:
        return
    if not call.manifest_id:
        call.coverage = 'unknown_manifest'
        call.processed = True
        return
    manifest = db.get(Manifest, call.manifest_id)
    client = db.get(Client, call.client_id)
    if str(call.agent_version) != str(manifest.approved_agent_version):
        db.add(Finding(call_id=call.id, type='agent_drift', state='flagged',
                       reason=f'Running Retell version {call.agent_version}; manifest approved for {manifest.approved_agent_version}',
                       promised={}, observed={}, manifest_hash=call.manifest_hash,
                       agent_version=call.agent_version))
    # One Phase-0 transcript check. A classifier does NOT decide policy violations;
    # it merely labels alleged agent promises. Flags require human review.
    if call.transcript and not client.is_demo:
        labels = label_agent_promises(call.transcript)
        violations = [v for v in unauthorized_promises(labels, manifest.body.get('never_promise', []))
                      if v.exact_quote and v.exact_quote.lower() in call.transcript.lower()]
        if violations:
            detail = '; '.join(f"{label.kind}: {label.exact_quote[:170]}" for label in violations[:8])
            db.add(Finding(call_id=call.id, type='unauthorized_promise', state='flagged',
                           reason=f'For review: {detail}', promised={'labels':[x.model_dump() for x in violations]},
                           observed={}, manifest_hash=call.manifest_hash,
                           agent_version=call.agent_version))
    backend = extraction_backend or os.getenv('EXTRACTION_BACKEND', 'openai')
    extraction = extract_commitment(
        call.transcript, client.timezone,
        backend=backend, reference_time=call.started_at
    )
    db.add(ExtractionResult(
        call_id=call.id,
        backend=backend,
        confirmed=extraction.confirmed, abstain=extraction.abstain,
        final_start=extraction.final_start, evidence=extraction.evidence,
        reason=extraction.reason,
    ))
    db.flush()
    if extraction.abstain:
        call.coverage = 'unknown_extraction'
        call.processed = True
        return
    if not extraction.confirmed:
        # A unilateral booking claim is not caller consent. It may be an
        # unrequested booking incident, so flag for human review, never verify.
        # The transcript is untrusted evidence; no CRM action is taken.
        from .event_engine import parse_turns, BOOKED, NEGATED, REFUSED, QUESTIONS, REQUEST
        turns = parse_turns(call.transcript or '')
        agent_claim = any(BOOKED.search(t) and not NEGATED.search(t) for r,t in turns if r in {'agent','assistant'})
        rejection = any(REFUSED.search(t) for r,t in turns if r in {'caller','customer','patient','user'})
        no_positive_request = not any(REQUEST.search(t) and not REFUSED.search(t) for r,t in turns if r in {'caller','customer','patient','user'})
        if agent_claim and (rejection or no_positive_request):
            db.add(Finding(call_id=call.id, type='unaccepted_booking_claim', state='flagged',
                           reason='Agent claimed a booking without clear caller acceptance; review transcript/tool trace',
                           promised={'extraction_backend':backend}, observed={},
                           manifest_hash=call.manifest_hash, agent_version=call.agent_version))
        call.coverage = 'no_confirmed_booking'
        call.processed = True
        return
    refs = tool_refs(call.tool_trace)
    try:
        final_start = to_utc(extraction.final_start, client.timezone).isoformat()
    except (ValueError, TypeError, AmbiguousTime):
        final_start = None
    promised = {
        'final_start':final_start,
        'contact_id':refs['contact_id'],
        'calendar_id':client.calendar_id,
        'appointment_id':refs['appointment_id'],
        'evidence':extraction.evidence,
        'extraction_backend': backend,
        'tool_calendar_id':refs['calendar_id'],
    }
    issue = None
    if any(refs[k+'_ambiguous'] for k in ('appointment_id','contact_id','calendar_id')):
        issue = 'Conflicting identifier values in tool events'
    elif not final_start or not refs['contact_id']:
        issue = 'Cannot safely determine final time or contact identity'
    if 'book' not in manifest.body.get('allowed_actions', []):
        db.add(Finding(call_id=call.id,type='policy_unauthorized_action',state='flagged',
                       reason='Agent booked an appointment outside approved allowed_actions; review required',
                       promised={'action':'book'}, observed={},manifest_hash=call.manifest_hash,
                       agent_version=call.agent_version))
    finding = create_booking_finding(db, call, promised)
    if issue:
        finding.state = 'unknown'
        finding.reason = issue
        # Do not run scheduled checks when required identifiers are absent.
        for job in db.new:
            if getattr(job,'finding_id',None) == finding.id: job.done = True
        call.coverage = 'unknown_identity_or_time'
    else:
        call.coverage = 'booking_check_scheduled'
    call.processed = True
    from .accountability import ensure_case
    for f in db.scalars(select(Finding).where(Finding.call_id == call.id)).all():
        if f.state in {'contradicted','unknown','flagged','pending'}:
            ensure_case(db, f.id)


def process_unprocessed_calls(db, limit=10):
    stmt = select(Call).where(Call.processed.is_(False)).order_by(Call.id).limit(limit)
    if db.bind.dialect.name == 'postgresql':
        stmt = stmt.with_for_update(skip_locked=True)
    calls = db.scalars(stmt).all()
    for call in calls:
        process_call(db, call)
        db.flush()
        from .accountability import ensure_case
        for f in db.scalars(select(Finding).where(Finding.call_id == call.id)).all():
            if f.state in {'contradicted','unknown','flagged','pending'}:
                ensure_case(db, f.id)
    db.commit()
    return len(calls)
