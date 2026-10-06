#!/usr/bin/env python3
"""
form_flow.py — The paperwork loop that turns "form required" into real CRM changes.

For every pending request in crm_clients.json (client['pending_changes'][kind]):

    AWAITING_FORM ──form_agent makes a signed form──▶ FORM_RECEIVED ──Orbit reviews──┬─▶ APPROVED → CRM changed,
         ▲                                                                            │            change_history + audit log,
         │                                                                            │            pending removed, confirmation letter
         └── next run: Orbit re-requests, a corrected form arrives (fresh defect roll)◀┴─▶ REJECTED → status RETURNED,
                                                                                                       reasons stored, letter to FA

Rules of the road
  * The CRM only changes after a form passes review. Nothing here edits it before that.
  * Each write re-reads crm_clients.json and touches only that client, so Venus/Neptune/the editor
    can't be clobbered (and vice-versa).
  * Image budget: at most MAX_IMAGES new forms per run (ORBIT_MAX_IMAGES, default 10). Anything
    beyond the cap stays queued for the next run. Forms already on file (FORM_RECEIVED — e.g. a run
    that crashed after paying for the image) are reviewed first and cost no new image.
  * Append-only audit log: orbit_forms/audit_log.jsonl (one JSON object per event).
"""

import copy
import json
import os
import sys
from datetime import date, datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
import form_agent
import form_review
from crm_schema import (format_address, format_designation, history_entry,
                        validate_address, validate_designation)

FORMS_DIR = form_agent.OUT_DIR
AUDIT_FILE = os.path.join(FORMS_DIR, "audit_log.jsonl")
WORK_STATUSES = ("FORM_RECEIVED", "RETURNED", "AWAITING_FORM")     # processing order


# ── CRM helpers (fresh read, touch one client, atomic write) ───────────────

def _load(path):
    with open(path) as f:
        return json.load(f)


def _save(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def _mutate(clients_file, account_id, fn):
    data = _load(clients_file)
    for c in data["clients"]:
        if c["account_id"] == account_id:
            out = fn(c)
            _save(clients_file, data)
            return out
    raise KeyError(account_id)


def _get_client(clients_file, account_id):
    return next((c for c in _load(clients_file)["clients"] if c["account_id"] == account_id), None)


def audit(event, **fields):
    os.makedirs(FORMS_DIR, exist_ok=True)
    rec = dict({"ts": datetime.now().isoformat(), "event": event}, **fields)
    with open(AUDIT_FILE, "a") as f:
        f.write(json.dumps(rec) + "\n")


# ── Work discovery ─────────────────────────────────────────────────────────

def find_work(clients_file, only_request_ids=None):
    """[(account_id, kind, pending)] ordered: forms already on file first, then RETURNED, then new."""
    items, skipped = [], []
    for c in _load(clients_file)["clients"]:
        for kind, p in (c.get("pending_changes") or {}).items():
            if not isinstance(p, dict) or p.get("status") not in WORK_STATUSES:
                continue
            if only_request_ids is not None and p.get("request_id") not in only_request_ids:
                continue
            if c.get("status") != "ACTIVE":
                skipped.append({"account_id": c["account_id"], "kind": kind,
                                "reason": "account is {}".format(c.get("status"))})
                continue
            items.append((c["account_id"], kind, p))
    items.sort(key=lambda t: (WORK_STATUSES.index(t[2]["status"]), t[2].get("requested_at") or ""))
    return items, skipped


# ── Applying an approved change ────────────────────────────────────────────

def apply_change(clients_file, account_id, kind, requested, form_id):
    """Change the CRM for one approved form. Returns {'before': text, 'after': text}."""
    def fn(c):
        pend = (c.get("pending_changes") or {}).get(kind)
        hist = c.setdefault("change_history", [])
        if kind == "address":
            new = {k: requested.get(k, "") for k in ("street", "unit", "city", "state", "zip")}
            errs = validate_address(new)
            if errs:
                raise ValueError("refusing to apply invalid address: {}".format("; ".join(errs)))
            old = copy.deepcopy(c.get("address"))
            c["address"] = new
            hist.append(history_entry("address", old, new, source_form_id=form_id,
                                      note="Signed address-change form approved by Orbit"))
            old_mail = bool(c.get("mail_to_fa"))
            new_mail = bool(requested.get("mail_to_fa", old_mail))
            if new_mail != old_mail:
                c["mail_to_fa"] = new_mail
                hist.append(history_entry("mail_to_fa", old_mail, new_mail, source_form_id=form_id,
                                          note="Statement delivery changed on the same form"))
            before = format_address(old) + (" (mail c/o FA)" if old_mail else "")
            after = format_address(new) + (" (mail c/o FA)" if new_mail else "")
        else:
            errs = validate_designation(requested)
            if errs:
                raise ValueError("refusing to apply invalid designation: {}".format("; ".join(errs)))
            old = copy.deepcopy(c.get("beneficiary"))
            c["beneficiary"] = copy.deepcopy(requested)
            hist.append(history_entry("beneficiary", old, copy.deepcopy(requested), source_form_id=form_id,
                                      note="Signed and notarized beneficiary form approved by Orbit"))
            before, after = format_designation(old) if old else "Not on file", format_designation(requested)
        if pend is not None:
            del c["pending_changes"][kind]
        return {"before": before, "after": after}
    return _mutate(clients_file, account_id, fn)


# ── Letters (Orbit's LLM writes them; facts come from the review, never invented) ──

def _issues_text(findings):
    return "\n".join("- {}".format(f["detail"]) for f in findings)


def letter_approved(llm, client, kind, change, form_id):
    what = "address change" if kind == "address" else "beneficiary designation"
    return llm(
        "You are Orbit, a warm but compliance-aware customer service rep at Obsidian Capital.\n\n"
        "Client: {name} (Account {acct}). Their signed {what} form ({form}) has been reviewed and APPROVED, "
        "and the change is now made on the account.\n"
        "Before: {before}\nNow: {after}\n\n"
        "Write a 3-4 sentence confirmation to the client, copying their financial advisor ({fa}). "
        "State plainly what changed and that it is effective now.{tail} Do not invent details, policies "
        "or mailing practices. Address the client by full name (no Mr./Ms./Mrs.).\n"
        "Sign as: — Orbit | Obsidian Capital Client Services".format(
            name=client["name"], acct=client["account_id"], what=what, form=form_id,
            before=change["before"], after=change["after"], fa=client.get("fa_name", "your advisor"),
            tail=(" Add that a written confirmation of an address change is normally also sent to the previous "
                  "address on file." if kind == "address" else "")))


def letter_rejected(llm, client, kind, findings, form_id, attempt):
    what = "address-change" if kind == "address" else "beneficiary designation"
    extra = (" A beneficiary form must be signed AND notarized (signature, notary signature, notary seal)."
             if kind == "beneficiary" else "")
    return llm(
        "You are Orbit, a warm but compliance-aware customer service rep at Obsidian Capital.\n\n"
        "Client: {name} (Account {acct}). Their {what} form ({form}, submission #{n}) was reviewed and "
        "could NOT be accepted. Nothing on the account has been changed.\n"
        "Problems found:\n{issues}\n\n"
        "Write a 4-5 sentence letter to the client's financial advisor ({fa}) and the client. Say clearly "
        "that the account is unchanged, list each problem in plain language, and explain that a corrected, "
        "complete form is needed.{extra} Do not invent problems that are not listed. Address the client by full "
        "name (no Mr./Ms./Mrs.).\n"
        "Sign as: — Orbit | Obsidian Capital Client Services".format(
            name=client["name"], acct=client["account_id"], what=what, form=form_id, n=attempt,
            issues=_issues_text(findings), fa=client.get("fa_name", "the advisor"), extra=extra))


def letter_rerequest(llm, client, kind, last_findings, attempt):
    what = "address-change" if kind == "address" else "beneficiary designation"
    return llm(
        "You are Orbit, a warm but compliance-aware customer service rep at Obsidian Capital.\n\n"
        "Client: {name} (Account {acct}). Their earlier {what} form was returned for correction "
        "(reasons: {why}). Write a 2-sentence note to their financial advisor ({fa}) re-requesting a "
        "corrected form (this is request #{n}) and saying the account stays unchanged until it is approved.\n"
        "Sign as: — Orbit | Obsidian Capital Client Services".format(
            name=client["name"], acct=client["account_id"], what=what,
            why="; ".join(f["detail"] for f in (last_findings or [])[:3]) or "see prior letter",
            fa=client.get("fa_name", "the advisor"), n=attempt))


# ── Main loop ──────────────────────────────────────────────────────────────

def _load_manifest(form_id):
    p = os.path.join(FORMS_DIR, form_id + ".json")
    if not os.path.isfile(p):
        return None
    with open(p) as f:
        m = json.load(f)
    img = m.get("image_path")
    if not img or not os.path.isfile(img):                   # repo moved between machines
        for ext in (".png", ".jpg", ".webp"):
            cand = os.path.join(FORMS_DIR, form_id + ext)
            if os.path.isfile(cand):
                m["image_path"] = cand
                break
    return m


def live_ready():
    """(ok, message) — both profiles need an OpenRouter key before a live run makes sense."""
    import hermes_profile
    missing = [p for p in (form_agent.PROFILE, form_review.REVIEW_PROFILE)
               if not hermes_profile.profile_settings(p)["api_key"]]
    if missing:
        return False, "no OpenRouter API key found for profile(s): {}".format(", ".join(missing))
    return True, ""


def process_forms(clients_file, llm, mode="live", max_images=None, only_request_ids=None,
                  today=None, verbose=False, print_fn=print, defect_rate=None):
    """
    Generate → review → apply/return for every pending request. Returns (results, summary).
    mode: 'live' (image model + vision reader) or 'mock' (Python-drawn forms + manifest reader).
    """
    today = today or date.today()
    if defect_rate is None:
        defect_rate = float(os.environ.get("ORBIT_DEFECT_RATE", form_agent.DEFECT_RATE))
    max_images = form_agent.MAX_IMAGES_PER_RUN if max_images is None else max_images
    budget = form_agent.FormBudget(max_images) if mode == "live" else None
    backend = "mock" if mode == "mock" else "openrouter"

    items, skipped = find_work(clients_file, only_request_ids)
    results = []
    summary = {"queued": len(items), "images": 0, "applied": 0, "returned": 0, "errors": 0,
               "deferred": 0, "skipped": len(skipped), "image_cost": 0.0, "review_cost": 0.0,
               "injected": 0, "detected": 0, "missed": 0, "extra_findings": 0}
    for s in skipped:
        print_fn("  ⏭  {} {}: skipped ({})".format(s["account_id"], s["kind"], s["reason"]))

    for n, (account_id, kind, pend0) in enumerate(items, 1):
        client = _get_client(clients_file, account_id)
        pend = (client.get("pending_changes") or {}).get(kind) or pend0
        requested = pend["requested"]
        status = pend["status"]
        res = {"account_id": account_id, "client_name": client["name"], "kind": kind,
               "request_id": pend.get("request_id"), "legacy": pend.get("legacy", False),
               "prior_status": status, "attempt": (pend.get("attempts") or 0)}
        icon = "📮" if kind == "address" else "👤"
        print_fn("  {} [{}/{}] {} — {} ({}, {})".format(icon, n, len(items), client["name"], kind,
                                                        account_id, status))

        # ── 1. get a form on file ────────────────────────────────────────────
        manifest = None
        if status == "FORM_RECEIVED" and pend.get("form_id"):
            manifest = _load_manifest(pend["form_id"])
            if manifest is None:
                print_fn("     ⚠ stored form {} is missing — requesting a new one".format(pend["form_id"]))
        retry_findings = None
        if manifest is None:
            if summary["images"] >= max_images:
                summary["deferred"] += 1
                res.update(outcome="DEFERRED", note="image cap of {} per run reached".format(max_images))
                results.append(res)
                print_fn("     ⏸  deferred to next run (image cap {})".format(max_images))
                continue
            if status == "RETURNED":
                last = (pend.get("last_result") or {}).get("findings") or []
                retry_findings = last
                res["re_request_letter"] = letter_rerequest(llm, client, kind, last, res["attempt"] + 1)
                audit("re_requested", account_id=account_id, kind=kind, request_id=pend.get("request_id"),
                      attempt=res["attempt"] + 1)
            try:
                manifest = form_agent.generate_form(
                    kind, client, requested, issue_id=pend.get("issue_id"), request_id=pend.get("request_id"),
                    backend=backend, budget=budget, verbose=verbose, id_prefix="FORM",
                    defect_rate=defect_rate, retry_findings=retry_findings)
            except form_agent.FormBudgetExceeded:
                summary["deferred"] += 1
                res.update(outcome="DEFERRED", note="image cap reached")
                results.append(res)
                continue
            except form_agent.FormGenerationError as e:
                summary["errors"] += 1
                res.update(outcome="ERROR", error="form generation failed: {}".format(e))
                results.append(res)
                audit("form_generation_failed", account_id=account_id, kind=kind, error=str(e))
                print_fn("     ❌ {}".format(str(e).splitlines()[0]))
                continue
            summary["images"] += 1
            cost = (manifest.get("generation") or {}).get("cost") or 0
            summary["image_cost"] += cost
            res["attempt"] += 1

            def mark(c, m=manifest, a=res["attempt"]):
                p = c["pending_changes"][kind]
                p.update(status="FORM_RECEIVED", form_id=m["form_id"], attempts=a,
                         form_received_at=datetime.now().isoformat())
            _mutate(clients_file, account_id, mark)
            audit("form_received", account_id=account_id, kind=kind, request_id=pend.get("request_id"),
                  form_id=manifest["form_id"], model=manifest.get("model"), attempt=res["attempt"],
                  cost=cost)
        else:
            res["attempt"] = max(res["attempt"], 1)

        res.update(form_id=manifest["form_id"], image_path=manifest.get("image_path"),
                   manifest_path=manifest.get("manifest_path"))

        # ── 2. Orbit reviews it ──────────────────────────────────────────────
        try:
            review = form_review.review_form(kind, manifest, client, requested,
                                             "mock" if mode == "mock" else "live", today, verbose)
        except form_review.ReviewError as e:
            summary["errors"] += 1
            res.update(outcome="ERROR", error="review failed (form stays on file, reviewed next run): {}".format(e))
            results.append(res)
            audit("review_failed", account_id=account_id, kind=kind, form_id=manifest["form_id"], error=str(e))
            print_fn("     ❌ {}".format(str(e).splitlines()[0]))
            continue
        summary["review_cost"] += (review["reader"].get("cost") or 0)
        sc = review["score"]
        summary["injected"] += len(sc["injected"])
        summary["detected"] += len(sc["detected"])
        summary["missed"] += len(sc["missed"])
        summary["extra_findings"] += len(sc["extra"])
        res.update(decision=review["decision"], findings=review["findings"], concerns=review["concerns"],
                   score=sc, reading=review["reading"], reader=review["reader"],
                   read_disagreement=review.get("read_disagreement") or [])
        audit("form_reviewed", account_id=account_id, kind=kind, form_id=manifest["form_id"],
              decision=review["decision"], findings=review["findings"], score=sc)

        # ── 3. apply or return ───────────────────────────────────────────────
        if review["decision"] == "APPROVE":
            try:
                change = apply_change(clients_file, account_id, kind, requested, manifest["form_id"])
            except ValueError as e:                       # never write bad data
                summary["errors"] += 1
                res.update(outcome="ERROR", error=str(e))
                results.append(res)
                audit("apply_refused", account_id=account_id, kind=kind, form_id=manifest["form_id"], error=str(e))
                print_fn("     ❌ {}".format(e))
                continue
            res.update(outcome="APPLIED", change=change,
                       letter=letter_approved(llm, client, kind, change, manifest["form_id"]))
            summary["applied"] += 1
            audit("change_applied", account_id=account_id, kind=kind, form_id=manifest["form_id"],
                  request_id=pend.get("request_id"), before=change["before"], after=change["after"])
            print_fn("     ✅ APPROVED — {} updated".format(kind))
            print_fn("        {} → {}".format(change["before"], change["after"]))
        else:
            def ret(c, m=manifest, rv=review):
                p = c["pending_changes"][kind]
                p.update(status="RETURNED", form_id=m["form_id"],
                         last_result={"form_id": m["form_id"], "decision": "REJECT",
                                      "reviewed_at": datetime.now().isoformat(),
                                      "findings": rv["findings"]})
            _mutate(clients_file, account_id, ret)
            res.update(outcome="RETURNED",
                       letter=letter_rejected(llm, client, kind, review["findings"], manifest["form_id"],
                                              res["attempt"]))
            summary["returned"] += 1
            audit("form_returned", account_id=account_id, kind=kind, form_id=manifest["form_id"],
                  request_id=pend.get("request_id"), findings=review["findings"])
            print_fn("     ❌ REJECTED — form returned")
            for f in review["findings"]:
                print_fn("        • {}".format(f["detail"]))
        if sc["injected"] or sc["extra"]:
            print_fn("        (planted: {} | caught: {} | missed: {} | unplanted flaws: {})".format(
                ", ".join(sc["injected"]) or "none", ", ".join(sc["detected"]) or "-",
                ", ".join(sc["missed"]) or "-", ", ".join(sc["extra"]) or "-"))
        results.append(res)

    return results, summary


if __name__ == "__main__":
    sys.exit("Run via orbit.py (python3 orbit.py --forms live|mock)")
