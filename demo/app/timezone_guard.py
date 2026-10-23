"""Conservative IANA timezone validation of an LLM's extracted booking time.

This never decides whether the caller agreed to a booking, and never inspects
benchmark gold or HighLevel records. It runs only after a model prediction.
If an offset is inconsistent with the business zone, change the UTC instant
ONLY if a caller-local wall time is independently corroborated in transcript;
otherwise abstain. A valid UTC representation of an instant stays an instant.
"""
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .time_utils import AmbiguousTime, unique_local_wall


def _caller_mentions_wall_time(transcript: str, naive: datetime, tzname: str,
                               reference_time: datetime | None) -> bool:
    """Conservative evidence check for explicit caller date + AM/PM pair.

    No LLM/gold data is used. Avoid accidentally matching date A with the time
    belonging to date B in a different clause. Relative/spoken forms aren't
    resolved by this narrow guard; those cases stay unchanged/unknown.
    """
    if reference_time is None or reference_time.tzinfo is None:
        return False
    from .extract import _DATE, _TIME, _ROLE, _MONTHS
    ref = reference_time.astimezone(ZoneInfo(tzname))
    for line in transcript.splitlines():
        match = _ROLE.match(line)
        if not match or match.group(1).lower() not in {'caller', 'customer', 'patient', 'user'}:
            continue
        text = match.group(2)
        for date in _DATE.finditer(text):
            # A caller may list two candidate slots. Associate each date only
            # with the nearby following time, not an arbitrary later time.
            end = min(len(text), date.end() + 26)
            tm = _TIME.search(text, date.end(), end)
            if tm is None or _DATE.search(text, date.end(), tm.start()):
                continue
            month = _MONTHS[date.group(1).lower()[:3]]
            day = int(date.group(2))
            try:
                year = int(date.group(3)) if date.group(3) else ref.year
                if not date.group(3) and datetime(year, month, day).date() < (ref - timedelta(days=1)).date():
                    year += 1
                hour = int(tm.group(1)) % 12 + (12 if tm.group(3).lower().startswith('p') else 0)
                wall = datetime(year, month, day, hour, int(tm.group(2) or 0))
            except ValueError:
                continue
            if wall == naive:
                return True
    return False


def _caller_explicit_utc(transcript: str) -> bool:
    """Did the caller explicitly use UTC/GMT (rather than business local time)?"""
    from .extract import _ROLE
    for line in transcript.splitlines():
        match = _ROLE.match(line)
        if match and match.group(1).lower() in {'caller', 'customer', 'patient', 'user'}:
            if re.search(r'\b(?:UTC|GMT|Zulu)\b', match.group(2), re.I):
                return True
    return False


def timezone_guard(prediction, transcript: str, tzname: str,
                   reference_time: datetime | None):
    """Return normalized prediction, or explicit abstention on uncertain times.

    The `confirmed` flag is never invented by this guard; the correction
    guard runs separately. Unlike `astimezone` alone, the code distinguishes
    a wrong UTC offset on an intended local 9AM from a legitimate 15:00 UTC.
    """
    if prediction.abstain or not prediction.confirmed:
        return prediction

    def unknown(reason):
        return prediction.model_copy(update={
            'confirmed': False, 'final_start': None, 'abstain': True,
            'reason': 'Timezone guard: ' + reason,
        })

    if not prediction.final_start:
        return unknown('No timestamp supplied')
    try:
        when = datetime.fromisoformat(prediction.final_start.replace('Z', '+00:00'))
        if when.tzinfo is None or when.utcoffset() is None:
            return unknown('Missing UTC offset')
        zone = ZoneInfo(tzname)
        local_fields = when.replace(tzinfo=None)
        converted_fields = when.astimezone(zone).replace(tzinfo=None)
        local_said = _caller_mentions_wall_time(transcript, local_fields, tzname, reference_time)
        converted_said = _caller_mentions_wall_time(transcript, converted_fields, tzname, reference_time)

        if local_fields == converted_fields:
            # Already represents the same local wall time. Still reject DST
            # repeats/gaps, even when the model supplied an offset.
            local = unique_local_wall(local_fields, tzname)
            if when.utcoffset() != local.utcoffset():
                return unknown('Offset inconsistent with business timezone')
            return prediction.model_copy(update={'final_start': local.isoformat()})

        # A non-matching offset may mean either: (1) model intended the
        # business's 9AM but calculated -05 instead of -06; OR (2) it
        # correctly emitted a UTC/fixed-offset instant. We must not guess.
        if local_said and converted_said:
            return unknown('Both local-time and instant interpretations match caller slots')
        if local_said:
            local = unique_local_wall(local_fields, tzname)
            return prediction.model_copy(update={
                'final_start': local.isoformat(),
                'reason': (prediction.reason + ' | ' if prediction.reason else '') +
                          'IANA correction: caller-local wall time corroborated',
            })
        if converted_said or (when.utcoffset() == timedelta(0) and _caller_explicit_utc(transcript)):
            local = unique_local_wall(converted_fields, tzname)
            return prediction.model_copy(update={'final_start': local.isoformat()})
        return unknown('Offset differs from business timezone without unambiguous transcript support')
    except (ValueError, TypeError, OverflowError, ZoneInfoNotFoundError, AmbiguousTime):
        return unknown('Invalid, ambiguous or nonexistent business-local time')
