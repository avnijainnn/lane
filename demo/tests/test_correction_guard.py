"""Guard regression tests. All Groq output is mocked; no live API requests."""
import json
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

from app.extract import Commitment, extract_commitment, guard_confirmed_correction
from scripts.compare_extractors import compare


REF = datetime(2026, 10, 22, 14, 0, tzinfo=timezone.utc)
TZ = 'America/Chicago'
CORRECTION = (
    'Caller: October 28 at 9 AM, please. Actually, change that to November 3 at 11 AM.\n'
    'Agent: November 3 at 11 AM is booked.'
)
NO_BOOKING = Commitment(confirmed=False, final_start=None, abstain=False, reason='No booking')


def test_corrected_booking_recovered_using_transcript_only():
    pred = guard_confirmed_correction(NO_BOOKING, CORRECTION, TZ, REF)
    assert pred.confirmed and not pred.abstain
    assert pred.final_start == '2026-11-03T11:00:00-06:00'
    assert pred.reason.startswith('Deterministic guard:')


def test_guard_refuses_false_bookings_and_ambiguous_evidence():
    rows = [
        ('Caller: Is November 3 at 10 AM available?\nAgent: Yes, you are booked.\n'
         'Caller: No, I was only asking, please do not book it.'),
        ('Caller: October 28 at 9 AM, please. Actually change that to November 3 at 11 AM.\n'
         'Agent: November 3 at 10 AM is booked.'),
        ('Caller: October 28 at 9 AM, please. Actually change that to November 3 at 11 AM.\n'
         'Agent: November 3 at 11 AM is NOT booked.'),
        ('Caller: October 28 at 9 AM, please. Actually change that to November 3 at 11 AM.\n'
         'Agent: Is November 3 at 11 AM booked?'),
        ('Caller: October 28 at 9 AM, please. Actually change that to November 3 at 11 AM.\n'
         'Agent: November 3 at 11 AM is booked.\nCaller: Actually no, please do not book it.'),
        ('Caller: October 28 at 9 AM, please. Actually change that to November 3 at 11 AM.\n'
         'Agent: November 3 at 11 AM is booked.\nCaller: Actually make it November 4 at 11 AM.'),
        ('Caller: Can you check November 3 at 11 AM?\nAgent: The time is available.'),
        ('Caller: Actually move it to November 1 at 1:30 AM.\n'
         'Agent: November 1 at 1:30 AM is booked.'),  # DST fold, no offset
    ]
    for transcript in rows:
        result = guard_confirmed_correction(NO_BOOKING, transcript, TZ, REF)
        assert result == NO_BOOKING, transcript


def test_no_api_error_can_be_reinterpreted_as_success():
    raw = Commitment(reason='Extractor unavailable/invalid: RateLimitError')
    result = guard_confirmed_correction(raw, CORRECTION, TZ, REF)
    assert result == raw


def test_guard_keeps_model_correct_predictions_untouched():
    raw = Commitment(confirmed=True, final_start='2026-11-04T12:00:00-06:00', abstain=False)
    assert guard_confirmed_correction(raw, CORRECTION, TZ, REF) is raw


def test_groq_guarded_backend_rescues_mock_false_negative(monkeypatch):
    monkeypatch.setenv('GROQ_API_KEY', 'fake-key')
    response = json.dumps({'confirmed': False, 'final_start': None,
                           'evidence': 'No booking', 'abstain': False, 'reason': ''})
    def fake_client(**opts):
        def create(**kwargs):
            assert kwargs['model'] == 'openai/gpt-oss-120b'
            return types.SimpleNamespace(choices=[types.SimpleNamespace(
                message=types.SimpleNamespace(content=response))])
        return types.SimpleNamespace(chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(create=create)))
    monkeypatch.setitem(sys.modules, 'openai', types.SimpleNamespace(OpenAI=fake_client))
    raw = extract_commitment(CORRECTION, TZ, backend='groq', reference_time=REF)
    guarded = extract_commitment(CORRECTION, TZ, backend='groq_guarded', reference_time=REF)
    assert not raw.confirmed
    assert guarded.confirmed and guarded.final_start == '2026-11-03T11:00:00-06:00'


def test_comparison_caches_one_groq_response_per_case(tmp_path):
    calls = []
    def fake_extractor(transcript, timezone, backend=None, reference_time=None):
        calls.append((backend, transcript, timezone, reference_time))
        return NO_BOOKING
    result = compare('datasets/extraction_challenge.jsonl',
                     backends=('groq','groq_guarded'), extractor=fake_extractor,
                     output=tmp_path/'paired.json')
    assert len(calls) == 8  # not 16: matched raw model responses
    assert {c[0] for c in calls} == {'groq'}
    assert result['cases'][1]['groq']['outcome'] == 'missed_booking'
    assert result['cases'][1]['groq_guarded']['outcome'] == 'correct'
    assert result['cases'][5]['groq_guarded']['outcome'] == 'correct'  # unaccepted assertion
    assert result['backends']['groq_guarded']['model'].endswith('correction guard')
