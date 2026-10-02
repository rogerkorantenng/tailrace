from datetime import date, timedelta

import pytest

from tailrace import db, invoices, reconcile, payouts, runs
from tailrace.invoices import stage_for


@pytest.mark.parametrize("days,stage", [(-5, 0), (0, 0), (1, 1), (6, 1), (7, 2), (13, 2), (14, 3), (40, 3)])
def test_ladder_boundaries(days, stage):
    assert stage_for(days) == stage


class FakeInvoices:
    def __init__(self):
        self.status, self.paid, self.reminders = "SENT", "0.00", []

    def get_invoice(self, _):
        return {"status": self.status, "payments": {"paid_amount": {"value": self.paid}}}

    def remind_invoice(self, i, subject, note):
        self.reminders.append((i, subject, note))


@pytest.fixture
def inv(monkeypatch):
    f = FakeInvoices()
    from tailrace import paypal
    paypal._client = f
    db.x("insert into invoices(id,payee_name,email,amount,currency,due_date) values('INV-1','Acme','a@example.com',400,'USD',%s)", (date.today() - timedelta(days=9),))
    yield f
    paypal._client = None


def test_each_stage_is_sent_once_however_often_the_step_runs(inv):
    for _ in range(3):
        invoices.chase("r1", "INV-1")
    assert len(inv.reminders) == 1 and "9 days" in inv.reminders[0][1]


def test_a_paid_invoice_is_never_chased(inv):
    inv.status = "PAID"; inv.paid = "400.00"
    assert invoices.chase("r1", "INV-1")["action"] == "none" and inv.reminders == []


def test_a_reminder_that_died_midway_is_not_sent_twice(inv):
    db.x("insert into reminders(invoice_id,stage,state,run_id) values('INV-1',2,'sending','r0')")   # claimed, never confirmed
    out = invoices.chase("r1", "INV-1")
    assert out["action"] == "skipped" and inv.reminders == []
    assert db.one("select state from reminders where invoice_id='INV-1' and stage=2")["state"] == "unconfirmed"


def test_a_part_payment_lowers_the_balance_named_in_the_reminder(inv):
    inv.paid = "150.00"
    invoices.chase("r1", "INV-1")
    assert "USD 250.00" in inv.reminders[0][2]


def test_final_stage_raises_an_escalation(inv):
    db.x("update invoices set due_date=%s", (date.today() - timedelta(days=20),))
    invoices.chase("r1", "INV-1")
    e = db.one("select * from escalations where subject_id='INV-1'")
    assert e and "20 days" in e["reason"]


def test_reconciliation_heals_a_ledger_that_missed_a_payment(inv):
    inv.paid = "150.00"
    rows = reconcile.reconcile_invoices("r1")
    assert rows[0]["verdict"] == "healed"
    assert str(db.one("select ledger_paid from invoices")["ledger_paid"]) == "150.00"
    assert reconcile.reconcile_invoices("r2")[0]["verdict"] == "match"


def test_payout_reconciliation_flags_an_amount_that_differs(fake, seeded):
    rid = runs.create("settle")
    payouts.send_claimed(payouts.claim_first_batch(rid, "USD", ["po-1001"]), payouts.batch_id_for(rid, "USD"))
    payouts.refresh_batch(fake.batches[payouts.batch_id_for(rid, "USD")]["id"])
    db.x("update obligations set amount=999.00 where id='po-1001'")
    row = reconcile.reconcile_payouts("r1", ["po-1001"])[0]
    assert row["verdict"] == "mismatch" and "Amount" in row["note"]


def test_payout_reconciliation_heals_a_ledger_behind_paypal(fake, seeded):
    rid = runs.create("settle")
    payouts.send_claimed(payouts.claim_first_batch(rid, "USD", ["po-1001"]), payouts.batch_id_for(rid, "USD"))
    payouts.refresh_batch(fake.batches[payouts.batch_id_for(rid, "USD")]["id"])
    db.x("update obligations set status='sending' where id='po-1001'")   # a webhook was missed
    row = reconcile.reconcile_payouts("r1", ["po-1001"])[0]
    assert row["verdict"] == "healed" and db.one("select status from obligations where id='po-1001'")["status"] == "settled"
