"""Invoices and the dunning ladder. The ledger copy is ours; PayPal's copy is the truth."""
import uuid
from datetime import date, timedelta
from decimal import Decimal

from . import db
from .config import settings
from .paypal import PayPalError, client, invoice_paid

# (name, email, amount, currency, days since due (negative = not yet due), description, already paid at PayPal)
SAMPLE_INVOICES = [
    ("Harbour Street Print", "billing@harbourprint.example.com", "1840.00", "USD", -5, "Spring packaging run", "0.00"),
    ("Okafor & Wynn Studio", "accounts@okaforwynn.example.com", "620.00", "USD", 3, "Brand audit, phase 1", "0.00"),
    ("Brightwater Cafe Group", "finance@brightwatercafe.example.com", "2250.00", "USD", 9, "Quarterly menu photography", "0.00"),
    ("Linden Park Dental", "office@lindenpark.example.com", "400.00", "USD", 10, "Website maintenance, Q3", "150.00"),
    ("Merrow Logistics", "ap@merrowlog.example.com", "3975.00", "USD", 24, "Route planning licence", "0.00"),
]

STAGES = {
    1: ("Friendly reminder", "A quick reminder that this invoice passed its due date. If it is already on its way, thank you and please ignore this note."),
    2: ("Invoice now {days} days overdue", "This invoice is {days} days past due. Please arrange payment, or reply if something is blocking it."),
    3: ("Final notice: invoice {days} days overdue", "This invoice is {days} days past due and has been passed to our accounts team. Please pay today or contact us to agree a date."),
}


def stage_for(days_overdue: int) -> int:
    if days_overdue < 1:
        return 0
    if days_overdue < 7:
        return 1
    if days_overdue < 14:
        return 2
    return 3


def seed_invoices() -> list[str]:
    """Create real sandbox invoices, send them, and record the ones that were part-paid."""
    pp, ids, salt = client(), [], uuid.uuid4().hex[:8]
    today = date.today()
    for i, (name, email, amount, cur, overdue, desc, paid) in enumerate(SAMPLE_INVOICES):
        due = today - timedelta(days=overdue)
        body = {"detail": {"invoice_date": (due - timedelta(days=30)).isoformat(), "currency_code": cur, "note": desc,
                           "payment_term": {"term_type": "DUE_ON_DATE_SPECIFIED", "due_date": due.isoformat()}},
                "invoicer": {"business_name": "Tailrace Demo Studio", "email_address": settings.sender_email},
                "primary_recipients": [{"billing_info": {"email_address": email, "business_name": name}}],
                "items": [{"name": desc, "quantity": "1", "unit_amount": {"currency_code": cur, "value": amount}}]}
        inv_id = pp.create_invoice(body, request_id=f"tailrace-seed-{salt}-{i}")
        pp.send_invoice(inv_id)
        if Decimal(paid) > 0:
            pp.record_payment(inv_id, paid, cur, "Bank transfer received outside this app")
        db.x("insert into invoices(id,payee_name,email,amount,currency,due_date,description) values(%s,%s,%s,%s,%s,%s,%s) on conflict do nothing",
             (inv_id, name, email, amount, cur, due, desc))
        ids.append(inv_id)
    return ids


def sync(invoice_id: str) -> dict:
    """Read the invoice from PayPal and store what it says. Returns the row."""
    inv = client().get_invoice(invoice_id)
    paid, status = invoice_paid(inv)
    db.x("update invoices set paypal_status=%s, paypal_paid=%s, synced_at=now() where id=%s", (status, paid, invoice_id))
    return db.one("select * from invoices where id=%s", (invoice_id,))


def balance(row: dict) -> Decimal:
    return Decimal(row["amount"]) - Decimal(row["paypal_paid"])


def chase(run_id: str, invoice_id: str) -> dict:
    """Send the reminder for the invoice's current stage, at most once per stage."""
    row = sync(invoice_id)
    days = (date.today() - row["due_date"]).days
    stage = stage_for(days)
    if row["paypal_status"] in ("PAID", "MARKED_AS_PAID", "CANCELLED", "REFUNDED") or balance(row) <= 0:
        return {"invoice_id": invoice_id, "action": "none", "why": f"PayPal shows it {row['paypal_status'].lower()}", "stage": stage}
    if stage == 0:
        return {"invoice_id": invoice_id, "action": "none", "why": "not overdue yet", "stage": 0}
    claimed = db.x("insert into reminders(invoice_id,stage,state,run_id) values(%s,%s,'sending',%s) on conflict do nothing", (invoice_id, stage, run_id))
    if not claimed:
        prev = db.one("select state from reminders where invoice_id=%s and stage=%s", (invoice_id, stage))
        if prev["state"] == "sending":
            # A previous attempt died between claiming and confirming. We cannot tell whether PayPal sent it.
            # A missed reminder is cheaper than a duplicate, so mark it and wait for the next stage.
            db.x("update reminders set state='unconfirmed' where invoice_id=%s and stage=%s", (invoice_id, stage))
            return {"invoice_id": invoice_id, "action": "skipped", "why": "an earlier attempt may already have sent stage %d" % stage, "stage": stage}
        return {"invoice_id": invoice_id, "action": "skipped", "why": f"stage {stage} reminder already {prev['state']}", "stage": stage}
    subject, note = STAGES[stage]
    cur = row["currency"]
    note = note.format(days=days) + f" Balance due: {cur} {balance(row):.2f}."
    client().remind_invoice(invoice_id, subject.format(days=days), note)
    db.x("update reminders set state='sent', ts=now() where invoice_id=%s and stage=%s", (invoice_id, stage))
    if stage == 3:
        db.x("insert into escalations(run_id,subject_type,subject_id,reason) values(%s,'invoice',%s,%s)",
             (run_id, invoice_id, f"{row['payee_name']} is {days} days overdue with {cur} {balance(row):.2f} outstanding after a final notice."))
    return {"invoice_id": invoice_id, "action": "reminded", "stage": stage, "days": days, "balance": str(balance(row))}
