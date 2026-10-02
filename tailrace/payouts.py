"""Everything that moves or inspects payout money. This is where double payment is prevented.

Order of defence, outermost first:
  1. obligations can only be claimed from 'queued' (first pay) or after the previous item is dead (reissue)
  2. a payments row is inserted *before* PayPal is called; (obligation_id, attempt) is unique
  3. the PayPal sender_batch_id is derived from the run, so a repeat call is refused by PayPal
  4. that refusal is read as success and the existing batch is adopted
"""
import asyncio
import time

from . import db
from .config import settings
from .paypal import LIVE_ITEM, TERMINAL_BATCH, PayPalError, client

LEDGER_FOR_ITEM = {
    "SUCCESS": "settled", "UNCLAIMED": "unclaimed", "FAILED": "failed", "BLOCKED": "failed", "DENIED": "failed",
    "RETURNED": "returned", "REFUNDED": "returned", "CANCELED": "returned", "REVERSED": "returned",
    "ONHOLD": "held", "PENDING": "sending", "PROCESSING": "sending",
}
DEAD_ITEM = {"FAILED", "BLOCKED", "DENIED", "RETURNED", "REFUNDED", "CANCELED", "REVERSED"}
HUMAN_STATES = {"escalated", "stopped"}


class Refused(Exception):
    """A guard stopped the payment. Not retryable: retrying would hit the same guard."""


def batch_id_for(run_id: str, currency: str) -> str:
    """One payout batch holds one currency, so a run sends one batch per currency."""
    return f"{run_id}-{currency.lower()}"


def reissue_id_for(obligation_id: str, attempt: int) -> str:
    return f"reissue-{obligation_id}-a{attempt}"


def _payload(row: dict) -> dict:
    return {
        "recipient_type": row["receiver_type"],
        "amount": {"value": f"{row['amount']:.2f}", "currency": row["currency"]},
        "receiver": row["receiver"],
        "note": f"Tailrace payout {row['obligation_id']}",
        "sender_item_id": f"{row['obligation_id']}-a{row['attempt']}",
    }


def claim_first_batch(run_id: str, currency: str, obligation_ids: list[str]) -> list[dict]:
    """Insert the payments rows for a first batch. Safe to repeat: a second call returns the same rows."""
    sb = batch_id_for(run_id, currency)
    with db.tx() as c:
        c.execute("select pg_advisory_xact_lock(hashtext(%s))", (sb,))
        for oid in sorted(obligation_ids):
            ob = c.execute("select * from obligations where id=%s for update", (oid,)).fetchone()
            if not ob or ob["status"] != "queued" or ob["attempts"] != 0 or ob["currency"] != currency:
                continue
            attempt = 1
            c.execute(
                "insert into payments(obligation_id,attempt,sender_batch_id,receiver_type,receiver,amount,currency,run_id)"
                " values(%s,%s,%s,%s,%s,%s,%s,%s) on conflict(obligation_id,attempt) do nothing",
                (oid, attempt, sb, ob["receiver_type"], ob["receiver"], ob["amount"], ob["currency"], run_id))
            c.execute("update obligations set status='sending', attempts=%s, updated_at=now() where id=%s", (attempt, oid))
        return c.execute("select * from payments where sender_batch_id=%s order by obligation_id", (sb,)).fetchall()


def send_claimed(rows: list[dict], sender_batch_id: str, chaos=None) -> tuple[str, bool]:
    """Send a claimed batch to PayPal exactly once. Returns (payout_batch_id, created_now)."""
    already = {r["payout_batch_id"] for r in rows if r["payout_batch_id"]}
    if already:
        return already.pop(), False
    batch_id, created = client().create_payout(sender_batch_id, [_payload(r) for r in rows])
    if chaos:
        chaos()  # fault injection point: PayPal has the money instruction, our database does not know yet
    with db.tx() as c:
        for r in rows:
            c.execute("update payments set payout_batch_id=%s, updated_at=now() where id=%s", (batch_id, r["id"]))
    return batch_id, created


def refresh_batch(batch_id: str) -> dict:
    """Read the batch from PayPal and write what it says into payments and the ledger."""
    b = client().get_payout(batch_id)
    status = b["batch_header"]["batch_status"]
    out = []
    with db.tx() as c:
        c.execute("update payments set batch_status=%s, updated_at=now() where payout_batch_id=%s", (status, batch_id))
        for it in b.get("items", []):
            sid = (it.get("payout_item") or {}).get("sender_item_id", "")
            pay = c.execute("select * from payments where payout_batch_id=%s and obligation_id||'-a'||attempt::text=%s",
                            (batch_id, sid)).fetchone()
            if not pay:
                continue
            ts = it.get("transaction_status", "")
            err = it.get("errors") or {}
            c.execute("update payments set item_status=%s, payout_item_id=%s, error_name=%s, error_message=%s, updated_at=now() where id=%s",
                      (ts, it.get("payout_item_id"), err.get("name"), err.get("message"), pay["id"]))
            apply_to_ledger(c, pay["obligation_id"], pay["attempt"], ts, err.get("name"))
            out.append({"obligation_id": pay["obligation_id"], "attempt": pay["attempt"], "item_status": ts,
                        "error_name": err.get("name"), "payout_item_id": it.get("payout_item_id")})
    live = any(i["item_status"] in LIVE_ITEM for i in out) or len(out) == 0
    return {"batch_id": batch_id, "batch_status": status, "items": out,
            "terminal": status in TERMINAL_BATCH and not live}


def apply_to_ledger(c, obligation_id: str, attempt: int, item_status: str, error_name: str | None):
    ob = c.execute("select * from obligations where id=%s for update", (obligation_id,)).fetchone()
    if not ob or attempt != ob["attempts"]:
        return
    new = LEDGER_FOR_ITEM.get(item_status, ob["status"])
    if ob["status"] in HUMAN_STATES and new != "settled":
        return
    c.execute("update obligations set status=%s, last_error=%s, updated_at=now() where id=%s",
              (new, error_name if new in ("failed", "unclaimed", "held") else None, obligation_id))
    if new == "settled":
        c.execute("update escalations set status='resolved', resolved_at=now() where subject_id=%s and status='open'", (obligation_id,))


async def wait_terminal(batch_id: str, give_up_s: float = 150, every_s: float = 4) -> dict:
    """Poll until PayPal says the batch and every item have settled into a final state."""
    deadline = time.monotonic() + give_up_s
    while True:
        snap = await asyncio.to_thread(refresh_batch, batch_id)
        if snap["terminal"]:
            return snap
        if time.monotonic() > deadline:
            raise RuntimeError(f"batch {batch_id} still {snap['batch_status']} after {int(give_up_s)}s")
        await asyncio.sleep(every_s)


def latest_payment(obligation_id: str) -> dict | None:
    return db.one("select * from payments where obligation_id=%s order by attempt desc limit 1", (obligation_id,))


def reissue(obligation_id: str, target_attempt: int, address: str, run_id: str, chaos=None) -> dict:
    """Cancel the old item if it is still holding money, then pay once to `address`."""
    sb = reissue_id_for(obligation_id, target_attempt)
    existing = db.one("select * from payments where obligation_id=%s and attempt=%s", (obligation_id, target_attempt))
    if not existing:
        prior = db.one("select * from payments where obligation_id=%s and attempt=%s", (obligation_id, target_attempt - 1))
        if prior is None:
            raise Refused(f"{obligation_id} has no earlier payment to replace")
        status = prior["item_status"]
        if status == "UNCLAIMED":
            confirmed = client().cancel_item(prior["payout_item_id"])
            db.x("update payments set item_status=%s, updated_at=now() where id=%s", (confirmed, prior["id"]))
        elif status not in DEAD_ITEM:
            raise Refused(f"{obligation_id} attempt {target_attempt - 1} is {status}; money may still be moving")
        with db.tx() as c:
            ob = c.execute("select * from obligations where id=%s for update", (obligation_id,)).fetchone()
            c.execute("insert into payments(obligation_id,attempt,sender_batch_id,receiver_type,receiver,amount,currency,run_id)"
                      " values(%s,%s,%s,'EMAIL',%s,%s,%s,%s) on conflict(obligation_id,attempt) do nothing",
                      (obligation_id, target_attempt, sb, address, ob["amount"], ob["currency"], run_id))
            c.execute("update obligations set status='sending', attempts=greatest(attempts,%s), receiver_type='EMAIL', receiver=%s, updated_at=now() where id=%s",
                      (target_attempt, address, obligation_id))
    rows = db.q("select * from payments where obligation_id=%s and attempt=%s", (obligation_id, target_attempt))
    batch_id, created = send_claimed(rows, rows[0]["sender_batch_id"], chaos)
    return {"payout_batch_id": batch_id, "created_now": created, "sender_batch_id": rows[0]["sender_batch_id"]}


def cancel_if_unclaimed(obligation_id: str) -> str | None:
    """Return held money to the sender. Used when a payout is stopped."""
    pay = latest_payment(obligation_id)
    if pay and pay["item_status"] == "UNCLAIMED":
        confirmed = client().cancel_item(pay["payout_item_id"])
        db.x("update payments set item_status=%s, updated_at=now() where id=%s", (confirmed, pay["id"]))
        return confirmed
    return None


def is_self_pay(receiver: str) -> bool:
    return receiver.lower() == settings.sender_email.lower()
