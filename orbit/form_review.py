#!/usr/bin/env python3
"""
form_review.py — Orbit's second pass: read a returned form image, then decide.

Design (same split as the Week-3 slot pre-selection: the model READS, Python DECIDES)
  1. BLIND READING. The vision model set on the Hermes profile "orbit" (google/gemma-4-26b-a4b-it)
     is shown the form image and asked to transcribe what is actually on the page as JSON. It is
     never told what the form is *supposed* to say, so it can't be led into "confirming" anything.
  2. RULES. `evaluate()` compares that reading with the CRM record and the client's pending request
     and returns findings (each has a code from the defect catalog). No findings -> APPROVE.
  3. SCORING. `score()` compares the findings with the manifest form_agent wrote (the defects it
     deliberately injected) -> detected / missed / extra (extra = flaws the image model made by
     accident, or false alarms).

`mock_reading()` produces a perfect "reading" straight from the manifest so the whole flow can be
tested offline (`orbit.py --forms mock`).

    python3 form_review.py orbit_forms/FORM-....json            # review one stored form (live)
    python3 form_review.py orbit_forms/FORM-....json --mock     # same, offline
    python3 form_review.py --show-config
"""

import argparse
import base64
import difflib
import io
import json
import os
import re
import sys
from datetime import date, datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
import hermes_profile
import form_agent                                     # reuses the OpenRouter helpers

REVIEW_PROFILE = "orbit"
DEFAULT_REVIEW_MODEL = "google/gemma-4-26b-a4b-it"
STALE_DAYS = 60            # a form signed longer ago than this is refused
MAX_IMAGE_SIDE = 1600      # px; larger scans are shrunk before sending (cheaper, still legible)


class ReviewError(Exception):
    pass


# ═══════════════════════════════════════════════════════════════════════════
# 1. Blind reading (vision model)
# ═══════════════════════════════════════════════════════════════════════════

_COMMON_RULES = """You are a document-intake clerk. The image is a scanned, hand-completed form.
Transcribe EXACTLY what is written or stamped on the page into JSON. You are NOT told what the
form should say, so do not guess, correct, complete or 'fix' anything.

Rules:
- Use null for any box that is empty. Use false for a 'present' flag when nothing is there.
- Copy handwriting character for character (spelling, digits, capitalization). Dates exactly as written.
- 'signature_present' is true only when there are pen strokes in the SIGNATURE box (a flowing signature).
  The Printed Name box is not a signature. Text written in some other box is not a signature.
- "misplaced_entries": list every handwritten entry that sits in a box other than the one its label names,
  as {"text": "...", "written_in": "label of the box it is in", "belongs_in": "label of the box it should be in"}.
  Signature text in the Effective Date box, a relationship in the name box, a percent in the Relationship
  box are all misplaced. Empty list when every entry is in its own box.
- Report where entries actually appear. If something is written in the wrong box, or is illegible,
  scribbled over, or a printed paragraph looks altered/garbled, describe it in "concerns".
- Percentages are numbers without the % sign.
- Return ONLY one JSON object, no prose, no markdown fences."""

_ADDRESS_SCHEMA = """{
  "account_number": null,
  "account_holder_name": null,
  "financial_advisor": null,
  "current_address": {"street": null, "unit": null, "city": null, "state": null, "zip": null},
  "new_address":     {"street": null, "unit": null, "city": null, "state": null, "zip": null},
  "effective_date": null,
  "delivery_boxes": {
    "new_address_box": {"has_mark": false, "mark_description": null},
    "financial_advisor_box": {"has_mark": false, "mark_description": null}
  },
  "signature_present": false,
  "signature_reading": null,
  "date_signed": null,
  "printed_name": null,
  "misplaced_entries": [],
  "concerns": []
}
Section 4 has TWO checkboxes. Look at each one separately, top box first, then the bottom box.
"new_address_box" is the box beside 'Send my statements and account mail to my NEW ADDRESS above'.
"financial_advisor_box" is the box beside 'Send my statements and account mail in care of my FINANCIAL ADVISOR'.
has_mark is true when that square contains ANY pen mark (an X, a tick, a check, a scribble, a filled square);
false only when the square is completely empty. mark_description says what you see inside it ("X", "check", "empty")."""

_BENEFICIARY_SCHEMA = """{
  "account_number": null,
  "account_holder_name": null,
  "financial_advisor": null,
  "primary":    [{"name": null, "relationship": null, "percent": null}],
  "primary_total": null,
  "contingent": [{"name": null, "relationship": null, "percent": null}],
  "contingent_total": null,
  "signature_present": false,
  "signature_reading": null,
  "date_signed": null,
  "printed_name": null,
  "notary": {
    "county": null,
    "date": null,
    "officer_name_title": null,
    "signer_name": null,
    "signature_present": false,
    "signature_reading": null,
    "seal_present": false,
    "seal_text": null,
    "commission_no": null,
    "commission_expires": null
  },
  "misplaced_entries": [],
  "concerns": []
}
List only rows that have handwriting in "primary" and "contingent" (empty list if none).
Each row has THREE separate boxes: Full Legal Name (wide, left), Relationship (middle), Percent (right).
Report each value under the box it is PHYSICALLY written in, even when it is in the wrong box: do NOT move
text to the column where it belongs. If a box is empty use null. Example: if 'Child' is written inside the
name box, the name is 'Sarah Brown Child' and relationship is null; if '100%' is inside the Relationship box,
relationship is '100%' and percent is null. Add a "concerns" note for any text sitting in the wrong box or
crossing a box border. Do NOT autocorrect spelling anywhere, including on the notary seal.
'seal_present' is true only when an ink stamp/seal is visible inside the dashed '(Seal)' box.
'seal_text' is the words you can read on the seal, or null.
The notary block is section 5 (county, date, 'before me' officer name, 'personally appeared' signer,
notary signature, commission number and expiry)."""


def reading_prompt(kind):
    schema = _ADDRESS_SCHEMA if kind == "address" else _BENEFICIARY_SCHEMA
    title = "Address Change Form" if kind == "address" else "Beneficiary Designation Form"
    return "{}\n\nThe form is an Obsidian Capital {}. Fill in this JSON structure:\n{}".format(
        _COMMON_RULES, title, schema)


def review_settings():
    s = hermes_profile.profile_settings(REVIEW_PROFILE)
    model = os.environ.get("ORBIT_REVIEW_MODEL") or s["model"] or DEFAULT_REVIEW_MODEL
    return dict(s, model=model)


def _image_data_url(path):
    """Shrink very large scans and send as JPEG; small files go as-is."""
    with open(path, "rb") as f:
        raw = f.read()
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        if max(img.size) > MAX_IMAGE_SIDE:
            k = MAX_IMAGE_SIDE / float(max(img.size))
            img = img.resize((int(img.width * k), int(img.height * k)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=88)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return "data:{};base64,{}".format(form_agent._sniff(raw), base64.b64encode(raw).decode())


def _extract_json(text):
    if not text:
        raise ReviewError("the review model returned nothing")
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.I)
    try:
        return json.loads(t)
    except ValueError:
        pass
    a, b = t.find("{"), t.rfind("}")
    if a >= 0 and b > a:
        try:
            return json.loads(t[a:b + 1])
        except ValueError:
            pass
    raise ReviewError("could not parse JSON from the review model: {}".format(t[:200]))


def _derive_delivery(r):
    """Turn the per-box marks into delivery_choice (new_address / financial_advisor / both / none)."""
    if not isinstance(r, dict):
        return
    b = r.get("delivery_boxes")
    if isinstance(b, dict):
        def mark(k):
            v = b.get(k)
            return bool(v.get("has_mark")) if isinstance(v, dict) else bool(v)
        n, f = mark("new_address_box"), mark("financial_advisor_box")
        r["delivery_choice"] = ("both" if n and f else "new_address" if n
                                else "financial_advisor" if f else "none")
    else:
        r.setdefault("delivery_choice", "none")


def read_form_image(kind, image_path, verbose=False):
    """Blind reading of the form. Returns (reading_dict, info)."""
    st = review_settings()
    if not st["api_key"]:
        raise ReviewError("No OpenRouter API key found for the review model "
                          "(set OPENROUTER_API_KEY or add it to ~/.hermes/profiles/orbit/.env).")
    if st["provider"] and st["provider"] != "openrouter":
        raise ReviewError("The orbit profile uses provider '{}', but the form review calls OpenRouter "
                          "directly. Set the orbit profile's model provider to OpenRouter.".format(st["provider"]))
    url = _image_data_url(image_path)
    payload = {
        "model": st["model"], "temperature": 0, "max_tokens": 2500,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": reading_prompt(kind)},
            {"type": "image_url", "image_url": {"url": url}}]}],
        "response_format": {"type": "json_object"},
        "usage": {"include": True},                      # ask OpenRouter to report the cost
    }
    if verbose:
        print("  👁  reading form with {}".format(st["model"]))
    last = None
    for attempt in range(2):
        try:
            try:
                resp = form_agent._post_json(form_agent.OPENROUTER_BASE + "/chat/completions",
                                             payload, st["api_key"], timeout=240)
            except form_agent.OpenRouterError as e:
                if e.status in (400, 422) and "response_format" in payload:
                    payload.pop("response_format")      # model doesn't support JSON mode
                    resp = form_agent._post_json(form_agent.OPENROUTER_BASE + "/chat/completions",
                                                 payload, st["api_key"], timeout=240)
                else:
                    raise
            msg = resp["choices"][0]["message"]
            reading = _extract_json(msg.get("content") or "")
            _derive_delivery(reading)
            return reading, {"model": st["model"], "cost": (resp.get("usage") or {}).get("cost")}
        except ReviewError as e:
            last = e                                     # bad JSON: one more try
        except form_agent.OpenRouterError as e:
            raise ReviewError("OpenRouter error — {}".format(e))
        except form_agent.FormGenerationError as e:
            raise ReviewError(str(e))
    raise last


def mock_reading(kind, manifest):
    """A perfect reader: turns the manifest's ground-truth values into a reading."""
    v = manifest["values"]
    r = {"account_number": v.get("account_number"), "account_holder_name": v.get("account_holder"),
         "financial_advisor": v.get("financial_advisor"),
         "signature_present": bool(v.get("signature")), "signature_reading": v.get("signature"),
         "date_signed": v.get("signature_date"), "printed_name": v.get("printed_name"), "concerns": []}
    if kind == "address":
        for side in ("current", "new"):
            r[side + "_address"] = {f: (v.get("{}_{}".format(side, f)) or None)
                                    for f in ("street", "unit", "city", "state", "zip")}
        r["effective_date"] = v.get("effective_date")
        r["delivery_choice"] = {"fa": "financial_advisor", "client": "new_address"}.get(v.get("delivery"), "none")
    else:
        r["primary"] = [dict(b) for b in v.get("primary", [])]
        r["contingent"] = [dict(b) for b in v.get("contingent", [])]
        r["primary_total"] = v.get("primary_total")
        r["contingent_total"] = v.get("contingent_total")
        n = v.get("notary") or {}
        r["notary"] = {"county": n.get("county"), "date": n.get("date"),
                       "officer_name_title": n.get("name"), "signer_name": n.get("signer_name"),
                       "signature_present": bool(n.get("signature")), "signature_reading": n.get("signature"),
                       "seal_present": bool(n.get("seal")),
                       "seal_text": ("NOTARY PUBLIC - CALIFORNIA" if n.get("seal") else None),
                       "commission_no": n.get("commission_no"),
                       "commission_expires": n.get("commission_expires")}
    return r


# ═══════════════════════════════════════════════════════════════════════════
# 2. Rules (Python decides)
# ═══════════════════════════════════════════════════════════════════════════

def _clean(s):
    return re.sub(r"[^a-z0-9 ]", "", re.sub(r"\s+", " ", str(s or "").lower())).strip()


def _sim(a, b):
    return difflib.SequenceMatcher(None, _clean(a), _clean(b)).ratio()


def same_name(a, b, thr=0.88):
    return bool(_clean(a)) and bool(_clean(b)) and _sim(a, b) >= thr


def _digits(s):
    return re.sub(r"\D", "", str(s or ""))


def parse_date(s):
    if not s:
        return None
    t = str(s).strip().replace(".", "/").replace("-", "/")
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d"):
        try:
            return datetime.strptime(t, fmt).date()
        except ValueError:
            continue
    return None


def same_street(a, b):
    """Street numbers must match exactly; the words may differ a little (Street/St)."""
    na, nb = _digits(str(a or "").split(" ")[0]), _digits(str(b or "").split(" ")[0])
    if na != nb:
        return False
    strip = lambda s: re.sub(r"\b(street|st|avenue|ave|boulevard|blvd|drive|dr|lane|ln|court|ct|place|pl|road|rd|terrace|ter|way)\b",
                             "", _clean(s)).strip()
    return _sim(strip(a), strip(b)) >= 0.8


def _addr_matches(obs, want):
    """True when the transcribed address equals `want` (street, city, state, zip; unit if given)."""
    obs = obs or {}
    if not same_street(obs.get("street"), want.get("street")):
        return False
    if not same_name(obs.get("city"), want.get("city"), 0.85):
        return False
    if _clean(obs.get("state")) != _clean(want.get("state") or "CA"):
        return False
    if _digits(obs.get("zip")) != _digits(want.get("zip")):
        return False
    wu, ou = _clean(want.get("unit")), _clean(obs.get("unit"))
    return (not wu and not ou) or _sim(wu, ou) >= 0.8 or (wu and ou and _digits(wu) == _digits(ou))


class _Findings(list):
    def add(self, code, detail):
        self.append({"code": code, "detail": detail})

    def codes(self):
        return {f["code"] for f in self}


def _common_checks(kind, r, client, today, F):
    # identity
    acct = r.get("account_number")
    if not acct:
        F.add("blank_required_field", "Account number is blank")
    elif _clean(acct).replace(" ", "") != _clean(client["account_id"]).replace(" ", ""):
        F.add("wrong_account_number", "Form says {} but the request is for {}".format(acct, client["account_id"]))
    holder, printed = r.get("account_holder_name"), r.get("printed_name")
    if not holder:
        F.add("blank_required_field", "Account holder name is blank")
    elif not same_name(holder, client["name"]):
        F.add("name_mismatch", "Account holder on the form is '{}', registered owner is '{}'".format(holder, client["name"]))
    # Printed name is OPTIONAL (it duplicates the account holder name in section 1).
    # Blank is fine; if something is written it must still match the registered owner.
    if printed and not same_name(printed, client["name"]):
        F.add("name_mismatch", "Printed name on the form is '{}', registered owner is '{}'".format(printed, client["name"]))
    _misplaced(r, F)
    # signature + date
    if not r.get("signature_present"):
        F.add("missing_signature", "The account holder's signature box is empty")
    signed = None
    if not r.get("date_signed"):
        F.add("missing_date", "Date signed is blank")
    else:
        signed = parse_date(r["date_signed"])
        if signed is None:
            F.add("illegible_field", "Date signed is unreadable: '{}'".format(r["date_signed"]))
        elif signed > today:
            F.add("future_date", "Signed {} — a date in the future".format(r["date_signed"]))
        elif (today - signed).days > STALE_DAYS:
            F.add("stale_date", "Signed {} — more than {} days ago".format(r["date_signed"], STALE_DAYS))
    return signed


def _check_address(r, client, requested, F):
    cur = r.get("current_address") or {}
    if not _addr_matches(cur, client.get("address") or {}):
        F.add("current_address_mismatch", "'Current address' on the form ({}) is not the address on file ({})".format(
            _fmt_addr(cur), _fmt_addr(client.get("address") or {})))
    new = r.get("new_address") or {}
    blanks = [f for f in ("street", "city", "state", "zip") if not (new.get(f) or "").strip()]
    if blanks:
        F.add("blank_required_field", "New address is incomplete (missing {})".format(", ".join(blanks)))
    elif not _addr_matches(new, requested):
        F.add("address_mismatch", "New address on the form ({}) is not the one requested ({})".format(
            _fmt_addr(new), _fmt_addr(requested)))
    if not r.get("effective_date"):
        F.add("blank_required_field", "Effective date is blank")
    choice = r.get("delivery_choice") or "none"
    if choice in ("none", "both"):
        F.add("missing_delivery_choice", "Statement delivery: {} — exactly one box must be checked".format(
            "no box checked" if choice == "none" else "both boxes checked"))
    else:
        want = "financial_advisor" if requested.get("mail_to_fa") else "new_address"
        if choice != want:
            F.add("wrong_delivery_choice", "Form asks for delivery '{}' but the request was '{}'".format(
                choice.replace("_", " "), want.replace("_", " ")))


def _fmt_addr(a):
    a = a or {}
    line = ", ".join(x for x in [(a.get("street") or "").strip(), (a.get("unit") or "").strip()] if x)
    return "{}, {} {} {}".format(line or "?", a.get("city") or "?", a.get("state") or "", a.get("zip") or "").strip()


def _num(x):
    try:
        return float(str(x).replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _misplaced(r, F):
    """Entries the reader says are sitting in the wrong box (structured, so it can be acted on)."""
    for m in (r.get("misplaced_entries") or []):
        if not isinstance(m, dict):
            continue
        txt, at, should = (str(m.get(k) or "").strip() for k in ("text", "written_in", "belongs_in"))
        if txt and at and should and at.lower() != should.lower():
            F.add("misplaced_entry", "'{}' is written in the '{}' box".format(txt, at))


def _check_beneficiary(r, client, requested, signed, today, F):
    want_p = [b for b in requested if b["type"] == "primary"]
    want_c = [b for b in requested if b["type"] == "contingent"]
    rows_p = [b for b in (r.get("primary") or []) if (b.get("name") or "").strip()]
    rows_c = [b for b in (r.get("contingent") or []) if (b.get("name") or "").strip()]

    # percentages on the form itself
    sum_p = sum(_num(b.get("percent")) or 0 for b in rows_p)
    if not rows_p:
        F.add("blank_required_field", "No primary beneficiary is listed")
    elif abs(sum_p - 100) > 0.01:
        F.add("percent_not_100", "Primary percentages total {:g}%, not 100%".format(sum_p))
    if rows_c:
        sum_c = sum(_num(b.get("percent")) or 0 for b in rows_c)
        if abs(sum_c - 100) > 0.01:
            F.add("percent_not_100", "Contingent percentages total {:g}%, not 100%".format(sum_c))

    # entries written in the wrong box of a row
    for b in rows_p + rows_c:
        nm, rel, pct = (b.get("name") or ""), str(b.get("relationship") or ""), str(b.get("percent") or "")
        bad = (re.search(r"\d", nm) or re.search(r"\d\s*%|^\s*\d+(\.\d+)?\s*$", rel)
               or (pct.strip() and _num(pct) is None)
               or re.search(r"\b(spouse|child|son|daughter|parent|mother|father|sibling|brother|sister)\s*$", nm, re.I))
        if bad:
            F.add("misplaced_entry", "Entry for '{}' is written in the wrong box (name '{}', relationship "
                  "'{}', percent '{}')".format(nm, nm, rel, pct))
            break

    # relationship must be filled in for every named beneficiary
    for b in rows_p + rows_c:
        if not (b.get("relationship") or "").strip():
            F.add("blank_relationship", "No relationship given for {}".format(b["name"]))
            break

    # form vs request
    def compare(rows, want, label):
        used = set()
        for w in want:
            hit = None
            for i, b in enumerate(rows):
                if i not in used and same_name(b.get("name"), w["name"]):
                    hit = i
                    break
            if hit is None:
                F.add("designation_mismatch", "{} beneficiary '{}' from the request is not on the form".format(label, w["name"]))
                continue
            used.add(hit)
            b = rows[hit]
            if (b.get("relationship") or "").strip() and _clean(b["relationship"]) != _clean(w.get("relationship")):
                F.add("designation_mismatch", "{}: relationship on the form is '{}', requested '{}'".format(
                    w["name"], b["relationship"], w.get("relationship")))
            # a different percent only counts here when the totals still add up (else it's percent_not_100)
            pct_ok_total = abs(sum(_num(x.get("percent")) or 0 for x in rows) - 100) <= 0.01
            if pct_ok_total and _num(b.get("percent")) is not None and abs(_num(b["percent"]) - w["percent"]) > 0.01:
                F.add("designation_mismatch", "{}: form shows {:g}%, requested {:g}%".format(
                    w["name"], _num(b["percent"]), w["percent"]))
        for i, b in enumerate(rows):
            if i not in used:
                F.add("designation_mismatch", "{} beneficiary '{}' on the form was not requested".format(label, b["name"]))
    compare(rows_p, want_p, "Primary")
    compare(rows_c, want_c, "Contingent")

    # notary block
    n = r.get("notary") or {}
    fields = [n.get("county"), n.get("date"), n.get("officer_name_title"), n.get("signer_name"),
              n.get("commission_no"), n.get("commission_expires")]
    blank_notary = (not any((str(x or "").strip()) for x in fields) and not n.get("signature_present")
                    and not n.get("seal_present"))
    if blank_notary:
        F.add("not_notarized", "The notary section is blank — the form is not notarized")
        return
    if not n.get("seal_present"):
        F.add("missing_notary_seal", "No notary seal/stamp in the seal box")
    if not n.get("signature_present"):
        F.add("missing_notary_signature", "The notary has not signed")
    missing = [lbl for lbl, val in (("county", n.get("county")), ("date", n.get("date")),
                                    ("officer name", n.get("officer_name_title")),
                                    ("signer name", n.get("signer_name")),
                                    ("commission number", n.get("commission_no")),
                                    ("commission expiry", n.get("commission_expires"))) if not str(val or "").strip()]
    if missing:
        F.add("blank_required_field", "Notary section incomplete (missing {})".format(", ".join(missing)))
    if n.get("signer_name") and not same_name(n["signer_name"], client["name"]):
        F.add("notary_signer_mismatch", "Notary acknowledged '{}', not the account holder '{}'".format(
            n["signer_name"], client["name"]))
    sig_read, officer = (n.get("signature_reading") or "").strip(), n.get("officer_name_title") or ""
    officer = re.sub(r",?\s*notary.*$", "", officer, flags=re.I).strip()
    if sig_read and officer and same_name(sig_read, client["name"]) and not same_name(sig_read, officer):
        F.add("notary_signature_mismatch", "Notary signature reads '{}' (the account holder), but the officer is '{}'".format(
            sig_read, officer))
    exp = parse_date(n.get("commission_expires"))
    if n.get("commission_expires") and exp is None:
        F.add("illegible_field", "Notary commission expiry unreadable: '{}'".format(n["commission_expires"]))
    elif exp and exp < today:
        F.add("notary_expired", "Notary commission expired {}".format(n["commission_expires"]))
    nd = parse_date(n.get("date"))
    if n.get("date") and nd is None:
        F.add("illegible_field", "Notary date unreadable: '{}'".format(n["date"]))
    elif nd and signed and nd < signed:
        F.add("notary_date_before_signing", "Notarized {} — before the form was signed ({})".format(n["date"], r.get("date_signed")))


# When entries are in the wrong boxes, anything read from the shifted boxes is unreliable (a name where a date
# should be, a missing commission number, "future" dates...). Those are effects, not separate problems.
_KNOCK_ON = {"percent_not_100", "designation_mismatch", "blank_relationship", "illegible_field", "future_date",
             "stale_date", "missing_date", "blank_required_field", "notary_signer_mismatch",
             "notary_date_before_signing", "notary_expired", "name_mismatch", "wrong_delivery_choice",
             "missing_delivery_choice", "current_address_mismatch", "address_mismatch"}


def _collapse_misplaced(findings):
    """One 'misplaced_entry' finding per form. Row errors caused by a shifted column (bad percent total,
    garbled name vs the request) are knock-on effects, so they are dropped to keep the letter to the
    client short and clear; the form is re-requested either way."""
    mis = [f for f in findings if f["code"] == "misplaced_entry"]
    if not mis:
        return findings
    merged = {"code": "misplaced_entry",
              "detail": "Some entries are written in the wrong box: " + "; ".join(
                  f["detail"] for f in mis[:3]) + ". Each entry must go in the box carrying its own label."}
    out, done = [], False
    for f in findings:
        if f["code"] == "misplaced_entry":
            if not done:
                out.append(merged)
                done = True
        elif f["code"] not in _KNOCK_ON:
            out.append(f)
    return out


def evaluate(kind, reading, client, requested, today=None):
    """Returns {"decision": APPROVE|REJECT, "findings": [{code, detail}], "concerns": [...]}."""
    today = today or date.today()
    F = _Findings()
    signed = _common_checks(kind, reading, client, today, F)
    if kind == "address":
        _check_address(reading, client, requested, F)
    else:
        _check_beneficiary(reading, client, requested, signed, today, F)
    findings = _collapse_misplaced(list(F))
    return {"decision": "REJECT" if findings else "APPROVE", "findings": findings,
            "concerns": list(reading.get("concerns") or [])}


# ═══════════════════════════════════════════════════════════════════════════
# 3. Scoring against the manifest
# ═══════════════════════════════════════════════════════════════════════════

def score(manifest, findings):
    injected = {d["name"] for d in manifest.get("defects", [])}
    found = {f["code"] for f in findings}
    return {"injected": sorted(injected), "found": sorted(found),
            "detected": sorted(injected & found), "missed": sorted(injected - found),
            "extra": sorted(found - injected),
            "correct_decision": (bool(found) == bool(injected)) if not (found - injected) else False}


# ── focused signature check ────────────────────────────────────────────────
# The whole-page read can credit the notary's script (or anything else) to the account holder's box.
# So the signature strip is cropped out of the page — the notary section is not even in the picture —
# and the model is asked ONE narrow question about it.
_SIG_STRIP = {"address": (0.76, 0.96), "beneficiary": (0.50, 0.665)}     # vertical fractions of the page

_SIG_PROMPT = """This is a cropped strip of a hand-completed form. Find the box labelled
'Signature of Account Holder'. Look ONLY inside that box.
Is there any pen handwriting (a signature, scribble or name) inside it? Printed labels, the Date Signed
box and the Printed Name box do not count. An empty white box means no.
Return ONLY this JSON object:
{"box_found": true, "has_pen_strokes": false, "what_is_inside": "empty"}"""


def _strip_data_url(kind, path):
    from PIL import Image
    img = Image.open(path).convert("RGB")
    top, bot = _SIG_STRIP[kind]
    img = img.crop((0, int(img.height * top), img.width, int(img.height * bot)))
    if img.width > MAX_IMAGE_SIDE:
        k = MAX_IMAGE_SIDE / float(img.width)
        img = img.resize((int(img.width * k), int(img.height * k)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def check_holder_signature(kind, image_path, verbose=False):
    """Returns ({"box_found", "has_pen_strokes", "what_is_inside"} or None, cost). None = check unavailable."""
    st = review_settings()
    if not st["api_key"]:
        return None, 0.0
    try:
        payload = {"model": st["model"], "temperature": 0, "max_tokens": 300,
                   "messages": [{"role": "user", "content": [
                       {"type": "text", "text": _SIG_PROMPT},
                       {"type": "image_url", "image_url": {"url": _strip_data_url(kind, image_path)}}]}],
                   "usage": {"include": True}}
        resp = form_agent._post_json(form_agent.OPENROUTER_BASE + "/chat/completions",
                                     payload, st["api_key"], timeout=120)
        out = _extract_json(resp["choices"][0]["message"].get("content") or "")
        return out, (resp.get("usage") or {}).get("cost") or 0.0
    except Exception as e:                                   # never block a review on the extra check
        if verbose:
            print("  ⚠ signature strip check unavailable: {}".format(e))
        return None, 0.0


def _reads():
    try:
        return max(1, int(os.environ.get("ORBIT_REVIEW_READS", "2")))
    except ValueError:
        return 2


def review_form(kind, manifest, client, requested, mode="live", today=None, verbose=False, reads=None):
    """Read + evaluate + score one stored form. mode: 'live' (vision model) or 'mock'.

    Live reviews read the image ORBIT_REVIEW_READS times (default 2) and REJECT if any read finds a problem:
    the vision model is not perfectly repeatable, and approving a bad form is worse than re-checking a good one.
    """
    if mode == "mock":
        reading, info = mock_reading(kind, manifest), {"model": "mock reader", "cost": 0}
        verdict = evaluate(kind, reading, client, requested, today)
        findings, disagreement = verdict["findings"], []
    else:
        n = reads or _reads()
        runs, cost = [], 0.0

        def _read_once():
            nonlocal cost
            rd, inf = read_form_image(kind, manifest["image_path"], verbose)
            cost += inf.get("cost") or 0
            runs.append((rd, evaluate(kind, rd, client, requested, today)))
            return inf

        for _ in range(n):
            info = _read_once()
        # Reads that split on a finding mean the model is unsure (e.g. a handwritten 2030 read as 2020).
        # Take one more read and keep the finding only if most reads found it. A missing signature is the
        # exception: it stays "reject if any read sees it", and the cropped check below has the final say.
        def _counts():
            c = {}
            for _, v in runs:
                for f in v["findings"]:
                    c[f["code"]] = c.get(f["code"], 0) + 1
            return c
        split = [k for k, v in _counts().items() if v < len(runs) and k != "missing_signature"]
        if split and len(runs) < 3:
            info = _read_once()
        seen, total = _counts(), len(runs)
        info = dict(info, cost=cost, reads=total)
        best = max(runs, key=lambda x: len(x[1]["findings"]))
        reading, verdict = best
        findings = []
        for _, v in runs:
            for f in v["findings"]:
                if f["code"] in {x["code"] for x in findings}:
                    continue
                if f["code"] == "missing_signature" or seen[f["code"]] * 2 > total:
                    findings.append(f)
        disagreement = sorted(c for c, k in seen.items() if k < total)
        verdict = dict(verdict, decision="REJECT" if findings else "APPROVE", findings=findings)
        sig, scost = check_holder_signature(kind, manifest["image_path"], verbose)
        info["cost"] += scost
        if (sig and sig.get("box_found") and not sig.get("has_pen_strokes")
                and "missing_signature" not in seen):
            findings.append({"code": "missing_signature",
                             "detail": "The account holder's signature box is empty"})
            disagreement = sorted(set(disagreement) | {"missing_signature"})
        elif sig and sig.get("has_pen_strokes") and "missing_signature" in seen:
            disagreement = sorted(set(disagreement) | {"missing_signature"})
        verdict = dict(verdict, decision="REJECT" if findings else "APPROVE", findings=findings)
    return {"reading": reading, "reader": info, "decision": verdict["decision"],
            "findings": verdict["findings"], "concerns": verdict["concerns"],
            "read_disagreement": disagreement,
            "score": score(manifest, verdict["findings"])}


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="Review one stored form (uses the manifest .json)")
    ap.add_argument("manifest", nargs="?")
    ap.add_argument("--mock", action="store_true", help="use the manifest as a perfect reader (no API call)")
    ap.add_argument("--show-config", action="store_true")
    ap.add_argument("--print-prompt", action="store_true")
    ap.add_argument("--clients", default=form_agent.CLIENTS_FILE)
    a = ap.parse_args()

    if a.show_config:
        st = review_settings()
        key = st["api_key"]
        print("review profile: {}\n  model:    {}   (from {})\n  provider: {}\n  api key:  {}".format(
            REVIEW_PROFILE, st["model"], st["model_source"] or "default",
            st["provider"] or "unknown", ("…" + key[-4:]) if key else "NOT FOUND"))
        return
    if not a.manifest:
        ap.error("give a manifest .json (or --show-config)")
    with open(a.manifest) as f:
        m = json.load(f)
    if not m.get("image_path") or not os.path.isabs(m["image_path"]):
        m["image_path"] = os.path.join(os.path.dirname(os.path.abspath(a.manifest)),
                                       os.path.basename(m.get("image_path") or a.manifest.replace(".json", ".png")))
    if a.print_prompt:
        print(reading_prompt(m["kind"]))
        return
    with open(a.clients) as f:
        client = next(c for c in json.load(f)["clients"] if c["account_id"] == m["account_id"])
    # review against the CRM as the form was made (address form compares to the address on file)
    res = review_form(m["kind"], m, client, m["requested"], "mock" if a.mock else "live", verbose=True)
    print("\nDecision: {}".format(res["decision"]))
    for f in res["findings"]:
        print("  ✗ [{}] {}".format(f["code"], f["detail"]))
    for c in res["concerns"]:
        print("  ℹ concern: {}".format(c))
    if res.get("read_disagreement"):
        print("  ⚠ the reads disagreed on: {}".format(", ".join(res["read_disagreement"])))
    s = res["score"]
    print("\nInjected: {} | Detected: {} | Missed: {} | Extra: {}".format(
        s["injected"] or "none", s["detected"] or "-", s["missed"] or "-", s["extra"] or "-"))


if __name__ == "__main__":
    main()
