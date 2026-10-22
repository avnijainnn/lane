"""Read-only, unlabelled SGD stress-report dashboard data.

Use only fixed file locations, never user-provided paths. The reports represent
predictions, not independently validated accuracy or HighLevel bookings.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

DATASET = Path('datasets/public/sgd_new_relevant_100.jsonl')
REPORTS = {
    'gemini': Path('datasets/reports/sgd_new_relevant_gemini.json'),
    'rules': Path('datasets/reports/sgd_new_relevant_rules.json'),
}
MAX_INPUT_BYTES = 10_000_000


def _expression_hints(transcript: str) -> list[str]:
    """Lexical hints only, not a diagnosis of the actual model decision."""
    checks = (
        (r'\b(?:1[3-9]|2[0-3]):[0-5]\d\b', '24-hour clock'),
        (r'\b\d{1,2}(?:st|nd|rd|th)\b', 'ordinal date'),
        (r'\bhalf\s+past\b', 'half-past time'),
        (r'\b(?:quarter\s+(?:to|past)|o.clock)\b', 'colloquial time'),
    )
    return [label for pattern, label in checks if re.search(pattern, transcript, flags=re.I)]



def _read_json(path: Path) -> dict | None:
    if not path.is_file() or path.stat().st_size > MAX_INPUT_BYTES:
        return None
    try:
        value = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError, UnicodeError):
        return None
    return value if isinstance(value, dict) else None


def _read_candidates(path: Path) -> list[dict]:
    if not path.is_file() or path.stat().st_size > MAX_INPUT_BYTES:
        return []
    try:
        items = [json.loads(line) for line in path.read_text(encoding='utf-8-sig').splitlines() if line.strip()]
    except (OSError, ValueError, UnicodeError):
        return []
    if not isinstance(items, list) or len(items) > 10000:
        return []
    result = []
    used = set()
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get('id'), str) or item['id'] in used:
            return []
        used.add(item['id'])
        result.append({'id': item['id'], 'domain': str(item.get('domain', '')), 'transcript': str(item.get('transcript', ''))})
    return result


def public_sgd_view(base: Path) -> dict:
    """Join Gemini and rules by stable conversation ID, retaining all missing/error rows."""
    candidates = _read_candidates(base / DATASET)
    ids = {item['id'] for item in candidates}
    reports = {}
    for name, rel in REPORTS.items():
        report = _read_json(base / rel)
        if not report or report.get('kind') != 'unlabelled_public_corpus_stress' or report.get('backend') != ('gemini_events' if name == 'gemini' else 'rules'):
            continue
        # Reject reports from unrelated corpora even if IDs incidentally overlap.
        if Path(str(report.get('source', '')).replace('\\', '/')).name != DATASET.name:
            continue
        cases = report.get('cases', [])
        if not isinstance(cases, list) or len(cases) > len(candidates):
            continue
        entries = {}
        for entry in cases:
            if not isinstance(entry, dict) or entry.get('id') not in ids or entry.get('id') in entries:
                entries = {}
                break
            if entry.get('status') not in {'evaluated','provider_error'}:
                entries = {}
                break
            entries[entry['id']] = entry
        if not entries and cases:
            continue
        reports[name] = {'entries': entries,
                         'evaluated':sum(v.get('status') == 'evaluated' for v in entries.values()),
                         'errors':sum(v.get('status') == 'provider_error' for v in entries.values()),
                         'unprocessed':len(candidates)-len(entries),
                         'predicted_bookings':sum(v.get('predicted_booking') is True for v in entries.values() if v['status']=='evaluated'),
                         'abstentions':sum(v.get('abstain') is True for v in entries.values() if v['status']=='evaluated')}
    out = {'total':len(candidates), 'reports': reports, 'cases': [],
           'paired':0,'comparable':0,'agreements':0,'disagreements':0,
           'time_presence_disagreements':0, 'abstention_affected':0}
    for item in candidates:
        rid = item['id']
        preds = {name: report['entries'].get(rid, {'status':'unprocessed'})
                 for name, report in reports.items()}
        g = preds.get('gemini', {'status':'unprocessed'})
        r = preds.get('rules', {'status':'unprocessed'})
        both = g['status']=='evaluated' and r['status']=='evaluated'
        comparable = both and not g.get('abstain', False) and not r.get('abstain', False)
        disagreement = comparable and (bool(g.get('predicted_booking')) != bool(r.get('predicted_booking')))
        different_time_presence = comparable and bool(g.get('has_time')) != bool(r.get('has_time'))
        if both:
            out['paired'] += 1
            if not comparable: out['abstention_affected'] += 1
        if comparable:
            out['comparable'] += 1
            out['disagreements' if disagreement else 'agreements'] += 1
            out['time_presence_disagreements'] += int(different_time_presence)
        status = ('disagreement' if disagreement or different_time_presence
                  else 'abstention' if both and not comparable
                  else 'agreement' if comparable
                  else 'provider_error' if any(v.get('status') == 'provider_error' for v in preds.values())
                  else 'unpaired')
        out['cases'].append({**item,'gemini':g,'rules':r,'compare_status':status, 'expression_hints':_expression_hints(item['transcript'])})
    return out
