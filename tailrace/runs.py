import json
import secrets

from . import db


def new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(3)}"


def create(kind: str, params: dict | None = None) -> str:
    rid = new_id(kind[:3])
    db.x("insert into runs(id,kind,params) values(%s,%s,%s)", (rid, kind, db.jb(params or {})))
    return rid


def finish(run_id: str, summary: dict, status: str = "done"):
    db.x("update runs set status=%s, summary=%s, finished_at=now() where id=%s", (status, db.jb(summary), run_id))


def fail(run_id: str, why: str):
    db.x("update runs set status='failed', summary=%s, finished_at=now() where id=%s", (db.jb({"error": why}), run_id))
