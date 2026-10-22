"""Lane public benchmark intake: public dialogues are UNLABELED for booking outcome.

Fetch selected original files, convert to review candidates, create a CSV annotation
sheet, validate human reviews into the existing Lane comparison JSONL format,
or perform an unlabelled stress run. Never turns slot tags into booking ground truth.
"""
import argparse
import csv
import gzip
import hashlib
import json
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

TM = 'https://raw.githubusercontent.com/google-research-datasets/Taskmaster/master/TM-1-2019/'
SGD = 'https://raw.githubusercontent.com/google-research-datasets/dstc8-schema-guided-dialogue/master/'
ABCD = 'https://raw.githubusercontent.com/asappresearch/abcd/master/data/'
RELEVANT = re.compile(r'book|appoint|reserv|schedul|reschedul|cancel|slot|availab|\bdate\b|\btime\b', re.I)
LABEL_COLUMNS = ['id','source','domain','transcript','timezone','call_started_at','confirmed','final_start','reviewer','review_notes']


def fetch(source, directory, *, split='train', files=3, full=False):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if source == 'sgd':
        if split not in {'train','dev','test'} or not 1 <= files <= 127:
            raise ValueError('Invalid SGD split/files')
        urls = [(f'{SGD}{split}/dialogues_{i:03d}.json', f'{split}_dialogues_{i:03d}.json') for i in range(1, files+1)]
    elif source == 'taskmaster1':
        urls = [(TM + ('woz-dialogs.json' if not full else 'self-dialogs.json'), 'woz-dialogs.json' if not full else 'self-dialogs.json')]
    elif source == 'abcd':
        urls = [(ABCD + ('abcd_v1.1.json.gz' if full else 'abcd_sample.json'), 'abcd_v1.1.json.gz' if full else 'abcd_sample.json')]
    else:
        raise ValueError('Unsupported source')
    paths = []
    for url, name in urls:
        target = directory / name
        if target.exists() and target.stat().st_size > 0:
            print(f'Already downloaded: {target}')
        else:
            req = Request(url, headers={'User-Agent': 'LanePublicBenchmark/1.0'})
            temporary = target.with_name(target.name + '.partial')
            try:
                with urlopen(req, timeout=90) as response, temporary.open('wb') as stream:
                    while True:
                        chunk = response.read(1024*1024)
                        if not chunk:
                            break
                        stream.write(chunk)
                temporary.replace(target)
            finally:
                if temporary.exists():
                    temporary.unlink()
            print(f'Downloaded: {target} ({target.stat().st_size:,} bytes)')
        paths.append(target)
    return paths


def read_json(path):
    path = Path(path)
    if path.suffix == '.gz':
        with gzip.open(path, 'rt', encoding='utf-8') as f:
            return json.load(f)
    with path.open(encoding='utf-8-sig') as f:
        return json.load(f)


def as_lines(turns, source):
    lines = []
    for turn in turns:
        if source == 'abcd':
            if isinstance(turn, (list,tuple)) and len(turn) >= 2:
                speaker,text = turn[0],turn[1]
            elif isinstance(turn, dict):
                speaker,text = turn.get('speaker'), turn.get('text')
            else:
                continue
        elif source == 'taskmaster1':
            speaker,text = turn.get('speaker'),turn.get('text')
        else:
            speaker,text = turn.get('speaker'),turn.get('utterance')
        if speaker is None or not isinstance(text,str) or not text.strip():
            continue
        role = ('Caller' if str(speaker).lower() in ('user','customer')
                else 'Agent' if str(speaker).lower() in ('system','assistant','agent')
                else 'Tool')
        lines.append(f'{role}: {text.strip()}')
    return '\n'.join(lines)


def iter_source(source, data, name):
    if source == 'abcd':
        if isinstance(data,dict):
            data = [r for split in ('train','dev','test') for r in data.get(split,[])]
        for i,row in enumerate(data):
            scenario = row.get('scenario') or {}
            yield row.get('convo_id', f'{name}:{i}'), str(scenario.get('flow') or 'policy'), as_lines(row.get('original',[]),'abcd')
    elif source == 'sgd':
        for i,row in enumerate(data):
            services = [str(s) for s in row.get('services',[])]
            yield row.get('dialogue_id',f'{name}:{i}'), '/'.join(services), as_lines(row.get('turns',[]),'sgd')
    else:
        for i,row in enumerate(data):
            ident = row.get('conversation_id') or row.get('conversationId') or f'{name}:{i}'
            domain = row.get('instruction_id') or row.get('instructionId') or ''
            yield ident, str(domain), as_lines(row.get('utterances',[]),'taskmaster1')


def prepare(source, inputs, output, *, maximum=2000, seed=17, only_relevant=True):
    if maximum < 1:
        raise ValueError('maximum must be >= 1')
    output = Path(output)
    source_paths = []
    for raw in inputs:
        path = Path(raw)
        if not path.exists():
            raise FileNotFoundError(path)
        if path.is_dir():
            source_paths.extend(sorted(p for p in path.rglob('*') if p.suffix in {'.json','.gz'} and ('dialogues_' in p.name or 'dialogs' in p.name or p.name.startswith('abcd_'))))
        else:
            source_paths.append(path)
    if not source_paths:
        raise ValueError('No matching public corpus JSON files')
    # Priority sampling: deterministic so run can be reproduced. Distinct ID
    # includes corpus file to avoid collisions across SGD split files.
    candidates = []
    seen = set()
    for path in source_paths:
        data = read_json(path)
        for original_id,domain,transcript in iter_source(source,data,path.name):
            if not transcript:
                continue
            if only_relevant and not (RELEVANT.search(domain) or RELEVANT.search(transcript)):
                continue
            uid = hashlib.sha256(f'{source}:{path.name}:{original_id}'.encode()).hexdigest()[:16]
            if uid in seen:
                continue
            seen.add(uid)
            candidates.append({'id':f'{source}_{uid}', 'source':source, 'source_file':path.name,
                               'original_id':str(original_id), 'domain':domain,
                               'transcript':transcript, 'annotation_status':'UNLABELED'})
    random.Random(seed).shuffle(candidates)
    candidates=candidates[:maximum]
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('w',encoding='utf-8') as f:
        for c in candidates:
            f.write(json.dumps(c,ensure_ascii=False)+'\n')
    print(f'Wrote {len(candidates)} UNLABELED conversations to {output}')
    print('No booking accuracy can be computed until human-reviewed final commitments exist.')
    return candidates


def rows(path):
    with Path(path).open(encoding='utf-8-sig') as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def template(source, output, n=100):
    candidates=list(rows(source))[:n]
    dest=Path(output); dest.parent.mkdir(parents=True,exist_ok=True)
    with dest.open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=LABEL_COLUMNS)
        writer.writeheader()
        for r in candidates:
            writer.writerow({k:r.get(k,'') for k in ('id','source','domain','transcript')})
    print(f'Annotation template: {dest} ({len(candidates)} rows)')
    return dest


def reviewed(source, output):
    """Require explicit human label + time context. Do not infer it from slots."""
    dest=Path(output); dest.parent.mkdir(parents=True,exist_ok=True)
    converted=[]
    with Path(source).open(encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f)
        if not set(LABEL_COLUMNS).issubset(set(reader.fieldnames or [])):
            raise ValueError('Missing annotation columns')
        for rownum,r in enumerate(reader,2):
            flag=r['confirmed'].strip().lower()
            if flag not in ('yes','no','true','false'):
                continue  # pending review: NEVER default to no booking
            if not r['reviewer'].strip():
                raise ValueError(f'Row {rownum}: reviewer required')
            if not r['transcript'].strip():
                raise ValueError(f'Row {rownum}: transcript empty')
            tz=r['timezone'].strip()
            ZoneInfo(tz)
            timestamp=r['call_started_at'].strip()
            try:
                dt=datetime.fromisoformat(timestamp.replace('Z','+00:00'))
                if dt.tzinfo is None:
                    raise ValueError()
            except ValueError:
                raise ValueError(f'Row {rownum}: call_started_at must have an explicit UTC offset')
            yes=flag in ('yes','true')
            start=r['final_start'].strip()
            if yes:
                try:
                    booked=datetime.fromisoformat(start.replace('Z','+00:00'))
                    if booked.tzinfo is None:
                        raise ValueError()
                except ValueError:
                    raise ValueError(f'Row {rownum}: confirmed booking needs offset-aware final_start')
            elif start:
                raise ValueError(f'Row {rownum}: nonbooking final_start must be empty')
            converted.append({'id':r['id'],'transcript':r['transcript'],'timezone':tz,
                              'call_started_at':timestamp,
                              'commitment':{'confirmed':yes,'final_start':start if yes else None}})
    if not converted:
        raise ValueError('No reviewed labels. Fill reviewer, confirmed, timezone and call_started_at')
    if len({r['id'] for r in converted}) != len(converted):
        raise ValueError('Duplicate reviewed IDs')
    with dest.open('w',encoding='utf-8') as f:
        for obj in converted:
            f.write(json.dumps(obj,ensure_ascii=False)+'\n')
    print(f'Wrote {len(converted)} reviewed evaluation examples: {dest}')
    return converted


def _stress_signature(samples, backend):
    import os
    # Include model, source IDs and full input hashes so a resumed run cannot
    # silently mix different datasets, model versions, or experiment parameters.
    canonical = {
        'backend': backend,
        'model': (os.environ.get('GEMINI_MODEL', 'gemini-3.5-flash-lite') if backend.startswith('gemini') else (os.environ.get('GROQ_EXTRACTION_MODEL', 'openai/gpt-oss-120b') if backend != 'rules' else 'rules')),
        'reference_clock': '2026-10-22T14:00:00+00:00',
        'timezone': 'America/Chicago',
        'items': [(c['id'], hashlib.sha256(c['transcript'].encode('utf-8')).hexdigest()) for c in samples],
    }
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode('utf-8')).hexdigest()


def _write_stress_report(dest, *, source, backend, samples, outcomes, signature):
    """Atomic, cumulative checkpoint. Provider errors are not predictions."""
    ordered = [outcomes[c['id']] for c in samples if c['id'] in outcomes]
    evaluated = [r for r in ordered if r.get('status') == 'evaluated']
    failed = [r for r in ordered if r.get('status') == 'provider_error']
    artifact = {
        'kind': 'unlabelled_public_corpus_stress',
        'source': str(source), 'backend': backend, 'run_signature': signature,
        'requested_n': len(samples),
        'n': len(evaluated),  # backwards-compatible, but NEVER count provider errors
        'evaluated_n': len(evaluated), 'provider_error_n': len(failed),
        'unprocessed_n': len(samples) - len(ordered),
        'status': 'complete' if len(ordered) == len(samples) and not failed else 'partial',
        'abstentions': sum(r['abstain'] for r in evaluated),
        'predicted_bookings': sum(r['predicted_booking'] for r in evaluated),
        'reference_clock': 'ARTIFICIAL 2026-10-22 UTC; NOT EVALUATION GROUND TRUTH',
        'accuracy': None,
        'warning': 'UNLABELLED. Provider failures are missing predictions, never abstentions or booking outcomes.',
        'cases': ordered,
    }
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = dest.with_name(dest.name + '.tmp')
    temporary.write_text(json.dumps(artifact, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    temporary.replace(dest)
    return artifact


TRANSIENT_HTTP_STATUSES = frozenset({408, 500, 502, 503, 504})


def _provider_status(reason):
    match = re.search(r'\bhttp_status=(\d{3})(?:;|$)', reason)
    return int(match.group(1)) if match else None


def stress(source, *, backend='rules', n=100, pace=5.0, output='datasets/reports/public_stress.json',
           allow_cloud=False, start_at=1, resume=False, continue_on_bad_request=False,
           retry_provider_errors=False, transient_retries=0, transient_backoff=3.0,
           skip_transient_errors=False):
    """Unlabelled stress test, incrementally checkpointed and safely resumable.

    --start-at 43 avoids repeating 1..42, but those 42 are marked unprocessed:
    the previous interrupted run never saved its predictions.
    """
    if transient_retries < 0 or transient_retries > 5:
        raise ValueError('--transient-retries must be between 0 and 5')
    if transient_backoff < 0 or transient_backoff > 120:
        raise ValueError('--transient-backoff-seconds must be between 0 and 120')
    if backend not in ('rules', 'groq', 'groq_guarded', 'groq_events', 'gemini', 'gemini_events'):
        raise ValueError('Unsupported backend')
    if backend != 'rules' and not allow_cloud:
        raise ValueError('Explicit --allow-cloud needed before sending corpus text to provider')
    if not 1 <= start_at <= n:
        raise ValueError('--start-at must be between 1 and --n')
    if retry_provider_errors and not resume:
        raise ValueError('--retry-provider-errors requires --resume')
    from app.extract import extract_commitment
    from dotenv import load_dotenv
    import os
    load_dotenv(Path(__file__).resolve().parents[1]/'.env')
    if backend == 'rules':
        os.environ['DEMO_MODE'] = 'true'
    samples = list(rows(source))[:n]
    if not samples:
        raise ValueError('No candidates')
    if start_at > len(samples):
        raise ValueError(f'--start-at {start_at} exceeds available candidates ({len(samples)})')
    if len({c['id'] for c in samples}) != len(samples):
        raise ValueError('Duplicate candidate IDs: cannot resume safely')
    signature = _stress_signature(samples, backend)
    dest = Path(output)
    outcomes = {}
    if resume:
        if not dest.exists():
            raise ValueError(f'Cannot --resume: report not found at {dest}')
        old = json.loads(dest.read_text(encoding='utf-8'))
        if old.get('run_signature') != signature:
            raise ValueError('Cannot --resume: dataset, model, backend or anchor differs from checkpoint')
        outcomes = {c['id']: c for c in old.get('cases', [])}
    elif dest.exists():
        raise ValueError(f'Report already exists: {dest}. Use --resume or a different --output path.')
    # Create report BEFORE the first API call, then after every successful/failing call.
    artifact = _write_stress_report(dest, source=source, backend=backend,
                                     samples=samples, outcomes=outcomes, signature=signature)
    consecutive_errors = 0
    for idx in range(start_at, len(samples) + 1):
        c = samples[idx - 1]
        if c['id'] in outcomes and not (retry_provider_errors and outcomes[c['id']].get('status') == 'provider_error'):
            print(f'[{idx}/{len(samples)}] checkpoint exists, skipped', flush=True)
            continue
        anchor = datetime.fromisoformat('2026-10-22T14:00:00+00:00')
        retry_count = 0
        while True:
            p = extract_commitment(c['transcript'], 'America/Chicago', backend=backend,
                                   reference_time=anchor)
            failure = bool(p.abstain and p.reason.startswith(
                ('Extractor unavailable/invalid:', 'Groq API key missing', 'OpenAI API key missing', 'Gemini API key missing')))
            http_status = _provider_status(p.reason) if failure else None
            if not (failure and http_status in TRANSIENT_HTTP_STATUSES
                    and retry_count < transient_retries):
                break
            # Bound retries; no retries for 400/401/403/429. Never print provider
            # response bodies, transcript text, or credentials.
            backoff = min(90.0, transient_backoff * (2 ** retry_count))
            delay = backoff + random.uniform(0.0, min(1.0, backoff * 0.2))
            retry_count += 1
            print(f'[{idx}/{len(samples)}] temporary HTTP {http_status}; '
                  f'retry {retry_count}/{transient_retries}', flush=True)
            time.sleep(delay)
        if failure:
            # This is an unscored provider failure. Do not fake a model abstention.
            kind_match = re.search(r'Extractor unavailable/invalid: ([A-Za-z0-9_]+)', p.reason)
            kind = kind_match.group(1) if kind_match else 'Unavailable'
            # Extract ONLY allowlisted metadata from the sanitized reason. The
            # reason's prefix contains ':' and '/', so checking the full reason
            # against a restricted regex used to erase all HTTP status codes.
            safe_fields = re.findall(r'\b(http_status|code|type|param)=([A-Za-z0-9_.-]{1,64})(?=;|$)', p.reason)
            diagnostic = '; '.join([kind] + [f'{name}={value}' for name, value in safe_fields])
            outcomes[c['id']] = {'id': c['id'], 'index': idx, 'status': 'provider_error',
                                  'error_kind': kind, 'diagnostic': diagnostic}
            consecutive_errors += 1
        else:
            outcomes[c['id']] = {'id': c['id'], 'index': idx, 'status': 'evaluated',
                                  'abstain': bool(p.abstain),
                                  'predicted_booking': bool(p.confirmed),
                                  'has_time': bool(p.final_start)}
            consecutive_errors = 0
        artifact = _write_stress_report(dest, source=source, backend=backend,
                                         samples=samples, outcomes=outcomes, signature=signature)
        print(f'[{idx}/{len(samples)}] ' + ('PROVIDER ERROR ' + kind if failure else 'processed')
              + f' (checkpoint: {dest})', flush=True)
        if failure:
            isolated_bad_request = (kind == 'BadRequestError' or
                (kind == 'GeminiProviderError' and 'http_status=400' in diagnostic))
            skip_case = ((continue_on_bad_request and isolated_bad_request)
                         or (skip_transient_errors and http_status in TRANSIENT_HTTP_STATUSES))
            if not skip_case or consecutive_errors >= 3:
                raise RuntimeError(f'Provider failure at {idx}: {diagnostic}. '
                                   f'Checkpoint saved at {dest}; use --resume to continue.')
        if backend != 'rules' and idx < len(samples):
            time.sleep(max(0, pace))
    print(f"Saved {artifact['evaluated_n']}/{len(samples)} evaluated, "
          f"{artifact['provider_error_n']} provider errors, "
          f"{artifact['unprocessed_n']} unprocessed to {dest}", flush=True)
    return artifact



def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('fetch');p.add_argument('--source',required=True,choices=['sgd','taskmaster1','abcd'])
    p.add_argument('--dir',default='datasets/public/raw');p.add_argument('--split',default='train');p.add_argument('--files',type=int,default=3);p.add_argument('--full',action='store_true')
    p=sub.add_parser('prepare');p.add_argument('--source',required=True,choices=['sgd','taskmaster1','abcd']);p.add_argument('--input',nargs='+',required=True)
    p.add_argument('--output',default='datasets/public/candidates.jsonl');p.add_argument('--max',type=int,default=2000);p.add_argument('--seed',type=int,default=17);p.add_argument('--all-domains',action='store_true')
    p=sub.add_parser('template');p.add_argument('source');p.add_argument('--output',default='datasets/public/review.csv');p.add_argument('--n',type=int,default=100)
    p=sub.add_parser('reviewed');p.add_argument('source');p.add_argument('--output',default='datasets/public/gold_reviewed.jsonl')
    p=sub.add_parser('stress');p.add_argument('source');p.add_argument('--backend',default='rules');p.add_argument('--n',type=int,default=100)
    p.add_argument('--pace-seconds',type=float,default=5);p.add_argument('--output',default='datasets/reports/public_stress.json');p.add_argument('--allow-cloud',action='store_true')
    p.add_argument('--start-at',type=int,default=1,help='1-based starting position; prior positions remain UNPROCESSED, not scored')
    p.add_argument('--resume',action='store_true',help='Skip saved results and continue after interruption')
    p.add_argument('--continue-on-bad-request',action='store_true',help='Mark isolated HTTP 400 cases as provider errors and continue; stop after 3 consecutive errors')
    p.add_argument('--retry-provider-errors',action='store_true',help='With --resume, retry previously failed provider requests')
    p.add_argument('--transient-retries',type=int,default=0,help='Bounded retries per case for HTTP 408/5xx; never retry HTTP 429 or 400')
    p.add_argument('--transient-backoff-seconds',type=float,default=3,help='Initial exponential-backoff delay with jitter')
    p.add_argument('--skip-transient-errors',action='store_true',help='After retries, retain a provider_error and move to the next case; halt after three consecutive errors')
    a=parser.parse_args(argv)
    try:
        if a.command=='fetch':fetch(a.source,a.dir,split=a.split,files=a.files,full=a.full)
        elif a.command=='prepare':prepare(a.source,a.input,a.output,maximum=a.max,seed=a.seed,only_relevant=not a.all_domains)
        elif a.command=='template':template(a.source,a.output,n=a.n)
        elif a.command=='reviewed':reviewed(a.source,a.output)
        elif a.command=='stress':stress(a.source,backend=a.backend,n=a.n,pace=a.pace_seconds,output=a.output,allow_cloud=a.allow_cloud,start_at=a.start_at,resume=a.resume,continue_on_bad_request=a.continue_on_bad_request,retry_provider_errors=a.retry_provider_errors,transient_retries=a.transient_retries,transient_backoff=a.transient_backoff_seconds,skip_transient_errors=a.skip_transient_errors)
    except (ValueError,FileNotFoundError,RuntimeError) as err:
        parser.exit(2,f'Error: {err}\n')


if __name__=='__main__':main()
