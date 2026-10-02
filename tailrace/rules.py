"""The gate between what the agent wants and what the pipeline will do.

The agent proposes. These rules decide. A rejected proposal goes back to the agent as a list of reasons,
so it can pick a different action instead of the pipeline guessing.
"""
from .payouts import is_self_pay

ACTIONS = ("retry", "correct", "escalate", "stop")
MAX_ATTEMPTS = 3

# PayPal error names that mean "try the same thing again later". Our classification, not PayPal's list.
TRANSIENT = {"INTERNAL_SERVICE_ERROR", "PAYOUT_DELAYED", "TEMPORARILY_UNAVAILABLE", "RATE_LIMIT_REACHED"}

ERROR_GUIDE = {
    "RECEIVER_UNREGISTERED": {
        "meaning": "The email has no PayPal account. PayPal holds the money and tells the payee to sign up. It returns to the sender after 30 days.",
        "usual_remedy": "If the directory has a verified address for this payee, correct to it. If not, escalate so someone can ask the payee which email to use. Retrying the same email changes nothing.",
        "retry_helps": False},
    "RECEIVER_UNCONFIRMED": {
        "meaning": "A PayPal account exists for the email, but the payee has not confirmed that address. The money is held until they do.",
        "usual_remedy": "The payee can confirm the email themselves. If a verified alternative is on file, correct to it; otherwise escalate to ask them to confirm.",
        "retry_helps": False},
    "SELF_PAY_NOT_ALLOWED": {
        "meaning": "The receiver is the account that is sending the money. PayPal refuses it and no money moves.",
        "usual_remedy": "This is a data error in who the payee is. Never retry. If the directory has a different verified address, that is suspicious: stop and say so. Otherwise stop.",
        "retry_helps": False},
    "RECEIVER_ACCOUNT_INVALID": {
        "meaning": "The PayPal ID does not belong to any account. No money moved.",
        "usual_remedy": "Fall back to the payee's email on file if it is verified, by correcting to it. Otherwise escalate.",
        "retry_helps": False},
    "INTERNAL_SERVICE_ERROR": {
        "meaning": "PayPal had a fault on its side. The payment may not have been made.",
        "usual_remedy": "Retry to the same receiver. The retry uses a new attempt number, and the pipeline will not send while the earlier item could still be live.",
        "retry_helps": True},
}


def guide_for(name: str | None) -> dict:
    if name and name in ERROR_GUIDE:
        return {"error_name": name, **ERROR_GUIDE[name]}
    return {"error_name": name or "NONE", "meaning": "Not in the internal guide. Read the raw message on the item.",
            "usual_remedy": "Escalate unless the message makes the cause obvious.", "retry_helps": False}


def validate(action: str, address: str | None, reasoning: str, ob: dict, payee: dict, pay: dict | None) -> list[str]:
    """Return a list of reasons the proposal is refused. Empty list means it may run."""
    errs: list[str] = []
    if action not in ACTIONS:
        return [f"action must be one of {', '.join(ACTIONS)}"]
    if not reasoning or len(reasoning.strip()) < 15:
        errs.append("give a reason of at least a sentence, so a person reading the run can follow it")
    if ob["attempts"] >= MAX_ATTEMPTS and action in ("retry", "correct"):
        errs.append(f"{ob['id']} has already used {ob['attempts']} of {MAX_ATTEMPTS} attempts; only escalate or stop are allowed")
    err_name = (pay or {}).get("error_name")
    if action == "retry":
        if err_name not in TRANSIENT:
            errs.append(f"retry is only allowed for transient errors ({', '.join(sorted(TRANSIENT))}); this one is {err_name or 'unknown'}")
        if is_self_pay(ob["receiver"]):
            errs.append("the receiver is the sending account")
    if action == "correct":
        verified = (payee.get("verified_email") or "").lower()
        if not address:
            errs.append("correct needs an address")
        elif address.lower() == ob["receiver"].lower():
            errs.append("the address is the one that just failed")
        elif not verified:
            errs.append(f"{payee['name']} has no verified address on file, so there is nothing safe to correct to; escalate instead")
        elif address.lower() != verified:
            errs.append(f"{address} is not the verified address on file for {payee['name']}; only the directory's verified address is allowed")
        if address and is_self_pay(address):
            errs.append("the address is the sending account")
        if err_name == "SELF_PAY_NOT_ALLOWED":
            errs.append("a self-pay error means the payee record is wrong; correcting silently hides that, so stop or escalate")
    return errs
