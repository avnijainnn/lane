"""Fetch public Appointment Bench turn metadata for extraction experiments.

This benchmark contains conversational and tool-call *expected* outputs, not a
HighLevel calendar record. Do not treat it as evidence of real bookings landing.

    python -m scripts.prepare_appointment_bench

Creates datasets/appointment_bench_turns.jsonl. Requires internet access.
"""
import json
import urllib.request
from pathlib import Path

SOURCE = 'https://huggingface.co/datasets/arcada-labs/appointment-bench/resolve/main/metadata.jsonl'
DESTINATION = Path('datasets/appointment_bench_turns.jsonl')


def main():
    request = urllib.request.Request(SOURCE,headers={'User-Agent':'Lane-research-fixture-importer/0.1'})
    with urllib.request.urlopen(request,timeout=30) as r:
        data = r.read(3_000_000).decode('utf-8')
    rows = [json.loads(line) for line in data.splitlines() if line.strip()]
    # Audio speaker variants share the same scripted turn; dedupe and preserve
    # the original benchmark turn metadata rather than inventing appointments.
    turns = {}
    for row in rows:
        t = row.get('turn_id')
        if not isinstance(t,int): continue
        if t not in turns:
            turns[t] = {'turn_id':t,'source':'arcada-labs/appointment-bench',
                        'input_text':row.get('input_text'),
                        'golden_text':row.get('golden_text'),
                        'required_function_call':row.get('required_function_call'),
                        'function_call_response':row.get('function_call_response'),
                        'categories':row.get('categories')}
    DESTINATION.parent.mkdir(parents=True,exist_ok=True)
    DESTINATION.write_text(''.join(json.dumps(v,ensure_ascii=False)+'\n' for _,v in sorted(turns.items())),encoding='utf-8')
    print(f'Wrote {len(turns)} unique benchmark turns to {DESTINATION}')
    print('Use these for extraction/behavior testing; independently observed calendar ground truth NOT included.')


if __name__ == '__main__':
    main()
