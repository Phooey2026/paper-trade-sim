#!/usr/bin/env python3
"""
crm_schema.py — Shared helpers for the CRM v2 client schema.

Used by migrate_crm_v2.py, generate_issues.py, orbit.py, form_agent.py,
and the dashboard so they all agree on what an address, a beneficiary
designation, a pending change, and a history entry look like.

Schema v2 additions to each client record in crm_clients.json:

  address:        {street, unit, city, state, zip}
  beneficiary:    [ {name, relationship, type, percent}, ... ]
                  type is "primary" or "contingent"; primaries sum to 100,
                  contingents sum to 100 (if there are any).
  change_history: [ {change_id, field, old, new, effective_at,
                     source_form_id, approved_by, note}, ... ]
  pending_changes:{ "address": {...}, "beneficiary": {...} }   (keyed by kind)
  status:         ACTIVE | INACTIVE | CLOSED   (+ closed_date when CLOSED)

Everything here is deterministic when given a seeded random.Random, so the
migration is repeatable.

Compatible with Pillow-era Jetson Python (3.8+): no match statements, no
new typing syntax.
"""

import hashlib
import random
import re
from datetime import datetime

SCHEMA_VERSION = 2

# ── Vocabulary ─────────────────────────────────────────────────────────────

BENE_TYPES = ("primary", "contingent")

RELATIONSHIPS = (
    "Spouse", "Domestic Partner", "Child", "Grandchild", "Sibling",
    "Parent", "Friend", "Trust", "Other",
)

PENDING_KINDS = ("address", "beneficiary")
PENDING_STATUSES = ("AWAITING_FORM", "FORM_RECEIVED", "RETURNED", "APPLIED")

# ── Geography: city -> real zip codes (addresses themselves are synthetic) ──

TERRITORY_CITIES = {
    "Los Angeles": {
        "Los Angeles":      ["90012", "90025", "90035", "90036", "90049", "90064", "90066"],
        "Santa Monica":     ["90401", "90403", "90405"],
        "Pasadena":         ["91101", "91105", "91106"],
        "Beverly Hills":    ["90210", "90211", "90212"],
        "Burbank":          ["91501", "91504", "91506"],
        "Glendale":         ["91202", "91203", "91206"],
        "Long Beach":       ["90802", "90803", "90808"],
        "Torrance":         ["90501", "90503", "90505"],
        "Culver City":      ["90230", "90232"],
        "West Hollywood":   ["90069"],
        "Manhattan Beach":  ["90266"],
        "Redondo Beach":    ["90277", "90278"],
        "Calabasas":        ["91302"],
        "Malibu":           ["90265"],
    },
    "San Diego": {
        "San Diego":        ["92101", "92103", "92106", "92109", "92115", "92116", "92117", "92122", "92124"],
        "La Jolla":         ["92037"],
        "Coronado":         ["92118"],
        "Carlsbad":         ["92008", "92009", "92010"],
        "Encinitas":        ["92024"],
        "Chula Vista":      ["91910", "91911", "91913"],
        "Del Mar":          ["92014"],
        "Escondido":        ["92025", "92027"],
        "Poway":            ["92064"],
        "El Cajon":         ["92019", "92020", "92021"],
        "Oceanside":        ["92054", "92056", "92057"],
    },
    "Orange County": {
        "Irvine":           ["92602", "92603", "92604", "92612", "92618", "92620"],
        "Newport Beach":    ["92660", "92661", "92663"],
        "Costa Mesa":       ["92626", "92627"],
        "Huntington Beach": ["92646", "92647", "92648", "92649"],
        "Anaheim":          ["92801", "92804", "92805", "92807"],
        "Laguna Beach":     ["92651"],
        "Mission Viejo":    ["92691", "92692"],
        "Tustin":           ["92780", "92782"],
        "Fullerton":        ["92831", "92832", "92833"],
        "Dana Point":       ["92629"],
        "San Clemente":     ["92672", "92673"],
        "Orange":           ["92865", "92866", "92867"],
        "Santa Ana":        ["92701", "92704", "92705"],
        "Aliso Viejo":      ["92656"],
        "Lake Forest":      ["92630"],
    },
    "San Francisco": {
        "San Francisco":    ["94102", "94103", "94105", "94107", "94108", "94109", "94110",
                             "94114", "94115", "94116", "94117", "94118", "94121", "94122",
                             "94123", "94131"],
        "Oakland":          ["94602", "94609", "94610", "94611", "94618"],
        "Berkeley":         ["94702", "94703", "94704", "94705", "94707", "94709", "94710"],
        "Palo Alto":        ["94301", "94303", "94306"],
        "San Mateo":        ["94401", "94402", "94403"],
        "Mill Valley":      ["94941"],
        "Walnut Creek":     ["94596", "94597", "94598"],
        "San Jose":         ["95112", "95125", "95126", "95128", "95129"],
        "Menlo Park":       ["94025"],
        "Redwood City":     ["94061", "94062", "94063"],
        "Sausalito":        ["94965"],
        "Burlingame":       ["94010"],
        "Alameda":          ["94501", "94502"],
    },
}

# Cities where apartments/condos are common (higher unit probability).
DENSE_CITIES = {
    "San Francisco", "Oakland", "Berkeley", "Los Angeles", "Santa Monica",
    "Long Beach", "San Diego", "Santa Ana", "West Hollywood", "Irvine",
}

STREET_NAMES = [
    "Ocean", "Sunset", "Harbor", "Mission", "Market", "Valencia", "Wilshire",
    "Olive", "Cedar", "Elm", "Maple", "Oak", "Pine", "Palm", "Magnolia",
    "Laurel", "Sycamore", "Jacaranda", "Bayview", "Hillcrest", "Ridgeview",
    "Lakeshore", "Marina", "Beacon", "Canyon", "Coral", "Eucalyptus", "Grand",
    "Highland", "Jefferson", "Lincoln", "Monterey", "Newport", "Orchid",
    "Parkside", "Quail", "Redwood", "Seaside", "Terrace", "Union", "Vermont",
    "Washington", "Alder", "Birch", "Camino", "Del Mar", "Fairview", "Garden",
    "Heritage", "Iris", "Juniper", "Kensington", "Linden", "Meadow", "Noble",
    "Orange Grove", "Prospect", "Rosewood", "Summit", "Tamarind", "Vista",
]
STREET_SUFFIXES = ["St", "Ave", "Blvd", "Dr", "Way", "Ln", "Ct", "Pl", "Rd", "Ter"]
STREET_SUFFIX_WEIGHTS = [20, 22, 8, 16, 6, 8, 6, 4, 8, 2]

# ── Name pools ─────────────────────────────────────────────────────────────

FIRST_M = [
    "Aaron", "Adam", "Alan", "Andrew", "Anthony", "Benjamin", "Brian", "Bruce",
    "Carlos", "Charles", "Christopher", "Daniel", "David", "Donald", "Douglas",
    "Edward", "Eric", "Frank", "Gary", "George", "Gregory", "Henry", "Jack",
    "James", "Jason", "Jeffrey", "Jeremy", "John", "Jonathan", "Joseph",
    "Joshua", "Justin", "Kenneth", "Kevin", "Larry", "Mark", "Matthew",
    "Michael", "Nathan", "Patrick", "Paul", "Peter", "Raymond", "Richard",
    "Robert", "Ronald", "Ryan", "Samuel", "Scott", "Stephen", "Steven",
    "Thomas", "Timothy", "Walter", "William", "Miguel", "Luis", "Jose",
    "Kenji", "Wei", "Raj",
]
FIRST_F = [
    "Abigail", "Alice", "Amanda", "Amy", "Andrea", "Angela", "Anna", "Ashley",
    "Ava", "Barbara", "Betty", "Brenda", "Carol", "Catherine", "Charlotte",
    "Christine", "Cynthia", "Deborah", "Diane", "Donna", "Dorothy",
    "Elizabeth", "Emily", "Emma", "Grace", "Hannah", "Helen", "Isabella",
    "Jennifer", "Jessica", "Joan", "Judith", "Julia", "Karen", "Katherine",
    "Kathleen", "Laura", "Lauren", "Linda", "Lisa", "Margaret", "Maria",
    "Marie", "Mary", "Megan", "Melissa", "Mia", "Michelle", "Nancy", "Nicole",
    "Olivia", "Patricia", "Rachel", "Rebecca", "Rosa", "Ruth", "Sandra",
    "Sarah", "Sharon", "Sophia", "Stephanie", "Susan", "Teresa", "Victoria",
    "Yuki", "Mei", "Priya",
]
LAST_NAMES = [
    "Adams", "Allen", "Alvarez", "Anderson", "Bailey", "Baker", "Bennett",
    "Brooks", "Brown", "Butler", "Campbell", "Carter", "Castillo", "Chen",
    "Clark", "Coleman", "Collins", "Cooper", "Cruz", "Davis", "Diaz",
    "Edwards", "Evans", "Fisher", "Flores", "Foster", "Garcia", "Gomez",
    "Gonzalez", "Gray", "Green", "Griffin", "Hall", "Harris", "Hayes",
    "Henderson", "Hernandez", "Hill", "Howard", "Hughes", "Jackson", "James",
    "Jenkins", "Johnson", "Jones", "Kelly", "Kim", "King", "Lee", "Lewis",
    "Lopez", "Martin", "Martinez", "Miller", "Mitchell", "Moore", "Morales",
    "Morgan", "Morris", "Murphy", "Myers", "Nakamura", "Nelson", "Nguyen",
    "Ortiz", "Parker", "Patel", "Perez", "Peterson", "Phillips", "Powell",
    "Price", "Ramirez", "Reed", "Reyes", "Richardson", "Rivera", "Roberts",
    "Robinson", "Rodriguez", "Rogers", "Ross", "Sanchez", "Sanders", "Scott",
    "Shah", "Simmons", "Smith", "Stewart", "Sullivan", "Taylor", "Thomas",
    "Thompson", "Torres", "Turner", "Walker", "Ward", "Washington", "Watson",
    "White", "Williams", "Wilson", "Wood", "Wright", "Young",
]

_FEMALE = set(FIRST_F)
_MALE = set(FIRST_M)


def infer_gender(first_name):
    """'F', 'M', or None when the first name isn't in our pools."""
    if first_name in _FEMALE:
        return "F"
    if first_name in _MALE:
        return "M"
    return None


def stable_rng(*parts):
    """Deterministic Random from arbitrary string parts (stable across runs)."""
    key = ":".join(str(p) for p in parts)
    return random.Random(int(hashlib.sha256(key.encode()).hexdigest(), 16))


def _weighted(rng, pairs):
    items = [p[0] for p in pairs]
    weights = [p[1] for p in pairs]
    return rng.choices(items, weights=weights, k=1)[0]


# ── Addresses ──────────────────────────────────────────────────────────────

def random_address(rng, territory=None, cross_territory_prob=0.0, exclude=None):
    """
    Build a synthetic-but-plausible CA address.

    territory: one of TERRITORY_CITIES keys (falls back to a random one)
    cross_territory_prob: chance the address is in a *different* territory
        (a client who moved farther away)
    exclude: an existing address dict; the result is guaranteed to differ
    """
    territories = list(TERRITORY_CITIES.keys())
    for _ in range(20):
        terr = territory if territory in TERRITORY_CITIES else rng.choice(territories)
        if rng.random() < cross_territory_prob:
            terr = rng.choice([t for t in territories if t != terr])
        cities = TERRITORY_CITIES[terr]
        city = rng.choice(list(cities.keys()))
        zip_code = rng.choice(cities[city])
        street = "{} {} {}".format(
            rng.randint(100, 9899),
            rng.choice(STREET_NAMES),
            _weighted(rng, list(zip(STREET_SUFFIXES, STREET_SUFFIX_WEIGHTS))),
        )
        unit = ""
        if rng.random() < (0.30 if city in DENSE_CITIES else 0.08):
            unit = rng.choice([
                "Apt {}{}".format(rng.randint(1, 30), rng.choice("ABCD")),
                "Unit {}".format(rng.randint(100, 620)),
                "#{}".format(rng.randint(2, 48)),
            ])
        addr = {"street": street, "unit": unit, "city": city, "state": "CA", "zip": zip_code}
        if exclude is None or not address_equal(addr, exclude):
            return addr
    return addr


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def address_equal(a, b):
    """Case/punctuation-insensitive address comparison."""
    if not a or not b:
        return False
    return all(_norm(a.get(k)) == _norm(b.get(k))
               for k in ("street", "unit", "city", "state", "zip"))


def format_address(addr, one_line=True):
    if not addr:
        return "(no address on file)"
    street = addr.get("street", "")
    if addr.get("unit"):
        street = "{}, {}".format(street, addr["unit"])
    line2 = "{}, {} {}".format(addr.get("city", ""), addr.get("state", ""), addr.get("zip", "")).strip()
    return "{}, {}".format(street, line2) if one_line else "{}\n{}".format(street, line2)


def validate_address(addr):
    """Return a list of problems (empty list = OK)."""
    errs = []
    if not addr:
        return ["address missing"]
    if not str(addr.get("street", "")).strip():
        errs.append("street missing")
    if not str(addr.get("city", "")).strip():
        errs.append("city missing")
    if str(addr.get("state", "")).strip().upper() != "CA":
        errs.append("state must be CA")
    if not re.fullmatch(r"\d{5}", str(addr.get("zip", "")).strip()):
        errs.append("zip must be 5 digits")
    return errs


# ── Beneficiary designations ───────────────────────────────────────────────

_SPLITS = {
    2: [(50, 50), (60, 40), (70, 30), (75, 25)],
    3: [(50, 25, 25), (40, 30, 30), (60, 20, 20), (34, 33, 33)],
}

_PRIMARY_REL_WEIGHTS = [
    ("Spouse", 46), ("Child", 30), ("Sibling", 12), ("Parent", 5),
    ("Domestic Partner", 3), ("Friend", 4),
]
_CONTINGENT_REL_WEIGHTS = [
    ("Child", 45), ("Sibling", 25), ("Parent", 10), ("Grandchild", 10), ("Friend", 10),
]
# Chance that a beneficiary shares the client's surname, by relationship.
_SAME_SURNAME_PROB = {
    "Spouse": 0.50, "Child": 0.88, "Sibling": 0.75, "Parent": 0.70,
    "Grandchild": 0.80, "Domestic Partner": 0.05, "Friend": 0.0, "Other": 0.0,
}


def _person_name(rng, client_last, relationship, taken, gender=None):
    for _ in range(50):
        g = gender or rng.choice("MF")
        first = rng.choice(FIRST_F if g == "F" else FIRST_M)
        if rng.random() < _SAME_SURNAME_PROB.get(relationship, 0.0):
            last = client_last
        else:
            last = rng.choice(LAST_NAMES)
        name = "{} {}".format(first, last)
        if name not in taken:
            return name
    return name


def make_designation(rng, client_name, keep_primary_name=None):
    """
    Build a realistic beneficiary designation for a client.

    keep_primary_name: if given, this exact name is kept as the first primary
        (used by the migration to preserve the name already on file).
    """
    client_last = client_name.split()[-1]
    taken = {client_name}
    designation = []

    # ~7% of clients name a trust as sole primary.
    if keep_primary_name is None and rng.random() < 0.07:
        variant = rng.choice(["Family Trust", "Family Revocable Trust", "Living Trust"])
        designation.append({
            "name": "{} {}".format(client_last, variant),
            "relationship": "Trust", "type": "primary", "percent": 100,
        })
    else:
        n_primary = _weighted(rng, [(1, 65), (2, 27), (3, 8)])
        split = (100,) if n_primary == 1 else rng.choice(_SPLITS[n_primary])
        used_partner = False
        for i in range(n_primary):
            rel = _weighted(rng, _PRIMARY_REL_WEIGHTS)
            if rel in ("Spouse", "Domestic Partner"):
                if used_partner:
                    rel = "Child"
                used_partner = True
            if i == 0 and keep_primary_name:
                # Existing name on file. Same surname => spouse/child most likely.
                same = keep_primary_name.split()[-1] == client_last
                rel = _weighted(rng, [("Spouse", 55), ("Child", 28), ("Sibling", 10), ("Parent", 7)]
                                ) if same else _weighted(rng, [("Spouse", 55), ("Friend", 20), ("Domestic Partner", 15), ("Child", 10)])
                if rel in ("Spouse", "Domestic Partner"):
                    used_partner = True
                name = keep_primary_name
            else:
                name = _person_name(rng, client_last, rel, taken)
            taken.add(name)
            designation.append({"name": name, "relationship": rel,
                                "type": "primary", "percent": split[i]})

    # ~45% also name contingent beneficiaries.
    if rng.random() < 0.45:
        n_cont = _weighted(rng, [(1, 65), (2, 35)])
        split = (100,) if n_cont == 1 else rng.choice([(50, 50), (60, 40)])
        for i in range(n_cont):
            rel = _weighted(rng, _CONTINGENT_REL_WEIGHTS)
            name = _person_name(rng, client_last, rel, taken)
            taken.add(name)
            designation.append({"name": name, "relationship": rel,
                                "type": "contingent", "percent": split[i]})
    return designation


def validate_designation(designation):
    """Return a list of problems (empty list = OK)."""
    errs = []
    if not isinstance(designation, list) or not designation:
        return ["no beneficiaries listed"]
    seen = set()
    sums = {"primary": 0, "contingent": 0}
    counts = {"primary": 0, "contingent": 0}
    for i, b in enumerate(designation, 1):
        name = str(b.get("name", "")).strip()
        if not name:
            errs.append("beneficiary {}: name missing".format(i))
        elif name.lower() in seen:
            errs.append("beneficiary {}: duplicate name {}".format(i, name))
        seen.add(name.lower())
        if b.get("relationship") not in RELATIONSHIPS:
            errs.append("beneficiary {}: relationship missing/invalid".format(i))
        t = b.get("type")
        if t not in BENE_TYPES:
            errs.append("beneficiary {}: type must be primary/contingent".format(i))
            continue
        try:
            pct = float(b.get("percent"))
        except (TypeError, ValueError):
            errs.append("beneficiary {}: percent missing".format(i))
            continue
        if pct <= 0:
            errs.append("beneficiary {}: percent must be > 0".format(i))
        sums[t] += pct
        counts[t] += 1
    if counts["primary"] == 0:
        errs.append("no primary beneficiary")
    for t in BENE_TYPES:
        if counts[t] and abs(sums[t] - 100) > 0.001:
            errs.append("{} percentages total {:g}, not 100".format(t, sums[t]))
    return errs


def designation_equal(a, b):
    def key(d):
        return sorted((_norm(x.get("name")), x.get("type"), float(x.get("percent", 0)))
                      for x in (d or []))
    return key(a) == key(b)


def format_designation(designation, short=False):
    """Human-readable: 'Primary: Jane Doe (Spouse) 60%, ... | Contingent: ...'"""
    if not designation:
        return "(none on file)"
    if isinstance(designation, str):      # legacy v1 string
        return designation
    parts = []
    for t in BENE_TYPES:
        rows = [b for b in designation if b.get("type") == t]
        if not rows:
            continue
        if short:
            txt = ", ".join("{} {:g}%".format(b["name"], b["percent"]) for b in rows)
        else:
            txt = ", ".join("{} ({}) {:g}%".format(b["name"], b.get("relationship", "?"), b["percent"])
                            for b in rows)
        parts.append("{}: {}".format(t.capitalize(), txt))
    return " | ".join(parts)


def primary_names(designation):
    if isinstance(designation, str):
        return designation
    return ", ".join(b["name"] for b in designation if b.get("type") == "primary") or "—"


# ── Pending changes and history ────────────────────────────────────────────

def make_pending(account_id, kind, requested, issue_id=None,
                 requested_at=None, legacy=False):
    """A pending-change record (lives at client['pending_changes'][kind])."""
    assert kind in PENDING_KINDS
    return {
        "request_id": "REQ-{}".format(issue_id) if issue_id
                      else "REQ-LEGACY-{}-{}".format(account_id, kind),
        "kind": kind,
        "issue_id": issue_id,
        "requested_at": requested_at or datetime.now().isoformat(),
        "status": "AWAITING_FORM",
        "attempts": 0,
        "form_id": None,
        "last_result": None,
        "requested": requested,
        "legacy": bool(legacy),
    }


def history_entry(field, old, new, source_form_id=None, approved_by="orbit", note=""):
    return {
        "change_id": "CHG-{}-{}".format(datetime.now().strftime("%Y%m%d%H%M%S"), field[:3].upper()),
        "field": field,
        "old": old,
        "new": new,
        "effective_at": datetime.now().isoformat(),
        "source_form_id": source_form_id,
        "approved_by": approved_by,
        "note": note,
    }


def client_summary_line(client):
    """One-liner used in logs."""
    return "{} ({})".format(client.get("name"), client.get("account_id"))


# ── City -> county (used on notarial acknowledgments) ──────────────────────

_TERRITORY_DEFAULT_COUNTY = {
    "Los Angeles": "Los Angeles", "San Diego": "San Diego",
    "Orange County": "Orange", "San Francisco": "San Francisco",
}
_COUNTY_OVERRIDES = {
    "Oakland": "Alameda", "Berkeley": "Alameda", "Alameda": "Alameda",
    "San Mateo": "San Mateo", "Redwood City": "San Mateo", "Menlo Park": "San Mateo",
    "Burlingame": "San Mateo", "Palo Alto": "Santa Clara", "San Jose": "Santa Clara",
    "Mill Valley": "Marin", "Sausalito": "Marin", "Walnut Creek": "Contra Costa",
}


def county_for_city(city):
    """County name (no 'County' suffix) for any city in TERRITORY_CITIES."""
    if city in _COUNTY_OVERRIDES:
        return _COUNTY_OVERRIDES[city]
    for terr, cities in TERRITORY_CITIES.items():
        if city in cities:
            return _TERRITORY_DEFAULT_COUNTY[terr]
    return "Los Angeles"
