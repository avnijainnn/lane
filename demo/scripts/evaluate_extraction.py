"""Evaluate transcript extraction on user-provided labelled dialogues (NO calendar access).

Input: JSONL objects with:
 {"id":"call1", "transcript":"Caller: ...\\nAgent: ...",
  "timezone":"America/Chicago", "call_started_at":"2026-10-22T14:00:00Z",
  "commitment":{"confirmed":true,"final_start":"2026-11-03T10:00:00-06:00"}}

The `commitment` label is NEVER provided to the extractor. Outputs are written
as JSON to a local file containing no raw transcripts. Obtain consent and de-ID
any real transcripts before using a cloud LLM backend.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
from app.extract import extract_commitment
from app.evaluation import correct_extraction, report


def evaluate(path: str, backend='rules', output=None):
    source = Path(path)
    results = []
    for n, line in enumerate(source.read_text(encoding='utf-8').splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        for k in ('id','transcript','timezone','call_started_at','commitment'):
            if k not in row:
                raise ValueError(f'Line {n}: missing {k}')
        gold = row['commitment']
        if not isinstance(gold.get('confirmed'), bool):
            raise ValueError(f'Line {n}: commitment.confirmed must be boolean')
        if gold['confirmed'] and not gold.get('final_start'):
            raise ValueError(f'Line {n}: expected booking must have final_start')
        call_start = datetime.fromisoformat(row['call_started_at'].replace('Z', '+00:00'))
        if call_start.tzinfo is None:
            raise ValueError(f'Line {n}: call_started_at must have explicit timezone')
        predicted = extract_commitment(row['transcript'], row['timezone'],
                                       backend=backend, reference_time=call_start)
        results.append({
            'id':row['id'], 'gold':gold, 'prediction':predicted,
            'extraction_correct':correct_extraction(gold,predicted),
            'state_correct':None,
        })
    score = report(results)
    artifact = {
        'description':'Extraction evaluation only, NOT independently observed calendar fulfillment',
        'source':source.name, 'backend':backend, 'metrics':score,
        'cases':[{
            'id':r['id'], 'gold':r['gold'],
            'prediction':r['prediction'].model_dump(),
            'correct':r['extraction_correct'],
        } for r in results],
    }
    dest = Path(output) if output else Path('datasets') / f'{source.stem}.{backend}.evaluation.json'
    dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_text(json.dumps(artifact,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(f'Cases evaluated: {score["total"]}; coverage: {score["covered"]}/{score["total"]}')
    print(f'Exact correctness with abstentions counted incorrect: {score["extraction_correct"]}/{score["total"]}')
    print(f'Report: {dest}; does not measure real calendar writes or real-world accuracy unless source is independently annotated real data')
    return artifact

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('labels_jsonl')
    parser.add_argument('--backend',choices=['rules','openai','groq','groq_guarded','groq_events'],default='rules')
    parser.add_argument('--output')
    args = parser.parse_args()
    evaluate(args.labels_jsonl,backend=args.backend,output=args.output)
