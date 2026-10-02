"""The triage agent. It reads a failed payout item, works out why from PayPal's own error name,
and proposes one of four actions. It loops with tools until the rules gate accepts a decision.

Three providers share one loop: Anthropic direct, Bedrock, and a scripted policy used when no model
credentials exist. The run records which one decided, and the UI says so.
"""
import json
import time

from . import db, rules
from .config import settings
from .paypal import client

MAX_TURNS = 8

SYSTEM = """You are the triage agent inside a payout pipeline. One payout item has not landed. Your job is to decide what happens to it.

Work like this:
1. Call read_failed_item to see the item and what PayPal said about it.
2. Call read_payee to see who the payee is and whether a verified address is on file.
3. Call error_guide with PayPal's error name to see what that error usually means.
4. Choose one action and call check_action to test it. If it is refused, read the reasons and choose again.
5. Call decide with the action that passed.

Actions:
- retry: send again to the same receiver. Only for transient PayPal faults.
- correct: cancel the held item if there is one, then send once to the payee's verified address from the directory.
- escalate: put it in front of a person. Use when no safe fix exists and the payee can still be reached.
- stop: end it. Use when sending again would be wrong, such as a payee record that points at our own account. Held money is returned.

Rules you cannot override: never invent an address, only the directory's verified address may be used. A retry cannot fix an error that is not transient. After three attempts only escalate or stop are allowed.

Write the reasoning as two plain sentences a finance person can check: what PayPal said, and why this action follows from it. No filler."""

TOOLS = [
    {"name": "read_failed_item", "description": "Read the obligation, its payment attempts, and PayPal's live view of the latest item.",
     "input_schema": {"type": "object", "properties": {"obligation_id": {"type": "string"}}, "required": ["obligation_id"]}},
    {"name": "read_payee", "description": "Read the payee directory entry, including the verified address if one exists.",
     "input_schema": {"type": "object", "properties": {"payee_id": {"type": "string"}}, "required": ["payee_id"]}},
    {"name": "error_guide", "description": "Look up what a PayPal payout error name usually means and what usually fixes it.",
     "input_schema": {"type": "object", "properties": {"error_name": {"type": "string"}}, "required": ["error_name"]}},
    {"name": "check_action", "description": "Dry-run a proposed action through the rules gate. Nothing is executed.",
     "input_schema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": list(rules.ACTIONS)}, "address": {"type": "string"}, "reasoning": {"type": "string"}},
         "required": ["action", "reasoning"]}},
    {"name": "decide", "description": "Submit the final decision. Runs the rules gate again; if refused you will get reasons back.",
     "input_schema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": list(rules.ACTIONS)}, "address": {"type": "string"}, "reasoning": {"type": "string"}},
         "required": ["action", "reasoning"]}},
]


# ---------------------------------------------------------------- providers
class Anthropic:
    def __init__(self):
        import anthropic
        self.name = f"Claude via Anthropic API ({settings.anthropic_model})"
        self.client = anthropic.Anthropic(api_key=settings.anthropic_key)

    def turn(self, system, history, tools):
        msgs = []
        for h in history:
            if h["role"] == "user":
                msgs.append({"role": "user", "content": h["text"]})
            elif h["role"] == "assistant":
                blocks = ([{"type": "text", "text": h["text"]}] if h.get("text") else []) + [
                    {"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["input"]} for c in h.get("calls", [])]
                msgs.append({"role": "assistant", "content": blocks})
            else:
                msgs.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": r["id"], "content": r["text"]} for r in h["results"]]})
        r = self.client.messages.create(model=settings.anthropic_model, max_tokens=1200, system=system, tools=tools, messages=msgs)
        text = "".join(b.text for b in r.content if b.type == "text")
        calls = [{"id": b.id, "name": b.name, "input": b.input} for b in r.content if b.type == "tool_use"]
        return text, calls


class Bedrock:
    def __init__(self):
        import boto3
        self.name = f"Claude via Amazon Bedrock ({settings.bedrock_model})"
        from botocore.config import Config
        self.client = boto3.client("bedrock-runtime", region_name=settings.aws_region,
                                   config=Config(retries={"max_attempts": 8, "mode": "adaptive"}, read_timeout=60))

    def turn(self, system, history, tools):
        msgs = []
        for h in history:
            if h["role"] == "user":
                msgs.append({"role": "user", "content": [{"text": h["text"]}]})
            elif h["role"] == "assistant":
                blocks = ([{"text": h["text"]}] if h.get("text") else []) + [
                    {"toolUse": {"toolUseId": c["id"], "name": c["name"], "input": c["input"]}} for c in h.get("calls", [])]
                msgs.append({"role": "assistant", "content": blocks})
            else:
                msgs.append({"role": "user", "content": [
                    {"toolResult": {"toolUseId": r["id"], "content": [{"text": r["text"]}]}} for r in h["results"]]})
        spec = [{"toolSpec": {"name": t["name"], "description": t["description"], "inputSchema": {"json": t["input_schema"]}}} for t in tools]
        r = self.client.converse(modelId=settings.bedrock_model, system=[{"text": system}], messages=msgs,
                                 toolConfig={"tools": spec}, inferenceConfig={"maxTokens": 1200})
        text, calls = "", []
        for b in r["output"]["message"]["content"]:
            if "text" in b:
                text += b["text"]
            if "toolUse" in b:
                calls.append({"id": b["toolUse"]["toolUseId"], "name": b["toolUse"]["name"], "input": b["toolUse"]["input"]})
        return text, calls


class Policy:
    """Scripted stand-in used when no model credentials exist. It walks the same tools in the same order."""
    name = "Rule-based policy (no model credentials configured)"

    def turn(self, system, history, tools):
        first = history[0]["text"]
        ob_id = first.split("obligation ")[1].split(" ")[0]
        results = [r for h in history if h["role"] == "tool" for r in h["results"]]
        by = {r["name"]: json.loads(r["text"]) for r in results}
        n = lambda: f"p{len(history)}"
        if "read_failed_item" not in by:
            return "", [{"id": n(), "name": "read_failed_item", "input": {"obligation_id": ob_id}}]
        item = by["read_failed_item"]
        if "read_payee" not in by:
            err = (item.get("latest_payment") or {}).get("error_name") or ""
            return "", [{"id": n() + "a", "name": "read_payee", "input": {"payee_id": item["obligation"]["payee_id"]}},
                        {"id": n() + "b", "name": "error_guide", "input": {"error_name": err}}]
        payee = by["read_payee"]
        err = (item.get("latest_payment") or {}).get("error_name") or ""
        ob = item["obligation"]
        verified = payee.get("verified_email")
        if err in rules.TRANSIENT:
            act, addr, why = "retry", None, f"PayPal reported {err}, a transient fault, so sending again to the same receiver is allowed."
        elif err == "SELF_PAY_NOT_ALLOWED":
            act, addr, why = "stop", None, "The receiver is the sending account, so the payee record is wrong and sending again would fail the same way."
        elif verified and verified.lower() != ob["receiver"].lower():
            act, addr, why = "correct", verified, f"PayPal reported {err}. The directory holds a verified address for {payee['name']}, so the payout goes there."
        else:
            act, addr, why = "escalate", None, f"PayPal reported {err or 'no error name'} and there is no verified address on file, so a person has to ask the payee."
        if "decide" not in by or by["decide"].get("accepted") is False:
            return "", [{"id": n(), "name": "decide", "input": {"action": act, "address": addr, "reasoning": why}}]
        return "", []


def provider():
    kind = settings.agent_provider
    try:
        if kind == "anthropic":
            return Anthropic()
        if kind == "bedrock":
            return Bedrock()
    except Exception:
        pass
    return Policy()


# ---------------------------------------------------------------- tools
class Toolbox:
    def __init__(self, run_id: str, obligation_id: str):
        self.run_id, self.oid = run_id, obligation_id
        self.decision: dict | None = None

    def _ctx(self):
        ob = db.one("select * from obligations where id=%s", (self.oid,))
        payee = db.one("select * from payees where id=%s", (ob["payee_id"],))
        pay = db.one("select * from payments where obligation_id=%s order by attempt desc limit 1", (self.oid,))
        return ob, payee, pay

    def call(self, name: str, args: dict) -> dict:
        try:
            return getattr(self, "t_" + name)(**args)
        except TypeError as e:
            return {"error": f"bad arguments for {name}: {e}"}

    def t_read_failed_item(self, obligation_id: str):
        ob, _, pay = self._ctx()
        live = None
        if pay and pay.get("payout_item_id"):
            try:
                it = client().get_item(pay["payout_item_id"])
                live = {"transaction_status": it.get("transaction_status"), "errors": it.get("errors"),
                        "time_processed": it.get("time_processed")}
            except Exception as e:
                live = {"unavailable": str(e)[:160]}
        attempts = db.q("select attempt,receiver,item_status,error_name from payments where obligation_id=%s order by attempt", (self.oid,))
        return json.loads(json.dumps({"obligation": {k: ob[k] for k in ("id", "payee_id", "description", "amount", "currency", "receiver_type", "receiver", "status", "attempts")},
                                      "latest_payment": pay and {k: pay[k] for k in ("attempt", "item_status", "error_name", "error_message", "receiver")},
                                      "all_attempts": attempts, "paypal_live_item": live,
                                      "attempts_left": max(0, rules.MAX_ATTEMPTS - ob["attempts"])}, default=str))

    def t_read_payee(self, payee_id: str):
        _, payee, _ = self._ctx()
        return {k: payee[k] for k in ("id", "name", "country", "email", "paypal_id", "verified_email", "note")}

    def t_error_guide(self, error_name: str):
        return rules.guide_for(error_name)

    def _gate(self, action, address, reasoning):
        ob, payee, pay = self._ctx()
        return rules.validate(action, address, reasoning, ob, payee, pay)

    def t_check_action(self, action: str, reasoning: str, address: str | None = None):
        problems = self._gate(action, address, reasoning)
        return {"ok": not problems, "problems": problems}

    def t_decide(self, action: str, reasoning: str, address: str | None = None):
        problems = self._gate(action, address, reasoning)
        if problems:
            return {"accepted": False, "problems": problems}
        self.decision = {"action": action, "address": address, "reasoning": reasoning.strip()}
        return {"accepted": True}


# ---------------------------------------------------------------- loop
def _log(run_id, oid, turn, kind, name="", payload=None):
    db.x("insert into agent_turns(run_id,obligation_id,turn,kind,name,payload) values(%s,%s,%s,%s,%s,%s)",
         (run_id, oid, turn, kind, name, db.jb(payload or {})))


def triage(run_id: str, obligation_id: str) -> dict:
    db.x("delete from agent_turns where run_id=%s and obligation_id=%s", (run_id, obligation_id))
    prov = provider()
    tb = Toolbox(run_id, obligation_id)
    history = [{"role": "user", "text": f"Payout obligation {obligation_id} did not land in run {run_id}. Diagnose it and decide."}]
    _log(run_id, obligation_id, 0, "start", prov.name, {"provider": prov.name})
    source, gate_trail = prov.name, []
    for turn in range(1, MAX_TURNS + 1):
        text, calls = prov.turn(SYSTEM, history, TOOLS)
        if text.strip():
            _log(run_id, obligation_id, turn, "thought", payload={"text": text.strip()})
        if not calls:
            if tb.decision:
                break
            history.append({"role": "assistant", "text": text})
            history.append({"role": "user", "text": "Call decide now with one of: retry, correct, escalate, stop."})
            continue
        history.append({"role": "assistant", "text": text, "calls": calls})
        results = []
        for c in calls:
            _log(run_id, obligation_id, turn, "call", c["name"], c["input"])
            out = tb.call(c["name"], c["input"] or {})
            if c["name"] in ("check_action", "decide") and (out.get("problems")):
                gate_trail.append({"action": c["input"].get("action"), "problems": out["problems"]})
            _log(run_id, obligation_id, turn, "result", c["name"], out)
            results.append({"id": c["id"], "name": c["name"], "text": json.dumps(out, default=str)})
        history.append({"role": "tool", "results": results})
        if tb.decision:
            break
    if not tb.decision:
        tb.decision = {"action": "escalate", "address": None,
                       "reasoning": "The agent did not reach a decision the rules would accept, so a person should look at this item."}
        source += " (fell back to escalate)"
        _log(run_id, obligation_id, MAX_TURNS + 1, "fallback", payload=tb.decision)
    ob = db.one("select attempts from obligations where id=%s", (obligation_id,))
    d = dict(tb.decision, source=source, gate=gate_trail, target_attempt=ob["attempts"] + 1, obligation_id=obligation_id)
    db.x("delete from decisions where run_id=%s and obligation_id=%s", (run_id, obligation_id))
    db.x("insert into decisions(run_id,obligation_id,action,address,reasoning,source,gate,target_attempt) values(%s,%s,%s,%s,%s,%s,%s,%s)",
         (run_id, obligation_id, d["action"], d["address"], d["reasoning"], source, db.jb(gate_trail), d["target_attempt"]))
    _log(run_id, obligation_id, MAX_TURNS + 2, "decision", d["action"], d)
    return d
