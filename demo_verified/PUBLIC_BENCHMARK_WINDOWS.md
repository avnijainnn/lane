# Lane: larger public-dataset testing (Windows)

This is an additive patch for `C:\Users\91921\Downloads\yc\lane\demo_verified`. It does not modify SQLite tables, existing case histories, or `.env`.

**Important:** SGD, Taskmaster, and ABCD are useful source dialogues, but none provide independently read HighLevel calendar outcomes. Also, Taskmaster API-slot annotations and SGD dialogue-state annotations are **not** the same as Lane's gold `confirmed`/`final_start` commitments. Import does not invent gold labels or claim accuracy. Dialogue datasets often lack actual call timestamps/time zones, especially for phrases like "tomorrow"; manually review them before scoring. The `stress` command uses a fake date purely to exercise code paths. Do not report its booking predictions as accuracy.

## Install

Stop Uvicorn (Ctrl+C). Download `Lane_Public_Dataset_Benchmark_Patch.zip` to Downloads.

```powershell
Set-Location 'C:\Users\91921\Downloads\yc\lane\demo_verified'
Expand-Archive -LiteralPath "$env:USERPROFILE\Downloads\Lane_Public_Dataset_Benchmark_Patch.zip" -DestinationPath '.' -Force
$python = 'C:\Users\91921\Downloads\yc\lane\.venv\Scripts\python.exe'
& $python -m pytest tests/test_public_benchmark.py -q
```

## A. SGD: download a starting slice (roughly several hundred dialogues per JSON file)

```powershell
& $python -m scripts.public_benchmark fetch --source sgd --split train --files 5 --dir datasets/public/raw/sgd
& $python -m scripts.public_benchmark prepare --source sgd --input datasets/public/raw/sgd --max 1000 --output datasets/public/sgd_candidates.jsonl
```

To go larger, increase `--files` (up to 127 for train) and `--max`. Import count depends on how many dialogues pass the relevance filter. For a held-out evaluation, download `dev` or `test` separately (and do not tune prompts on those labels).

## B. Taskmaster-1: original two-person speech transcriptions

```powershell
& $python -m scripts.public_benchmark fetch --source taskmaster1 --dir datasets/public/raw/tm1
& $python -m scripts.public_benchmark prepare --source taskmaster1 --input datasets/public/raw/tm1 --max 1500 --output datasets/public/tm1_candidates.jsonl
```

This fetches `woz-dialogs.json` (two-person dialogues); using `--full` fetches `self-dialogs.json` (single-worker written dialogues) **instead**, not both.

## C. ABCD: policy-oriented conversations

```powershell
& $python -m scripts.public_benchmark fetch --source abcd --full --dir datasets/public/raw/abcd
& $python -m scripts.public_benchmark prepare --source abcd --input datasets/public/raw/abcd --max 1500 --output datasets/public/abcd_candidates.jsonl
```

This fetches the full compressed corpus. Without `--full`, only the three-example sample downloads. ABCD is mostly about return/refund/customer support policy, not appointment bookings: use it for mandate and forbidden-promise trials, rather than calendar-booking accuracy.

## D. Run a large unlabelled robustness sweep (NOT accuracy)

```powershell
& $python -m scripts.public_benchmark stress datasets/public/sgd_candidates.jsonl --backend rules --n 1000 --output datasets/reports/sgd_rules_stress.json
```

Groq example for the first 100 public dialogues only (data will be sent to your provider; check license, terms, privacy and quota):

```powershell
& $python -m scripts.public_benchmark stress datasets/public/sgd_candidates.jsonl --backend groq_events --n 100 --pace-seconds 8 --allow-cloud --output datasets/reports/sgd_groq_stress.json
```

The stress JSON intentionally contains `accuracy: null` because there are no human-validated commitment labels. The source conversations may be text written/paraphrased by humans, not raw customer phone recordings.

## E. Create a *scored* Lane benchmark

```powershell
& $python -m scripts.public_benchmark template datasets/public/sgd_candidates.jsonl --n 100 --output datasets/public/sgd_review.csv
```

Open `datasets/public/sgd_review.csv` in Excel and fill `confirmed` (`yes` or `no`), `reviewer`, `timezone` (e.g. `America/Chicago`) and `call_started_at` (ISO 8601 with offset) **only when evidence supports those assumptions**. For confirmed bookings, fill `final_start` with a real offset-aware ISO timestamp. For no-booking examples, leave `final_start` blank. Exclude dialogues where historical date/time context is unavailable instead of inventing a correct answer. Reviewers should distinguish customer authorization from an agent falsely claiming success. Do not relabel an ambiguous dialogue as `no` merely to make a complete row.

```powershell
& $python -m scripts.public_benchmark reviewed datasets/public/sgd_review.csv --output datasets/public/sgd_gold_reviewed.jsonl
& $python -m scripts.compare_extractors datasets/public/sgd_gold_reviewed.jsonl --backends rules,groq,groq_guarded,groq_events --pace-seconds 8 --output datasets/reports/sgd_reviewed.comparison.json
```

That final comparison has *human-reviewed extraction labels*, but still **does not verify actual HighLevel calendar writes**. Explicitly record how many corpus conversations were dropped as unresolvable.

For cross-dataset robustness, repeat review for Taskmaster and ABCD, reserving different corpora as held-out sets. For direct service+tool+database-state outcome evaluation, integrate a transactional simulator such as τ-bench/ToolSandbox as a separate benchmark adapter rather than assigning synthetic calendar outcomes to unrelated public transcripts.

Sources:
- SGD: https://github.com/google-research-datasets/dstc8-schema-guided-dialogue
- Taskmaster: https://github.com/google-research-datasets/Taskmaster
- ABCD: https://github.com/asappresearch/abcd
- τ-bench: https://github.com/sierra-research/tau-bench

## Other especially relevant first-party datasets/benchmarks (not in this importer yet)

- Cekura Bench: 82 published caller situations, of which 59 are appointment scenarios. Includes expected agent tools and behavior; good for Lane's manifest-driven Trials. https://github.com/cekura-ai/cekura-bench
- τ-Voice: 278 grounded voice tasks with spoken interactions, domain policies and tools. Requires its own runner, not the JSONL transcript-only scorer. https://proceedings.mlr.press/v306/ray26a.html
- VoiceAgentBench: about 5.39k audio/tool calling samples on Hugging Face, including multi-turn and safety cases. Community license, verify intended use. https://huggingface.co/datasets/krutrim-ai-labs/VoiceAgentBench
- τ-bench/τ²-bench: customer-service API environment where tasks are evaluated against database state. Useful for business authorization vs execution, not directly a HighLevel integration.
