"""Run by the Render Cron Job. Workflows have no scheduler of their own, so this starts the nightly task."""
import asyncio

from tailrace import db, flow, runs


async def main():
    db.init_schema()
    rid = runs.create("nightly", {"by": "cron"})
    info = await flow.start("nightly", rid, [])
    print(f"started nightly run {rid} via {info['executor']} ({info['render_run_id']})")
    if info["executor"] != "inline":
        # keep the process alive until the run finishes so the cron job's own log shows the outcome
        import time
        for _ in range(900):
            row = db.one("select status from runs where id=%s", (rid,))
            if row["status"] != "running":
                print("run finished:", row["status"])
                return
            await asyncio.sleep(2)


asyncio.run(main())
