"""No synthetic precision claims. Evaluate a human-labelled JSONL set.
Each line: {"transcript": "AGENT: ...", "has_unauthorized_promise": true,
            "never_promise": ["discount","refund","price","guarantee"]}
Run with OPENAI_API_KEY set (use consented/anonymized data).
"""
import argparse, json
from app.policy import label_agent_promises, unauthorized_promises

parser = argparse.ArgumentParser()
parser.add_argument('dataset', help='Human-labelled JSONL file')
args = parser.parse_args()
TP=FP=FN=TN=ABSTAIN=0
for line in open(args.dataset, encoding='utf-8'):
    if not line.strip(): continue
    row = json.loads(line)
    labels = label_agent_promises(row['transcript'])
    if labels.abstain:
        ABSTAIN += 1
        continue
    predicted = any(x.exact_quote and x.exact_quote.lower() in row['transcript'].lower()
                    for x in unauthorized_promises(labels, row.get('never_promise',['discount','refund','price','guarantee'])))
    truth = bool(row['has_unauthorized_promise'])
    if predicted and truth: TP+=1
    elif predicted and not truth: FP+=1
    elif not predicted and truth: FN+=1
    else: TN+=1
print(json.dumps({'evaluated':TP+FP+FN+TN,'abstained':ABSTAIN, 'TP':TP,'FP':FP,'FN':FN,'TN':TN,
                  'precision':round(TP/(TP+FP),4) if TP+FP else None,
                  'recall':round(TP/(TP+FN),4) if TP+FN else None},indent=2))
