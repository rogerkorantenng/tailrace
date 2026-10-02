"""Direct PayPal REST. A 201 proves nothing, so callers poll batches to a terminal state.

Two layers keep a retried step from paying twice:
  * the sender_batch_id is derived from the work, never from the clock
  * PayPal answers a repeated sender_batch_id with 400 and a link to the batch it already made;
    that is treated as success and the existing batch is adopted
"""
import threading
import time
from urllib.parse import quote

import httpx

from .config import settings

TERMINAL_BATCH = {"SUCCESS", "DENIED", "CANCELED"}
# Item states that will still change on their own.
LIVE_ITEM = {"PENDING", "PROCESSING", "ONHOLD"}


class PayPalError(Exception):
    def __init__(self, status: int, body: dict | None, where: str):
        self.status, self.body, self.where = status, body or {}, where
        self.name = self.body.get("name", "PAYPAL_ERROR")
        self.issue = ""
        for d in self.body.get("details", []) or []:
            self.issue = d.get("issue", self.issue)
        super().__init__(f"{where}: {status} {self.name} {self.body.get('message', '')} {self.issue}".strip())

    @property
    def transient(self) -> bool:
        return self.status in (429, 500, 502, 503, 504)


def existing_batch_id(err: PayPalError) -> str | None:
    """The batch PayPal already made for this sender_batch_id, if the error is that duplicate refusal."""
    for d in err.body.get("details", []) or []:
        if d.get("field") == "SENDER_BATCH_ID":
            for link in d.get("link", []) or []:
                href = link.get("href", "")
                if "/payouts/" in href:
                    return href.rstrip("/").split("/")[-1].split("?")[0]
    return None


class PayPal:
    def __init__(self, client_id=None, secret=None, api=None, http: httpx.Client | None = None):
        self.client_id = client_id or settings.paypal_client_id
        self.secret = secret or settings.paypal_secret
        self.api = (api or settings.paypal_api).rstrip("/")
        self.http = http or httpx.Client(timeout=25)
        self._tok = None
        self._exp = 0.0
        self._lock = threading.Lock()

    def _token(self) -> str:
        with self._lock:
            if self._tok and time.time() < self._exp - 30:
                return self._tok
            r = self.http.post(f"{self.api}/v1/oauth2/token", auth=(self.client_id, self.secret),
                               data={"grant_type": "client_credentials"})
            if r.status_code != 200:
                raise PayPalError(r.status_code, _json(r), "oauth")
            j = r.json()
            self._tok, self._exp = j["access_token"], time.time() + int(j.get("expires_in", 300))
            return self._tok

    def call(self, method: str, path: str, body=None, request_id: str | None = None, where: str = ""):
        headers = {"Authorization": f"Bearer {self._token()}", "Content-Type": "application/json"}
        if request_id:
            headers["PayPal-Request-Id"] = request_id
        last = None
        for attempt in range(3):
            r = self.http.request(method, f"{self.api}{path}", json=body, headers=headers)
            if r.status_code in (429, 502, 503, 504) and attempt < 2:
                time.sleep(0.6 * (attempt + 1))
                last = r
                continue
            if r.status_code >= 400:
                raise PayPalError(r.status_code, _json(r), where or f"{method} {path}")
            return _json(r) if r.content else {}
        raise PayPalError(last.status_code, _json(last), where or path)

    # ---- payouts ----
    def create_payout(self, sender_batch_id: str, items: list[dict], memo: str = "") -> tuple[str, bool]:
        """Returns (payout_batch_id, created_now). created_now is False when PayPal already had this batch."""
        body = {"sender_batch_header": {"sender_batch_id": sender_batch_id,
                                        "email_subject": "You have a payout",
                                        "email_message": memo or "Your payout is ready."},
                "items": items}
        try:
            j = self.call("POST", "/v1/payments/payouts", body, request_id=sender_batch_id, where="create payout")
            return j["batch_header"]["payout_batch_id"], True
        except PayPalError as e:
            existing = existing_batch_id(e)
            if existing:
                return existing, False
            raise

    def get_payout(self, batch_id: str) -> dict:
        """Batch header plus every item, following pages."""
        first = self.call("GET", f"/v1/payments/payouts/{quote(batch_id)}?page_size=100&page=1", where="get batch")
        items = list(first.get("items", []))
        pages = int(first.get("total_pages", 1) or 1)
        for p in range(2, pages + 1):
            items += self.call("GET", f"/v1/payments/payouts/{quote(batch_id)}?page_size=100&page={p}",
                               where="get batch page").get("items", [])
        first["items"] = items
        return first

    def get_item(self, item_id: str) -> dict:
        return self.call("GET", f"/v1/payments/payouts-item/{quote(item_id)}", where="get item")

    def cancel_item(self, item_id: str) -> str:
        """Cancel an UNCLAIMED item. Safe to repeat: a second call finds it already RETURNED."""
        try:
            self.call("POST", f"/v1/payments/payouts-item/{quote(item_id)}/cancel", where="cancel item")
        except PayPalError as e:
            if e.status not in (400, 404, 422):
                raise
        status = self.get_item(item_id).get("transaction_status")
        if status not in ("RETURNED", "CANCELED", "REFUNDED"):
            raise PayPalError(409, {"name": "CANCEL_NOT_CONFIRMED", "message": f"item is {status}"}, "cancel item")
        return status

    # ---- invoices ----
    def create_invoice(self, body: dict, request_id: str) -> str:
        j = self.call("POST", "/v2/invoicing/invoices", body, request_id=request_id, where="create invoice")
        return j["href"].rstrip("/").split("/")[-1]

    def send_invoice(self, invoice_id: str):
        return self.call("POST", f"/v2/invoicing/invoices/{invoice_id}/send",
                         {"send_to_recipient": True, "send_to_invoicer": False}, where="send invoice")

    def remind_invoice(self, invoice_id: str, subject: str, note: str):
        return self.call("POST", f"/v2/invoicing/invoices/{invoice_id}/remind",
                         {"subject": subject, "note": note, "send_to_recipient": True}, where="remind invoice")

    def get_invoice(self, invoice_id: str) -> dict:
        return self.call("GET", f"/v2/invoicing/invoices/{invoice_id}", where="get invoice")

    def record_payment(self, invoice_id: str, amount: str, currency: str, note: str = ""):
        return self.call("POST", f"/v2/invoicing/invoices/{invoice_id}/payments",
                         {"method": "BANK_TRANSFER", "amount": {"currency_code": currency, "value": amount},
                          "note": note}, where="record payment")

    # ---- webhooks ----
    def list_webhooks(self):
        return self.call("GET", "/v1/notifications/webhooks", where="list webhooks")

    def create_webhook(self, url: str, event_types: list[str]):
        return self.call("POST", "/v1/notifications/webhooks",
                         {"url": url, "event_types": [{"name": n} for n in event_types]}, where="create webhook")


def _json(r):
    try:
        return r.json()
    except Exception:
        return {"raw": r.text[:300]}


_client: PayPal | None = None


def client() -> PayPal:
    global _client
    if _client is None:
        _client = PayPal()
    return _client


def invoice_paid(inv: dict) -> tuple[str, str]:
    """(paid_amount, status) as PayPal reports it."""
    paid = (inv.get("payments") or {}).get("paid_amount", {}).get("value", "0.00")
    return paid, inv.get("status", "")
