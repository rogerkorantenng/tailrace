"""Register the deployed URL with PayPal and print the webhook id to put in PAYPAL_WEBHOOK_ID.
usage: python scripts/register_webhook.py https://tailrace-web.onrender.com"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tailrace.paypal import client
from tailrace.webhook import EVENT_TYPES

url = sys.argv[1].rstrip("/") + "/api/webhook/paypal"
pp = client()
existing = [w for w in pp.list_webhooks().get("webhooks", []) if w["url"] == url]
w = existing[0] if existing else pp.create_webhook(url, EVENT_TYPES)
print("webhook id:", w["id"], "\nurl:", w["url"], "\nset PAYPAL_WEBHOOK_ID to that id on the tailrace-web service")
