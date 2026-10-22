"""Idempotent case materialization for findings created before worker changes.

Preview is read-only. Explicit --apply required to insert and audit tickets.
"""
import argparse
from sqlalchemy import select
from app.db import SessionLocal, Finding, CaseTicket, init_db
from app.accountability import ensure_case


def backfill(*, apply=False):
    init_db()
    with SessionLocal() as db:
        findings=db.scalars(select(Finding).where(Finding.state.in_(
            ['contradicted','unknown','flagged','pending']))).all()
        existing={x.finding_id for x in db.scalars(select(CaseTicket)).all()}
        ids=[f.id for f in findings if f.id not in existing]
        if apply:
            for ident in ids:
                ensure_case(db,ident)
            db.commit()
        return {'eligible_missing_cases':len(ids), 'created':len(ids) if apply else 0,
                'mode':'apply' if apply else 'preview'}


if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--apply',action='store_true')
    args=p.parse_args()
    print(backfill(apply=args.apply))
