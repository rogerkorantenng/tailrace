# Tailrace

A payout reconciliation and dunning pipeline for a marketplace that pays sellers through PayPal. It runs as a **Render Workflow**: PayPal activity goes in, a chain of independently retried tasks works through it, and an agent decides what to do about every payout that did not land.

Built for the PayPal AI Hackathon, prize "Best Use of Render". Everything talks to the PayPal **sandbox**. No real money moves.

## What it does

1. **Sends a run of payouts.** Queued payouts are grouped by currency (PayPal allows one currency per batch) and each batch goes out in parallel.
2. **Does not believe the 201.** A task polls each batch until PayPal reports a final state for every item.
3. **Triages what did not land.** Each failed or unclaimed item gets its own agent run. The agent reads PayPal's real error name, looks up the payee, and proposes `retry`, `correct`, `escalate` or `stop`. A rules gate checks the proposal and sends reasons back if it refuses.
4. **Fixes what is safe.** `correct` cancels the held item, confirms PayPal returned the money, then pays once to the payee's verified address.
5. **Chases ageing invoices.** A dunning sweep reads every invoice from PayPal, then sends one reminder per overdue stage (friendly, firm, final). The final stage opens a note for a person.
6. **Reconciles.** Each payout and invoice is compared with what PayPal reports. Drift the ledger can safely absorb is corrected and labelled. Anything else goes to a person.
7. **Takes webhooks.** Signed PayPal events are verified, stored, answered with 200, then processed by a workflow task.

## Where Render comes in

| Render feature | How it is used |
|---|---|
| **Workflows** | 15 tasks in `tailrace/tasks.py`. `ctx.run()` chains them, `asyncio.gather` fans out one lane per currency and one triage per failed item, each task has its own `Retry` policy and timeout. A step that dies is retried alone, not the whole run. |
| **Cron Job** | Workflows have no scheduler (Render's docs say so), so `tailrace-nightly` runs `trigger_nightly.py` at 03:00 and starts the `nightly` task: settlement and dunning in parallel, then reconciliation. |
| **Web Service** | The UI, the JSON API and the PayPal webhook listener (`tailrace/api.py`). Starts workflow runs through the Render SDK. |
| **Postgres** | The ledger, the idempotency claims, every step event, every agent turn. Tasks run on separate instances, so nothing lives in memory. |
| **Blueprint** | `render.yaml` defines all four. Secrets are `sync: false`, so Render asks for them at deploy time. |

Not used: Background Worker and Key Value. Nothing here needed a long-running consumer or a cache, and adding them would be decoration.

### The pipeline is visible in the product

The Pipeline panel draws the task tree for the selected run: state, attempt count, the error from a failed attempt, and a timeline bar. These rows are written by each task as it runs (`tailrace/trace.py`), with Render's task-run id stored alongside when Render provides one. The UI does not need a Render API key to draw them.

```
settle_run
 |- payout_lane USD ---- submit_batch -> await_terminal
 |- payout_lane GBP ---- submit_batch (attempt 2, adopted PayPal's batch) -> await_terminal
 |- triage_item x N        (parallel, one agent loop each)
 |- apply_decision x N     (parallel)
 |     '- reissue_item -> await_terminal
 '- reconcile_run
nightly = settle_run || dunning_sweep (sync_invoices -> chase_invoice x N -> reconcile_invoices)
```

## Why a retry cannot pay twice

This is the part that matters most for a pipeline that retries by design. Four layers, outermost first:

1. An obligation can only be claimed from `queued`, once. A second run finds nothing to send.
2. A `payments` row is inserted **before** PayPal is called, with `unique(obligation_id, attempt)`.
3. `sender_batch_id` is derived from the run id and currency (`set-ab12cd-usd`) or, for reissues, from the obligation and attempt (`reissue-po-1003-a2`). It is also sent as `PayPal-Request-Id`.
4. PayPal answers a repeated `sender_batch_id` with a 400 and a link to the first batch, **even when the request id matches**. The client parses that link and adopts the existing batch. The "duplicate" is treated as success.

A reissue also refuses to run while the earlier item could still be live, and cancelling is confirmed by reading the item back, so a repeated cancel is harmless. Reminders are at-most-once: a reminder that died between claim and confirmation is marked `unconfirmed` and not resent, because a missed reminder costs less than a duplicate.

The crash checkbox in the UI proves it live. It makes `submit_batch` throw **after** PayPal accepts the batch and **before** the database learns the batch id. Render retries the step; the step shows `attempt 2 ... PayPal already had it, adopted`; each payee is paid once.

## The agent

`tailrace/agent.py` gives the model five tools and lets it loop: `read_failed_item`, `read_payee`, `error_guide`, `check_action`, `decide`. The rules in `tailrace/rules.py` decide whether a proposal runs: only the directory's verified address may be used, a retry is refused for non-transient errors, self-pay errors cannot be "corrected" around, and after three attempts only `escalate` or `stop` are allowed. A refusal goes back to the model as a list of reasons.

Providers, in order: Anthropic API (`ANTHROPIC_API_KEY`), Amazon Bedrock (AWS credentials), then a scripted policy that walks the same tools. The UI names whichever one decided. If the model never reaches an accepted decision in 8 turns, the item is escalated.

## PayPal surface used

Payouts (create, poll, per-item cancel), Invoicing v2 (create, send, remind, record payment, read), Webhooks (offline signature verification, registration script). Direct REST, no SDK. The six sample payouts produce six different sandbox outcomes: `SUCCESS` in USD, `SUCCESS` in GBP, `UNCLAIMED / RECEIVER_UNREGISTERED`, `UNCLAIMED / RECEIVER_UNCONFIRMED`, `FAILED / SELF_PAY_NOT_ALLOWED` and `FAILED / RECEIVER_ACCOUNT_INVALID`. None is mocked.

## Deploy on Render

1. Put this folder in a Git repository on GitHub, GitLab or Bitbucket.
2. Render Dashboard, **New > Blueprint**, pick the repository. Render reads `render.yaml`.
3. Fill the prompts: `PAYPAL_CLIENT_ID`, `PAYPAL_SECRET`, `SENDER_EMAIL`, `RENDER_API_KEY` (Account Settings > API Keys), and optionally `ANTHROPIC_API_KEY`.
4. When the web service is live: `python scripts/register_webhook.py https://<your-web-url>`, then put the printed id in `PAYPAL_WEBHOOK_ID` on `tailrace-web`.
5. Check the workflow's slug in the dashboard. If it is not `tailrace-flow`, set `WORKFLOW_SLUG` on the web service and the cron job to match.

Credits: Render's free Postgres and web tiers cover the demo. The cron job is the only paid piece (starter plan). The Bedrock path needs AWS keys as environment variables; the Anthropic path needs one key.

## Run it locally

```
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements-dev.txt
docker run -d --name tailrace-pg -e POSTGRES_PASSWORD=pg -e POSTGRES_DB=payrail -p 5439:5432 postgres:16
render workflows dev -- python workflow_main.py                 # Render CLI 2.12+, local task server on :8120
RENDER_USE_LOCAL_DEV=true python -m uvicorn tailrace.api:app --port 8010
python -m pytest                    # 51 offline tests
python -m pytest -m live -s         # 3 tests against the PayPal sandbox
```

PayPal keys are read from the environment, or from `../../.env` when running here. Nothing is committed.

## Accessibility

Contrast is computed from the hex values in `web/style.css` by `scripts/contrast.py`. Text needs 4.5:1; interface borders and focus rings need 3:1.

| Pair | Dark | Light | Needs |
|---|---|---|---|
| body text | 15.89:1 | 15.39:1 | 4.5:1 |
| body text on panel | 14.64:1 | 17.09:1 | 4.5:1 |
| muted text on page | 8.50:1 | 6.68:1 | 4.5:1 |
| muted text on panel | 7.83:1 | 7.42:1 | 4.5:1 |
| muted text on inset | 7.01:1 | 5.99:1 | 4.5:1 |
| link / live state on panel | 13.25:1 | 9.24:1 | 4.5:1 |
| done state | 10.44:1 | 6.46:1 | 4.5:1 |
| retry / held state | 10.19:1 | 6.59:1 | 4.5:1 |
| failed state | 7.43:1 | 7.01:1 | 4.5:1 |
| reconciled-ledger state | 9.11:1 | 8.12:1 | 4.5:1 |
| primary button label | 14.39:1 | 14.39:1 | 4.5:1 |
| button text on inset | 13.10:1 | 13.81:1 | 4.5:1 |
| control border vs panel (non-text) | 3.50:1 | 4.07:1 | 3.0:1 |
| focus ring vs panel (non-text) | 13.25:1 | 17.09:1 | 3.0:1 |
| progress bar, live vs track (non-text) | 11.86:1 | 7.46:1 | 3.0:1 |

Other measures, all checked by `scripts/ui_test.mjs`: body text is 17.5px (13pt), the smallest text is 13.4px (10pt); every button and input is at least 38px (28pt) square; text at 200% does not scroll sideways at 360px; every state is an icon plus a word, never colour alone; focus is a 3px outline; reduced-motion turns the spinner off.

## Limits worth knowing

- The webhook listener needs a public URL, so it cannot be registered with PayPal until the service is deployed. Signature verification is tested with a generated key pair, not with a live PayPal delivery.
- The PayPal certificate chain is not validated in the webhook check; the certificate must come from an `https://*.paypal.com` address and the signature must match. PayPal's own verify endpoint would add the chain check.
- Anyone who opens the demo can start sandbox payouts. A run in progress blocks a second one.
- GBP and USD both settled in testing. PayPal may change sandbox balances.

MIT licensed. See `LICENSE`.
