import hashlib, json
from sqlalchemy import select
from .db import Manifest
from .time_utils import db_utc


def hash_policy(body: dict) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')).hexdigest()


def manifest_for_start(db, client_id, started_at):
    rows = db.scalars(select(Manifest).where(Manifest.client_id == client_id).order_by(Manifest.effective_from.desc())).all()
    return next((m for m in rows if db_utc(m.effective_from) <= db_utc(started_at)), None)
