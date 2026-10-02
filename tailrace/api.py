"""Web service: the UI, the JSON API behind it, and the PayPal webhook listener."""
import json
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import agent, db, flow, invoices, payouts, rules, runs, seed, webhook
from .config import settings
from .paypal import PayPalError, client

WEB = Path(__file__).resolve().parent.parent / "web"


@asynccontextmanager
async def lifespan(app):
    db.init_schema()
    yield


app = FastAPI(title="Tailrace", lifespan=lifespan, docs_url=None, redoc_url=None)


def out(data):
    return JSONResponse(jsonable_encoder(data, custom_encoder={Decimal: str}))


def agent_label() -> str:
    return {"anthropic": f"Claude via Anthropic API ({settings.anthropic_model})",
            "bedrock": f"Claude via Amazon Bedrock ({settings.bedrock_model})"}.get(settings.agent_provider, agent.Policy.name)


# ------------------------------------------------------------------ state
@app.get("/api/health")
def health():
    row = db.one("select 1 ok")
    return {"ok": bool(row), "executor": flow.mode()}


@app.get("/api/state")
def state():
    obs = db.q("""select o.*, p.name payee_name, p.country, p.verified_email,
                  (select row_to_json(x) from (select item_status,error_name,payout_batch_id,attempt from payments where obligation_id=o.id order by attempt desc limit 1) x) latest
                  from obligations o join payees p on p.id=o.payee_id order by o.id""")
    today = date.today()
    invs = []
    for i in db.q("select * from invoices order by due_date"):
        days = (today - i["due_date"]).days
        rems = db.q("select stage,state from reminders where invoice_id=%s order by stage", (i["id"],))
        invs.append({**i, "days_overdue": days, "stage": invoices.stage_for(days),
                     "balance": str(Decimal(i["amount"]) - Decimal(i["paypal_paid"] if i["synced_at"] else 0)), "reminders": rems})
    return out({
        "env": {"executor": flow.mode(), "agent": agent_label(), "paypal": "PayPal sandbox", "slug": settings.workflow_slug,
                "webhook_verifying": bool(settings.webhook_id)},
        "obligations": obs, "invoices": invs,
        "escalations": db.q("select * from escalations where status='open' order by id"),
        "runs": db.q("select id,kind,status,started_at,finished_at,summary,render_run_id,params from runs order by started_at desc limit 12"),
        "webhooks": db.q("select id,event_type,verified,received_at,processed_at,effect from webhook_events order by received_at desc limit 8"),
        "max_attempts": rules.MAX_ATTEMPTS,
    })


def _fold_steps(run_id: str):
    rows = db.q("select * from steps where run_id=%s order by id", (run_id,))
    steps: dict[tuple, dict] = {}
    for r in rows:
        k = (r["task"], r["subject"])
        s = steps.setdefault(k, {"task": r["task"], "subject": r["subject"], "attempts": [], "rids": set(), "parents": set(),
                                 "first": r["ts"], "last": r["ts"], "state": "running", "detail": ""})
        s["last"] = r["ts"]
        if r["render_run_id"]:
            s["rids"].add(r["render_run_id"])
        if r["parent_render_run_id"]:
            s["parents"].add(r["parent_render_run_id"])
        if r["event"] == "started":
            s["attempts"].append({"attempt": r["attempt"], "outcome": "running", "detail": "", "at": r["ts"]})
            s["state"] = "running"
        else:
            if s["attempts"]:
                s["attempts"][-1].update(outcome=r["event"], detail=r["detail"])
            s["state"] = {"retrying": "retrying", "failed": "failed", "succeeded": "succeeded"}[r["event"]]
            if r["detail"]:
                s["detail"] = r["detail"]
    by_rid = {rid: k for k, s in steps.items() for rid in s["rids"]}
    t0 = min((s["first"] for s in steps.values()), default=None)
    now = datetime.now(timezone.utc)
    result = []
    def static_parent(k, s):
        """Local task servers do not send run ids, so fall back to the fixed shape of the workflow."""
        task, subj = k
        of = lambda t: [kk for kk in steps if kk[0] == t]
        if task in ("payout_lane", "triage_item", "apply_decision", "reconcile_run"):
            return (of("settle_run") or [None])[0]
        if task == "submit_batch":
            return next((kk for kk in of("payout_lane") if kk[1] == subj), None)
        if task == "reissue_item":
            return next((kk for kk in of("apply_decision") if kk[1] == subj), None) or (of("resend_run") or [None])[0]
        if task == "await_terminal":
            for src in of("submit_batch") + of("reissue_item"):
                if subj in steps[src]["detail"] or subj in steps[src].get("batch", ""):
                    return static_parent(src, steps[src]) if src[0] == "submit_batch" else static_parent(src, steps[src])
            return None
        if task in ("sync_invoices", "chase_invoice", "reconcile_invoices"):
            return (of("dunning_sweep") or [None])[0]
        if task in ("settle_run", "dunning_sweep"):
            return (of("nightly") or [None])[0]
        return None

    for k, s in steps.items():
        parent = next((by_rid[p] for p in s["parents"] if p in by_rid and by_rid[p] != k), None) or static_parent(k, s)
        end = s["last"] if s["state"] in ("succeeded", "failed") else now
        result.append({"key": f"{k[0]}:{k[1]}", "task": s["task"], "subject": s["subject"], "state": s["state"], "detail": s["detail"],
                       "attempts": s["attempts"], "retries": max(0, len(s["attempts"]) - 1),
                       "render_run_id": sorted(s["rids"])[0] if s["rids"] else None,
                       "parent": f"{parent[0]}:{parent[1]}" if parent else None,
                       "start_ms": int((s["first"] - t0).total_seconds() * 1000), "dur_ms": int((end - s["first"]).total_seconds() * 1000)})
    result.sort(key=lambda s: s["start_ms"])
    return result


@app.get("/api/runs/{run_id}")
def run_detail(run_id: str):
    run = db.one("select * from runs where id=%s", (run_id,))
    if not run:
        raise HTTPException(404, "No such run.")
    turns = db.q("select obligation_id,turn,kind,name,payload from agent_turns where run_id=%s order by id", (run_id,))
    return out({"run": run, "steps": _fold_steps(run_id), "agent_turns": turns,
                "decisions": db.q("select * from decisions where run_id=%s order by id", (run_id,)),
                "reconciliation": db.q("select subject_type,subject_id,ledger,paypal,verdict,note from reconciliation where run_id=%s order by id", (run_id,)),
                "payments": db.q("select obligation_id,attempt,sender_batch_id,payout_batch_id,receiver,item_status,error_name from payments where run_id=%s order by obligation_id,attempt", (run_id,))})


# ------------------------------------------------------------------ actions
def _busy():
    row = db.one("select id from runs where status='running' and started_at > now() - interval '20 minutes' limit 1")
    if row:
        raise HTTPException(409, f"Run {row['id']} is still going. Wait for it to finish, then start another.")


@app.post("/api/seed")
def load_payouts():
    n = seed.seed_payouts()
    return {"added": n}


@app.post("/api/invoices/seed")
def load_invoices():
    ids = invoices.seed_invoices()
    return {"created": len(ids)}


@app.post("/api/runs/settle")
async def run_settle(request: Request):
    body = await request.json() if (await request.body()) else {}
    _busy()
    ids = [r["id"] for r in db.q("select id from obligations where status='queued' and attempts=0 order by id")]
    if not ids:
        raise HTTPException(400, "Nothing is queued. Load the sample payouts first.")
    crash = bool(body.get("crash"))
    rid = runs.create("settle", {"crash": crash, "obligations": ids})
    info = await flow.start("settle_run", rid, [ids, crash])
    return {"run_id": rid, **info}


@app.post("/api/runs/dunning")
async def run_dunning():
    _busy()
    if not db.one("select 1 from invoices limit 1"):
        raise HTTPException(400, "There are no invoices to chase. Load the sample invoices first.")
    rid = runs.create("dunning")
    info = await flow.start("dunning_sweep", rid, [])
    return {"run_id": rid, **info}


@app.post("/api/runs/nightly")
async def run_nightly():
    _busy()
    rid = runs.create("nightly")
    info = await flow.start("nightly", rid, [])
    return {"run_id": rid, **info}


@app.post("/api/invoices/{invoice_id}/payment")
async def record_payment(invoice_id: str, request: Request):
    body = await request.json()
    inv = db.one("select * from invoices where id=%s", (invoice_id,))
    if not inv:
        raise HTTPException(404, "No such invoice.")
    try:
        amount = Decimal(str(body.get("amount")))
    except Exception:
        raise HTTPException(400, "Enter an amount such as 50.00.")
    if amount <= 0 or amount > Decimal(inv["amount"]) - Decimal(inv["paypal_paid"]):
        raise HTTPException(400, "The amount must be above zero and no more than the balance.")
    try:
        client().record_payment(invoice_id, f"{amount:.2f}", inv["currency"], "Recorded from the Tailrace sandbox panel")
    except PayPalError as e:
        raise HTTPException(502, f"PayPal refused the payment: {e.issue or e.name}. Try a smaller amount.")
    return {"recorded": str(amount)}


@app.post("/api/escalations/{eid}/send")
async def escalation_send(eid: int, request: Request):
    body = await request.json()
    esc = db.one("select * from escalations where id=%s and status='open'", (eid,))
    if not esc or esc["subject_type"] != "payout":
        raise HTTPException(404, "That escalation is not open.")
    address = (body.get("address") or "").strip()
    ob = db.one("select * from obligations where id=%s", (esc["subject_id"],))
    if "@" not in address or "." not in address.split("@")[-1]:
        raise HTTPException(400, "Enter a full email address such as name@example.com.")
    if payouts.is_self_pay(address):
        raise HTTPException(400, "That is the sending account's own address. Use the payee's address.")
    if address.lower() == ob["receiver"].lower():
        raise HTTPException(400, "That is the address that already failed. Enter a different one.")
    _busy()
    db.x("update payees set verified_email=%s where id=%s", (address, ob["payee_id"]))
    rid = runs.create("resend", {"obligation": ob["id"], "address": address})
    info = await flow.start("resend_run", rid, [ob["id"], address])
    return {"run_id": rid, **info}


@app.post("/api/escalations/{eid}/close")
def escalation_close(eid: int):
    n = db.x("update escalations set status='resolved', resolved_at=now() where id=%s and status='open'", (eid,))
    if not n:
        raise HTTPException(404, "That escalation is not open.")
    return {"closed": eid}


@app.post("/api/reset")
def reset():
    _busy()
    db.wipe()
    return {"reset": True}


# ------------------------------------------------------------------ webhook
@app.post("/api/webhook/paypal")
async def paypal_webhook(request: Request, bg: BackgroundTasks):
    body = await request.body()
    ok, why = webhook.verify(dict(request.headers), body, settings.webhook_id)
    if not ok:
        return JSONResponse({"error": "signature rejected", "why": why}, status_code=401)
    try:
        evt = json.loads(body)
    except ValueError:
        return JSONResponse({"error": "body is not JSON"}, status_code=400)
    fresh = webhook.store(evt, True)
    if fresh:
        bg.add_task(_process_event, evt.get("id"))
    return Response(status_code=200)


async def _process_event(event_id: str):
    evt = db.one("select * from webhook_events where id=%s", (event_id,))
    if not evt or not webhook.is_ours({"resource": evt["resource"]}):
        db.x("update webhook_events set processed_at=now(), effect='not ours, ignored' where id=%s", (event_id,))
        return
    rid = runs.create("webhook", {"event": event_id, "type": evt["event_type"]})
    await flow.start("ingest_webhook", rid, [event_id])


# ------------------------------------------------------------------ static
app.mount("/assets", StaticFiles(directory=WEB), name="assets")


@app.get("/")
def index():
    return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-cache"})
