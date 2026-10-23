"""Independent event engine regression tests. No API calls or fixture gold leakage."""
import json
from datetime import datetime, timezone
import pytest
from app.event_engine import resolve_events, extract_supported_dates, extract_supported_times
from app.evaluation import correct_extraction

START = datetime(2026, 10, 22, 14, tzinfo=timezone.utc)
TZ = 'America/Chicago'

def event(turn, quote, type, rid=None, target=None, date=None, time=None):
    return dict(turn=turn, quote=quote, type=type, request_id=rid,
                target_request_id=target, local_date=date, local_time=time)

def check(transcript, *events, tz=TZ, at=START):
    return resolve_events({'events':list(events)}, transcript, tz, at)

@pytest.mark.parametrize('identifier,events', [
    ('relative_tomorrow', [
        event(0,'book me for tomorrow at 10 AM','request','r1',date='2026-10-23',time='10:00'),
        event(1,'booked for tomorrow at ten','confirmation',target='r1'),
    ]),
    ('date_correction', [
        event(0,'October 28 at 9 AM','request','r1',date='2026-10-28',time='09:00'),
        event(0,'November 3 at 11 AM','revision','r2','r1','2026-11-03','11:00'),
        event(1,'November 3 at 11 AM is booked','confirmation',target='r2'),
    ]),
    ('conditional_fallback', [
        event(0,'November 4 at 1 PM','request','r1',date='2026-11-04',time='13:00'),
        event(0,'November 3 at 9 AM','request','r2',date='2026-11-03',time='09:00'),
        event(1,'I booked your original November 3 appointment','confirmation',target='r2'),
    ]),
    ('spoken_time', [
        event(0,'November 4 at three in the afternoon','request','r1',date='2026-11-04',time='15:00'),
        event(1,'Your 3 PM appointment is confirmed','confirmation',target='r1'),
    ]),
    ('availability_only', [
        event(0,'Could you check whether November 3 at 10 AM is free','availability_query'),
        event(1,'I have not booked it','not_booked'),
    ]),
    ('unaccepted_assertion', [
        event(0,'Is November 3 at 10 AM available','availability_query'),
        event(2,'please do not book it','reject'),
    ]),
    ('explicit_clear', [
        event(0,'November 4 at 2 PM','request','r1',date='2026-11-04',time='14:00'),
        event(1,'Your appointment is booked','confirmation',target='r1'),
    ]),
    ('two_callers', [
        event(0,'November 3 at 9 AM for my sister','request','r1',date='2026-11-03',time='09:00'),
        event(0,'mine November 4 at 10 AM','request','r2',date='2026-11-04',time='10:00'),
        event(1,'You are booked for November 4 at 10 AM','confirmation',target='r2'),
    ]),
])
def test_eight_known_transcripts_as_proposed_events(identifier, events):
    rows = {x['id']:x for x in (json.loads(l) for l in open('datasets/extraction_challenge.jsonl'))}
    row = rows[identifier]
    prediction = check(row['transcript'], *events)
    assert not prediction.abstain, prediction.reason
    assert correct_extraction(row['commitment'], prediction), (identifier,prediction)

@pytest.mark.parametrize('transcript,events,confirmed,abstain,reason', [
    ('Caller: November 3 at 11 AM please.\nAgent: Booked.', [
        event(0,'November 3 at 11 AM','request','r1',date='2026-11-03',time='11:00'),event(1,'Booked','confirmation',target='r1')], True,False,''),
    ('Caller: Is Nov 3 at 11 AM available?\nAgent: It is available.', [
        event(0,'Nov 3 at 11 AM available','availability_query')], False,False,''),
    ('Caller: Please book Nov 3 at 11 AM.\nAgent: We cannot book that.', [
        event(0,'book Nov 3 at 11 AM','request','r1',date='2026-11-03',time='11:00'),event(1,'cannot book that','not_booked')], False,False,''),
    ('Caller: Please book Nov 3 at 11 AM.\nAgent: Let me check.', [
        event(0,'book Nov 3 at 11 AM','request','r1',date='2026-11-03',time='11:00')], False,True,'confirmation'),
    ('Caller: Book Nov 3 at 11 AM.\nAgent: Booked.\nCaller: No! Do not book it.', [
        event(0,'Book Nov 3 at 11 AM','request','r1',date='2026-11-03',time='11:00'),event(1,'Booked','confirmation',target='r1'),event(2,'Do not book it','reject')], False,False,''),
    ('Caller: Book Nov 3 at 11 AM.\nAgent: Booked.\nCaller: Actually Nov 4 at 10 AM.', [
        event(0,'Nov 3 at 11 AM','request','r1',date='2026-11-03',time='11:00'),event(1,'Booked','confirmation',target='r1'),event(2,'Nov 4 at 10 AM','revision','r2','r1','2026-11-04','10:00')], False,True,'later'),
    ('Caller: Please book November 1 at 1:30 AM.\nAgent: Booked.', [
        event(0,'November 1 at 1:30 AM','request','r1',date='2026-11-01',time='01:30'),event(1,'Booked','confirmation',target='r1')], False,True,'ambiguous'),
    ('Caller: Please book March 8, 2026 at 2:30 AM.\nAgent: Booked.', [
        event(0,'March 8, 2026 at 2:30 AM','request','r1',date='2026-03-08',time='02:30'),event(1,'Booked','confirmation',target='r1')], False,True,'nonexistent'),
    ('Caller: Book Nov 3 at 11 AM.\nAgent: Booked.\nCaller: Book Nov 4 at 10 AM.\nAgent: Booked.', [
        event(0,'Nov 3 at 11 AM','request','r1',date='2026-11-03',time='11:00'),event(1,'Booked','confirmation',target='r1'),event(2,'Nov 4 at 10 AM','request','r2',date='2026-11-04',time='10:00'),event(3,'Booked','confirmation',target='r2')], False,True,'Multiple'),
    ('Caller: Book Nov 3 at 11 AM.\nAgent: Booked.', [
        event(0,'Nov 3 at 11 AM','request','r1',date='2026-11-03',time='11:00'),event(1,'Booked','confirmation',target='does_not_exist')], False,True,'link'),
    ('Caller: Book Nov 3 at 11 AM.\nAgent: Booked.', [
        event(0,'Nov 3 at 11 AM','request','r1',date='2026-11-03',time='11:00'),event(1,'Booked','confirmation',target='r1'),event(1,'Not in the transcript','other')], False,True,'grounded'),
    ('Caller: Book Nov 3 at 11 AM.\nAgent: Booked.', [
        event(0,'Nov 3 at 11 AM','request','r1',date='2026-11-04',time='11:00'),event(1,'Booked','confirmation',target='r1')], False,True,'supported'),
    ('Caller: Book Nov 3 at 11 AM.\nAgent: Booked.', [
        event(0,'Nov 3 at 11 AM','request','r1',date='2026-11-03',time='10:00'),event(1,'Booked','confirmation',target='r1')], False,True,'supported'),
    ('Caller: Book Nov 3 at 11 AM.\nAgent: You are booked for Nov 3 at 10 AM.', [
        event(0,'Nov 3 at 11 AM','request','r1',date='2026-11-03',time='11:00'),event(1,'booked for Nov 3 at 10 AM','confirmation',target='r1')], False,True,'different time'),
    ('Caller: Book Nov 3 at 11 AM.\nAgent: You are not booked.', [
        event(0,'Nov 3 at 11 AM','request','r1',date='2026-11-03',time='11:00'),event(1,'not booked','confirmation',target='r1')], False,True,'not a completed'),
])
def test_state_and_validation_matrix(transcript, events, confirmed, abstain, reason):
    prediction = check(transcript, *events)
    assert prediction.confirmed is confirmed, prediction.reason
    assert prediction.abstain is abstain, prediction.reason
    assert reason.lower() in prediction.reason.lower(),prediction.reason


def test_missing_model_events_cannot_hide_obvious_booking():
    result = check('Caller: Please book November 3 at 11 AM.\nAgent: Your appointment is booked.')
    assert result.abstain and not result.confirmed


def test_model_cannot_convert_query_to_booking_with_agent_hallucination():
    result = check('Caller: Is November 3 at 11 AM open?\nAgent: Booked.',
                   event(0,'November 3 at 11 AM','request','r1',date='2026-11-03',time='11:00'),
                   event(1,'Booked','confirmation',target='r1'))
    # Explicit question-only utterance needs a guard independent of model event typing.
    assert not result.confirmed


def test_date_and_spoken_time_are_computed_in_business_local_time():
    assert '2026-10-23' in extract_supported_dates('tomorrow', START.astimezone(__import__('zoneinfo').ZoneInfo(TZ)))
    assert '15:00' in extract_supported_times('three in the afternoon')

@pytest.mark.parametrize('transcript,events,want_abstain', [
    ('Caller: DO NOT book November 3 at 11 AM.\nAgent: Booked.', [
        event(0,'November 3 at 11 AM','request','r1',date='2026-11-03',time='11:00'),
        event(1,'Booked','confirmation',target='r1')],False),
    ('Caller: Please book Nov 3 at 11 AM.\nAgent: It is not booked. It failed.', [
        event(0,'Nov 3 at 11 AM','request','r1',date='2026-11-03',time='11:00'),
        event(1,'booked','confirmation',target='r1')],True),
    ('Caller: Book next Friday at 11 AM.\nAgent: It is booked for next Friday.', [
        event(0,'next Friday at 11 AM','request','r1',date='2026-10-23',time='11:00'),
        event(1,'booked for next Friday','confirmation',target='r1')],True),
    ('Caller: Book next Friday at 11 AM.\nAgent: October 23 at 11 AM is booked.', [
        event(0,'next Friday at 11 AM','request','r1',date='2026-10-23',time='11:00'),
        event(1,'October 23 at 11 AM is booked','confirmation',target='r1')],False),
])
def test_ambiguous_and_negative_evidence(transcript, events, want_abstain):
    pred=check(transcript,*events)
    assert pred.abstain is want_abstain, pred.reason
    if 'DO NOT' in transcript:
        assert not pred.confirmed


def test_structured_groq_backend_uses_event_schema_and_no_gold(monkeypatch):
    import sys, types
    from app.extract import extract_commitment
    monkeypatch.setenv('GROQ_API_KEY','fake-key')
    payload={'events':[
        event(0,'November 3 at 11 AM','request','r1',date='2026-11-03',time='11:00'),
        event(1,'November 3 at 11 AM is booked','confirmation',target='r1')
    ]}
    transcript='Caller: Please book November 3 at 11 AM.\nAgent: November 3 at 11 AM is booked.'
    def reply(**kwargs):
        assert kwargs['response_format']['json_schema']['name']=='booking_events'
        assert kwargs['response_format']['json_schema']['strict'] is True
        assert 'commitment' not in kwargs['messages'][1]['content']
        assert transcript in kwargs['messages'][1]['content']
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=json.dumps(payload)))])
    sdk=types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=reply)))
    monkeypatch.setitem(sys.modules,'openai',types.SimpleNamespace(OpenAI=lambda **options:sdk))
    predicted=extract_commitment(transcript,TZ,backend='groq_events',reference_time=START)
    assert predicted.confirmed and not predicted.abstain
    assert predicted.final_start=='2026-11-03T11:00:00-06:00'
    payload['events'][0]['local_date']='2026-11-04'
    invalid=extract_commitment(transcript,TZ,backend='groq_events',reference_time=START)
    assert invalid.abstain and not invalid.confirmed


def test_agent_offer_caller_acceptance_and_agent_booking():
    transcript=('Caller: Do you have openings?\n'
                'Agent: November 3 at 2 PM is available.\n'
                'Caller: Yes, that works. Please book it.\n'
                'Agent: Your appointment is booked.')
    prediction=check(transcript,
        event(0,'Do you have openings','availability_query'),
        event(1,'November 3 at 2 PM','offer','s1',date='2026-11-03',time='14:00'),
        event(2,'Yes, that works. Please book it','accept',target='s1'),
        event(3,'Your appointment is booked','confirmation',target='s1'))
    assert prediction.confirmed and prediction.final_start=='2026-11-03T14:00:00-06:00', prediction.reason


def test_offered_slot_is_not_booking_without_caller_acceptance():
    transcript=('Caller: Any openings?\nAgent: November 3 at 2 PM is available.\n'
                'Agent: Okay, your appointment is booked.')
    prediction=check(transcript,
        event(0,'Any openings','availability_query'),
        event(1,'November 3 at 2 PM','offer','s1',date='2026-11-03',time='14:00'),
        event(2,'your appointment is booked','confirmation',target='s1'))
    assert prediction.abstain and not prediction.confirmed
    assert 'acceptance' in prediction.reason.lower()
