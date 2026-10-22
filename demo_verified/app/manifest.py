import hashlib, json
from sqlalchemy import select
from .db import Manifest, ApprovalEvent, Client
from .time_utils import db_utc


def hash_policy(body: dict) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')).hexdigest()


def manifest_for_start(db, client_id, started_at):
    rows = db.scalars(select(Manifest).where(Manifest.client_id == client_id).order_by(Manifest.effective_from.desc())).all()
    client = db.get(Client, client_id)
    for m in rows:
        if db_utc(m.effective_from) > db_utc(started_at):
            continue
        # Existing historical/demo data is still readable for regression and
        # migration, but unapproved legacy policies cannot authorize live calls.
        event = db.scalar(select(ApprovalEvent).where(ApprovalEvent.manifest_id == m.id))
        if event and event.approved_sha256 == m.sha256 and hash_policy(m.body) == m.sha256:
            return m
        if client and client.is_demo:
            return m
    return None
