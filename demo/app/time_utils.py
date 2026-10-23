from datetime import datetime, timezone
from zoneinfo import ZoneInfo

class AmbiguousTime(ValueError):
    pass


def to_utc(value, tzname: str) -> datetime:
    """Fail closed on DST folds/gaps and unknown representations."""
    if isinstance(value, (int, float)):
        dt = datetime.fromtimestamp(value / 1000 if value > 1e11 else value, timezone.utc)
        return dt
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Missing timestamp')
    try:
        dt = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError('Timestamp must be ISO8601') from exc
    if dt.tzinfo is None:
        zone = ZoneInfo(tzname)
        a, b = dt.replace(tzinfo=zone, fold=0), dt.replace(tzinfo=zone, fold=1)
        if a.utcoffset() != b.utcoffset():
            raise AmbiguousTime('Ambiguous or nonexistent DST wall time; require offset')
        if a.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != dt:
            raise AmbiguousTime('Nonexistent local time')
        dt = a
    return dt.astimezone(timezone.utc)


def unique_local_wall(naive: datetime, tzname: str) -> datetime:
    """Resolve a business-local civil time using IANA tzdata, or fail closed.

    `datetime.replace(tzinfo=ZoneInfo(...))` alone does NOT validate a local
    time. Round-tripping both PEP 495 folds distinguishes unique, repeated
    (fall-back) and nonexistent (spring-forward) wall-clock times.
    """
    if naive.tzinfo is not None:
        raise ValueError('Expected timezone-naive business-local datetime')
    zone = ZoneInfo(tzname)
    possible = []
    for fold in (0, 1):
        attempt = naive.replace(tzinfo=zone, fold=fold)
        back = attempt.astimezone(timezone.utc).astimezone(zone)
        if back.replace(tzinfo=None) == naive and back.fold == fold:
            possible.append(attempt)
    if not possible:
        raise AmbiguousTime('Nonexistent business-local time (DST gap)')
    # Outside a transition, fold=1 is invalid and does not round-trip as
    # fold=1. A repeated wall time has two distinct, valid UTC instants.
    if len(possible) > 1 and possible[0].utcoffset() != possible[1].utcoffset():
        raise AmbiguousTime('Repeated business-local time (DST fold); needs clarification')
    return possible[0]


def db_utc(dt: datetime) -> datetime:
    # SQLite strips timezone, Postgres preserves it.
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
