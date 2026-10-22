"""Merge 9 transcript-derived labels into YOUR existing SGD review CSV.

No guessed dates or timezones; no changes to source CSV; no model output used as gold.
"""
from pathlib import Path
import csv
import argparse

p=argparse.ArgumentParser()
p.add_argument('--review',default='datasets/public/sgd_review.csv')
p.add_argument('--labels',default='sgd_9_semantic_labels.csv')
p.add_argument('--output',default='datasets/public/sgd_review_enriched.csv')
a=p.parse_args()
source=Path(a.review).resolve()
dest=Path(a.output).resolve()
if source==dest:
    raise SystemExit('Refusing to overwrite source review CSV')
if dest==Path('datasets/public/sgd_candidates.jsonl').resolve():
    raise SystemExit('Refusing to overwrite original candidate JSONL')
with Path(a.labels).open(encoding='utf-8-sig',newline='') as f:
    labels={r['id']:r for r in csv.DictReader(f)}
with source.open(encoding='utf-8-sig',newline='') as f:
    reader=csv.DictReader(f)
    originals=list(reader)
    fields=list(reader.fieldnames or [])
if not {'id','transcript','confirmed','timezone','call_started_at','final_start','reviewer','review_notes'}.issubset(fields):
    raise SystemExit('Not an SGD review CSV: required columns missing')
extra=[c for c in next(iter(labels.values())) if c != 'id' and c not in fields]
matched=0
for row in originals:
    lab=labels.get(row['id'])
    if not lab:
        continue
    matched+=1
    for k in extra:
        row[k]=lab[k]
    # The existing exact-time evaluator's confirmed field requires full time
    # context. Leave it BLANK rather than turn AI transcript claims into gold.
    # Likewise do not invent final_start, call_started_at, or timezone.
    row['review_notes']=' | '.join(s for s in [row.get('review_notes','').strip(),lab['annotation_notes']] if s)
# Preserve existing human-filled core labels without modification.
dest.parent.mkdir(parents=True,exist_ok=True)
with dest.open('w',encoding='utf-8-sig',newline='') as f:
    writer=csv.DictWriter(f,fieldnames=fields+extra)
    writer.writeheader()
    writer.writerows(originals)
print(f'Saved {dest}')
print(f'Attached semantic labels: {matched}/{len(originals)} rows')
print('Intentionally did not change confirmed/final_start/timezone/call_started_at/reviewer')
print('Use the new semantic fields for transcript analysis, NOT scripts.public_benchmark reviewed.')
