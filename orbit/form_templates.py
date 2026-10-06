#!/usr/bin/env python3
"""
form_templates.py — Blank form templates for Orbit's signed-form workflow.

Two forms, both drawn with Pillow only (no browser, no reportlab):

  address       Address Change Form              8.5" x 11"  (1275 x 1650 px @150dpi)
  beneficiary   Beneficiary Designation Form     8.5" x 13"  (1275 x 1950 px @150dpi)
                (includes a California notary acknowledgment block)

Each template returns the blank page image plus a field map:
    fields[name] = {"box": (x0, y0, x1, y1), "label": str, "section": str, "type": str}
The field map is used two ways:
  * form_agent builds the image-model prompt from the printed labels
  * the mock renderer (used for offline tests) writes values into the boxes

Compatible with Pillow 9.0.1 (the Jetson's version): no APIs newer than 9.0.
"""

import os
import random
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageChops

# ── Palette ────────────────────────────────────────────────────────────────
NAVY = (10, 22, 40)
GOLD = (201, 168, 76)
INK = (25, 25, 30)
GRAY = (105, 110, 120)
LIGHT = (226, 231, 240)
BOX = (70, 75, 85)
PEN = (24, 48, 150)        # blue ballpoint
SEAL = (88, 40, 130)       # purple notary ink

PAGE_SIZES = {"address": (1275, 1650), "beneficiary": (1275, 1950)}
FORM_TITLES = {
    "address": ("ADDRESS CHANGE FORM", "Form OC-ADR-01  ·  Rev. 09/2026  ·  Complete in ink, sign, and return through your Financial Advisor"),
    "beneficiary": ("BENEFICIARY DESIGNATION FORM", "Form OC-BEN-01  ·  Rev. 09/2026  ·  Must be signed AND notarized  ·  Return through your Financial Advisor"),
}

# ── Fonts ──────────────────────────────────────────────────────────────────
_FONT_DIRS = [
    "/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu",
    "/usr/share/fonts/truetype/liberation", "/usr/share/fonts/truetype/freefont",
    "/usr/share/fonts/TTF", "/Library/Fonts", "/System/Library/Fonts/Supplemental",
]
if os.environ.get("FORM_FONT_DIR"):
    _FONT_DIRS.insert(0, os.environ["FORM_FONT_DIR"])

_FONT_FILES = {
    "regular": ["DejaVuSans.ttf", "LiberationSans-Regular.ttf", "FreeSans.ttf", "Arial.ttf"],
    "bold":    ["DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "FreeSansBold.ttf", "Arial Bold.ttf"],
    "italic":  ["DejaVuSerif-Italic.ttf", "LiberationSerif-Italic.ttf", "FreeSerifItalic.ttf",
                "DejaVuSans-Oblique.ttf", "Georgia Italic.ttf"],
}
_font_cache = {}


def _font(size, style="regular"):
    key = (size, style)
    if key in _font_cache:
        return _font_cache[key]
    for d in _FONT_DIRS:
        for fn in _FONT_FILES[style]:
            path = os.path.join(d, fn)
            if os.path.exists(path):
                _font_cache[key] = ImageFont.truetype(path, size)
                return _font_cache[key]
    raise RuntimeError(
        "No TrueType font found. Install one (sudo apt install fonts-dejavu-core) "
        "or set FORM_FONT_DIR to a folder containing DejaVuSans.ttf.")


# ── Drawing helpers ────────────────────────────────────────────────────────

def _wrap(draw, text, font, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _header(draw, kind, W):
    draw.rectangle((0, 0, W, 118), fill=NAVY)
    draw.text((75, 59), "OBSIDIAN CAPITAL", font=_font(44, "bold"), fill=GOLD, anchor="lm")
    draw.text((W - 75, 46), "Client Services", font=_font(22), fill=(200, 205, 215), anchor="rm")
    draw.text((W - 75, 76), "Obsidian Capital Research Fund (OCRFF)", font=_font(17),
              fill=(160, 168, 182), anchor="rm")
    title, sub = FORM_TITLES[kind]
    draw.text((75, 158), title, font=_font(38, "bold"), fill=INK, anchor="lm")
    draw.text((75, 200), sub, font=_font(16), fill=GRAY, anchor="lm")


def _section(draw, y, text, W):
    draw.rectangle((75, y, W - 75, y + 34), fill=NAVY)
    draw.text((88, y + 17), text, font=_font(18, "bold"), fill=(255, 255, 255), anchor="lm")


class _Builder:
    def __init__(self, kind):
        self.kind = kind
        self.W, self.H = PAGE_SIZES[kind]
        self.img = Image.new("RGB", (self.W, self.H), (255, 255, 255))
        self.draw = ImageDraw.Draw(self.img)
        self.fields = {}
        self.section = ""

    def section_bar(self, y, text):
        self.section = text
        _section(self.draw, y, text, self.W)

    def field(self, name, label, x, y, w, h=52, ftype="text", show_label=True, label_text=None):
        """Label above a boxed entry area."""
        if show_label and label:
            self.draw.text((x, y - 6), label, font=_font(15), fill=GRAY, anchor="ls")
        self.draw.rectangle((x, y, x + w, y + h), outline=BOX, width=2)
        self.fields[name] = {"box": (x, y, x + w, y + h), "label": label_text or label,
                             "section": self.section, "type": ftype}

    def checkbox(self, name, text, x, y):
        d = self.draw
        d.rectangle((x, y, x + 34, y + 34), outline=BOX, width=2)
        d.text((x + 52, y + 17), text, font=_font(21), fill=INK, anchor="lm")
        self.fields[name] = {"box": (x, y, x + 34, y + 34), "label": text,
                             "section": self.section, "type": "checkbox"}

    def paragraph(self, text, x, y, w, size=19, style="regular", fill=INK, gap=6):
        f = _font(size, style)
        for line in _wrap(self.draw, text, f, w):
            self.draw.text((x, y), line, font=f, fill=fill, anchor="la")
            y += size + gap
        return y


def _account_block(b, y_bar, y_row):
    b.section_bar(y_bar, "1.  ACCOUNT INFORMATION")
    b.field("account_number", "Account Number", 75, y_row, 360)
    b.field("account_holder", "Account Holder Name (as registered)", 460, y_row, 430)
    b.field("financial_advisor", "Financial Advisor", 915, y_row, 285)


def _address_rows(b, prefix, y1, y2):
    b.field(prefix + "_street", "Street Address", 75, y1, 700)
    b.field(prefix + "_unit", "Unit / Apt / Suite", 800, y1, 400)
    b.field(prefix + "_city", "City", 75, y2, 575)
    b.field(prefix + "_state", "State", 675, y2, 200)
    b.field(prefix + "_zip", "ZIP Code", 900, y2, 300)


# ── Address change form ────────────────────────────────────────────────────

def _build_address():
    b = _Builder("address")
    _header(b.draw, "address", b.W)
    _account_block(b, 250, 320)

    b.section_bar(410, "2.  CURRENT ADDRESS ON FILE")
    _address_rows(b, "current", 476, 571)

    b.section_bar(660, "3.  NEW ADDRESS")
    _address_rows(b, "new", 726, 821)
    b.field("effective_date", "Effective Date (MM/DD/YYYY)", 75, 916, 360)

    b.section_bar(990, "4.  STATEMENT AND MAIL DELIVERY  (check one)")
    b.checkbox("delivery_client", "Send my statements and account mail to my NEW ADDRESS above", 75, 1046)
    b.checkbox("delivery_fa", "Send my statements and account mail in care of my FINANCIAL ADVISOR", 75, 1100)

    b.section_bar(1170, "5.  AUTHORIZATION")
    b.paragraph("I authorize Obsidian Capital to change the address of record for the account "
                "named above. I certify that the information on this form is true and complete.",
                75, 1218, 1125, size=19)
    b.field("signature", "Signature of Account Holder", 75, 1330, 650, h=90, ftype="signature")
    b.field("signature_date", "Date Signed (MM/DD/YYYY)", 750, 1330, 450)
    b.field("printed_name", "Printed Name of Account Holder", 75, 1475, 650)

    b.draw.line((75, 1560, b.W - 75, 1560), fill=LIGHT, width=2)
    b.draw.text((b.W // 2, 1592),
                "Return this completed form to your Financial Advisor. Address changes are typically "
                "processed within 3-5 business days of receipt.",
                font=_font(15), fill=GRAY, anchor="mm")
    b.draw.text((b.W // 2, 1620), "Obsidian Capital  ·  AI-driven investment research  ·  OCRFF",
                font=_font(14), fill=(150, 155, 165), anchor="mm")
    return b


# ── Beneficiary designation form ───────────────────────────────────────────

NOTARY_DISCLAIMER = (
    "A notary public or other officer completing this certificate verifies only the identity of the "
    "individual who signed the document to which this certificate is attached, and not the "
    "truthfulness, accuracy, or validity of that document.")

NOTARY_BODY = (
    "who proved to me on the basis of satisfactory evidence to be the person whose name is subscribed "
    "to the within instrument and acknowledged to me that he/she executed the same in his/her "
    "authorized capacity, and that by his/her signature on the instrument the person executed the "
    "instrument. I certify under PENALTY OF PERJURY under the laws of the State of California that "
    "the foregoing paragraph is true and correct. WITNESS my hand and official seal.")

PRIMARY_ROWS = 3
CONTINGENT_ROWS = 2


def _bene_table(b, prefix, label_prefix, y_labels, y_first, rows, total_name, total_label):
    d = b.draw
    d.text((75, y_labels), "Full Legal Name", font=_font(15), fill=GRAY, anchor="ls")
    d.text((680, y_labels), "Relationship", font=_font(15), fill=GRAY, anchor="ls")
    d.text((970, y_labels), "Percent", font=_font(15), fill=GRAY, anchor="ls")
    y = y_first
    for i in range(1, rows + 1):
        n = "{}{}".format(prefix, i)
        b.field(n + "_name", "", 75, y, 580, h=48, show_label=False,
                label_text="{} #{} - Full Legal Name".format(label_prefix, i))
        b.field(n + "_rel", "", 680, y, 265, h=48, show_label=False,
                label_text="{} #{} - Relationship".format(label_prefix, i))
        b.field(n + "_pct", "", 970, y, 230, h=48, show_label=False,
                label_text="{} #{} - Percent".format(label_prefix, i))
        y += 54
    d.text((945, y + 24), "TOTAL", font=_font(17, "bold"), fill=INK, anchor="rm")
    b.field(total_name, "", 970, y, 230, h=48, show_label=False, label_text=total_label)
    return y + 48


def _build_beneficiary():
    b = _Builder("beneficiary")
    _header(b.draw, "beneficiary", b.W)
    _account_block(b, 250, 320)

    b.section_bar(410, "2.  PRIMARY BENEFICIARIES  (percentages must total 100%)")
    _bene_table(b, "p", "Primary Beneficiary", 462, 472, PRIMARY_ROWS,
                "primary_total", "Primary Beneficiaries - TOTAL percent")

    b.section_bar(712, "3.  CONTINGENT BENEFICIARIES  (optional; must total 100% if listed)")
    _bene_table(b, "c", "Contingent Beneficiary", 764, 774, CONTINGENT_ROWS,
                "contingent_total", "Contingent Beneficiaries - TOTAL percent")

    b.section_bar(972, "4.  SIGNATURE")
    b.paragraph("This designation replaces ALL prior beneficiary designations on the account named "
                "above. I certify that the information on this form is true and complete.",
                75, 1018, 1125, size=18)
    b.field("signature", "Signature of Account Holder", 75, 1110, 575, h=80, ftype="signature")
    b.field("signature_date", "Date Signed (MM/DD/YYYY)", 675, 1110, 525)
    b.field("printed_name", "Printed Name of Account Holder", 75, 1230, 575)

    # ── Notary acknowledgment (California) ──
    b.section_bar(1300, "5.  NOTARY PUBLIC ACKNOWLEDGMENT  (State of California)")
    d = b.draw
    d.rectangle((75, 1350, b.W - 75, 1428), outline=BOX, width=2)
    by = 1358
    for line in _wrap(d, NOTARY_DISCLAIMER, _font(16), 1090):
        d.text((90, by), line, font=_font(16), fill=INK, anchor="la")
        by += 22
    b.field("notary_county", "State of California, County of", 75, 1478, 425, h=48)
    b.field("notary_date", "On (date)", 525, 1478, 275, h=48)
    b.field("notary_name", "before me, (name and title of officer)", 825, 1478, 375, h=48)
    b.field("notary_signer_name", "personally appeared (name of signer)", 75, 1572, 625, h=48)
    b.paragraph(NOTARY_BODY, 75, 1648, 1125, size=15, gap=5)
    b.field("notary_signature", "Signature of Notary Public", 75, 1790, 575, h=84, ftype="signature")
    b.field("notary_commission_no", "Commission No.", 75, 1904, 250, h=40)
    b.field("notary_commission_expires", "Commission Expires", 350, 1904, 300, h=40)

    # Seal area: dashed box with caption
    sx0, sy0, sx1, sy1 = 720, 1738, 1200, 1938
    for x in range(sx0, sx1, 16):
        d.line((x, sy0, min(x + 8, sx1), sy0), fill=GRAY, width=2)
        d.line((x, sy1, min(x + 8, sx1), sy1), fill=GRAY, width=2)
    for y in range(sy0, sy1, 16):
        d.line((sx0, y, sx0, min(y + 8, sy1)), fill=GRAY, width=2)
        d.line((sx1, y, sx1, min(y + 8, sy1)), fill=GRAY, width=2)
    d.text(((sx0 + sx1) // 2, sy1 - 14), "(Seal)", font=_font(15), fill=GRAY, anchor="mm")
    b.fields["notary_seal"] = {"box": (sx0, sy0, sx1, sy1), "label": "(Seal) box",
                               "section": b.section, "type": "seal"}
    return b


class Template:
    def __init__(self, kind, image, fields):
        self.kind, self.image, self.fields = kind, image, fields
        self.size = image.size


def build_template(kind):
    if kind == "address":
        b = _build_address()
    elif kind == "beneficiary":
        b = _build_beneficiary()
    else:
        raise ValueError("unknown form kind: {}".format(kind))
    return Template(kind, b.img, b.fields)


# ── Values -> flat field map ───────────────────────────────────────────────
# A "values" dict is what the form should visibly contain. None / "" means the
# field is left empty (which is how injected defects like a missing signature
# are expressed).

def flatten_values(kind, v):
    """Ordered list of (field_name, value). value: str, bool (checkbox/seal) or None."""
    out = [("account_number", v.get("account_number")),
           ("account_holder", v.get("account_holder")),
           ("financial_advisor", v.get("financial_advisor"))]
    if kind == "address":
        for p in ("current", "new"):
            for f in ("street", "unit", "city", "state", "zip"):
                out.append(("{}_{}".format(p, f), v.get("{}_{}".format(p, f))))
        out += [("effective_date", v.get("effective_date")),
                ("delivery_client", bool(v.get("delivery") == "client")),
                ("delivery_fa", bool(v.get("delivery") == "fa"))]
    else:
        for prefix, rows, key in (("p", PRIMARY_ROWS, "primary"), ("c", CONTINGENT_ROWS, "contingent")):
            lst = v.get(key) or []
            for i in range(1, rows + 1):
                b = lst[i - 1] if i <= len(lst) else {}
                pct = b.get("percent")
                out += [("{}{}_name".format(prefix, i), b.get("name")),
                        ("{}{}_rel".format(prefix, i), b.get("relationship")),
                        ("{}{}_pct".format(prefix, i), None if pct in (None, "") else "{:g}%".format(pct))]
            tot = v.get(key + "_total")
            out.append((key + "_total", None if tot in (None, "") else "{:g}%".format(tot)))
    out += [("signature", v.get("signature")),
            ("signature_date", v.get("signature_date")),
            ("printed_name", v.get("printed_name"))]
    if kind == "beneficiary":
        n = v.get("notary") or {}
        out += [("notary_county", n.get("county")),
                ("notary_date", n.get("date")),
                ("notary_name", n.get("name")),
                ("notary_signer_name", n.get("signer_name")),
                ("notary_signature", n.get("signature")),
                ("notary_commission_no", n.get("commission_no")),
                ("notary_commission_expires", n.get("commission_expires")),
                ("notary_seal", bool(n.get("seal")))]
    return out


# ── Mock renderer (no model; used for offline tests and as a reference) ────

def _put_text(draw, box, text, size=27, pad=12):
    x0, y0, x1, y1 = box
    f = _font(size, "italic")
    while size > 14 and draw.textlength(text, font=f) > (x1 - x0 - 2 * pad):
        size -= 2
        f = _font(size, "italic")
    draw.text((x0 + pad, (y0 + y1) // 2 + 2), text, font=f, fill=PEN, anchor="lm")


def _signature(img, box, name, rng):
    """A loose cursive-ish signature: sheared italic name plus a flourish."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    first, last = (name.split() + [""])[0], name.split()[-1]
    size = int(h * 0.62)
    f = _font(size, "italic")
    txt = "{}. {}".format(first[0], last) if rng.random() < 0.5 else name
    while ld.textlength(txt, font=f) > w - 40 and size > 20:
        size -= 3
        f = _font(size, "italic")
    ld.text((20, h // 2), txt, font=f, fill=PEN + (255,), anchor="lm")
    layer = layer.transform(layer.size, Image.AFFINE, (1, 0.28, -h * 0.14, 0, 1, 0), resample=Image.BICUBIC)
    ld = ImageDraw.Draw(layer)
    y = int(h * 0.82)
    pts = [(20, y)]
    for i in range(1, 9):
        pts.append((20 + i * (w - 60) / 8.0, y + rng.randint(-5, 5) - (i % 2) * 4))
    ld.line(pts, fill=PEN + (230,), width=3)
    img.paste(layer, (x0, y0), layer)


def _seal(img, box, notary):
    """Round purple notary stamp with the standard California seal details."""
    x0, y0, x1, y1 = box
    r = min(x1 - x0, y1 - y0) // 2 - 10
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2 - 8
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    col = SEAL + (215,)
    ld.ellipse((cx - r, cy - r, cx + r, cy + r), outline=col, width=5)
    ld.ellipse((cx - r + 11, cy - r + 11, cx + r - 11, cy + r - 11), outline=col, width=2)
    name = (notary.get("name") or "").split(",")[0].upper()      # drop ", Notary Public"
    rows = [("NOTARY PUBLIC", 15, -46), ("CALIFORNIA", 13, -26), (name, 15, -2),
            ("COMM. # {}".format(notary.get("commission_no") or ""), 13, 20),
            ("{} COUNTY".format((notary.get("county") or "").upper()), 12, 38),
            ("EXP. {}".format(notary.get("commission_expires") or ""), 12, 56)]
    for text, size, dy in rows:
        avail = 2 * ((r - 16) ** 2 - dy ** 2) ** 0.5 - 8      # chord width at this height
        f = _font(size, "bold")
        while ld.textlength(text, font=f) > avail and size > 8:
            size -= 1
            f = _font(size, "bold")
        ld.text((cx, cy + dy), text, font=f, fill=col, anchor="mm")
    layer = layer.rotate(-7, resample=Image.BICUBIC, center=(cx, cy))
    img.paste(layer, (0, 0), layer)


def scan_look(img, rng, tilt=None):
    """Light 'photocopied scan' treatment: tilt, blur, grain, faint edge shadow."""
    tilt = rng.uniform(-0.7, 0.7) if tilt is None else tilt
    img = img.rotate(tilt, resample=Image.BICUBIC, expand=True, fillcolor=(244, 244, 242))
    img = img.filter(ImageFilter.GaussianBlur(0.6))
    noise = Image.effect_noise(img.size, 14).convert("RGB")
    img = ImageChops.multiply(img, ImageChops.lighter(noise, Image.new("RGB", img.size, (243, 243, 241))))
    return img


def render_filled_mock(kind, values, seed=0, scan=True):
    """Draw the filled form in Python (no model). Returns a PIL image."""
    rng = random.Random(seed)
    tpl = build_template(kind)
    img = tpl.image.copy()
    d = ImageDraw.Draw(img)
    for name, val in flatten_values(kind, values):
        fld = tpl.fields.get(name)
        if fld is None or val in (None, "", False):
            continue
        box = fld["box"]
        if fld["type"] == "checkbox":
            x0, y0, x1, y1 = box
            d.line((x0 + 5, y0 + 5, x1 - 5, y1 - 5), fill=PEN, width=5)
            d.line((x0 + 5, y1 - 5, x1 - 5, y0 + 5), fill=PEN, width=5)
        elif fld["type"] == "signature":
            _signature(img, box, str(val), rng)
        elif fld["type"] == "seal":
            _seal(img, box, values.get("notary") or {})
        else:
            _put_text(d, box, str(val))
    return scan_look(img, rng) if scan else img


if __name__ == "__main__":
    import sys
    outdir = sys.argv[1] if len(sys.argv) > 1 else "."
    for k in PAGE_SIZES:
        t = build_template(k)
        p = os.path.join(outdir, "blank_{}.png".format(k))
        t.image.save(p)
        print("wrote", p, t.size, len(t.fields), "fields")
