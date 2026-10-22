import time
from .db import init_db, SessionLocal, WorkerHeartbeat, now
from .verify import process_due_jobs
from .pipeline import process_unprocessed_calls

if __name__ == '__main__':
    init_db()
    print('Lane worker started', flush=True)
    while True:
        try:
            with SessionLocal() as db:
                count = process_unprocessed_calls(db)
                count += process_due_jobs(db)
                heart = db.get(WorkerHeartbeat, 'pipeline')
                if not heart:
                    heart = WorkerHeartbeat(name='pipeline')
                    db.add(heart)
                heart.last_seen_at = now()
                heart.last_processed = count
                heart.last_error = None
                db.commit()
        except Exception as exc:
            print(f'Worker iteration failed: {type(exc).__name__}', flush=True)
        time.sleep(5)
