"""Fault injection for proving retries are safe. Fires once per run, then never again for that run."""
from . import db
from .payouts import Refused  # noqa: F401  (re-exported for tests)


class InjectedCrash(RuntimeError):
    pass


def crash_once(run_id: str, point: str, enabled: bool):
    """Returns a callable that raises InjectedCrash the first time it is called for (run, point)."""
    def fire():
        if not enabled:
            return
        first = db.x("insert into chaos_fired(key) values(%s) on conflict do nothing", (f"{run_id}:{point}",))
        if first:
            raise InjectedCrash(f"injected crash at {point}: PayPal accepted the batch, the database write never happened")
    return fire
