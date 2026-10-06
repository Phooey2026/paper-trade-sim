#!/usr/bin/env python3
"""
migrate_crm_v2.py — One-time migration of crm_clients.json to schema v2.

What it does
  1. Seeds a realistic California street address for every client
     (address: {street, unit, city, state, zip}), matched to the client's territory.
  2. Converts `beneficiary` from a single name string into a list of
     {name, relationship, type, percent} rows (primary/contingent, splits,
     trusts, varied surnames). 45% of existing names are kept as the first
     primary; the rest are replaced with new realistic names. The 21 clients
     who were listed as their own beneficiary always get a new name.
  3. Converts legacy pending_changes (new_street/... and new_beneficiary)
     into the keyed v2 format with status AWAITING_FORM so the next Orbit run
     can pick them up.
  4. Marks fully-redeemed accounts (0 shares after a full_close) as CLOSED.
  5. Adds change_history: [] for the audit trail Orbit will write to.

Safety
  * Writes crm_clients_v1_backup.json (the untouched original) before changing anything.
  * Writes crm_clients_ORIGINAL.json (the pristine v2 file) for test-cycle resets:
        cp venus/crm_clients_ORIGINAL.json venus/crm_clients.json
  * Idempotent: an already-migrated file is left alone.
  * Deterministic: same input + same --salt gives the same output.

Usage
    python3 migrate_crm_v2.py --dry-run
    python3 migrate_crm_v2.py
    python3 migrate_crm_v2.py --file /path/to/crm_clients.json
"""

import argparse
import collections
import json
import os
import re
import shutil
import sys
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PAPER_DIR = os.path.dirname(SCRIPT_DIR)
DEFAULT_FILE = os.path.join(PAPER_DIR, "venus", "crm_clients.json")

sys.path.insert(0, SCRIPT_DIR)
from crm_schema import (SCHEMA_VERSION, PENDING_KINDS, stable_rng, random_address,
                        make_designation, make_pending, validate_designation,
                        validate_address, _weighted)


def legacy_bene_to_designation(name, client_name, rng):
    """Turn an old single-string pending beneficiary request into a designation."""
    n = name.strip()
    client_last = client_name.split()[-1]

    m = re.match(r"^(?:The\s+)?(.+?)\s+Family(?:\s+Trust)?$", n, re.I)
    if m:                                    # "The Torres Family" is not a legal payee
        return [{"name": "{} Family Trust".format(m.group(1)),
                 "relationship": "Trust", "type": "primary", "percent": 100}]
    if re.search(r"\bTrust$", n, re.I):
        return [{"name": n, "relationship": "Trust", "type": "primary", "percent": 100}]

    same = n.split()[-1] == client_last
    rel = (_weighted(rng, [("Spouse", 55), ("Child", 28), ("Sibling", 10), ("Parent", 7)])
           if same else _weighted(rng, [("Spouse", 50), ("Friend", 25), ("Domestic Partner", 15), ("Child", 10)]))
    return [{"name": n, "relationship": rel, "type": "primary", "percent": 100}]


def is_legacy_pending(pc):
    return bool(pc) and not set(pc.keys()).issubset(set(PENDING_KINDS))


def migrate_client(c, salt):
    """Mutates one client dict. Returns a dict of what changed (for the summary)."""
    acct = c["account_id"]
    info = {"pending_addr": 0, "pending_bene": 0, "closed": 0, "self_bene_fixed": 0,
            "kept_name": 0}

    # 1. Address
    rng_a = stable_rng(salt, acct, "address")
    c["address"] = random_address(rng_a, c.get("territory"), cross_territory_prob=0.04)

    # 2. Beneficiary
    rng_b = stable_rng(salt, acct, "beneficiary")
    legacy = c.get("beneficiary")
    keep = None
    if isinstance(legacy, str) and legacy.strip():
        if legacy.strip() == c["name"]:
            info["self_bene_fixed"] = 1          # a client can't be their own beneficiary
        elif rng_b.random() < 0.45:
            keep = legacy.strip()
            info["kept_name"] = 1
    c["beneficiary"] = make_designation(rng_b, c["name"], keep_primary_name=keep)

    # 3. History container
    c.setdefault("change_history", [])

    # 4. Legacy pending changes -> keyed v2 format
    pc = c.get("pending_changes")
    if is_legacy_pending(pc):
        rng_p = stable_rng(salt, acct, "pending")
        new_pc = {}
        if "new_street" in pc:
            new_pc["address"] = make_pending(
                acct, "address",
                {"street": pc["new_street"], "unit": "", "city": pc.get("new_city", ""),
                 "state": pc.get("new_state", "CA"), "zip": pc.get("new_zip", ""),
                 "mail_to_fa": bool(c.get("mail_to_fa"))},
                requested_at=pc.get("requested_at"), legacy=True)
            info["pending_addr"] = 1
        if "new_beneficiary" in pc:
            new_pc["beneficiary"] = make_pending(
                acct, "beneficiary",
                legacy_bene_to_designation(pc["new_beneficiary"], c["name"], rng_p),
                requested_at=pc.get("requested_at"), legacy=True)
            info["pending_bene"] = 1
        c["pending_changes"] = new_pc

    # 5. Fully redeemed accounts are CLOSED, not ACTIVE
    closes = [t for t in c.get("transactions", []) if t.get("is_full_close")]
    if closes and float(c.get("ocrff_shares", 0)) <= 0.0001 and c.get("status") == "ACTIVE":
        c["status"] = "CLOSED"
        c["closed_date"] = closes[-1].get("date", "")
        info["closed"] = 1
    return info


def save_json_atomic(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser(description="Migrate crm_clients.json to schema v2")
    ap.add_argument("--file", default=DEFAULT_FILE)
    ap.add_argument("--dry-run", action="store_true", help="Report only; write nothing")
    ap.add_argument("--salt", default="obsidian-v2", help="Seed salt (changes generated data)")
    ap.add_argument("--refresh-original", action="store_true",
                    help="Overwrite crm_clients_ORIGINAL.json even if it exists")
    args = ap.parse_args()

    if not os.path.exists(args.file):
        sys.exit("❌ Not found: {}".format(args.file))
    with open(args.file) as f:
        data = json.load(f)

    print("=" * 60)
    print(" 🗂  CRM v2 migration | {}".format(os.path.basename(args.file)))
    if args.dry_run:
        print("    *** DRY RUN — nothing will be written ***")
    print("=" * 60)

    if data.get("schema_version") == SCHEMA_VERSION:
        print("✅ Already at schema v{} — nothing to do.".format(SCHEMA_VERSION))
        return

    clients = data["clients"]
    totals = collections.Counter()
    for c in clients:
        for k, v in migrate_client(c, args.salt).items():
            totals[k] += v

    data["schema_version"] = SCHEMA_VERSION
    data["migrated_at"] = datetime.now().isoformat()

    # ── Validate every record before writing anything ──────────────────────
    problems = []
    for c in clients:
        for e in validate_address(c["address"]):
            problems.append("{}: address {}".format(c["account_id"], e))
        for e in validate_designation(c["beneficiary"]):
            problems.append("{}: beneficiary {}".format(c["account_id"], e))
        for kind, p in c.get("pending_changes", {}).items():
            errs = validate_designation(p["requested"]) if kind == "beneficiary" \
                   else validate_address(p["requested"])
            for e in errs:
                problems.append("{}: pending {} {}".format(c["account_id"], kind, e))
    if problems:
        print("❌ Validation failed ({} problems) — nothing written:".format(len(problems)))
        for p in problems[:15]:
            print("   " + p)
        sys.exit(1)

    # ── Summary ────────────────────────────────────────────────────────────
    n = len(clients)
    multi_primary = sum(1 for c in clients if sum(1 for b in c["beneficiary"] if b["type"] == "primary") > 1)
    with_cont = sum(1 for c in clients if any(b["type"] == "contingent" for b in c["beneficiary"]))
    trusts = sum(1 for c in clients if c["beneficiary"][0]["relationship"] == "Trust")
    diff_surname = sum(1 for c in clients
                       if c["beneficiary"][0]["name"].split()[-1] != c["name"].split()[-1]
                       and c["beneficiary"][0]["relationship"] != "Trust")
    rels = collections.Counter(b["relationship"] for c in clients for b in c["beneficiary"]
                               if b["type"] == "primary")
    cities = collections.Counter(c["address"]["city"] for c in clients)
    cross = sum(1 for c in clients
                if c["address"]["city"] not in
                __import__("crm_schema").TERRITORY_CITIES.get(c["territory"], {}))
    print("\n  Clients migrated:            {}".format(n))
    print("  Addresses seeded:            {} ({} cities; {} outside FA territory)".format(
        n, len(cities), cross))
    print("  Beneficiary lists built:     {}".format(n))
    print("    multiple primaries:        {}".format(multi_primary))
    print("    with contingents:          {}".format(with_cont))
    print("    trust as primary:          {}".format(trusts))
    print("    first primary, diff surname:{}".format(diff_surname))
    print("    existing name kept:        {}".format(totals["kept_name"]))
    print("    self-beneficiary replaced: {}".format(totals["self_bene_fixed"]))
    print("    primary relationships:     {}".format(dict(rels.most_common())))
    print("  Legacy pending converted:    {} address, {} beneficiary".format(
        totals["pending_addr"], totals["pending_bene"]))
    print("  Accounts marked CLOSED:      {}".format(totals["closed"]))

    if args.dry_run:
        print("\n(dry run — no files written)")
        return

    # ── Write files ────────────────────────────────────────────────────────
    d = os.path.dirname(os.path.abspath(args.file))
    backup = os.path.join(d, "crm_clients_v1_backup.json")
    original = os.path.join(d, "crm_clients_ORIGINAL.json")
    if not os.path.exists(backup):
        shutil.copy2(args.file, backup)
        print("\n💾 v1 backup:      {}".format(backup))
    else:
        print("\n💾 v1 backup already exists — left untouched")
    save_json_atomic(args.file, data)
    print("✅ Migrated:       {}".format(args.file))
    if args.refresh_original or not os.path.exists(original):
        shutil.copy2(args.file, original)
        print("📌 Reset copy:     {}".format(original))
    else:
        print("📌 crm_clients_ORIGINAL.json already exists — left untouched "
              "(use --refresh-original to replace)")
    print("\nReset between test cycles:")
    print("  cp {} {}".format(original, args.file))


if __name__ == "__main__":
    main()
