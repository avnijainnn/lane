"""Evaluation helpers; labels must never be passed to the transcript extractor."""
from datetime import datetime
from .time_utils import to_utc, AmbiguousTime


def equal_instant(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    try:
        return to_utc(a, 'UTC') == to_utc(b, 'UTC')
    except (ValueError, TypeError, AmbiguousTime):
        return False


def correct_extraction(gold: dict, prediction) -> bool:
    """Abstentions are NOT correct predictions, even if no finding appears."""
    if prediction is None or prediction.abstain:
        return False
    if bool(prediction.confirmed) != bool(gold.get('confirmed')):
        return False
    if not gold.get('confirmed'):
        return not prediction.final_start
    return equal_instant(prediction.final_start, gold.get('final_start'))


def report(rows):
    """Rows contain prediction, gold and a boolean state match."""
    n = len(rows)
    covered = sum(r['prediction'] is not None and not r['prediction'].abstain for r in rows)
    correct = sum(r['extraction_correct'] for r in rows)
    correct_covered = sum(r['extraction_correct'] for r in rows if r['prediction'] is not None and not r['prediction'].abstain)
    state_correct = sum(r['state_correct'] for r in rows if r.get('state_correct') is not None)
    state_n = sum(r.get('state_correct') is not None for r in rows)
    positive = [r for r in rows if r['gold'].get('confirmed')]
    exact_dates = sum(equal_instant(r['prediction'].final_start, r['gold'].get('final_start'))
                      for r in positive if r['prediction'] is not None and not r['prediction'].abstain and r['prediction'].confirmed)
    return {
        'total': n,
        'covered': covered,
        'abstained': n-covered,
        'coverage': covered/n if n else None,
        'extraction_correct': correct,
        'extraction_accuracy_all': correct/n if n else None,
        'extraction_accuracy_covered': correct_covered/covered if covered else None,
        'exact_booking_times': exact_dates,
        'positive_bookings': len(positive),
        'state_correct': state_correct,
        'state_total': state_n,
        'state_accuracy': state_correct/state_n if state_n else None,
    }
