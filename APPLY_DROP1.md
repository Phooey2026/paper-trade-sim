# Drop 1 — apply guide (Orbit forms upgrade)

Unzip over ~/paper_trade (files keep their relative paths). Back up first: `cp -r ~/paper_trade ~/paper_trade_bak`.

## 1. Migrate the CRM to schema v2
    cd ~/paper_trade/orbit
    python3 migrate_crm_v2.py --dry-run     # review the summary
    python3 migrate_crm_v2.py               # writes crm_clients_v1_backup.json + crm_clients_ORIGINAL.json
Test reset any time:  cp ~/paper_trade/venus/crm_clients_ORIGINAL.json ~/paper_trade/venus/crm_clients.json
orbit.py and generate_issues.py now refuse to run on a non-v2 CRM.

## 2. Create the form_agent Hermes profile
    hermes profile create form_agent --clone-from default --description "Fills Orbit address/beneficiary forms (image model)"
    form_agent model            # or: hermes -p form_agent model  -> pick openrouter / qwen/qwen-image-3-pro
Then set toolsets to [] in ~/.hermes/profiles/form_agent/config.yaml and (optional) put a SOUL.md next to it.
Verify:
    python3 form_agent.py --show-config
    grep -n model ~/.hermes/profiles/form_agent/config.yaml
The key is read from OPENROUTER_API_KEY, the profile .env, or ~/.hermes/.env (never the repo).

## 3. Smoke test
    python3 form_agent.py --smoke-test --backend mock --defects random     # no cost
    python3 form_agent.py --smoke-test --kind address                       # real Qwen call (~1 image)
    python3 form_agent.py --smoke-test --kind beneficiary --print-prompt
Outputs land in orbit/orbit_forms/ (image + JSON manifest). Nothing touches crm_clients.json.
Check: printed text unchanged, values spelled exactly, signature present, notary seal legible.
Force defects: --defects missing_notary_seal,percent_not_100   (see --list-defects)

## 4. Bug fixes included
SELL_AMOUNTS now capped at 90% of account value; statement footer uses the real account ID and beneficiary list;
full closes set status CLOSED (8 existing ones migrated); issue IDs are unique per run;
editor FA tab has MLB/NBA/NFL teams; editor saves only changed fields (no clobbering Orbit/Venus writes).

## Other things found in the data
21 clients were their own beneficiary (replaced), 193 duplicate client names, statement footer hardcoded OC-CLIENT-001.

Orbit's review/apply step arrives in Drop 2.
