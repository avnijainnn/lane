"""Fair per-case rules vs LLM benchmark, using the SAME unchanged gold labels.

Examples:
    python -m scripts.compare_extractors datasets/extraction_challenge.jsonl --backends rules
    python -m scripts.compare_extractors datasets/extraction_challenge.jsonl --backends rules,groq,groq_guarded

Loads .env if present; environment variables take precedence. No network calls with
--backends rules. Fails before evaluating if openai is requested without an API
key; it never silently counts missing credentials as LLM abstentions.

Reports do not contain raw transcripts, evidence snippets or API credentials.
"""
import argparse
import hashlib
import json
import os
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from app.extract import extract_commitment, guard_booking_prediction
from app.evaluation import correct_extraction, equal_instant, report

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / 'datasets' / 'reports' / 'extraction_challenge.comparison.json'
SUPPORTED_BACKENDS = ('rules', 'openai', 'groq', 'groq_guarded', 'groq_events', 'gemini', 'gemini_events')


def load_cases(path):
    source = Path(path)
    raw = source.read_bytes()
    cases = []
    ids = set()
    for number, line in enumerate(raw.decode('utf-8-sig').splitlines(), 1):
        if not line.strip():
            continue
        case = json.loads(line)
        needed = {'id', 'transcript', 'timezone', 'call_started_at', 'commitment'}
        missing = needed - case.keys()
        if missing:
            raise ValueError(f'Line {number}: missing {sorted(missing)}')
        identifier = case['id']
        if not isinstance(identifier, str) or not identifier or identifier in ids:
            raise ValueError(f'Line {number}: IDs must be unique nonempty strings')
        ids.add(identifier)
        if not isinstance(case['transcript'], str) or not case['transcript'].strip():
            raise ValueError(f'Line {number}: nonempty transcript required')
        ZoneInfo(case['timezone'])
        call_time = datetime.fromisoformat(case['call_started_at'].replace('Z', '+00:00'))
        if call_time.tzinfo is None:
            raise ValueError(f'Line {number}: call_started_at requires timezone')
        gold = case['commitment']
        if not isinstance(gold, dict) or type(gold.get('confirmed')) is not bool:
            raise ValueError(f'Line {number}: commitment.confirmed must be boolean')
        if gold['confirmed']:
            if not gold.get('final_start') or not isinstance(gold['final_start'], str):
                raise ValueError(f'Line {number}: confirmed booking requires final_start')
            dt = datetime.fromisoformat(gold['final_start'].replace('Z', '+00:00'))
            if dt.tzinfo is None:
                raise ValueError(f'Line {number}: gold final_start requires UTC offset')
        elif gold.get('final_start') is not None:
            raise ValueError(f'Line {number}: unconfirmed booking must have final_start null')
        # No record/labels are passed to extractor. Keep full row only in evaluator.
        cases.append((case, call_time))
    if not cases:
        raise ValueError('No cases in dataset')
    return cases, hashlib.sha256(raw).hexdigest()


def classify(gold, prediction):
    if prediction.abstain:
        return 'abstained'
    if not gold['confirmed']:
        return 'false_booking' if prediction.confirmed or prediction.final_start else 'correct'
    if not prediction.confirmed:
        return 'missed_booking'
    return 'correct' if equal_instant(gold['final_start'], prediction.final_start) else 'wrong_time'


def compare(source, backends=('rules', 'groq'), output=DEFAULT_OUTPUT, extractor=None, pace_seconds=0.0):
    """Inject extractor only in isolated unit tests; real CLI calls production extractor."""
    load_dotenv(ROOT / '.env', override=False)
    if not 0 <= pace_seconds <= 120:
        raise ValueError('--pace-seconds must be between 0 and 120')
    last_cloud_request = None
    backends = tuple(backends)
    if not backends or len(set(backends)) != len(backends) or any(b not in SUPPORTED_BACKENDS for b in backends):
        raise ValueError('Backends must be unique and selected from rules,groq,groq_guarded,groq_events,gemini,gemini_events,openai')
    if any(b.startswith('gemini') for b in backends) and extractor is None and not os.getenv('GEMINI_API_KEY'):
        raise ValueError('GEMINI_API_KEY required for Gemini backend')
    if 'openai' in backends and extractor is None and not os.getenv('OPENAI_API_KEY'):
        raise RuntimeError(
            'OpenAI comparison NOT run: OPENAI_API_KEY is missing. Add it to .env '
            'or set it in PowerShell, then rerun. Rules scores are not an LLM score.'
        )
    if any(b in backends for b in ('groq', 'groq_guarded', 'groq_events')) and extractor is None and not os.getenv('GROQ_API_KEY'):
        raise RuntimeError(
            'Groq comparison NOT run: GROQ_API_KEY is missing. Add your Groq key to .env '
            'or set it in PowerShell. No Groq accuracy score was written.'
        )
    if 'rules' in backends and extractor is None:
        # Rules engine is explicitly demo-only. Do not silently evaluate a no-op.
        os.environ['DEMO_MODE'] = 'true'
    data, digest = load_cases(source)
    eval_extract = extractor or extract_commitment
    per_backend = {}
    per_case = {row['id']: {'id': row['id'], 'gold': {
        'confirmed': row['commitment']['confirmed'],
        'final_start': row['commitment'].get('final_start')}} for row, _ in data}
    # Raw Groq and guarded Groq reuse ONE provider call per transcript, so
    # side-by-side scores measure the guard rather than provider sampling drift.
    groq_cache = {}
    for backend in backends:
        evaluated = []
        failures = Counter()
        print(f'\nEvaluating {backend} ({len(data)} conversations) ...', flush=True)
        for ix, (case, call_time) in enumerate(data, 1):
            # Important: pass explicitly ONLY the transcript, timezone, and time.
            if backend in {'groq', 'groq_guarded'}:
                if case['id'] not in groq_cache:
                    if pace_seconds and extractor is None and last_cloud_request is not None:
                        time.sleep(max(0, pace_seconds-(time.monotonic()-last_cloud_request)))
                    if extractor is None:
                        last_cloud_request = time.monotonic()
                    groq_cache[case['id']] = eval_extract(
                        case['transcript'], case['timezone'],
                        backend='groq', reference_time=call_time)
                raw_prediction = groq_cache[case['id']]
                predicted = (guard_booking_prediction(raw_prediction, case['transcript'],
                            case['timezone'], call_time) if backend == 'groq_guarded' else raw_prediction)
            else:
                if backend in {'groq_events','openai','gemini','gemini_events'} and pace_seconds and extractor is None and last_cloud_request is not None:
                    time.sleep(max(0, pace_seconds-(time.monotonic()-last_cloud_request)))
                if backend in {'groq_events','openai','gemini','gemini_events'} and extractor is None:
                    last_cloud_request = time.monotonic()
                predicted = eval_extract(case['transcript'], case['timezone'],
                                         backend=backend, reference_time=call_time)
            if backend in {'openai', 'groq', 'groq_guarded', 'groq_events', 'gemini', 'gemini_events'} and predicted.abstain and predicted.reason.startswith(
                ('Extractor unavailable/invalid:', 'OpenAI API key missing', 'Groq API key missing', 'Gemini API key missing')
            ):
                # A network/auth/billing failure is NOT a model prediction. Abort,
                # and do not overwrite the prior report with fake 0/8 scores.
                raise RuntimeError(f'{backend} backend failed for {case["id"]}: '
                                   f'{predicted.reason}. Check API access and billing; '
                                   'no LLM accuracy report was written.')
            outcome = classify(case['commitment'], predicted)
            failures[outcome] += 1
            correct = correct_extraction(case['commitment'], predicted)
            evaluated.append({'prediction': predicted, 'gold': case['commitment'],
                              'extraction_correct': correct, 'state_correct': None})
            per_case[case['id']][backend] = {
                'confirmed': bool(predicted.confirmed),
                'final_start': predicted.final_start,
                'abstain': bool(predicted.abstain),
                'outcome': outcome,
                'exact_correct': bool(correct),
                # Do not save verbatim evidence or model-generated reason; may contain PII.
            }
            print(f'  [{ix}/{len(data)}] {case["id"]}: {outcome}', flush=True)
        metrics = report(evaluated)
        metrics['outcomes'] = dict(failures)
        model_name = {
            'openai': os.getenv('EXTRACTION_MODEL', 'gpt-4o-mini'),
            'groq': os.getenv('GROQ_EXTRACTION_MODEL', 'openai/gpt-oss-120b'),
            'groq_events': os.getenv('GROQ_EXTRACTION_MODEL', 'openai/gpt-oss-120b') + ' + typed event reducer',
            'groq_guarded': os.getenv('GROQ_EXTRACTION_MODEL', 'openai/gpt-oss-120b') + ' + deterministic correction/timezone guards',
            'gemini': os.getenv('GEMINI_MODEL','gemini-3.5-flash-lite'),
            'gemini_events': os.getenv('GEMINI_MODEL','gemini-3.5-flash-lite') + ' + event reducer',
            'rules': 'offline_rules_v1',
        }[backend]
        per_backend[backend] = {'metrics': metrics, 'model': model_name}
    artifact = {
        'kind': 'lane_transcript_extraction_comparison',
        'dataset': Path(source).name,
        'dataset_sha256': digest,
        'synthetic': Path(source).name in {'extraction_challenge.jsonl', 'booking_cases.jsonl'},
        'note': 'Transcript extraction only; not real HighLevel booking verification. No customer accuracy claim.',
        'backends': per_backend,
        'cases': list(per_case.values()),
    }
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(artifact, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    markdown = target.with_suffix('.md')
    render_markdown(artifact, markdown)
    print('\nBACKEND COMPARISON (abstentions included in denominator)')
    for name, value in per_backend.items():
        m = value['metrics']
        print(f'  {name:7}: exact={m["extraction_correct"]}/{m["total"]}, '
              f'covered={m["covered"]}/{m["total"]}, '
              f'false_bookings={m["outcomes"].get("false_booking", 0)}, '
              f'wrong_times={m["outcomes"].get("wrong_time", 0)}')
    print(f'JSON report: {target}\nMarkdown report: {markdown}')
    return artifact


def render_markdown(artifact, target):
    lines = [
        '# Lane extraction benchmark', '',
        f'Dataset: `{artifact["dataset"]}` · SHA-256: `{artifact["dataset_sha256"]}`',
        '',
        '**Synthetic evaluation; not a real-world reliability or HighLevel write success rate.**' if artifact['synthetic'] else
        '**Evaluate annotation provenance before making accuracy claims.**',
        '',
        '| Backend | Model | Exact (all) | Coverage | False bookings | Missed bookings | Wrong times | Abstained |',
        '|---|---|---:|---:|---:|---:|---:|---:|',
    ]
    for backend, value in artifact['backends'].items():
        m = value['metrics']
        count = m['outcomes']
        lines.append(f'| {backend} | {value["model"]} | {m["extraction_correct"]}/{m["total"]} | '
                     f'{m["covered"]}/{m["total"]} | {count.get("false_booking",0)} | '
                     f'{count.get("missed_booking",0)} | {count.get("wrong_time",0)} | '
                     f'{count.get("abstained",0)} |')
    lines.extend(['', '| Case ID | Ground truth | ' + ' | '.join(artifact['backends']) + ' |',
                  '|---|---|' + '|'.join(['---' for _ in artifact['backends']]) + '|'])
    for case in artifact['cases']:
        gold = case['gold']['final_start'] if case['gold']['confirmed'] else 'no booking'
        cells = [case['id'], gold]
        for name in artifact['backends']:
            value = case[name]
            predicted = value['final_start'] if value['confirmed'] else 'no booking'
            cells.append(f'{value["outcome"]} ({predicted})')
        lines.append('| ' + ' | '.join(cells) + ' |')
    target.write_text('\n'.join(lines) + '\n', encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dataset', nargs='?', default='datasets/extraction_challenge.jsonl')
    parser.add_argument('--backends', default='rules,groq,groq_guarded',
                        help='Comma-separated: rules,groq,groq_guarded,groq_events,gemini,gemini_events,openai; defaults to rules,groq,groq_guarded')
    parser.add_argument('--output', default=str(DEFAULT_OUTPUT))
    parser.add_argument('--pace-seconds',type=float,default=0.0,
                        help='Minimum seconds between cloud API requests (useful for free-tier limits)')
    args = parser.parse_args()
    try:
        compare(args.dataset, backends=args.backends.split(','), output=args.output,
                pace_seconds=args.pace_seconds)
    except (ValueError, RuntimeError, FileNotFoundError) as e:
        parser.exit(2, f'Comparison failed: {e}\n')
