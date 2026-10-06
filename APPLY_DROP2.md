# Drop 2 — Orbit reviews the forms and changes the CRM

Unzip over ~/paper_trade (Drop 1 must already be applied and the CRM migrated).
Replaces: orbit/orbit.py, orbit/build_orbit_dashboard.py, orbit/form_agent.py.  New: orbit/form_review.py, orbit/form_flow.py.

## What a run does now
1. **Paperwork first.** Every pending request from an earlier run is worked:
   AWAITING_FORM / RETURNED -> form_agent makes a signed form (fresh ~20% defect roll) -> FORM_RECEIVED
   -> Orbit reads the image (Gemma 4 26B, "orbit" profile model) -> Python rules decide.
   - APPROVED: crm_clients.json changed, change_history entry written, pending removed, confirmation letter.
   - REJECTED: status RETURNED with the reasons stored, letter to the FA. Next run Orbit re-requests and a new form arrives.
2. **New issues** (address / beneficiary requests create AWAITING_FORM; their forms arrive next run).
   Add `--process-new` to also push this run's new requests through the loop immediately.
3. Run record now includes form_results / form_summary; append-only audit log at orbit/orbit_forms/audit_log.jsonl.

The 21 converted legacy requests are picked up automatically on the first run.
At most 10 new images per run (`--max-images N` or `ORBIT_MAX_IMAGES`); the rest stay queued. A form already on file
(e.g. a run that died after paying for the image) is reviewed first and costs no new image.

## Commands
    python3 orbit.py --forms mock                 # free: Python-drawn forms + perfect reader (test the flow)
    python3 orbit.py                              # live: Qwen forms, Gemma review (needs OPENROUTER key in form_agent + orbit profiles)
    python3 orbit.py --forms live --process-new   # also process this run's new requests now
    python3 orbit.py --forms off                  # old behaviour (letters only)
    python3 orbit.py --dry-run                    # shows how many requests are waiting, generates nothing
    python3 form_review.py orbit_forms/FORM-....json          # re-review one stored form (live)
    python3 form_review.py orbit_forms/FORM-....json --mock   # same, offline
    python3 form_review.py --show-config                      # which model/key the review will use
    python3 build_orbit_dashboard.py              # new "Forms" and "Change History" tabs

Reset between test cycles:  cp ~/paper_trade/venus/crm_clients_ORIGINAL.json ~/paper_trade/venus/crm_clients.json
(also delete orbit/orbit_forms/ if you want a clean slate of images and audit log).

## How the review decides
The reader model is shown ONLY the image (never what the form should say) and returns what is on the page as JSON.
Python then checks it against the CRM and the pending request. Rejection codes:
 both forms:   missing_signature, missing_date, stale_date (> 60 days), future_date, name_mismatch, wrong_account_number,
               blank_required_field, illegible_field
 address:      current_address_mismatch, address_mismatch, missing_delivery_choice, wrong_delivery_choice
 beneficiary:  percent_not_100, designation_mismatch, blank_relationship, not_notarized, missing_notary_seal,
               missing_notary_signature, notary_expired, notary_date_before_signing, notary_signer_mismatch
Anything in the reader's "concerns" (garbled printed text, entry in the wrong box) is shown on the dashboard but only rejects
if it breaks one of the rules above. Seal spelling is not checked (Qwen sometimes writes "PUBIC").

Dashboard scoring: **planted** = defects form_agent injected, **caught / missed** = what Orbit found of them,
**unplanted** = flaws the image model made by accident (or reader false alarms). All count as real rejections.

## Tested here / not tested here
Tested offline: 64 defect combinations through the rules (all detected, none extra), two full mock cycles including RETURNED ->
re-request -> approval, the image cap and deferral, --process-new, CRM validity after 30+ changes, dashboard render.
NOT tested: the live Gemma reading (I can't reach OpenRouter). First live run: use a small batch and read the dashboard.
If Gemma's reading is shaky, `python3 form_review.py <manifest.json> --print-prompt` shows the exact prompt to tune.

## Also fixed in this drop
Run files were named to the minute, so two runs in the same minute overwrote each other — now to the second.

## README updates (paste in)
**Orbit SOUL.md policy lines 2-3 (~/.hermes/profiles/orbit/SOUL.md):**
    2. ADDRESS CHANGES: A signed address-change form is required. Orbit reviews the returned form; the address changes
       only after it passes review. 3-5 business days.
    3. BENEFICIARY CHANGES: A signed AND notarized designation form (with notary seal) is required, returned through the FA.
       Percentages for primary (and contingent) beneficiaries must total 100%. 5-7 business days.
**Orbit Issue Types table:** address -> "Signed form requested; next run: form arrives, Orbit reviews, CRM updated or form returned";
beneficiary -> same, plus notary + seal check.
**File structure:** orbit/crm_schema.py, migrate_crm_v2.py, form_templates.py, form_agent.py, form_review.py, form_flow.py,
hermes_profile.py, orbit_forms/ (images + manifests + audit_log.jsonl), backtest_fund_returns.py (repo root).
**OCRFF Fund table** (from your backtest run): YTD +14.2% vs S&P +13.1% | 1-year +23.0% vs +16.9% |
since inception +225.9% vs +159.2% | inception NAV $31.11 (rebased). Re-run backtest_fund_returns.py to refresh.
**.gitignore:** add `orbit/orbit_forms/` and `*.bak-*` (form images and fund-file backups shouldn't be committed).
**Profiles:** `form_agent` (image model: qwen/qwen-image-3-pro) and `orbit` (review + letters: google/gemma-4-26b-a4b-it),
both via OpenRouter; change either with `form_agent model` / `orbit model`.
