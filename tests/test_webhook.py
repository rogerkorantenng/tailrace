import base64
import datetime
import json
import zlib

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient

from tailrace import db, webhook
from tailrace.config import settings

WEBHOOK_ID = "WH-TEST-0001"
CERT_URL = "https://api.sandbox.paypal.com/v1/notifications/certs/CERT-TEST"

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
NAME = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "paypal test")])
CERT = (x509.CertificateBuilder().subject_name(NAME).issuer_name(NAME).public_key(KEY.public_key()).serial_number(1)
        .not_valid_before(datetime.datetime(2024, 1, 1)).not_valid_after(datetime.datetime(2034, 1, 1)).sign(KEY, hashes.SHA256()))
CERT_PEM = CERT.public_bytes(serialization.Encoding.PEM)


def signed(body: bytes, tid="TX-1", ts="2026-10-02T10:00:00Z", webhook_id=WEBHOOK_ID, cert_url=CERT_URL, key=KEY):
    crc = zlib.crc32(body) & 0xFFFFFFFF
    sig = key.sign(f"{tid}|{ts}|{webhook_id}|{crc}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return {"paypal-transmission-id": tid, "paypal-transmission-time": ts, "paypal-cert-url": cert_url,
            "paypal-auth-algo": "SHA256withRSA", "paypal-transmission-sig": base64.b64encode(sig).decode()}


def fetch(url):
    return CERT_PEM


BODY = json.dumps({"id": "WH-EVT-1", "event_type": "PAYMENT.PAYOUTS-ITEM.UNCLAIMED", "resource": {"payout_item_id": "NOPE", "payout_batch_id": "NOPE"}}).encode()


def test_valid_signature_is_accepted():
    ok, why = webhook.verify(signed(BODY), BODY, WEBHOOK_ID, fetch)
    assert ok, why


def test_tampered_body_is_rejected():
    tampered = BODY.replace(b"UNCLAIMED", b"SUCCEEDED")
    ok, why = webhook.verify(signed(BODY), tampered, WEBHOOK_ID, fetch)
    assert not ok and "does not match" in why


def test_signature_made_for_a_different_webhook_id_is_rejected():
    ok, _ = webhook.verify(signed(BODY, webhook_id="WH-OTHER"), BODY, WEBHOOK_ID, fetch)
    assert not ok


def test_signature_from_the_wrong_key_is_rejected():
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ok, _ = webhook.verify(signed(BODY, key=other), BODY, WEBHOOK_ID, fetch)
    assert not ok


def test_certificate_from_outside_paypal_is_never_fetched():
    called = []
    h = signed(BODY, cert_url="https://evil.example.com/cert.pem")
    ok, why = webhook.verify(h, BODY, WEBHOOK_ID, lambda u: called.append(u) or CERT_PEM)
    assert not ok and "paypal.com" in why and not called


def test_missing_headers_and_missing_webhook_id_are_rejected():
    assert not webhook.verify({}, BODY, WEBHOOK_ID, fetch)[0]
    assert not webhook.verify(signed(BODY), BODY, "", fetch)[0]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", WEBHOOK_ID)
    monkeypatch.setattr(webhook, "default_fetch_cert", fetch)
    from tailrace.api import app
    with TestClient(app) as c:
        yield c


def test_endpoint_returns_401_for_a_tampered_payload_and_stores_nothing(client):
    tampered = BODY.replace(b"UNCLAIMED", b"SUCCEEDED")
    r = client.post("/api/webhook/paypal", content=tampered, headers=signed(BODY))
    assert r.status_code == 401
    assert db.one("select count(*) n from webhook_events")["n"] == 0


def test_endpoint_returns_200_and_ignores_a_valid_event_that_is_not_ours(client):
    r = client.post("/api/webhook/paypal", content=BODY, headers=signed(BODY))
    assert r.status_code == 200 and r.content == b""
    row = db.one("select * from webhook_events where id='WH-EVT-1'")
    assert row["verified"] is True
    assert row["effect"] == "not ours, ignored"


def test_a_repeated_delivery_is_acknowledged_and_not_processed_twice(client):
    for _ in range(2):
        assert client.post("/api/webhook/paypal", content=BODY, headers=signed(BODY)).status_code == 200
    assert db.one("select count(*) n from webhook_events")["n"] == 1
