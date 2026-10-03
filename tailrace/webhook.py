"""PayPal webhooks: verify the signature, store the event, answer 200, then process.

PayPal signs  transmission_id | transmission_time | webhook_id | crc32(body)  with the private key behind
the certificate at PAYPAL-CERT-URL. We rebuild that string and check it against the certificate's public key.
Webhooks are per app, so unrelated events arrive here too: a valid signature on an event that
is not ours is acknowledged with 200 and ignored.
"""
import base64
import json
import zlib
from urllib.parse import urlparse

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.x509 import load_pem_x509_certificate

from . import db, payouts
from .config import settings
from .invoices import sync as sync_invoice
from .paypal import client

_certs: dict[str, bytes] = {}

EVENT_TYPES = [
    "PAYMENT.PAYOUTSBATCH.SUCCESS", "PAYMENT.PAYOUTSBATCH.DENIED", "PAYMENT.PAYOUTSBATCH.PROCESSING",
    "PAYMENT.PAYOUTS-ITEM.SUCCEEDED", "PAYMENT.PAYOUTS-ITEM.FAILED", "PAYMENT.PAYOUTS-ITEM.UNCLAIMED",
    "PAYMENT.PAYOUTS-ITEM.RETURNED", "PAYMENT.PAYOUTS-ITEM.BLOCKED", "PAYMENT.PAYOUTS-ITEM.HELD",
    "PAYMENT.PAYOUTS-ITEM.CANCELED", "PAYMENT.PAYOUTS-ITEM.DENIED", "PAYMENT.PAYOUTS-ITEM.REFUNDED",
    "INVOICING.INVOICE.PAID", "INVOICING.INVOICE.CANCELLED", "INVOICING.INVOICE.REFUNDED",
]


def default_fetch_cert(url: str) -> bytes:
    if url not in _certs:
        r = httpx.get(url, timeout=10)
        r.raise_for_status()
        _certs[url] = r.content
    return _certs[url]


def verify(headers: dict, body: bytes, webhook_id: str, fetch_cert=None) -> tuple[bool, str]:
    """(ok, reason). Never raises on bad input; a bad input is a failed verification."""
    h = {k.lower(): v for k, v in headers.items()}
    need = ("paypal-transmission-id", "paypal-transmission-time", "paypal-transmission-sig", "paypal-cert-url", "paypal-auth-algo")
    missing = [k for k in need if not h.get(k)]
    if missing:
        return False, "missing header " + missing[0]
    if not webhook_id:
        return False, "PAYPAL_WEBHOOK_ID is not set, so nothing can be verified"
    if h["paypal-auth-algo"].upper() not in ("SHA256WITHRSA", "SHA256-RSA"):
        return False, "unsupported algorithm " + h["paypal-auth-algo"]
    u = urlparse(h["paypal-cert-url"])
    if u.scheme != "https" or not (u.hostname or "").endswith(".paypal.com"):
        return False, "certificate URL is not a paypal.com address"
    try:
        crc = zlib.crc32(body) & 0xFFFFFFFF
        message = f"{h['paypal-transmission-id']}|{h['paypal-transmission-time']}|{webhook_id}|{crc}".encode()
        pub = load_pem_x509_certificate((fetch_cert or default_fetch_cert)(h["paypal-cert-url"])).public_key()
        pub.verify(base64.b64decode(h["paypal-transmission-sig"]), message, padding.PKCS1v15(), hashes.SHA256())
        return True, "signature valid"
    except InvalidSignature:
        return False, "signature does not match the body"
    except Exception as e:  # unreadable cert, bad base64, network
        return False, f"could not verify: {type(e).__name__}"


def is_ours(evt: dict) -> bool:
    res = evt.get("resource") or {}
    item = res.get("payout_item_id")
    batch = res.get("payout_batch_id") or (res.get("batch_header") or {}).get("payout_batch_id")
    inv = (res.get("invoice") or {}).get("id") or res.get("id")
    if item and db.one("select 1 from payments where payout_item_id=%s", (item,)):
        return True
    if batch and db.one("select 1 from payments where payout_batch_id=%s", (batch,)):
        return True
    if inv and db.one("select 1 from invoices where id=%s", (str(inv),)):
        return True
    return False


def store(evt: dict, verified: bool) -> bool:
    """Persist the event. False means we have seen this id before."""
    return bool(db.x("insert into webhook_events(id,event_type,resource,verified) values(%s,%s,%s,%s) on conflict do nothing",
                     (evt.get("id", "unknown"), evt.get("event_type", ""), db.jb(evt.get("resource") or {}), verified)))


def apply_event(event_id: str) -> str:
    row = db.one("select * from webhook_events where id=%s", (event_id,))
    if not row:
        return "event not found"
    evt = {"id": row["id"], "event_type": row["event_type"], "resource": row["resource"]}
    if not is_ours(evt):
        effect = "not ours, ignored"
    else:
        res, t = evt["resource"], evt["event_type"]
        if t.startswith("INVOICING."):
            iid = (res.get("invoice") or {}).get("id") or res.get("id")
            r = sync_invoice(str(iid))
            effect = f"invoice {iid} now {r['paypal_status']}, PayPal paid {r['paypal_paid']}"
        else:
            batch = res.get("payout_batch_id") or (res.get("batch_header") or {}).get("payout_batch_id")
            if not batch:
                pay = db.one("select payout_batch_id from payments where payout_item_id=%s", (res.get("payout_item_id"),))
                batch = pay and pay["payout_batch_id"]
            snap = payouts.refresh_batch(batch)
            effect = f"batch {batch} re-read: " + ", ".join(f"{i['obligation_id']} {i['item_status']}" for i in snap["items"])
    db.x("update webhook_events set processed_at=now(), effect=%s where id=%s", (effect, event_id))
    return effect
