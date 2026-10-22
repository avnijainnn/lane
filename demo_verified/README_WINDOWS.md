# Lane: enriched SGD transcript annotations

The review template has blank `timezone`, `call_started_at`, `confirmed`, `final_start`, and `reviewer` because public SGD dialogues lack the timestamp and independent booking-state context needed by Lane's strict absolute-time scoring. Empty cells are **not a software failure**. The nine provided transcripts have now been annotated in a separate CSV with fields which *can* be supported by the transcripts.

## Install and merge in Windows PowerShell

```powershell
cd "C:\Users\91921\Downloads\yc\lane\demo_verified"
$python = "C:\Users\91921\Downloads\yc\lane\.venv\Scripts\python.exe"
Expand-Archive -LiteralPath "$env:USERPROFILE\Downloads\Lane_SGD_Enriched_Review.zip" -DestinationPath . -Force
& $python .\merge_sgd_semantic.py --review datasets/public/sgd_review.csv --labels sgd_9_semantic_labels.csv --output datasets/public/sgd_review_enriched.csv
Invoke-Item datasets/public/sgd_review_enriched.csv
```

Matches the 9 provided sample IDs against *your* CSV, preserving all original full transcripts and all other rows. If your CSV is a different sample, matched counts can be 0. The script never overwrites original CSV or JSONL.

`confirmed_transcript_only` classifies agent-reported restaurant booking outcomes, not calendar record verification. 'not_applicable' means event ticket/media task outside restaurant-booking benchmark. No invented absolute timestamps or human-reviewer names.

**Do not run `scripts.public_benchmark reviewed` on this enriched CSV yet.** That strict converter requires a reviewer, known call start, timezone, and absolute timestamp for each confirmed booking; these cannot be reconstructed reliably from the supplied excerpts. A semantic evaluation mode would be needed to score these transcript-level fields across a larger dataset.
