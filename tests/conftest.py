import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:pg@localhost:5439/payrail_test")
os.environ["DATABASE_URL"] = os.environ["DATABASE_URL"].replace("/payrail", "/payrail_test") if "payrail_test" not in os.environ["DATABASE_URL"] else os.environ["DATABASE_URL"]
os.environ["AGENT_PROVIDER"] = os.environ.get("AGENT_PROVIDER_TEST", "policy")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from tailrace import db, paypal, seed


@pytest.fixture(scope="session", autouse=True)
def schema():
    db.init_schema()


@pytest.fixture(autouse=True)
def clean():
    db.wipe()
    paypal._client = None
    yield
    paypal._client = None


class FakePayPal(paypal.PayPal):
    """Replaces only the HTTP layer. The real PayPal class runs on top, so duplicate handling, paging and
    cancel confirmation are the production code. Like the sandbox, it refuses a repeated sender_batch_id
    with a 400 that links to the first batch."""
    REGISTERED = {"sb-patient@personal.example.com"}

    def __init__(self):
        self.api = "https://fake.paypal.test"
        self.batches: dict[str, dict] = {}      # sender_batch_id -> batch
        self.by_id: dict[str, dict] = {}
        self.create_calls = 0
        self.cancel_calls = 0

    def _outcome(self, item):
        r = item["receiver"]
        if r == "sb-mixsn53098231@business.example.com":
            return "FAILED", "SELF_PAY_NOT_ALLOWED"
        if item["recipient_type"] == "PAYPAL_ID":
            return "FAILED", "RECEIVER_ACCOUNT_INVALID"
        if r in self.REGISTERED:
            return "SUCCESS", None
        if r.startswith("sb-buyer"):
            return "UNCLAIMED", "RECEIVER_UNCONFIRMED"
        return "UNCLAIMED", "RECEIVER_UNREGISTERED"

    def _find(self, item_id):
        for b in self.by_id.values():
            for it in b["items"]:
                if it["payout_item_id"] == item_id:
                    return it
        raise paypal.PayPalError(404, {"name": "INVALID_RESOURCE_ID"}, "fake")

    def call(self, method, path, body=None, request_id=None, where=""):
        path = path.split("?")[0]
        if method == "POST" and path == "/v1/payments/payouts":
            self.create_calls += 1
            sb = body["sender_batch_header"]["sender_batch_id"]
            if sb in self.batches:
                bid = self.batches[sb]["id"]
                raise paypal.PayPalError(400, {"name": "USER_BUSINESS_ERROR", "details": [{"field": "SENDER_BATCH_ID",
                    "issue": "Batch with given sender_batch_id already exists",
                    "link": [{"href": f"https://api.sandbox.paypal.com/v1/payments/payouts/{bid}"}]}]}, where)
            bid = f"B{len(self.batches) + 1:04d}"
            built = []
            for n, it in enumerate(body["items"]):
                st, err = self._outcome(it)
                built.append({"payout_item_id": f"{bid}-I{n}", "transaction_status": st,
                              "errors": {"name": err, "message": err} if err else None,
                              "payout_item": {"sender_item_id": it["sender_item_id"], "amount": it["amount"], "receiver": it["receiver"]}})
            b = {"id": bid, "sender": sb, "items": built}
            self.batches[sb] = b
            self.by_id[bid] = b
            return {"batch_header": {"payout_batch_id": bid, "batch_status": "PENDING"}}
        if method == "GET" and path.startswith("/v1/payments/payouts/"):
            b = self.by_id[path.rsplit("/", 1)[1]]
            return {"batch_header": {"payout_batch_id": b["id"], "batch_status": "SUCCESS"}, "items": b["items"], "total_pages": 1}
        if method == "GET" and "/payouts-item/" in path:
            return self._find(path.rsplit("/", 1)[1])
        if method == "POST" and path.endswith("/cancel"):
            self.cancel_calls += 1
            it = self._find(path.split("/")[-2])
            if it["transaction_status"] != "UNCLAIMED":
                raise paypal.PayPalError(400, {"name": "ITEM_NOT_CANCELLABLE"}, where)
            it["transaction_status"] = "RETURNED"
            return {}
        raise AssertionError(f"unexpected call {method} {path}")

    def paid_count(self):
        """Per obligation: how many items hold money out (SUCCESS or UNCLAIMED, not RETURNED or FAILED)."""
        out: dict[str, int] = {}
        for b in self.batches.values():
            for it in b["items"]:
                if it["transaction_status"] in ("SUCCESS", "UNCLAIMED"):
                    oid = it["payout_item"]["sender_item_id"].rsplit("-a", 1)[0]
                    out[oid] = out.get(oid, 0) + 1
        return out


@pytest.fixture
def fake():
    f = FakePayPal()
    paypal._client = f
    return f


@pytest.fixture
def seeded():
    seed.seed_payouts()
    return [r["id"] for r in db.q("select id from obligations order by id")]
