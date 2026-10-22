from datetime import timedelta
from sqlalchemy import select
from .db import Finding, Observation, Job, Call, Client, now
from .highlevel import HighLevel, CRMError
from .time_utils import to_utc, db_utc, AmbiguousTime
from .redact import obj

FINAL_STAGE = '60m'
VALID_STATUSES = {'booked','confirmed'}


def match_record(record, expected: dict, tzname: str):
    """Returns (matched, reason); never treats unknown fields as success."""
    if not record:
        return False, 'Appointment missing from calendar read'
    required = ('contactId', 'calendarId', 'startTime', 'status')
    if any(record.get(k) is None for k in required):
        return False, 'UNKNOWN: Appointment record missing fields needed to verify'
    if expected.get('appointment_id') and record.get('id') != expected['appointment_id']:
        return False, 'Appointment ID differs from tool result'
    if record.get('contactId') != expected['contact_id']:
        return False, 'Contact mismatch'
    if record.get('calendarId') != expected['calendar_id']:
        return False, 'Calendar mismatch or calendar ID not present'
    if str(record.get('status','')).lower() not in VALID_STATUSES:
        return False, 'Booking status not confirmed/booked'
    try:
        start = to_utc(record.get('startTime'), tzname)
        wanted = to_utc(expected.get('final_start'), tzname)
    except (ValueError, AmbiguousTime):
        return False, 'UNKNOWN: Record timestamp ambiguous or missing'
    if abs((start - wanted).total_seconds()) > 0:
        return False, 'Start time differs from final accepted request'
    return True, 'Independent HighLevel read matches contact, calendar, time and status'


def create_booking_finding(db, call: Call, promised: dict):
    # One booking promise per call in Phase 0; multi-appointment calls deferred.
    finding = Finding(call_id=call.id, type='booking', state='pending', reason='Awaiting first calendar read',
                      promised=promised, observed={}, manifest_hash=call.manifest_hash,
                      agent_version=call.agent_version)
    db.add(finding)
    db.flush()
    for minutes, stage in [(2,'2m'), (15,'15m'), (60,'60m')]:
        db.add(Job(finding_id=finding.id, stage=stage,
                   run_at=db_utc(call.ended_at) + timedelta(minutes=minutes)))
    return finding


def check_finding(db, finding: Finding, stage='manual'):
    if finding.type != 'booking':
        raise ValueError('Only booking findings support calendar re-check')
    call = db.get(Call, finding.call_id)
    client = db.get(Client, call.client_id)
    crm = HighLevel(client, db)
    p = finding.promised
    previous_state = finding.state
    errors = []
    records = []
    source = 'highlevel' if not client.is_demo else 'demo_simulator'
    try:
        if not p.get('contact_id') or not p.get('calendar_id') or not p.get('final_start'):
            raise CRMError('Insufficient contact, calendar or final time evidence')
        if not crm.validate_contact_location(p['contact_id']):
            raise CRMError('Contact not demonstrably within this HighLevel sub-account')
        if p.get('appointment_id'):
            record = crm.by_id(p['appointment_id'])
            if record and record.get('contactId'):
                records.append(record)
            elif record:
                # A by-ID lookup lacking contactId cannot prove association.
                # Corroborate against the contact-specific endpoint instead.
                records = [r for r in crm.by_contact(p['contact_id'])
                           if r.get('id') == p['appointment_id']]
        if not records:
            records = crm.by_contact(p['contact_id'])
    except CRMError as exc:
        errors.append(str(exc))
    except Exception as exc:
        errors.append(f'Unexpected read error: {type(exc).__name__}')

    evidence = {'records':obj(records[:10]), 'errors':errors, 'stage':stage}
    db.add(Observation(finding_id=finding.id, source=source, data=evidence))
    finding.observed = evidence
    if errors:
        finding.state = 'unknown'
        finding.reason = '; '.join(errors)
    else:
        matched = [(r, match_record(r, p, client.timezone)) for r in records]
        good = [r for r, (ok,_) in matched if ok]
        if len(good) == 1:
            finding.state = 'verified'
            finding.reason = 'Fresh calendar read matched requested booking'
        elif len(good) > 1:
            finding.state = 'unknown'
            finding.reason = 'Multiple matching appointments; duplicates are deferred to v1.5'
        elif stage == FINAL_STAGE:
            unknown = any(reason.startswith('UNKNOWN:') for _, (ok,reason) in matched)
            finding.state = 'unknown' if unknown else 'contradicted'
            finding.reason = (matched[0][1][1] if matched else 'Appointment not found after final recheck')
        elif stage == 'manual':
            # Manual request is an operator-requested check. A mismatch remains open,
            # but not conclusively contradicted until the final scheduled check.
            finding.state = 'contradicted' if previous_state == 'contradicted' else 'pending'
            finding.reason = (matched[0][1][1] if matched else 'Appointment not found; scheduled checks remain')
        else:
            finding.state = 'pending'
            finding.reason = (matched[0][1][1] if matched else 'Appointment not found; checking again later')
    finding.updated_at = now()
    db.flush()
    return finding


def process_due_jobs(db, limit=20):
    # Postgres SKIP LOCKED prevents competing workers from checking the same row.
    stmt = select(Job).where(Job.done.is_(False), Job.run_at <= now()).order_by(Job.run_at).limit(limit)
    if db.bind.dialect.name == 'postgresql':
        stmt = stmt.with_for_update(skip_locked=True)
    processed = 0
    for job in db.scalars(stmt).all():
        finding = db.get(Finding, job.finding_id)
        if finding.state == 'verified':
            job.done = True
        else:
            try:
                check_finding(db, finding, job.stage)
                job.done = True
            except Exception as exc:
                job.attempts += 1
                job.last_error = type(exc).__name__
                if job.attempts >= 5:
                    job.done = True
                    finding.state = 'unknown'
                    finding.reason = 'Recheck worker repeatedly failed'
                else:
                    job.run_at = now() + timedelta(minutes=min(2**job.attempts, 30))
        if finding and finding.state in {'contradicted','unknown','flagged','pending'}:
            from .accountability import ensure_case
            ensure_case(db, finding.id)
        processed += 1
    db.commit()
    return processed
