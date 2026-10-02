# BUILD-LOG: Tailrace

Project 8 of 10, PayPal AI Hackathon. Prize target: Best Use of Render.

## 2026-10-02 session 1

- Read Render Workflows docs (workflows, defining, running, limits, local dev, Python SDK 1.2.0, blueprint spec).
- Findings that shaped the design:
  - Workflows have **no native scheduler**. Render's own FAQ says to use a cron job to trigger runs. So: Cron Job service -> `nightly` task.
  - Task runs expose no ports and each chained run gets its own instance, so step state has to live in Postgres, not memory.
  - Retries re-execute the whole function with the same args. Anything that pays money has to be idempotent on its own.
  - Python SDK: `Workflows()`, `@app.task(retry=Retry(...))`, `ctx.run()` chains, `asyncio.gather` fans out. `ctx.metadata` gives task_run_id / parent id, which is how the UI draws the tree.
  - Local task server: `render workflows dev -- python workflow_main.py` (CLI 2.28.0 installed to ~/.local/bin/render).
- No Render account or API key on this machine. Nothing in the environment, no `~/.render`. Deploy cannot be done from here. Everything is built so that a Blueprint deploy is one click, and verified against the local task server, which is the real SDK and the real task lifecycle.
- No ANTHROPIC_API_KEY on this machine. AWS root creds for Bedrock are present locally, so the agent runs on Bedrock here. The agent has three providers: Anthropic direct, Bedrock, and a rule-based fallback labelled as such in the UI.
- PayPal sandbox probes (live, $1 each):
  - sb-patient@personal.example.com -> SUCCESS (a registered account; this is the "corrected address")
  - sb-buyer@personal.example.com -> UNCLAIMED / RECEIVER_UNCONFIRMED
  - sb-nobody-*@personal.example.com -> UNCLAIMED / RECEIVER_UNREGISTERED
  - the sending business account itself -> batch DENIED, item FAILED / SELF_PAY_NOT_ALLOWED
  - Repeating a sender_batch_id returns 400 USER_BUSINESS_ERROR with a link to the existing batch, **even with the same PayPal-Request-Id**. So "duplicate = success" means parsing that link and adopting the batch.
  - Invoices can be created with past due dates, sent, and reminded (204).

## Build

- Python 3.14 / FastAPI / psycopg / Render SDK. UI is plain HTML, CSS and ES modules, no build step.
- First live run through the inline stand-in found PayPal refuses mixed currencies in one batch ("Multiple currencies within a batch is not allowed"). Redesigned: one `payout_lane` per currency, in parallel.
- Bedrock throttled four parallel triage agents. Fixed with adaptive retries in the client, and the inline stand-in now honours each task's Render retry policy so the behaviour matches.
- Local Render task server (`render workflows dev`) sends no run ids to the SDK context, so the step tree falls back to the workflow's fixed shape when ids are missing. The server log still shows the real parent links.
- First fake PayPal replaced the very code under test (it deduplicated by itself), so two mutations survived. Rewrote it to replace only the HTTP layer; mutations A, B and C are now caught.

## Design rounds (shots/r1 .. r5, each state at 360 / 768 / 1280 / 1920, dark and light)

| Round | Findings | Score |
|---|---|---|
| r1 | Works end to end. Step tree flat (no run ids locally). `US$` prefix on dollars. Agent chip wraps to three lines on a phone. | 7 |
| r2 | Tree nests correctly. Light-mode live bar failed contrast (1.0:1). Disabled primary buttons too faint. | 8 |
| r3 | Contrast fixed (7.5:1). Mono text 13.25px, under the 10pt floor. 200% text scrolled sideways at 360px. | 8.5 |
| r4 | Floor fixed. Overflow came from run pills, unbreakable ids, and grid tracks that grew to content. Empty Pipeline strip in the empty state. | 8.5 |
| r5 | All seven browser checks pass. Desktop 9. Phone 8.5: the pipeline is long when twenty steps are open and there is no way to fold it. | **9 desktop, 8.5 phone** |

Not done: folding the step list on phones. A late `.run-meta:empty` rule is untested by a fresh shoot.

## Not done, and why
- No Render deployment. There is no Render account or API key on this machine, and signing one up is not something I did on your behalf. `render.yaml` is written to the spec, the workflow ran on Render's own local task server with the real SDK, and README has the five steps to deploy.
- No webhook registered with PayPal. It needs the public URL.
- 5 of 10 PayPal webhook slots are untouched.
