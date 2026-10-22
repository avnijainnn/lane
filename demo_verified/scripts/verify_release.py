"""Verify that the extracted Lane recovery distribution is complete and unmixed."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "RELEASE_SHA256.json"


def main():
    known = json.loads(MANIFEST.read_text(encoding="utf-8"))
    problems = []
    for rel, expected in known.items():
        path = ROOT / rel
        if not path.exists():
            problems.append(f"MISSING {rel}")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            problems.append(f"MISMATCH {rel}")
    if problems:
        for item in problems:
            print(item)
        raise SystemExit(f"FAILED: {len(problems)} release file(s) missing or modified")
    print(f"CLEAN RELEASE VERIFIED: {len(known)} files match")
    print(f"Project: {ROOT}")
    from app.extract import guard_booking_prediction
    from app.time_utils import unique_local_wall, AmbiguousTime
    from app.timezone_guard import timezone_guard
    import app.accountability
    import scripts.compare_extractors
    print("CRITICAL IMPORTS: OK")


if __name__ == "__main__":
    main()
