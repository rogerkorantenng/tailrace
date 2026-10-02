"""Step recording. Every task run writes its own start, retry and finish rows to Postgres, so the UI can draw
the tree without needing Render's API key. Each row also carries Render's task-run ids."""
import functools
import inspect

from . import db


def _ids(ctx):
    md = getattr(ctx, "metadata", None)
    if md is None:
        return None, None
    return md.task_run_id, md.parent_task_run_id


def event(run_id, task, subject, ev, attempt, detail="", rid=None, parent=None):
    db.x("insert into steps(run_id,task,subject,event,attempt,detail,render_run_id,parent_render_run_id) values(%s,%s,%s,%s,%s,%s,%s,%s)",
         (run_id, task, subject, ev, attempt, (detail or "")[:400], rid, parent))


def traced(name: str, retries: int, subject=None):
    """Wrap an async task body so its life is recorded. `subject` maps the bound arguments to a short label."""
    def deco(fn):
        sig = inspect.signature(fn)

        @functools.wraps(fn)
        async def wrapper(ctx, *args, **kw):
            bound = sig.bind(ctx, *args, **kw)
            bound.apply_defaults()
            a = bound.arguments
            run_id = a["run_id"]
            subj = subject(a) if subject else ""
            rid, parent = _ids(ctx)
            prior = db.one("select count(*) n from steps where run_id=%s and task=%s and subject=%s and event='started'", (run_id, name, subj))
            attempt = prior["n"] + 1
            event(run_id, name, subj, "started", attempt, rid=rid, parent=parent)
            try:
                result = await fn(ctx, *args, **kw)
            except Exception as e:
                last = attempt > retries
                event(run_id, name, subj, "failed" if last else "retrying", attempt, f"{type(e).__name__}: {e}", rid, parent)
                raise
            detail = result.get("summary", "") if isinstance(result, dict) else ""
            event(run_id, name, subj, "succeeded", attempt, detail, rid, parent)
            return result
        wrapper.__trace_name__ = name
        return wrapper
    return deco
