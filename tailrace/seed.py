"""Sample payees and payouts for a marketplace that pays its sellers weekly.

Each row is chosen because PayPal's sandbox answers it differently. Nothing here is mocked: the outcomes
are what PayPal returns for these receivers.
"""
from . import db
from .config import settings

REGISTERED = "sb-patient@personal.example.com"   # a registered sandbox account, so payouts to it settle

PAYEES = [
    # id, name, country, email, paypal_id, verified_email, note
    ("lena", "Lena Ortiz", "US", REGISTERED, None, None, "Seller since 2023. Paid weekly."),
    ("marcus", "Marcus Bell", "GB", REGISTERED, None, None, "Seller since 2024. Paid weekly in sterling."),
    ("priya", "Priya Nair", "GB", "priya.nair.studio@example.com", None, REGISTERED,
     "Moved to a new PayPal email in September. She confirmed the new address by reply on 18 Sep and it is on file as verified."),
    ("dana", "Dana Whitcombe", "US", "sb-buyer@personal.example.com", None, None,
     "New seller. First payout. No second address on file."),
    ("sam", "Sam Reyes", "US", settings.sender_email, None, None,
     "Contractor. The email on file was typed from the company's own PayPal account during onboarding."),
    ("theo", "Theo Marsh", "GB", REGISTERED, "ZZZZZZZZZZZZZ", REGISTERED,
     "Onboarded with a PayPal ID. His email on file was verified by a link click."),
]

OBLIGATIONS = [
    # id, payee, description, amount, currency, receiver_type, receiver
    ("po-1001", "lena", "Seller payout, week of 22 Sep", "412.50", "USD", "EMAIL", REGISTERED),
    ("po-1002", "marcus", "Seller payout, week of 22 Sep", "280.00", "GBP", "EMAIL", REGISTERED),
    ("po-1003", "priya", "Seller payout, week of 22 Sep", "96.40", "GBP", "EMAIL", "priya.nair.studio@example.com"),
    ("po-1004", "dana", "First seller payout", "1260.00", "USD", "EMAIL", "sb-buyer@personal.example.com"),
    ("po-1005", "sam", "Contractor invoice 114", "75.00", "USD", "EMAIL", settings.sender_email),
    ("po-1006", "theo", "Seller payout, week of 22 Sep", "150.00", "GBP", "PAYPAL_ID", "ZZZZZZZZZZZZZ"),
]


def seed_payouts() -> int:
    with db.tx() as c:
        for p in PAYEES:
            c.execute("insert into payees(id,name,country,email,paypal_id,verified_email,note) values(%s,%s,%s,%s,%s,%s,%s) "
                      "on conflict(id) do update set name=excluded.name, email=excluded.email, verified_email=excluded.verified_email, note=excluded.note", p)
        n = 0
        for o in OBLIGATIONS:
            n += c.execute("insert into obligations(id,payee_id,description,amount,currency,receiver_type,receiver) "
                           "values(%s,%s,%s,%s,%s,%s,%s) on conflict(id) do nothing", o).rowcount
    return n
