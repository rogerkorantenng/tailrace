"""The workflow. Each @app.task runs on its own Render instance; ctx.run() chains them and asyncio.gather fans out.

Steps hold no state in memory. Anything that must survive a retry is in Postgres, and everything that
spends money is idempotent (see payouts.py).
"""
import asyncio

from render import Retry, TaskContext, Workflows

from . import agent, chaos, db, invoices, payouts, reconcile, runs, webhook
from .trace import traced

app = Workflows(default_timeout=900)


def task(name: str, retries: int, wait_ms: int = 1500, subject=None, timeout: int = 600):
    """Register a traced task with Render's retry policy attached."""
    def deco(fn):
        wrapped = traced(name, retries, subject)(fn)
        return app.task(name=name, retry=Retry(max_retries=retries, wait_duration_ms=wait_ms, backoff_scaling=1.6),
                        timeout_seconds=timeout)(wrapped)
    return deco


def _thread(fn, *a):
    return asyncio.to_thread(fn, *a)


# ------------------------------------------------------------------ payout steps
@task("submit_batch", retries=3, subject=lambda a: a["currency"])
async def submit_batch(ctx: TaskContext, run_id: str, currency: str, obligation_ids: list[str], crash: bool = False) -> dict:
    rows = await _thread(payouts.claim_first_batch, run_id, currency, obligation_ids)
    if not rows:
        return {"payout_batch_id": None, "items": 0, "summary": "nothing eligible to send"}
    fire = chaos.crash_once(run_id, "submit_batch", crash)
    batch_id, created = await _thread(payouts.send_claimed, rows, payouts.batch_id_for(run_id, currency), fire)
    how = "created at PayPal" if created else "PayPal already had it, adopted"
    return {"payout_batch_id": batch_id, "items": len(rows), "created_now": created, "summary": f"{len(rows)} items, batch {batch_id}, {how}"}


@task("await_terminal", retries=4, wait_ms=3000, subject=lambda a: a["batch_id"], timeout=300)
async def await_terminal(ctx: TaskContext, run_id: str, batch_id: str) -> dict:
    snap = await payouts.wait_terminal(batch_id)
    problems = [i for i in snap["items"] if i["item_status"] != "SUCCESS"]
    landed = len(snap["items"]) - len(problems)
    return {"batch_id": batch_id, "batch_status": snap["batch_status"], "problems": problems,
            "summary": f"batch {snap['batch_status']}: {landed} landed, {len(problems)} did not"}


@task("triage_item", retries=2, wait_ms=2000, subject=lambda a: a["obligation_id"])
async def triage_item(ctx: TaskContext, run_id: str, obligation_id: str) -> dict:
    d = await _thread(agent.triage, run_id, obligation_id)
    return {**d, "summary": f"{d['action']}" + (f" to {d['address']}" if d.get("address") else "")}


@task("reissue_item", retries=3, subject=lambda a: a["obligation_id"])
async def reissue_item(ctx: TaskContext, run_id: str, obligation_id: str, target_attempt: int, address: str) -> dict:
    try:
        info = await _thread(payouts.reissue, obligation_id, target_attempt, address, run_id)
    except payouts.Refused as e:
        return {"refused": str(e), "payout_batch_id": None, "summary": f"refused: {e}"}
    how = "created" if info["created_now"] else "adopted the existing batch"
    return {**info, "summary": f"attempt {target_attempt} to {address}: {how}, batch {info['payout_batch_id']}"}


@task("apply_decision", retries=2, subject=lambda a: a["decision"]["obligation_id"])
async def apply_decision(ctx: TaskContext, run_id: str, decision: dict) -> dict:
    oid, action = decision["obligation_id"], decision["action"]
    result = {"obligation_id": oid, "action": action}
    if action in ("correct", "retry"):
        addr = decision.get("address") or (await _thread(db.one, "select receiver from obligations where id=%s", (oid,)))["receiver"]
        out = await ctx.run(reissue_item, run_id, oid, decision["target_attempt"], addr)
        if out.get("refused") or not out.get("payout_batch_id"):
            await _thread(_escalate, run_id, oid, out.get("refused") or "The reissue could not be sent.")
            result["outcome"] = "escalated"
        else:
            snap = await ctx.run(await_terminal, run_id, out["payout_batch_id"])
            ob = await _thread(db.one, "select status from obligations where id=%s", (oid,))
            if ob["status"] != "settled":
                await _thread(_escalate, run_id, oid, f"The reissue ended {ob['status']}. It will not be tried again automatically.")
                result["outcome"] = "escalated"
            else:
                result["outcome"] = "settled"
    elif action == "stop":
        await _thread(_stop, oid, decision["reasoning"])
        result["outcome"] = "stopped"
    else:
        await _thread(_escalate, run_id, oid, decision["reasoning"])
        result["outcome"] = "escalated"
    db.x("update decisions set result=%s where run_id=%s and obligation_id=%s", (result["outcome"], run_id, oid))
    return {**result, "summary": f"{action} -> {result['outcome']}"}


def _escalate(run_id, oid, reason):
    with db.tx() as c:
        c.execute("update obligations set status='escalated', outcome=%s, updated_at=now() where id=%s and status<>'settled'", (reason, oid))
        c.execute("insert into escalations(run_id,subject_type,subject_id,reason) select %s,'payout',%s,%s "
                  "where not exists (select 1 from escalations where subject_id=%s and status='open')", (run_id, oid, reason, oid))


def _stop(oid, reason):
    payouts.cancel_if_unclaimed(oid)
    db.x("update obligations set status='stopped', outcome=%s, updated_at=now() where id=%s and status<>'settled'", (reason, oid))


@task("reconcile_run", retries=2, subject=lambda a: "payouts")
async def reconcile_run(ctx: TaskContext, run_id: str, obligation_ids: list[str]) -> dict:
    rows = await _thread(reconcile.reconcile_payouts, run_id, obligation_ids)
    bad = [r for r in rows if r["verdict"] == "mismatch"]
    healed = [r for r in rows if r["verdict"] == "healed"]
    return {"checked": len(rows), "mismatch": len(bad), "healed": len(healed),
            "summary": f"{len(rows)} checked, {len(healed)} healed, {len(bad)} need a person"}


@task("payout_lane", retries=1, subject=lambda a: a["currency"], timeout=600)
async def payout_lane(ctx: TaskContext, run_id: str, currency: str, obligation_ids: list[str], crash: bool = False) -> dict:
    """One currency: send its batch, then wait for PayPal to say where every item ended up."""
    sub = await ctx.run(submit_batch, run_id, currency, obligation_ids, crash)
    if not sub["payout_batch_id"]:
        return {"items": 0, "problems": [], "summary": "nothing to send"}
    term = await ctx.run(await_terminal, run_id, sub["payout_batch_id"])
    return {"items": sub["items"], "problems": term["problems"], "payout_batch_id": sub["payout_batch_id"],
            "summary": f"{currency}: {sub['items']} sent, {len(term['problems'])} did not land"}


@task("settle_run", retries=1, subject=lambda a: f"{len(a['obligation_ids'])} payouts", timeout=1200)
async def settle_run(ctx: TaskContext, run_id: str, obligation_ids: list[str], crash: bool = False, finalize: bool = True) -> dict:
    rows = await _thread(db.q, "select id,currency from obligations where id = any(%s)", (obligation_ids,))
    lanes: dict[str, list[str]] = {}
    for r in rows:
        lanes.setdefault(r["currency"], []).append(r["id"])
    done = await asyncio.gather(*[ctx.run(payout_lane, run_id, cur, ids, crash) for cur, ids in sorted(lanes.items())])
    problems = [p for d in done for p in d["problems"]]
    sent = sum(d["items"] for d in done)
    summary = {"sent": sent, "landed": sent - len(problems), "triaged": 0, "failed_steps": 0}
    if problems:
        decisions = await asyncio.gather(*[ctx.run(triage_item, run_id, p["obligation_id"]) for p in problems], return_exceptions=True)
        good = [d for d in decisions if not isinstance(d, Exception)]
        summary["failed_steps"] += len(decisions) - len(good)
        summary["triaged"] = len(good)
        applied = await asyncio.gather(*[ctx.run(apply_decision, run_id, d) for d in good], return_exceptions=True)
        summary["failed_steps"] += sum(isinstance(a, Exception) for a in applied)
        summary["outcomes"] = [a["outcome"] for a in applied if not isinstance(a, Exception)]
        for p, d in zip(problems, decisions):
            if isinstance(d, Exception):
                await _thread(_escalate, run_id, p["obligation_id"], "The triage step failed after its retries.")
    if sent:
        await ctx.run(reconcile_run, run_id, obligation_ids)
    if finalize:
        runs.finish(run_id, summary)
    return {**summary, "summary": f"{summary['landed']} landed first time, {summary['triaged']} triaged"}


# ------------------------------------------------------------------ dunning steps
@task("sync_invoices", retries=3, subject=lambda a: "all invoices")
async def sync_invoices(ctx: TaskContext, run_id: str) -> dict:
    ids = [r["id"] for r in await _thread(db.q, "select id from invoices order by due_date")]
    for i in ids:
        await _thread(invoices.sync, i)
    return {"ids": ids, "summary": f"{len(ids)} invoices read from PayPal"}


@task("chase_invoice", retries=3, subject=lambda a: a["invoice_id"])
async def chase_invoice(ctx: TaskContext, run_id: str, invoice_id: str) -> dict:
    out = await _thread(invoices.chase, run_id, invoice_id)
    return {**out, "summary": out["action"] + (f" (stage {out['stage']})" if out.get("stage") else "") + (f": {out['why']}" if out.get("why") else "")}


@task("reconcile_invoices", retries=2, subject=lambda a: "invoices")
async def reconcile_invoices(ctx: TaskContext, run_id: str) -> dict:
    rows = await _thread(reconcile.reconcile_invoices, run_id)
    healed = sum(r["verdict"] == "healed" for r in rows)
    return {"checked": len(rows), "healed": healed, "summary": f"{len(rows)} checked, {healed} healed"}


@task("dunning_sweep", retries=1, subject=lambda a: "overdue invoices", timeout=900)
async def dunning_sweep(ctx: TaskContext, run_id: str, finalize: bool = True) -> dict:
    synced = await ctx.run(sync_invoices, run_id)
    results = await asyncio.gather(*[ctx.run(chase_invoice, run_id, i) for i in synced["ids"]], return_exceptions=True)
    ok = [r for r in results if not isinstance(r, Exception)]
    reminded = sum(r["action"] == "reminded" for r in ok)
    await ctx.run(reconcile_invoices, run_id)
    summary = {"invoices": len(synced["ids"]), "reminded": reminded, "failed_steps": len(results) - len(ok)}
    if finalize:
        runs.finish(run_id, summary)
    return {**summary, "summary": f"{reminded} reminders sent across {len(synced['ids'])} invoices"}


# ------------------------------------------------------------------ nightly + webhook
@task("nightly", retries=0, subject=lambda a: "settle + dunning", timeout=1800)
async def nightly(ctx: TaskContext, run_id: str) -> dict:
    queued = [r["id"] for r in await _thread(db.q, "select id from obligations where status='queued' and attempts=0 order by id")]
    settle, dun = await asyncio.gather(ctx.run(settle_run, run_id, queued, False, False), ctx.run(dunning_sweep, run_id, False),
                                       return_exceptions=True)
    summary = {"settle": settle if not isinstance(settle, Exception) else {"error": str(settle)},
               "dunning": dun if not isinstance(dun, Exception) else {"error": str(dun)}}
    runs.finish(run_id, summary, "done" if not any(isinstance(r, Exception) for r in (settle, dun)) else "needs_attention")
    return {"summary": "settlement and dunning finished"}


@task("ingest_webhook", retries=3, subject=lambda a: a["event_id"])
async def ingest_webhook(ctx: TaskContext, run_id: str, event_id: str) -> dict:
    effect = await _thread(webhook.apply_event, event_id)
    runs.finish(run_id, {"effect": effect})
    return {"effect": effect, "summary": effect}


# ------------------------------------------------------------------ human follow-up
@task("resend_run", retries=1, subject=lambda a: a["obligation_id"], timeout=900)
async def resend_run(ctx: TaskContext, run_id: str, obligation_id: str, address: str) -> dict:
    """A person supplied a new address for an escalated payout. Cancel what is held, pay once, wait, reconcile."""
    ob = await _thread(db.one, "select attempts from obligations where id=%s", (obligation_id,))
    out = await ctx.run(reissue_item, run_id, obligation_id, ob["attempts"] + 1, address)
    if out.get("payout_batch_id"):
        await ctx.run(await_terminal, run_id, out["payout_batch_id"])
    await ctx.run(reconcile_run, run_id, [obligation_id])
    final = await _thread(db.one, "select status from obligations where id=%s", (obligation_id,))
    runs.finish(run_id, {"obligation": obligation_id, "status": final["status"], "refused": out.get("refused")})
    return {"status": final["status"], "summary": f"{obligation_id} is now {final['status']}"}
