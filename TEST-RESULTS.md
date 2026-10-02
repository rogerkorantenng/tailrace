# TEST-RESULTS

All output below was pasted from real runs on 2026-10-02. Python 3.14.3, Postgres 16 in Docker, Render CLI 2.28.0 local task server, Render SDK 1.2.0, PayPal sandbox.

## 1. Offline suite (`python -m pytest -o addopts= -m 'not live' -v`)
```
tests/test_agent.py::test_policy_decides_each_sandbox_failure_correctly[po-1003-correct-sb-patient@personal.example.com] PASSED
tests/test_agent.py::test_policy_decides_each_sandbox_failure_correctly[po-1004-escalate-None] PASSED
tests/test_agent.py::test_policy_decides_each_sandbox_failure_correctly[po-1005-stop-None] PASSED
tests/test_agent.py::test_policy_decides_each_sandbox_failure_correctly[po-1006-correct-sb-patient@personal.example.com] PASSED
tests/test_agent.py::test_gate_refusal_goes_back_to_the_agent_and_it_changes_course PASSED
tests/test_agent.py::test_an_agent_that_never_decides_falls_back_to_escalate PASSED
tests/test_agent.py::test_an_unsafe_retry_is_refused_even_if_the_agent_insists PASSED
tests/test_agent.py::test_tools_return_what_the_ledger_holds PASSED
tests/test_dunning.py::test_ladder_boundaries[-5-0] PASSED
tests/test_dunning.py::test_ladder_boundaries[0-0] PASSED
tests/test_dunning.py::test_ladder_boundaries[1-1] PASSED
tests/test_dunning.py::test_ladder_boundaries[6-1] PASSED
tests/test_dunning.py::test_ladder_boundaries[7-2] PASSED
tests/test_dunning.py::test_ladder_boundaries[13-2] PASSED
tests/test_dunning.py::test_ladder_boundaries[14-3] PASSED
tests/test_dunning.py::test_ladder_boundaries[40-3] PASSED
tests/test_dunning.py::test_each_stage_is_sent_once_however_often_the_step_runs PASSED
tests/test_dunning.py::test_a_paid_invoice_is_never_chased PASSED
tests/test_dunning.py::test_a_reminder_that_died_midway_is_not_sent_twice PASSED
tests/test_dunning.py::test_a_part_payment_lowers_the_balance_named_in_the_reminder PASSED
tests/test_dunning.py::test_final_stage_raises_an_escalation PASSED
tests/test_dunning.py::test_reconciliation_heals_a_ledger_that_missed_a_payment PASSED
tests/test_dunning.py::test_payout_reconciliation_flags_an_amount_that_differs PASSED
tests/test_dunning.py::test_payout_reconciliation_heals_a_ledger_behind_paypal PASSED
tests/test_idempotency.py::test_replayed_submit_step_pays_once PASSED
tests/test_idempotency.py::test_crash_after_paypal_accepts_then_retry_adopts_the_batch PASSED
tests/test_idempotency.py::test_crash_between_claim_and_send_then_retry_sends_once PASSED
tests/test_idempotency.py::test_a_second_run_cannot_pay_obligations_already_sent PASSED
tests/test_idempotency.py::test_two_workers_claiming_at_once_produce_one_batch PASSED
tests/test_idempotency.py::test_reissue_replay_pays_once_and_cancels_once PASSED
tests/test_idempotency.py::test_reissue_crash_after_paypal_accepts_then_retry PASSED
tests/test_idempotency.py::test_reissue_refuses_while_the_earlier_item_could_still_be_live PASSED
tests/test_idempotency.py::test_whole_workflow_with_a_crash_pays_each_obligation_at_most_once PASSED
tests/test_rules.py::test_correct_to_the_verified_address_passes PASSED
tests/test_rules.py::test_correct_to_an_address_not_in_the_directory_is_refused PASSED
tests/test_rules.py::test_correct_with_no_verified_address_on_file_is_refused PASSED
tests/test_rules.py::test_correct_to_the_address_that_just_failed_is_refused PASSED
tests/test_rules.py::test_retry_only_for_transient_errors PASSED
tests/test_rules.py::test_self_pay_cannot_be_corrected_around PASSED
tests/test_rules.py::test_after_three_attempts_only_escalate_or_stop PASSED
tests/test_rules.py::test_reasoning_is_required PASSED
tests/test_rules.py::test_unknown_action_is_refused PASSED
tests/test_webhook.py::test_valid_signature_is_accepted PASSED
tests/test_webhook.py::test_tampered_body_is_rejected PASSED
tests/test_webhook.py::test_signature_made_for_a_different_webhook_id_is_rejected PASSED
tests/test_webhook.py::test_signature_from_the_wrong_key_is_rejected PASSED
tests/test_webhook.py::test_certificate_from_outside_paypal_is_never_fetched PASSED
tests/test_webhook.py::test_missing_headers_and_missing_webhook_id_are_rejected PASSED
tests/test_webhook.py::test_endpoint_returns_401_for_a_tampered_payload_and_stores_nothing PASSED
tests/test_webhook.py::test_endpoint_returns_200_and_ignores_a_valid_event_that_is_not_ours PASSED
tests/test_webhook.py::test_a_repeated_delivery_is_acknowledged_and_not_processed_twice PASSED
======================= 51 passed, 3 deselected in 7.88s =======================
```

The webhook tests build an RSA key pair and certificate, sign a body the way PayPal does, and show a valid signature accepted, a changed body refused (`401` at the endpoint, nothing stored), a signature made for another webhook id refused, a wrong key refused, and a certificate URL outside paypal.com refused without being fetched.

## 2. Live PayPal sandbox (`python -m pytest -m live -s -v`)
```
 test session starts ==============================
platform linux -- Python 3.14.3, pytest-9.1.1, pluggy-1.6.0
rootdir: /home/rogerkorantenng/dev/Hackathons/paypal/projects/render
configfile: pytest.ini
testpaths: tests
plugins: anyio-4.15.1, asyncio-1.4.0
asyncio: mode=Mode.STRICT, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collected 54 items / 51 deselected / 3 selected
tests/test_live_sandbox.py first : 1 items, batch FLJ26UNYGZPE2, created at PayPal
replay: 1 items, batch FLJ26UNYGZPE2, PayPal already had it, adopted
status right after the 201: PENDING -> ['PENDING']
terminal: SUCCESS [{'obligation_id': 'po-1001', 'attempt': 1, 'item_status': 'SUCCESS', 'error_name': None, 'payout_item_id': '23W3XTXAB65EE'}]
.first: R2E23RCTVG5UG True  repeat: R2E23RCTVG5UG False
.po-1001 ('SUCCESS', None)
po-1002 ('SUCCESS', None)
po-1003 ('UNCLAIMED', 'RECEIVER_UNREGISTERED')
po-1004 ('UNCLAIMED', 'RECEIVER_UNCONFIRMED')
po-1005 ('FAILED', 'SELF_PAY_NOT_ALLOWED')
po-1006 ('FAILED', 'RECEIVER_ACCOUNT_INVALID')
.
 3 passed, 51 deselected in 103.91s (0:01:43) =================
```

The first test replays `submit_batch` with identical arguments: one batch id, one item at PayPal. The PayPal status right after the 201 is `PENDING`; only polling reached `SUCCESS`.

## 3. Does the replay test actually bite? Mutation checks
Each line breaks one safeguard, runs the nine idempotency tests, then restores the file.
```
### MUTATION A: stop treating PayPal's duplicate refusal as success
FAILED tests/test_idempotency.py::test_crash_after_paypal_accepts_then_retry_adopts_the_batch
FAILED tests/test_idempotency.py::test_two_workers_claiming_at_once_produce_one_batch
FAILED tests/test_idempotency.py::test_reissue_crash_after_paypal_accepts_then_retry
FAILED tests/test_idempotency.py::test_whole_workflow_with_a_crash_pays_each_obligation_at_most_once
4 failed, 5 passed in 18.43s
### MUTATION B: forget the stored batch id (always call PayPal again)
FAILED tests/test_idempotency.py::test_replayed_submit_step_pays_once - Asser...
1 failed, 8 passed in 2.95s
### MUTATION C: reissue without confirming the old item is dead
FAILED tests/test_idempotency.py::test_reissue_refuses_while_the_earlier_item_could_still_be_live
1 failed, 8 passed in 2.84s
### MUTATION D: claim without the unique (obligation, attempt) row and without the queued check
9 passed in 2.87s
### RESTORED
9 passed in 2.87s
```
Mutation D survives on purpose: removing only the `queued` check still leaves the `unique(obligation_id, attempt)` constraint, so a second run claims nothing. That is two layers covering the same hole.

## 4. A crashed step through the real Render task server
The UI's crash box was ticked. `submit_batch USD` threw after PayPal accepted the batch. Render retried it as attempt 2, which adopted PayPal's batch. Postgres afterwards (`psql`):
```
     task     | subject |   event   | attempt |                                                   detail                                                    
--------------+---------+-----------+---------+-------------------------------------------------------------------------------------------------------------
 submit_batch | USD     | started   |       1 | 
 submit_batch | GBP     | started   |       1 | 
 submit_batch | USD     | retrying  |       1 | InjectedCrash: injected crash at submit_batch: PayPal accepted the batch, the database write never happened
 submit_batch | GBP     | succeeded |       1 | 3 items, batch S437YZNJHFUX6, created at PayPal
 submit_batch | USD     | started   |       2 | 
 submit_batch | USD     | succeeded |       2 | 3 items, batch HN3U43Z8MC6MJ, PayPal already had it, adopted
(6 rows)

 obligation_id | attempt |  sender_batch_id   | payout_batch_id | item_status |        error_name        
---------------+---------+--------------------+-----------------+-------------+--------------------------
 po-1001       |       1 | set-22a475-usd     | HN3U43Z8MC6MJ   | SUCCESS     | 
 po-1002       |       1 | set-22a475-gbp     | S437YZNJHFUX6   | SUCCESS     | 
 po-1003       |       1 | set-22a475-gbp     | S437YZNJHFUX6   | RETURNED    | RECEIVER_UNREGISTERED
 po-1003       |       2 | reissue-po-1003-a2 | XCW8MVCRTQ73L   | SUCCESS     | 
 po-1004       |       1 | set-22a475-usd     | HN3U43Z8MC6MJ   | RETURNED    | RECEIVER_UNCONFIRMED
 po-1004       |       2 | reissue-po-1004-a2 | BXFP7YQDAZZEA   | SUCCESS     | 
 po-1005       |       1 | set-22a475-usd     | HN3U43Z8MC6MJ   | FAILED      | SELF_PAY_NOT_ALLOWED
 po-1006       |       1 | set-22a475-gbp     | S437YZNJHFUX6   | FAILED      | RECEIVER_ACCOUNT_INVALID
 po-1006       |       2 | reissue-po-1006-a2 | 27F4T6D92L6AA   | SUCCESS     | 
(9 rows)

 batches_at_paypal | payment_rows 
-------------------+--------------
                 5 |            9
(1 row)

```
Every obligation has exactly one first attempt row and, where a reissue happened, one reissue row. Five distinct PayPal batches exist for nine payment rows: two first-run batches (one per currency) and three single-item reissues. Nothing was paid twice.

Excerpt of the Render local task server log for the same run (parent links come from Render, not from this app):
```
 ↳ Subtask (parent: settle_run) running: trn-davij7h7rh87480ujk10 (payout_lane) input=["set-6bc943","GBP",["po-1002","po-1003","po-1006"],true]
 ↳ Subtask (parent: settle_run) running: trn-davij7h7rh87480ujk1g (payout_lane) input=["set-6bc943","USD",["po-1001","po-1004","po-1005"],true]
 ↳ Subtask (parent: payout_lane) running: trn-davij7p7rh87480ujk20 (submit_batch) input=["set-6bc943","GBP",["po-1002","po-1003","po-1006"],true]
 ↳ Subtask (parent: payout_lane) running: trn-davij7p7rh87480ujk2g (submit_batch) input=["set-6bc943","USD",["po-1001","po-1004","po-1005"],true]
 ↳ Subtask (parent: payout_lane) Failed: trn-davij7p7rh87480ujk2g (submit_batch) input=["set-6bc943","USD",["po-1001","po-1004","po-1005"],true] - injected crash at submit_batch: PayPal accepted the batch, the database write 
 ↳ Subtask (parent: payout_lane) Completed: trn-davij7p7rh87480ujk20 (submit_batch) input=["set-6bc943","GBP",["po-1002","po-1003","po-1006"],true] output=[{"payout_batch_id":"WAVNNCWH4AQ4S","items":3,"created_now":true,"summ
```

## 5. UI checks in a real browser (`node scripts/ui_test.mjs`, Chromium, 360px)
```
PASS bad email shows how to fix it
PASS 200% text: no horizontal scroll at 360px
PASS all controls >= 37.3px (28pt): none under
PASS no text under 10pt: ok
PASS focused element shows an outline of 2px or more
PASS resend run finished and the headline now reads: 5 of 6 payouts landed, 1 stopped on purpose.
PASS disabled button explains itself
```

The step that sends the corrected address ran a full resend through the Render task server and the headline moved from "4 of 6 payouts landed" to "5 of 6".

## 6. Contrast (`python scripts/contrast.py`)
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

## Not tested
- A live PayPal webhook delivery. The listener needs a public URL; verification is proven with generated keys only.
- Any deployed Render service. No Render account or API key exists on this machine. The Blueprint (`render.yaml`) has not been applied.
- The Anthropic API provider. No key available; the Bedrock provider ran for real, and the scripted policy is covered by unit tests.
