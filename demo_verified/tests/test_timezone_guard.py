"""IANA guard regressions. Provider output is stubbed; no live Groq calls."""
from datetime import datetime, timezone

import pytest

from app.extract import Commitment, guard_booking_prediction
from app.timezone_guard import timezone_guard
from app.time_utils import AmbiguousTime, unique_local_wall
from scripts.compare_extractors import compare

CHICAGO = 'America/Chicago'
START = datetime(2026, 10, 22, 14, tzinfo=timezone.utc)
FALLBACK = (
    'Caller: Try November 4 at 1 PM, but otherwise keep November 3 at 9 AM.\n'
    'Agent: Wednesday was full, so I booked your original November 3 appointment.'
)
CORRECTION = (
    'Caller: October 28 at 9 AM, please. Actually, change that to November 3 at 11 AM.\n'
    'Agent: November 3 at 11 AM is booked.'
)


def booking(timestamp):
    return Commitment(confirmed=True, final_start=timestamp, abstain=False)


def guard(value, conversation=FALLBACK, zone=CHICAGO):
    return timezone_guard(booking(value), conversation, zone, START)


def test_corroborated_chicago_wrong_dst_offset_normalized():
    result = guard('2026-11-03T09:00:00-05:00')
    assert result.confirmed and not result.abstain
    assert result.final_start == '2026-11-03T09:00:00-06:00'
    assert 'IANA correction' in result.reason


def test_correct_offset_and_valid_utc_representation_preserved():
    assert guard('2026-11-03T09:00:00-06:00').final_start == '2026-11-03T09:00:00-06:00'
    assert guard('2026-11-03T15:00:00+00:00').final_start == '2026-11-03T09:00:00-06:00'
    before_dst = 'Caller: Book October 23 at 10 AM please.\nAgent: October 23 at 10 AM is booked.'
    assert guard('2026-10-23T10:00:00-05:00', before_dst).final_start == '2026-10-23T10:00:00-05:00'


def test_november_correction_guard_and_timezone_guard_work_together():
    result = guard_booking_prediction(Commitment(confirmed=False, abstain=False), CORRECTION, CHICAGO, START)
    assert result.final_start == '2026-11-03T11:00:00-06:00'
    assert result.confirmed and not result.abstain


def test_no_booking_or_abstention_unchanged():
    no = Commitment(confirmed=False, final_start=None, abstain=False)
    abstain = Commitment(reason='Need clarification')
    assert timezone_guard(no, FALLBACK, CHICAGO, START) is no
    assert timezone_guard(abstain, FALLBACK, CHICAGO, START) is abstain


@pytest.mark.parametrize('datetime_string', [
    '2026-11-01T01:30:00-05:00',  # fall-back repeated 1:30 AM
    '2026-11-01T01:30:00-06:00',
    '2026-03-08T02:30:00-06:00',  # spring-forward missing 2:30 AM
])
def test_fold_and_gap_abstain_even_if_model_supplies_offset(datetime_string):
    result = guard(datetime_string)
    assert result.abstain and not result.confirmed
    assert result.final_start is None


def test_utc_output_does_not_silently_override_unsupported_caller_time():
    transcript = 'Caller: Could you call me next week?\nAgent: Booked.'
    assert guard('2026-11-03T15:00:00+00:00', transcript).abstain


def test_wrong_offset_without_caller_corroboration_abstains():
    transcript = 'Caller: Can you fit me in sometime next week?\nAgent: Booked.'
    result = guard('2026-11-03T09:00:00-05:00', transcript)
    assert result.abstain and result.final_start is None


def test_invalid_zone_and_naive_timestamp_abstain():
    assert guard('2026-11-03T09:00:00-06:00', zone='Fake/Timezone').abstain
    assert guard('2026-11-03T09:00:00').abstain


def test_unique_local_wall_handles_fold_and_gap_other_continents():
    for date in ('2026-10-25T02:30:00', '2026-03-29T02:30:00'):
        with pytest.raises(AmbiguousTime):
            unique_local_wall(datetime.fromisoformat(date), 'Europe/Berlin')
    assert unique_local_wall(datetime(2026, 10, 22, 9, 0), 'Asia/Kolkata').isoformat() == '2026-10-22T09:00:00+05:30'


def test_noncorroborated_wrong_booking_time_not_falsely_rewritten():
    caller = 'Caller: November 4 at 2 PM please.\nAgent: Confirmed.'
    assert guard('2026-11-04T11:00:00-05:00', caller).abstain
    # Consistent but wrong date/time is deliberately not "fixed" by the guard:
    # extracting the correct local slot is still the LLM's responsibility.
    assert guard('2026-11-04T11:00:00-06:00', caller).final_start == '2026-11-04T11:00:00-06:00'


def test_paired_comparison_corrects_timezone_without_second_model_call(tmp_path):
    calls = []
    def fake_extractor(transcript, timezone, backend=None, reference_time=None):
        calls.append((backend, transcript))
        if 'October 28 at 9 AM' in transcript:
            return Commitment(confirmed=False, abstain=False)
        if 'but otherwise keep November 3 at 9 AM' in transcript:
            return booking('2026-11-03T09:00:00-05:00')
        return Commitment(abstain=True)
    result = compare('datasets/extraction_challenge.jsonl',
                     backends=('groq', 'groq_guarded'), extractor=fake_extractor,
                     output=tmp_path / 'paired.json')
    assert len(calls) == 8
    assert result['cases'][1]['groq']['outcome'] == 'missed_booking'
    assert result['cases'][1]['groq_guarded']['outcome'] == 'correct'
    assert result['cases'][2]['groq']['outcome'] == 'wrong_time'
    assert result['cases'][2]['groq_guarded']['outcome'] == 'correct'
