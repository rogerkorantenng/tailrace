"""Opt-in: talks to the real PayPal sandbox and spends sandbox money. Run with:  pytest -m live -s"""
import asyncio
import time

import pytest

from tailrace import db, flow, payouts, runs, tasks
from tailrace.paypal import PayPal, PayPalError, existing_batch_id

pytestmark = pytest.mark.live


def test_a_201_is_not_money_moved_and_a_replayed_step_pays_once(seeded):
    pp = PayPal()
    rid = runs.create("settle")
    ctx = flow.InlineCtx()
    ids = ["po-1001", "po-1003"]        # one lands, one is UNCLAIMED
    a = asyncio.run(tasks.submit_batch.func(ctx, rid, "USD", ["po-1001"], False))
    b = asyncio.run(tasks.submit_batch.func(ctx, rid, "USD", ["po-1001"], False))
    print("first :", a["summary"]); print("replay:", b["summary"])
    assert a["payout_batch_id"] == b["payout_batch_id"]
    first = pp.get_payout(a["payout_batch_id"])
    print("status right after the 201:", first["batch_header"]["batch_status"], "->", [i["transaction_status"] for i in first["items"]])
    snap = asyncio.run(payouts.wait_terminal(a["payout_batch_id"], give_up_s=120))
    print("terminal:", snap["batch_status"], snap["items"])
    assert len(snap["items"]) == 1, "one obligation was claimed, so PayPal holds exactly one item"
    assert snap["items"][0]["item_status"] == "SUCCESS"


def test_paypal_refuses_a_repeated_sender_batch_id_and_the_link_is_adopted():
    pp = PayPal()
    sb = f"tailrace-live-dup-{int(time.time())}"
    item = {"recipient_type": "EMAIL", "amount": {"value": "1.00", "currency": "USD"}, "receiver": "sb-patient@personal.example.com", "sender_item_id": sb}
    first, created = pp.create_payout(sb, [item])
    again, created2 = pp.create_payout(sb, [item])
    print("first:", first, created, " repeat:", again, created2)
    assert created and not created2 and first == again
    with pytest.raises(PayPalError) as e:
        pp.call("POST", "/v1/payments/payouts", {"sender_batch_header": {"sender_batch_id": sb}, "items": [item]}, where="raw")
    assert e.value.status == 400 and existing_batch_id(e.value) == first


def test_gbp_and_usd_both_settle_and_each_failure_has_its_own_error_name(seeded):
    rid = runs.create("settle")
    ctx = flow.InlineCtx()
    usd = [r["id"] for r in db.q("select id from obligations where currency='USD'")]
    gbp = [r["id"] for r in db.q("select id from obligations where currency='GBP'")]
    out = {}
    for cur, ids in (("USD", usd), ("GBP", gbp)):
        sub = asyncio.run(tasks.submit_batch.func(ctx, rid, cur, ids, False))
        out[cur] = asyncio.run(payouts.wait_terminal(sub["payout_batch_id"], give_up_s=150))
    seen = {i["obligation_id"]: (i["item_status"], i["error_name"]) for s in out.values() for i in s["items"]}
    for k, v in sorted(seen.items()):
        print(k, v)
    assert seen["po-1001"][0] == "SUCCESS" and seen["po-1002"][0] == "SUCCESS"
    assert seen["po-1003"] == ("UNCLAIMED", "RECEIVER_UNREGISTERED")
    assert seen["po-1004"] == ("UNCLAIMED", "RECEIVER_UNCONFIRMED")
    assert seen["po-1005"] == ("FAILED", "SELF_PAY_NOT_ALLOWED")
    assert seen["po-1006"] == ("FAILED", "RECEIVER_ACCOUNT_INVALID")
