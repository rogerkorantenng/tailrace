"""Starting workflow runs. On Render this calls the Workflows API. With no Render connection at all it runs the
same task bodies in this process, and the UI says so; it is for tests and for an offline look, not for production."""
import asyncio
import uuid
from types import SimpleNamespace

from . import runs
from .config import settings

_background: set = set()


def mode() -> str:
    if settings.local_dev:
        return "render-local"
    if settings.render_key:
        return "render"
    return "inline"


class InlineCtx:
    """Stands in for Render's TaskContext. ctx.run awaits the task body directly."""

    def __init__(self, parent_id=None):
        self.metadata = SimpleNamespace(task_run_id="inl-" + uuid.uuid4().hex[:8], parent_task_run_id=parent_id, root_task_run_id=None)

    async def run(self, task, *args, **kwargs):
        """Same contract as Render: the task is retried on failure with the task's own backoff settings."""
        from .tasks import app
        info = app._registry.get_task(task.name)
        retry = info.options.retry if info and info.options else None
        tries = (retry.max_retries if retry else 0) + 1
        for i in range(tries):
            try:
                return await task.func(InlineCtx(self.metadata.task_run_id), *args, **kwargs)
            except Exception:
                if i == tries - 1:
                    raise
                await asyncio.sleep(retry.wait_duration_ms / 1000 * retry.backoff_scaling ** i)


def _keep(t: asyncio.Task):
    _background.add(t)
    t.add_done_callback(_background.discard)


async def start(task_name: str, run_id: str, args: list) -> dict:
    """Kick off a root task. Returns at once; the run's progress is read from Postgres."""
    m = mode()
    if m == "inline":
        from .tasks import app  # noqa: F401  (imports register every task)
        from . import tasks
        task = getattr(tasks, task_name)

        async def go():
            try:
                await task.func(InlineCtx(), run_id, *args)
            except Exception as e:
                runs.fail(run_id, f"{type(e).__name__}: {e}")
        _keep(asyncio.create_task(go()))
        return {"executor": "inline", "render_run_id": None}
    from render import RenderAsync
    render = RenderAsync()
    started = await render.workflows.start_task(f"{settings.workflow_slug}/{task_name}", [run_id, *args])

    async def watch():
        try:
            await started
        except Exception as e:
            runs.fail(run_id, f"Render task run failed: {e}")
    _keep(asyncio.create_task(watch()))
    from . import db
    db.x("update runs set render_run_id=%s where id=%s", (started.id, run_id))
    return {"executor": m, "render_run_id": started.id}
