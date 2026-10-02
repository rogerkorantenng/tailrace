import json

import pytest

from tailrace import agent, db, payouts, runs


@pytest.fixture
def failed(fake, seeded):
    """Send every obligation through the fake PayPal and read the outcomes back, as the pipeline would."""
    rid = runs.create("settle")
    for cur in ("USD", "GBP"):
        ids = [r["id"] for r in db.q("select id from obligations where currency=%s", (cur,))]
        payouts.send_claimed(payouts.claim_first_batch(rid, cur, ids), payouts.batch_id_for(rid, cur))
        payouts.refresh_batch(fake.batches[payouts.batch_id_for(rid, cur)]["id"])
    return rid


@pytest.mark.parametrize("oid,action,address", [
    ("po-1003", "correct", "sb-patient@personal.example.com"),   # unregistered email, verified address on file
    ("po-1004", "escalate", None),                                 # unconfirmed email, nothing on file
    ("po-1005", "stop", None),                                     # pays our own account
    ("po-1006", "correct", "sb-patient@personal.example.com"),   # bogus PayPal ID, verified email on file
])
def test_policy_decides_each_sandbox_failure_correctly(failed, oid, action, address):
    d = agent.triage(failed, oid)
    assert d["action"] == action and d["address"] == address
    assert d["gate"] == [], "the scripted policy should pass the gate first time"
    assert "policy" in d["source"].lower()


class Scripted:
    name = "scripted test provider"

    def __init__(self, proposals):
        self.proposals, self.i = proposals, 0

    def turn(self, system, history, tools):
        self.i += 1
        if self.i > len(self.proposals):
            return "", []
        return "thinking", [{"id": f"c{self.i}", "name": "decide", "input": self.proposals[self.i - 1]}]


def test_gate_refusal_goes_back_to_the_agent_and_it_changes_course(failed, monkeypatch):
    monkeypatch.setattr(agent, "provider", lambda: Scripted([
        {"action": "correct", "address": "invented@example.com", "reasoning": "I will try an address that looks right for Dana."},
        {"action": "escalate", "reasoning": "No verified address exists for Dana, so a person has to ask her."}]))
    d = agent.triage(failed, "po-1004")
    assert d["action"] == "escalate"
    assert len(d["gate"]) == 1 and "no verified address" in d["gate"][0]["problems"][0]
    kinds = [t["kind"] for t in db.q("select kind from agent_turns where run_id=%s order by id", (failed,))]
    assert kinds.count("call") == 2 and kinds.count("result") == 2


def test_an_agent_that_never_decides_falls_back_to_escalate(failed, monkeypatch):
    monkeypatch.setattr(agent, "provider", lambda: Scripted([]))
    d = agent.triage(failed, "po-1003")
    assert d["action"] == "escalate" and "fell back" in d["source"]


def test_an_unsafe_retry_is_refused_even_if_the_agent_insists(failed, monkeypatch):
    bad = {"action": "retry", "reasoning": "Trying the same email again in case the account exists now."}
    monkeypatch.setattr(agent, "provider", lambda: Scripted([bad, bad, bad]))
    d = agent.triage(failed, "po-1003")
    assert d["action"] == "escalate" and len(d["gate"]) == 3


def test_tools_return_what_the_ledger_holds(failed):
    tb = agent.Toolbox(failed, "po-1004")
    item = tb.call("read_failed_item", {"obligation_id": "po-1004"})
    assert item["latest_payment"]["error_name"] == "RECEIVER_UNCONFIRMED" and item["attempts_left"] == 2
    assert tb.call("read_payee", {"payee_id": "dana"})["verified_email"] is None
    assert "confirmed" in tb.call("error_guide", {"error_name": "RECEIVER_UNCONFIRMED"})["meaning"]
