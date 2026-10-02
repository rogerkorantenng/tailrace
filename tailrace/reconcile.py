"""Compare what PayPal says with what the ledger says, and write one row per subject."""
from decimal import Decimal

from . import db
from .payouts import HUMAN_STATES, LEDGER_FOR_ITEM
from .paypal import client
from .invoices import sync as sync_invoice

OK_WHEN_HUMAN = {"escalated": {"UNCLAIMED", "FAILED", "BLOCKED", "DENIED", "ONHOLD", "RETURNED"},
                 "stopped": {"FAILED", "BLOCKED", "DENIED", "RETURNED", "REFUNDED", "CANCELED"}}


def _row(run_id, kind, sid, ledger, paypal, verdict, note=""):
    db.x("insert into reconciliation(run_id,subject_type,subject_id,ledger,paypal,verdict,note) values(%s,%s,%s,%s,%s,%s,%s)",
         (run_id, kind, sid, ledger, paypal, verdict, note))
    return {"subject": sid, "verdict": verdict, "note": note}


def reconcile_payouts(run_id: str, obligation_ids: list[str] | None = None) -> list[dict]:
    obs = db.q("select * from obligations where attempts>0 and (%s::text[] is null or id = any(%s)) order by id", (obligation_ids, obligation_ids))
    batches: dict[str, dict] = {}
    out = []
    for ob in obs:
        pay = db.one("select * from payments where obligation_id=%s order by attempt desc limit 1", (ob["id"],))
        if not pay or not pay["payout_batch_id"]:
            out.append(_row(run_id, "payout", ob["id"], ob["status"], "no batch", "mismatch", "Ledger says money was sent but no PayPal batch is recorded."))
            continue
        b = batches.get(pay["payout_batch_id"]) or batches.setdefault(pay["payout_batch_id"], client().get_payout(pay["payout_batch_id"]))
        item = next((i for i in b["items"] if (i.get("payout_item") or {}).get("sender_item_id") == f"{ob['id']}-a{pay['attempt']}"), None)
        if item is None:
            out.append(_row(run_id, "payout", ob["id"], ob["status"], "not found", "mismatch", "PayPal has no item for this attempt."))
            continue
        amt = item["payout_item"]["amount"]
        ledger_s = f"{ob['currency']} {Decimal(ob['amount']):.2f} {ob['status']}"
        pp_s = f"{amt['currency']} {Decimal(amt['value']):.2f} {item['transaction_status']}"
        if amt["currency"] != ob["currency"] or Decimal(amt["value"]) != Decimal(ob["amount"]):
            out.append(_row(run_id, "payout", ob["id"], ledger_s, pp_s, "mismatch", "Amount or currency differs. Needs a person."))
            continue
        pp_state = item["transaction_status"]
        expect = LEDGER_FOR_ITEM.get(pp_state, ob["status"])
        if ob["status"] == expect or pp_state in OK_WHEN_HUMAN.get(ob["status"], set()):
            note = {"settled": "Landed.", "unclaimed": "Held by PayPal; returns to the sender after 30 days.",
                    "escalated": "Waiting on a person.", "stopped": "Ended on purpose."}.get(ob["status"], "")
            out.append(_row(run_id, "payout", ob["id"], ledger_s, pp_s, "match", note))
        else:
            with db.tx() as c:
                c.execute("update obligations set status=%s, updated_at=now() where id=%s", (expect, ob["id"]))
            out.append(_row(run_id, "payout", ob["id"], ledger_s, pp_s, "healed", f"Ledger said {ob['status']}; PayPal said {pp_state}. Ledger updated."))
    return out


def reconcile_invoices(run_id: str) -> list[dict]:
    out = []
    for inv in db.q("select * from invoices order by due_date"):
        before = Decimal(inv["ledger_paid"])
        now = sync_invoice(inv["id"])
        pp_paid = Decimal(now["paypal_paid"])
        ledger_s = f"{inv['currency']} {before:.2f} paid"
        pp_s = f"{inv['currency']} {pp_paid:.2f} paid, {now['paypal_status']}"
        if pp_paid == before:
            out.append(_row(run_id, "invoice", inv["id"], ledger_s, pp_s, "match", ""))
        else:
            db.x("update invoices set ledger_paid=%s where id=%s", (pp_paid, inv["id"]))
            out.append(_row(run_id, "invoice", inv["id"], ledger_s, pp_s, "healed",
                            f"PayPal shows {inv['currency']} {pp_paid:.2f} received that the ledger did not know about. Ledger updated."))
    return out
