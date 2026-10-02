"""Postgres access. One small pool per process; every helper is one short transaction."""
import atexit
import json
import threading
from contextlib import contextmanager
from decimal import Decimal
from datetime import date, datetime

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from .config import settings

SCHEMA = """
create table if not exists payees(
  id text primary key, name text not null, country text not null,
  email text, paypal_id text, verified_email text, note text default '');
create table if not exists obligations(
  id text primary key, payee_id text not null references payees(id),
  description text not null, amount numeric(12,2) not null, currency text not null,
  receiver_type text not null, receiver text not null,
  status text not null default 'queued', attempts int not null default 0,
  last_error text, outcome text default '',
  created_at timestamptz default now(), updated_at timestamptz default now());
create table if not exists payments(
  id serial primary key, obligation_id text not null references obligations(id),
  attempt int not null, sender_batch_id text not null, payout_batch_id text, payout_item_id text,
  receiver_type text not null, receiver text not null, amount numeric(12,2) not null, currency text not null,
  batch_status text, item_status text, error_name text, error_message text, run_id text,
  created_at timestamptz default now(), updated_at timestamptz default now(),
  unique(obligation_id, attempt));
create table if not exists runs(
  id text primary key, kind text not null, status text not null default 'running',
  params jsonb not null default '{}', render_run_id text, summary jsonb,
  started_at timestamptz default now(), finished_at timestamptz);
create table if not exists steps(
  id bigserial primary key, run_id text not null, task text not null, subject text not null default '',
  event text not null, attempt int not null default 1, detail text default '',
  render_run_id text, parent_render_run_id text, ts timestamptz default clock_timestamp());
create index if not exists steps_run on steps(run_id, id);
create table if not exists agent_turns(
  id bigserial primary key, run_id text not null, obligation_id text not null,
  turn int not null, kind text not null, name text default '', payload jsonb not null default '{}',
  ts timestamptz default clock_timestamp());
create index if not exists agent_turns_ob on agent_turns(run_id, obligation_id, id);
create table if not exists decisions(
  id bigserial primary key, run_id text not null, obligation_id text not null,
  action text not null, address text, reasoning text, source text, gate jsonb default '[]', target_attempt int default 0,
  result text default '', ts timestamptz default clock_timestamp());
create unique index if not exists decisions_one on decisions(run_id, obligation_id);
create table if not exists invoices(
  id text primary key, payee_name text not null, email text not null,
  amount numeric(12,2) not null, currency text not null, due_date date not null, description text,
  ledger_paid numeric(12,2) not null default 0, paypal_status text, paypal_paid numeric(12,2) not null default 0,
  created_at timestamptz default now(), synced_at timestamptz);
create table if not exists reminders(
  invoice_id text not null references invoices(id), stage int not null, state text not null,
  run_id text, ts timestamptz default now(), primary key(invoice_id, stage));
create table if not exists escalations(
  id serial primary key, run_id text, subject_type text not null, subject_id text not null,
  reason text not null, status text not null default 'open', created_at timestamptz default now(), resolved_at timestamptz);
create table if not exists reconciliation(
  id bigserial primary key, run_id text not null, subject_type text not null, subject_id text not null,
  ledger text, paypal text, verdict text not null, note text default '', ts timestamptz default clock_timestamp());
create table if not exists webhook_events(
  id text primary key, event_type text not null, resource jsonb, verified boolean not null,
  received_at timestamptz default now(), processed_at timestamptz, effect text default '');
create table if not exists chaos_fired(key text primary key, ts timestamptz default now());
"""

_pool: ConnectionPool | None = None
_lock = threading.Lock()


def _jsonable(o):
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    raise TypeError(type(o))


def jb(value):
    """Wrap a Python value for a jsonb column."""
    return Jsonb(json.loads(json.dumps(value, default=_jsonable)))


def pool() -> ConnectionPool:
    global _pool
    with _lock:
        if _pool is None:
            _pool = ConnectionPool(
                settings.database_url, min_size=1, max_size=8, open=True,
                kwargs={"row_factory": dict_row, "autocommit": False}, timeout=20,
            )
        return _pool


def reset_pool_for_tests(url: str | None = None):
    global _pool
    with _lock:
        if _pool is not None:
            _pool.close()
            _pool = None


@contextmanager
def tx():
    with pool().connection() as conn:
        yield conn


def q(sql: str, params=()):
    with tx() as c:
        cur = c.execute(sql, params)
        return cur.fetchall() if cur.description else []


def one(sql: str, params=()):
    rows = q(sql, params)
    return rows[0] if rows else None


def x(sql: str, params=()) -> int:
    with tx() as c:
        return c.execute(sql, params).rowcount


def init_schema():
    with tx() as c:
        c.execute("select pg_advisory_xact_lock(727274)")
        c.execute(SCHEMA)


def wipe():
    """Reset every demo table. PayPal keeps whatever it already holds."""
    with tx() as c:
        c.execute("truncate payments, decisions, agent_turns, steps, reminders, escalations, reconciliation, "
                  "webhook_events, chaos_fired, runs, invoices, obligations, payees restart identity cascade")


@atexit.register
def _close_pool():
    if _pool is not None:
        _pool.close()
