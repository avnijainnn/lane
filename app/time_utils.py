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


def db_utc(dt: datetime) -> datetime:
    # SQLite strips timezone, Postgres preserves it.
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
