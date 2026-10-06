#!/usr/bin/env python3
"""
form_agent.py — Fills in, signs and (for beneficiary forms) notarizes Orbit's paperwork.

Given a client and a pending request (new address / new beneficiary designation) this
module produces the *signed form image* a real client would send back, plus a JSON
manifest that records exactly what the form was supposed to contain and which defects
(if any) were deliberately injected (~20% of forms). The manifest is the ground truth
Orbit's review is later scored against.

How the image is made
  1. form_templates draws the blank form with Pillow (printed text stays reliable).
  2. The model set on the Hermes profile "form_agent" (change it any time with
     `form_agent model` / `hermes -p form_agent model`) is sent the blank page plus the
     exact entries to hand-write, the signature, and the notary block/seal.
  3. The result is saved to orbit_forms/ with a manifest next to it.
  The call goes straight to OpenRouter (Hermes -z is text-only), using the model, key and
  SOUL.md from the profile. Default model if the profile doesn't exist yet:
  qwen/qwen-image-3-pro.

  --backend mock draws the filled form in Python instead (no network, no cost) — used for
  offline testing and as a visual reference.

This script NEVER modifies crm_clients.json.

Usage
    python3 form_agent.py --show-config
    python3 form_agent.py --list-defects
    python3 form_agent.py --smoke-test                    # 1 clean form of each kind, via the model
    python3 form_agent.py --smoke-test --kind address --print-prompt      # no API call
    python3 form_agent.py --smoke-test --backend mock --defects random
    python3 form_agent.py --smoke-test --kind beneficiary --defects missing_notary_seal,percent_not_100
"""

import argparse
import base64
import io
import json
import os
import random
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PAPER_DIR = os.path.dirname(SCRIPT_DIR)
CLIENTS_FILE = os.path.join(PAPER_DIR, "venus", "crm_clients.json")
OUT_DIR = os.path.join(SCRIPT_DIR, "orbit_forms")

sys.path.insert(0, SCRIPT_DIR)
from crm_schema import (SCHEMA_VERSION, FIRST_F, FIRST_M, LAST_NAMES, infer_gender,
                        county_for_city, random_address, make_designation, format_address,
                        format_designation)
from form_templates import (build_template, flatten_values, render_filled_mock,
                            PRIMARY_ROWS, CONTINGENT_ROWS, FORM_TITLES)
import hermes_profile

PROFILE = "form_agent"
DEFAULT_IMAGE_MODEL = "qwen/qwen-image-3-pro"
OPENROUTER_BASE = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
DEFECT_RATE = 0.20
# The printed name duplicates the name in section 1 and Orbit treats it as optional. Asking the image model for it
# next to the cursive signature seems to make it drop the signature, so it is left blank unless this is set.
FILL_PRINTED_NAME = os.environ.get("ORBIT_FILL_PRINTED_NAME", "0") == "1"
MAX_IMAGES_PER_RUN = int(os.environ.get("ORBIT_MAX_IMAGES", "10"))
KIND_CODE = {"address": "ADR", "beneficiary": "BEN"}


class FormGenerationError(Exception):
    pass


class FormBudgetExceeded(FormGenerationError):
    pass


class FormBudget:
    """Caps how many images one run may request (each call costs money)."""
    def __init__(self, max_images=MAX_IMAGES_PER_RUN):
        self.max_images, self.used = max_images, 0

    def take(self):
        if self.used >= self.max_images:
            raise FormBudgetExceeded("image budget of {} per run reached "
                                     "(raise with ORBIT_MAX_IMAGES)".format(self.max_images))
        self.used += 1


# ═══════════════════════════════════════════════════════════════════════════
# Defect catalog
# ═══════════════════════════════════════════════════════════════════════════

def _fmt(d):
    return d.strftime("%m/%d/%Y")


def _alter_name(name, rng):
    first, last = name.split()[0], name.split()[-1]
    if rng.random() < 0.35:
        return "{} {}".format(first, rng.choice([n for n in LAST_NAMES if n != last]))
    g = infer_gender(first)
    pool = FIRST_F if g == "F" else FIRST_M if g == "M" else FIRST_M + FIRST_F
    return "{} {}".format(rng.choice([n for n in pool if n != first]), last)


def _alter_street_number(street, rng):
    m = re.match(r"^(\d+)(.*)$", street)
    if not m:
        return "9" + street
    n = int(m.group(1))
    new = max(1, n + rng.choice([-1, 1]) * rng.randint(1, 40))
    return "{}{}".format(new if new != n else n + 7, m.group(2))


def _d_missing_signature(v, c, r, rng, today):
    v["signature"] = None
    return "Signature line left blank"


def _d_missing_date(v, c, r, rng, today):
    v["signature_date"] = None
    return "Date signed left blank"


def _d_name_mismatch(v, c, r, rng, today):
    v["account_holder"] = _alter_name(c["name"], rng)
    return "Account holder name ({}) does not match the registered owner ({})".format(
        v["account_holder"], c["name"])


def _d_wrong_account(v, c, r, rng, today):
    real = c["account_id"]
    while True:
        wrong = "OC-CLIENT-{:04d}".format(rng.randint(1, 1000))
        if wrong != real:
            break
    v["account_number"] = wrong
    return "Account number ({}) does not match the account ({})".format(wrong, real)


def _d_stale_date(v, c, r, rng, today):
    v["signature_date"] = _fmt(today - timedelta(days=rng.randint(75, 200)))
    if v.get("notary"):
        v["notary"]["date"] = v["signature_date"]
    return "Form was signed {} — too old to accept".format(v["signature_date"])


def _d_future_date(v, c, r, rng, today):
    v["signature_date"] = _fmt(today + timedelta(days=rng.randint(5, 30)))
    if v.get("notary"):
        v["notary"]["date"] = v["signature_date"]
    return "Signature dated in the future ({})".format(v["signature_date"])


# address-only
def _d_address_mismatch(v, c, r, rng, today):
    v["new_street"] = _alter_street_number(v["new_street"], rng)
    return "New street address on the form ({}) differs from the requested one".format(v["new_street"])


def _d_current_mismatch(v, c, r, rng, today):
    v["current_street"] = _alter_street_number(v["current_street"], rng)
    return "'Current address' on the form ({}) is not the address on file".format(v["current_street"])


def _d_blank_required(v, c, r, rng, today):
    f = rng.choice(["new_city", "new_zip", "new_street"])
    v[f] = None
    return "Required field left blank: {}".format(f)


def _d_missing_delivery(v, c, r, rng, today):
    v["delivery"] = None
    return "Neither statement-delivery box was checked"


def _d_wrong_delivery(v, c, r, rng, today):
    v["delivery"] = "client" if v.get("delivery") == "fa" else "fa"
    return "Statement-delivery box contradicts the request"


# beneficiary-only
def _d_percent_not_100(v, c, r, rng, today):
    p = v["primary"][0]
    p["percent"] = p["percent"] - 10 if p["percent"] > 10 else p["percent"] + 10
    v["primary_total"] = sum(b["percent"] for b in v["primary"])
    return "Primary percentages total {:g}%, not 100%".format(v["primary_total"])


def _d_designation_mismatch(v, c, r, rng, today):
    p = v["primary"][0]
    p["name"] = _alter_name(p["name"], rng)
    return "First primary beneficiary on the form ({}) is not who was requested".format(p["name"])


def _d_blank_relationship(v, c, r, rng, today):
    v["primary"][0]["relationship"] = None
    return "Relationship left blank for the first primary beneficiary"


def _d_missing_seal(v, c, r, rng, today):
    v["notary"]["seal"] = False
    return "Notary seal/stamp missing"


def _d_missing_notary_sig(v, c, r, rng, today):
    v["notary"]["signature"] = None
    return "Notary signature missing"


def _d_not_notarized(v, c, r, rng, today):
    v["notary"] = {}
    return "Notary block entirely blank (form not notarized)"


def _d_notary_expired(v, c, r, rng, today):
    v["notary"]["commission_expires"] = _fmt(today - timedelta(days=rng.randint(30, 500)))
    return "Notary commission expired ({})".format(v["notary"]["commission_expires"])


def _d_notary_before_signing(v, c, r, rng, today):
    signed = datetime.strptime(v["signature_date"], "%m/%d/%Y").date()
    v["notary"]["date"] = _fmt(signed - timedelta(days=rng.randint(3, 10)))
    return "Notarized ({}) before the form was signed ({})".format(v["notary"]["date"], v["signature_date"])


def _d_notary_signer_mismatch(v, c, r, rng, today):
    v["notary"]["signer_name"] = _alter_name(c["name"], rng)
    return "Notary acknowledged a different person ({})".format(v["notary"]["signer_name"])


_COMMON = {
    "missing_signature": _d_missing_signature, "missing_date": _d_missing_date,
    "name_mismatch": _d_name_mismatch, "wrong_account_number": _d_wrong_account,
    "stale_date": _d_stale_date, "future_date": _d_future_date,
}
DEFECTS = {
    "address": dict(_COMMON, **{
        "address_mismatch": _d_address_mismatch, "current_address_mismatch": _d_current_mismatch,
        "blank_required_field": _d_blank_required, "missing_delivery_choice": _d_missing_delivery,
        "wrong_delivery_choice": _d_wrong_delivery}),
    "beneficiary": dict(_COMMON, **{
        "percent_not_100": _d_percent_not_100, "designation_mismatch": _d_designation_mismatch,
        "blank_relationship": _d_blank_relationship, "missing_notary_seal": _d_missing_seal,
        "missing_notary_signature": _d_missing_notary_sig, "not_notarized": _d_not_notarized,
        "notary_expired": _d_notary_expired, "notary_date_before_signing": _d_notary_before_signing,
        "notary_signer_mismatch": _d_notary_signer_mismatch}),
}

_NOTARY_ONLY = {"missing_notary_seal", "missing_notary_signature", "notary_expired",
                "notary_date_before_signing", "notary_signer_mismatch"}
_DATE_DEFECTS = {"missing_date", "stale_date", "future_date"}


def _compatible(names):
    s = set(names)
    if "not_notarized" in s and s & _NOTARY_ONLY:
        return False
    if len(s & _DATE_DEFECTS) > 1:
        return False
    if "notary_date_before_signing" in s and s & _DATE_DEFECTS:
        return False
    return True


def choose_defects(kind, rng, rate=DEFECT_RATE, forced=None):
    """forced=None -> roll `rate`; forced=[] -> none; forced=[names] -> exactly those."""
    catalog = DEFECTS[kind]
    if forced is not None:
        bad = [n for n in forced if n not in catalog]
        if bad:
            raise FormGenerationError("unknown defect(s) for {} form: {} (see --list-defects)".format(
                kind, ", ".join(bad)))
        return list(forced)
    if rng.random() >= rate:
        return []
    n = 1 if rng.random() < 0.8 else 2
    for _ in range(30):
        pick = rng.sample(sorted(catalog), n)
        if _compatible(pick):
            return pick
    return [rng.choice(sorted(catalog))]


# ═══════════════════════════════════════════════════════════════════════════
# Form contents
# ═══════════════════════════════════════════════════════════════════════════

def _notary(rng, client, signed, today):
    g = rng.choice("MF")
    first = rng.choice(FIRST_F if g == "F" else FIRST_M)
    name = "{} {}. {}".format(first, rng.choice("ABCDEFGHJKLMNPRSTW"), rng.choice(LAST_NAMES))
    return {
        "county": county_for_city(client["address"]["city"]),
        "date": _fmt(signed),
        "name": "{}, Notary Public".format(name),
        "signer_name": client["name"],
        "signature": name,
        "commission_no": "2{:06d}".format(rng.randint(0, 999999)),
        "commission_expires": _fmt(today + timedelta(days=rng.randint(400, 1400))),
        "seal": True,
    }


def build_form_values(kind, client, requested, rng, defects, today=None):
    """
    The complete content the form should visibly carry (clean values, then defects applied).
    Returns (values, defect_notes) where defect_notes = [{"name", "description"}].
    """
    today = today or date.today()
    signed = today - timedelta(days=rng.randint(0, 3))
    v = {"account_number": client["account_id"], "account_holder": client["name"],
         "financial_advisor": client["fa_name"], "signature": client["name"],
         "signature_date": _fmt(signed), "printed_name": client["name"] if FILL_PRINTED_NAME else None}

    if kind == "address":
        cur = client.get("address") or {}
        for f in ("street", "unit", "city", "state", "zip"):
            v["current_" + f] = cur.get(f, "")
            v["new_" + f] = requested.get(f, "")
        v["effective_date"] = _fmt(today)
        v["delivery"] = "fa" if requested.get("mail_to_fa") else "client"
    else:
        prim = [dict(name=b["name"], relationship=b.get("relationship"), percent=b["percent"])
                for b in requested if b["type"] == "primary"]
        cont = [dict(name=b["name"], relationship=b.get("relationship"), percent=b["percent"])
                for b in requested if b["type"] == "contingent"]
        if len(prim) > PRIMARY_ROWS or len(cont) > CONTINGENT_ROWS:
            raise FormGenerationError("the form has room for {} primary and {} contingent "
                                      "beneficiaries".format(PRIMARY_ROWS, CONTINGENT_ROWS))
        v["primary"], v["contingent"] = prim, cont
        v["primary_total"] = sum(b["percent"] for b in prim)
        v["contingent_total"] = sum(b["percent"] for b in cont) if cont else None
        v["notary"] = _notary(rng, client, signed, today)

    notes = []
    for name in defects:
        desc = DEFECTS[kind][name](v, client, requested, rng, today)
        notes.append({"name": name, "description": desc})
    return v, notes


# ═══════════════════════════════════════════════════════════════════════════
# Prompt for the image model
# ═══════════════════════════════════════════════════════════════════════════

_RETRY_HINTS = {
    "missing_signature": "The signature box was left empty. Sign in the 'Signature of Account Holder' box.",
    "misplaced_entry": "Entries were written in the wrong boxes. Write EACH entry only inside the box that carries "
                       "its own printed label, work section by section from the top, and leave every other box "
                       "(unused beneficiary rows, the whole contingent section when it is not listed) empty.",
    "missing_date": "A date box was left blank. Fill in the date exactly as given.",
    "illegible_field": "A field was unreadable. Write it neatly and clearly.",
    "blank_required_field": "A required box was blank. Fill in every entry listed above.",
    "missing_delivery_choice": "No delivery box was ticked. Tick exactly the one box listed above.",
    "notary_signer_mismatch": "The notary section named the wrong person. 'personally appeared' holds the "
                              "account holder's name; the officer's name goes only in 'before me'.",
}


def retry_text(findings):
    """Instruction block telling the image model what was wrong with the previous attempt."""
    seen, lines = set(), []
    for f in findings or []:
        code = f.get("code") if isinstance(f, dict) else None
        if code and code not in seen:
            seen.add(code)
            lines.append(_RETRY_HINTS.get(code) or "Last attempt problem: {}".format(f.get("detail", code)))
    if not lines:
        return ""
    return ("\nCORRECTIONS: the previous attempt at this form was returned. Fix these and change nothing else: "
            + " ".join(lines))


def build_prompt(kind, values, template, soul="", retry_findings=None):
    title = FORM_TITLES[kind][0].title()
    flat = flatten_values(kind, values)
    fields = template.fields
    entries, empties, checks = [], [], []
    signature = notary_sig = seal = None

    for name, val in flat:
        f = fields.get(name)
        if f is None:
            continue
        label = 'section "{}", label "{}"'.format(f["section"].split("  ", 1)[-1].strip(), f["label"])
        t = f["type"]
        if t == "checkbox":
            (checks if val else empties).append('"{}"'.format(f["label"]))
        elif t == "signature":
            if name == "signature":
                signature = val
            else:
                notary_sig = val
        elif t == "seal":
            seal = val
        elif val in (None, ""):
            empties.append(label)
        else:
            entries.append("- {}: {}".format(label, val))

    p = []
    if soul:
        p.append(soul.strip() + "\n")
    p.append("TASK: The attached image is a blank, printed U.S. financial-services form ({}). "
             "Return the SAME page, filled out by hand by the account holder and scanned flat."
             .format(title))
    p.append("\nSTRICT RULES\n"
             "1. Do not change, move, add or remove ANY printed text, section bars, boxes, lines or logos.\n"
             "2. Write only the entries listed below, in blue ballpoint-pen handwriting, small and neat, "
             "inside the box that carries the printed label given. Copy every character EXACTLY as given "
             "(letters, digits, capitalization, punctuation). Never correct, complete or 'fix' anything, "
             "even if it looks wrong.\n"
             "3. Every field not listed under ENTRIES must stay completely empty.\n"
             "4. Output the full portrait page with the same proportions as the attached image: header, "
             "every section and the footer must be visible.")
    p.append("\nENTRIES (printed label -> handwritten text):\n" + "\n".join(entries))
    if checks:
        p.append("\nCHECKBOXES: mark ONLY these with one hand-drawn X: " + "; ".join(checks)
                 + ". All other checkboxes stay empty.")
    elif kind == "address":
        p.append("\nCHECKBOXES: leave both statement-delivery boxes empty (no marks at all).")

    date_labels = [f["label"] for nm, f in fields.items() if "(MM/DD/YYYY)" in f["label"]
                   and any(nm == n2 and v not in (None, "") for n2, v in flat)]
    notes = []
    if date_labels:
        notes.append("Date boxes ({}) get ONLY digits and slashes, exactly as given. A date box never "
                     "contains a name or a signature.".format("; ".join(date_labels)))
    if any(f["label"].startswith("Printed Name") and any(nm == n2 and v not in (None, "") for n2, v in flat)
           for nm, f in fields.items()):
        notes.append("'Printed Name of Account Holder' is written in plain block-print handwriting, "
                     "NOT cursive.")
    if kind == "beneficiary":
        notes.append("Section 5 boxes, left to right: 'County' holds a county name; 'On (date)' holds ONLY a date; "
                     "'before me' holds the notary's printed NAME followed by the words Notary Public (letters, "
                     "never a date); 'personally appeared' holds the signer's name. Do not swap them.")
        notes.append("The long printed paragraphs (section 4 and the notary acknowledgment) must stay pixel-for-pixel "
                     "as printed; do not rewrite, smudge or write over them.")
    if notes:
        p.append("\nFIELD NOTES:\n" + "\n".join("- " + n for n in notes))

    if signature:
        p.append("\nSIGNATURE: In the large 'Signature of Account Holder' box of the SIGNATURE / "
                 "AUTHORIZATION section (the wide box under that printed label, to the left of the "
                 "'Date Signed' box), sign with a natural, flowing cursive pen signature of the name "
                 "\"{}\" (real handwriting with loops and a slight slant — not typed text). This box MUST "
                 "contain the signature: never leave it empty. It is the ONLY place the account holder's "
                 "cursive signature appears; do not sign anywhere else.".format(signature))
    if kind == "beneficiary":
        n = values.get("notary") or {}
        if notary_sig:
            p.append("NOTARY SIGNATURE: In the 'Signature of Notary Public' box, sign with a different, "
                     "natural cursive signature of \"{}\".".format(notary_sig))
        if seal:
            p.append("NOTARY SEAL: Inside the dashed box marked '(Seal)', press a round PURPLE rubber-stamp "
                     "notary seal about 1.5 inches across, slightly uneven, with two concentric rings and "
                     "this text inside (spell it exactly; the top arc reads N-O-T-A-R-Y  P-U-B-L-I-C  -  C-A-L-I-F-O-R-N-I-A): "
                     "NOTARY PUBLIC - CALIFORNIA / {} / COMM. # {} / {} COUNTY / EXP. {}."
                     .format((n.get("name") or "").split(",")[0].upper(), n.get("commission_no", ""),
                             (n.get("county") or "").upper(), n.get("commission_expires", "")))
    empty_lines = list(empties)
    if not signature:
        empty_lines.append("'Signature of Account Holder' box (unsigned)")
    if kind == "beneficiary":
        if not notary_sig:
            empty_lines.append("'Signature of Notary Public' box (unsigned)")
        if not seal:
            empty_lines.append("the '(Seal)' box — no stamp, ink or marks anywhere in it")
    if empty_lines:
        p.append("\nLEAVE COMPLETELY EMPTY: " + "; ".join(empty_lines) + ".")
    rt = retry_text(retry_findings)
    if rt:
        p.append(rt)
    p.append("\nFINISH: Make it look like a slightly imperfect photocopy scan: tilt under 1 degree, faint "
             "paper grain, a few tiny specks. Ink is blue; the notary seal is purple.")
    return "\n".join(p)


# ═══════════════════════════════════════════════════════════════════════════
# OpenRouter image call
# ═══════════════════════════════════════════════════════════════════════════

class OpenRouterError(Exception):
    def __init__(self, status, body):
        Exception.__init__(self, "HTTP {}: {}".format(status, body[:300]))
        self.status, self.body = status, body


def _post_json(url, payload, key, timeout=300):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST",
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
                 "HTTP-Referer": "https://github.com/Phooey2026/paper-trade-sim",
                 "X-Title": "Obsidian Capital Orbit"})
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            if e.code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(4 * (attempt + 1))
                last = OpenRouterError(e.code, body)
                continue
            raise OpenRouterError(e.code, body)
        except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
            last = e
            if attempt < 2:
                time.sleep(4 * (attempt + 1))
                continue
            raise FormGenerationError("Could not reach OpenRouter ({}). Are you online?".format(e))
    raise FormGenerationError(str(last))


def _sniff(b):
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if b[:2] == b"\xff\xd8":
        return "image/jpeg"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


def _from_data_url(u):
    if u.startswith("data:"):
        return base64.b64decode(u.split(",", 1)[1])
    with urllib.request.urlopen(u, timeout=120) as r:
        return r.read()


_model_cache = {}


def check_model_supports_images(model):
    """Best-effort: refuse a model that clearly can't take a template image or return an image."""
    try:
        if not _model_cache:
            with urllib.request.urlopen(OPENROUTER_BASE + "/models", timeout=30) as r:
                for m in json.loads(r.read().decode()).get("data", []):
                    _model_cache[m["id"]] = m.get("architecture", {})
    except Exception:
        return                                  # offline/lookup failed: let the real call decide
    arch = _model_cache.get(model)
    if arch is None:
        return
    outs, ins = arch.get("output_modalities") or [], arch.get("input_modalities") or []
    if "image" not in outs:
        raise FormGenerationError(
            "The model '{0}' set on the form_agent profile cannot output images.\n"
            "  Fix:  form_agent model      (or: hermes -p form_agent model)\n"
            "  and choose an image model, e.g. qwen/qwen-image-3-pro.".format(model))
    if "image" not in ins:
        raise FormGenerationError(
            "The model '{0}' cannot take an image as input, so it can't fill in the template.\n"
            "  Pick an image-editing model with `form_agent model`.".format(model))


def openrouter_fill_image(model, prompt, template_png, key, verbose=False):
    """Returns (image_bytes, media_type, info). Tries /images first, then chat/completions."""
    data_url = "data:image/png;base64," + base64.b64encode(template_png).decode()
    info = {"endpoint": None, "cost": None}
    try:
        resp = _post_json(OPENROUTER_BASE + "/images", {
            "model": model, "prompt": prompt, "n": 1,
            "input_references": [{"type": "image_url", "image_url": {"url": data_url}}],
        }, key)
        d = (resp.get("data") or [None])[0]
        if not d:
            raise FormGenerationError("OpenRouter /images returned no image: {}".format(str(resp)[:300]))
        raw = base64.b64decode(d["b64_json"]) if d.get("b64_json") else _from_data_url(d["url"])
        info.update(endpoint="/images", cost=(resp.get("usage") or {}).get("cost"))
        return raw, d.get("media_type") or _sniff(raw), info
    except OpenRouterError as e:
        if e.status not in (400, 404, 405, 422):
            raise FormGenerationError("OpenRouter error — {}".format(e))
        if verbose:
            print("  ↪ /images refused ({}); trying chat/completions".format(e.status))
        first_err = e

    last = first_err
    for modalities in (["image"], ["image", "text"]):
        try:
            resp = _post_json(OPENROUTER_BASE + "/chat/completions", {
                "model": model, "modalities": modalities,
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}}]}],
            }, key)
            msg = resp["choices"][0]["message"]
            imgs = msg.get("images") or []
            if not imgs:
                raise FormGenerationError("model returned no image (text only): {}".format(
                    str(msg.get("content"))[:200]))
            raw = _from_data_url(imgs[0]["image_url"]["url"])
            info.update(endpoint="/chat/completions", cost=(resp.get("usage") or {}).get("cost"))
            return raw, _sniff(raw), info
        except OpenRouterError as e:
            last = e
    raise FormGenerationError("OpenRouter could not generate the form image.\n"
                              "  /images said:           {}\n  /chat/completions said: {}".format(first_err, last))


# ═══════════════════════════════════════════════════════════════════════════
# Public entry point
# ═══════════════════════════════════════════════════════════════════════════

def _png_bytes(img):
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def new_form_id(client, kind, out_dir, prefix="FORM"):
    base = "{}-{}-{}-{}".format(prefix, client["account_id"], KIND_CODE[kind],
                                datetime.now().strftime("%Y%m%d%H%M%S"))
    fid, n = base, 1
    while any(os.path.exists(os.path.join(out_dir, fid + ext)) for ext in (".json", ".png", ".jpg", ".webp")):
        n += 1
        fid = "{}-{}".format(base, n)
    return fid


def generate_form(kind, client, requested, issue_id=None, request_id=None,
                  defect_rate=DEFECT_RATE, force_defects=None, backend="openrouter",
                  model=None, out_dir=OUT_DIR, seed=None, budget=None, today=None,
                  id_prefix="FORM", verbose=False, print_prompt=False, retry_findings=None):
    """
    Create one signed form. Returns the manifest dict (with image_path / manifest_path).
    Raises FormGenerationError on any failure — callers leave the request pending.
    """
    if kind not in DEFECTS:
        raise FormGenerationError("unknown form kind: {}".format(kind))
    os.makedirs(out_dir, exist_ok=True)
    rng = random.Random(seed) if seed is not None else random.Random()
    defects = choose_defects(kind, rng, defect_rate, force_defects)
    values, notes = build_form_values(kind, client, requested, rng, defects, today)
    tpl = build_template(kind)
    form_id = new_form_id(client, kind, out_dir, id_prefix)
    soul = hermes_profile.soul_text(PROFILE)
    prompt = build_prompt(kind, values, tpl, soul, retry_findings)

    manifest = {
        "form_id": form_id, "kind": kind, "account_id": client["account_id"],
        "account_name": client["name"], "issue_id": issue_id, "request_id": request_id,
        "created_at": datetime.now().isoformat(), "backend": backend, "model": None,
        "defects": notes, "expected_decision": "REJECT" if notes else "APPROVE",
        "values": values, "requested": requested, "prompt": prompt,
        "retry_findings": [f.get("code") for f in (retry_findings or []) if isinstance(f, dict)],
    }

    if print_prompt:
        blank = os.path.join(out_dir, "{}_blank.png".format(form_id))
        tpl.image.save(blank)
        manifest["blank_template"] = blank
        return manifest

    if backend == "mock":
        raw, ext, info = _png_bytes(render_filled_mock(kind, values, seed=rng.randint(0, 10 ** 6))), ".png", \
                         {"endpoint": "mock", "cost": 0}
        manifest["model"] = "mock (Python-rendered)"
    else:
        settings = hermes_profile.profile_settings(PROFILE)
        model = model or os.environ.get("FORM_AGENT_MODEL") or settings["model"] or DEFAULT_IMAGE_MODEL
        if not settings["model"] and not model == os.environ.get("FORM_AGENT_MODEL") and verbose:
            print("  ℹ️  No '{}' Hermes profile found — using default model {}".format(PROFILE, model))
        if settings["provider"] and settings["provider"] != "openrouter":
            raise FormGenerationError(
                "The form_agent profile uses provider '{}', but form generation calls OpenRouter directly.\n"
                "  Run `form_agent model` and pick OpenRouter.".format(settings["provider"]))
        if not settings["api_key"]:
            raise FormGenerationError(
                "No OpenRouter API key found. Set OPENROUTER_API_KEY, or make sure "
                "~/.hermes/profiles/{0}/.env (or ~/.hermes/.env) contains OPENROUTER_API_KEY.".format(PROFILE))
        check_model_supports_images(model)
        if budget:
            budget.take()
        if verbose:
            print("  🎨 {} → {}".format(kind, model))
        raw, media, info = openrouter_fill_image(model, prompt, _png_bytes(tpl.image),
                                                 settings["api_key"], verbose)
        ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(media, ".png")
        manifest["model"] = model

    image_path = os.path.join(out_dir, form_id + ext)
    with open(image_path, "wb") as f:
        f.write(raw)
    try:
        from PIL import Image
        with Image.open(io.BytesIO(raw)) as im:
            manifest["image_size"] = list(im.size)
    except Exception:
        manifest["image_size"] = None
    manifest.update(image_path=image_path, generation=info)
    manifest_path = os.path.join(out_dir, form_id + ".json")
    manifest["manifest_path"] = manifest_path
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    return manifest


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════

def _pick_client(clients, kind, account, rng):
    if account:
        c = next((c for c in clients if c["account_id"] == account), None)
        if not c:
            sys.exit("❌ No such account: {}".format(account))
        return c
    active = [c for c in clients if c.get("status") == "ACTIVE" and c["ocrff_account_value"] > 0]
    with_pending = [c for c in active if kind in c.get("pending_changes", {})]
    return rng.choice(with_pending or active)


def _sample_request(kind, client, rng):
    pending = client.get("pending_changes", {}).get(kind)
    if pending:
        return pending["requested"], "pending request {}".format(pending["request_id"])
    if kind == "address":
        addr = random_address(rng, client.get("territory"), 0.25, exclude=client.get("address"))
        addr["mail_to_fa"] = bool(client.get("mail_to_fa"))
        return addr, "a sample new address (no pending request)"
    return make_designation(rng, client["name"]), "a sample designation (no pending request)"


def main():
    ap = argparse.ArgumentParser(description="Orbit form_agent — fill, sign and notarize forms")
    ap.add_argument("--smoke-test", action="store_true", help="Make one form per kind for you to inspect")
    ap.add_argument("--kind", choices=["address", "beneficiary", "both"], default="both")
    ap.add_argument("--account", help="Account ID to use (default: a client with a pending request)")
    ap.add_argument("--backend", choices=["openrouter", "mock"], default="openrouter")
    ap.add_argument("--model", help="Override the model (default: form_agent profile's model)")
    ap.add_argument("--defects", default="none",
                    help="none | random | comma-separated defect names (see --list-defects)")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--out-dir", default=OUT_DIR)
    ap.add_argument("--print-prompt", action="store_true",
                    help="Build the prompt + blank template but do NOT call the model")
    ap.add_argument("--show-config", action="store_true", help="Show which model/key would be used")
    ap.add_argument("--list-defects", action="store_true")
    ap.add_argument("--blank", action="store_true", help="Write the blank templates and exit")
    args = ap.parse_args()

    if args.list_defects:
        for kind, cat in DEFECTS.items():
            print("\n{} form:".format(kind))
            for n in sorted(cat):
                print("  {}".format(n))
        print("\n~{:.0f}% of forms get 1 defect (20% of those get 2) when --defects random.".format(DEFECT_RATE * 100))
        return

    if args.show_config:
        print(hermes_profile.describe(PROFILE))
        s = hermes_profile.profile_settings(PROFILE)
        print("\nWould use model: {}".format(args.model or os.environ.get("FORM_AGENT_MODEL")
                                             or s["model"] or DEFAULT_IMAGE_MODEL + "  (default; no profile model found)"))
        print("SOUL.md: {}".format("found" if hermes_profile.soul_text(PROFILE) else "not found (built-in rules only)"))
        print("Image budget per run: {}".format(MAX_IMAGES_PER_RUN))
        return

    os.makedirs(args.out_dir, exist_ok=True)
    if args.blank:
        for k in ("address", "beneficiary"):
            p = os.path.join(args.out_dir, "blank_{}.png".format(k))
            build_template(k).image.save(p)
            print("wrote", p)
        return

    if not args.smoke_test:
        ap.print_help()
        return

    with open(CLIENTS_FILE) as f:
        data = json.load(f)
    if data.get("schema_version") != SCHEMA_VERSION:
        sys.exit("❌ crm_clients.json is not schema v2 yet.\n   Run: python3 migrate_crm_v2.py")
    rng = random.Random(args.seed)
    kinds = ["address", "beneficiary"] if args.kind == "both" else [args.kind]
    budget = FormBudget()

    print("=" * 62)
    print(" 🖋  form_agent smoke test | backend: {}".format(args.backend))
    print("=" * 62)
    for kind in kinds:
        client = _pick_client(data["clients"], kind, args.account, rng)
        requested, src = _sample_request(kind, client, rng)
        forced = None if args.defects == "random" else \
                 [] if args.defects == "none" else [d.strip() for d in args.defects.split(",") if d.strip()]
        print("\n▶ {} form for {} ({}), using {}".format(kind, client["name"], client["account_id"], src))
        print("   current:   {}".format(format_address(client["address"]) if kind == "address"
                                        else format_designation(client["beneficiary"])))
        print("   requested: {}".format(format_address(requested) if kind == "address"
                                        else format_designation(requested)))
        try:
            m = generate_form(kind, client, requested, backend=args.backend, model=args.model,
                              out_dir=args.out_dir, seed=rng.randint(0, 10 ** 6), budget=budget,
                              force_defects=forced, id_prefix="SMOKE", verbose=True,
                              print_prompt=args.print_prompt)
        except FormGenerationError as e:
            print("   ❌ {}".format(e))
            continue
        if args.print_prompt:
            print("   (no model call) blank template: {}".format(m["blank_template"]))
            print("\n----- PROMPT -----\n{}\n------------------".format(m["prompt"]))
            continue
        print("   ✅ image:    {}".format(m["image_path"]))
        print("      manifest: {}".format(m["manifest_path"]))
        print("      model: {} | size: {} | cost: {}".format(m["model"], m["image_size"], m["generation"].get("cost")))
        if m["defects"]:
            for d in m["defects"]:
                print("      injected defect: {} — {}".format(d["name"], d["description"]))
        else:
            print("      injected defects: none (clean form)")

    if not args.print_prompt and args.backend != "mock":
        print("\nWhen you open the image, check: (1) printed text/layout unchanged  (2) every value "
              "spelled exactly  (3) signature present  (4) notary seal legible (beneficiary form).")


if __name__ == "__main__":
    main()
