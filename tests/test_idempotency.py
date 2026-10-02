"""The worst outcome for a retrying pipeline is paying twice. Every test here replays a step and counts what PayPal holds."""
import asyncio
import threading

import pytest

from tailrace import chaos, db, flow, payouts, runs, tasks
from tailrace.payouts import Refused


def run(coro):
    return asyncio.run(coro)


def test_replayed_submit_step_pays_once(fake, seeded):
    rid = runs.create("settle")
    ctx = flow.InlineCtx()
    a = run(tasks.submit_batch.func(ctx, rid, "USD", seeded, False))
    b = run(tasks.submit_batch.func(ctx, rid, "USD", seeded, False))  # Render re-runs the step with the same input
    assert a["payout_batch_id"] == b["payout_batch_id"]
    assert fake.create_calls == 1, "PayPal must have been asked once; the replay finds the batch id in the database"
    assert max(fake.paid_count().values()) == 1


def test_crash_after_paypal_accepts_then_retry_adopts_the_batch(fake, seeded):
    rid = runs.create("settle")
    fire = chaos.crash_once(rid, "submit_batch", True)
    rows = payouts.claim_first_batch(rid, "USD", seeded)
    with pytest.raises(chaos.InjectedCrash):
        payouts.send_claimed(rows, payouts.batch_id_for(rid, "USD"), fire)
    assert fake.create_calls == 1 and db.one("select count(*) n from payments where payout_batch_id is not null")["n"] == 0
    # the retry: same claim rows, batch id unknown to the database, PayPal refuses the repeat and we adopt
    rows = payouts.claim_first_batch(rid, "USD", seeded)
    batch_id, created = payouts.send_claimed(rows, payouts.batch_id_for(rid, "USD"), fire)
    assert created is False
    assert fake.create_calls == 2 and len(fake.batches) == 1
    assert all(r["payout_batch_id"] == batch_id for r in db.q("select * from payments"))
    assert set(fake.paid_count().values()) == {1}


def test_crash_between_claim_and_send_then_retry_sends_once(fake, seeded):
    rid = runs.create("settle")
    payouts.claim_first_batch(rid, "USD", seeded)       # step died here, PayPal never called
    assert fake.create_calls == 0
    for _ in range(2):                                   # two retries after that
        rows = payouts.claim_first_batch(rid, "USD", seeded)
        payouts.send_claimed(rows, payouts.batch_id_for(rid, "USD"))
    assert len(fake.batches) == 1 and set(fake.paid_count().values()) == {1}


def test_a_second_run_cannot_pay_obligations_already_sent(fake, seeded):
    r1, r2 = runs.create("settle"), runs.create("settle")
    payouts.send_claimed(payouts.claim_first_batch(r1, "USD", seeded), payouts.batch_id_for(r1, "USD"))
    again = payouts.claim_first_batch(r2, "USD", seeded)
    assert again == [], "obligations leave 'queued' when claimed, so a different run finds nothing to send"
    assert len(fake.batches) == 1


def test_two_workers_claiming_at_once_produce_one_batch(fake, seeded):
    rid = runs.create("settle")
    results, errors = [], []

    def worker():
        try:
            rows = payouts.claim_first_batch(rid, "USD", seeded)
            results.append(payouts.send_claimed(rows, payouts.batch_id_for(rid, "USD"))[0])
        except Exception as e:  # pragma: no cover
            errors.append(e)

    ts = [threading.Thread(target=worker) for _ in range(4)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert not errors
    assert len(fake.batches) == 1, "four workers raced; PayPal saw one distinct sender_batch_id"
    assert set(fake.paid_count().values()) == {1}
    assert db.one("select count(*) n from payments")["n"] == 3  # the three USD obligations, once each


def test_reissue_replay_pays_once_and_cancels_once(fake, seeded):
    rid = runs.create("settle")
    payouts.send_claimed(payouts.claim_first_batch(rid, "GBP", ["po-1003"]), payouts.batch_id_for(rid, "GBP"))
    payouts.refresh_batch(fake.batches[payouts.batch_id_for(rid, "GBP")]["id"])     # item is UNCLAIMED
    first = payouts.reissue("po-1003", 2, "sb-patient@personal.example.com", rid)
    second = payouts.reissue("po-1003", 2, "sb-patient@personal.example.com", rid)  # the step is retried
    assert first["payout_batch_id"] == second["payout_batch_id"]
    assert fake.cancel_calls == 1
    assert fake.paid_count()["po-1003"] == 1, "the cancelled original no longer counts as money out; only the reissue does"


def test_reissue_crash_after_paypal_accepts_then_retry(fake, seeded):
    rid = runs.create("settle")
    payouts.send_claimed(payouts.claim_first_batch(rid, "GBP", ["po-1003"]), payouts.batch_id_for(rid, "GBP"))
    payouts.refresh_batch(fake.batches[payouts.batch_id_for(rid, "GBP")]["id"])
    fire = chaos.crash_once(rid, "reissue", True)
    with pytest.raises(chaos.InjectedCrash):
        payouts.reissue("po-1003", 2, "sb-patient@personal.example.com", rid, fire)
    payouts.reissue("po-1003", 2, "sb-patient@personal.example.com", rid, fire)
    assert fake.paid_count()["po-1003"] == 1
    assert len([b for b in fake.batches.values() if b["sender"].startswith("reissue-")]) == 1


def test_reissue_refuses_while_the_earlier_item_could_still_be_live(fake, seeded):
    rid = runs.create("settle")
    payouts.send_claimed(payouts.claim_first_batch(rid, "USD", ["po-1001"]), payouts.batch_id_for(rid, "USD"))
    payouts.refresh_batch(fake.batches[payouts.batch_id_for(rid, "USD")]["id"])    # SUCCESS: the money landed
    with pytest.raises(Refused):
        payouts.reissue("po-1001", 2, "sb-patient@personal.example.com", rid)
    assert len(fake.batches) == 1


def test_whole_workflow_with_a_crash_pays_each_obligation_at_most_once(fake, seeded):
    """settle_run through the inline Render stand-in: crash once after PayPal accepts, let the task retry, check the totals."""
    rid = runs.create("settle", {"crash": True})
    out = run(tasks.settle_run.func(flow.InlineCtx(), rid, seeded, True, True))
    counts = fake.paid_count()
    assert counts, "something must have been paid"
    assert max(counts.values()) == 1, f"double payment: {counts}"
    retried = db.one("select count(*) n from steps where run_id=%s and task='submit_batch' and event='retrying'", (rid,))["n"]
    assert retried == 1, "the injected crash should show up as exactly one retried step"
    adopted = db.one("select count(*) n from steps where run_id=%s and task='submit_batch' and detail like '%%already had it%%'", (rid,))["n"]
    assert adopted == 1
    statuses = {r["id"]: r["status"] for r in db.q("select id,status from obligations")}
    assert statuses["po-1001"] == "settled" and statuses["po-1003"] == "settled"
    assert statuses["po-1005"] == "stopped" and statuses["po-1004"] == "escalated"
    assert out["failed_steps"] == 0
