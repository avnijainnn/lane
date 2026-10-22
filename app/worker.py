import time
from .db import init_db, SessionLocal
from .verify import process_due_jobs
from .pipeline import process_unprocessed_calls

if __name__ == '__main__':
    init_db()
    print('Lane worker started', flush=True)
    while True:
        try:
            with SessionLocal() as db:
                process_unprocessed_calls(db)
                process_due_jobs(db)
        except Exception as exc:
            print(f'Worker iteration failed: {type(exc).__name__}', flush=True)
        time.sleep(5)
