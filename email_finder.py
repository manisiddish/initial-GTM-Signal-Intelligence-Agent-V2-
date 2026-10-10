"""
Find a person's email address.

Max's idea: every company has one email format (anna.schmidt@, aschmidt@, anna@ ...).
Guess about 20 variants and test which one really exists.

How it works:
  1. LEARN the company's format from real addresses we already saw
     (website, job ads, Hunter): "lisa.hoffmann@firma.de" + the name "Lisa Hoffmann"
     -> the format is {first}.{last}
  2. BUILD the person's email with that format first, then the other ~20 variants
  3. TEST them one by one with Hunter, stop at the first "valid"
     - catch-all server (says "yes" to everything): testing is useless, so we stop
       and keep the most likely address, marked "catch-all"
     - no Hunter key: nothing is tested, the best guess is marked "unverified"

Status in the results:
  valid       Hunter confirmed the mailbox exists               -> safe to send
  catch-all   server accepts everything, best guess             -> send carefully
  unverified  not tested (no Hunter key / credits used up)       -> test before sending
  unknown     server did not answer                             -> test again later
  not found   all variants were rejected                        -> use LinkedIn / generic email
"""

import re
import unicodedata
from collections import Counter

import config
import hunter

PATTERNS = [
    "{first}.{last}", "{first}", "{f}{last}", "{first}{last}", "{f}.{last}",
    "{last}", "{first}_{last}", "{first}-{last}", "{first}{l}", "{last}.{first}",
    "{last}{first}", "{f}{l}", "{first}.{l}", "{last}{f}", "{last}_{first}",
    "{f}_{last}", "{last}-{first}", "{last}.{f}", "{f}-{last}", "{l}{first}",
]

GENERIC = set("""
info kontakt contact hello hallo hi hey mail office jobs job career careers karriere bewerbung bewerbungen
recruiting recruitment hr team support service sales vertrieb presse press privacy datenschutz legal impressum
billing invoice invoices rechnung rechnungen buchhaltung accounting admin webmaster noreply no-reply
marketing partner partners ir investor investors security dpo compliance help feedback news newsletter
talent people apply applications empfang zentrale verwaltung post welcome enquiries inquiries general
hallo.team business media pr events community success customer customers kunden kundenservice order
orders shop store dsb datenschutzbeauftragter ki ai data gdpr rgpd dsgvo recrutement emploi bewerber
candidates candidature talents hiring contactez
""".split())
GENERIC_PREFERRED = ["info", "kontakt", "contact", "hello", "hallo", "office", "mail", "team"]


def _ascii(s: str, umlaut_e: bool = True) -> str:
    s = s.lower()
    if umlaut_e:
        s = s.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    s = s.replace("ß", "ss").replace("ø", "o").replace("æ", "ae").replace("ł", "l").replace("đ", "d")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z-]", "", s)


def name_forms(first: str, last: str) -> list:
    """All spellings of a name that companies use, most common first: [(first, last), ...]"""
    forms = []
    for umlaut_e in (True, False):
        f = _ascii(first, umlaut_e)
        l_full = _ascii(last.replace(" ", ""), umlaut_e)
        l_main = _ascii(last.split()[-1], umlaut_e)
        for ff in (f, f.replace("-", ""), f.split("-")[0]):
            for ll in (l_full.replace("-", ""), l_full, l_main):
                if ff and ll and (ff, ll) not in forms:
                    forms.append((ff, ll))
    return forms


def build(pattern: str, first: str, last: str) -> str:
    return pattern.format(first=first, last=last, f=first[:1], l=last[:1])


def candidates(first: str, last: str, domain: str, pattern: str = None) -> list:
    forms = name_forms(first, last)
    out = []

    def add(local):
        e = f"{local}@{domain}"
        if local and e not in out:
            out.append(e)

    if pattern:
        for f, l in forms[:3]:
            add(build(pattern, f, l))
    f0, l0 = forms[0]
    for p in PATTERNS:
        add(build(p, f0, l0))
    for f, l in forms[1:]:          # other spellings (Müller -> mueller / muller), top formats only
        for p in PATTERNS[:3]:
            add(build(p, f, l))
    return out


def is_generic(email: str) -> bool:
    """info@, jobs@, gdpr.dpo@, team-berlin@ ... = not a person"""
    local = email.split("@")[0].lower()
    parts = [p for p in re.split(r"[._+-]", local) if p]
    return local in GENERIC or any(p in GENERIC for p in parts) or bool(re.search(r"\d", local))


def detect_pattern(email: str, first: str, last: str):
    local = email.split("@")[0].lower()
    for f, l in name_forms(first, last):
        for p in PATTERNS:
            if build(p, f, l) == local:
                return p
    return None


def learn_pattern(domain: str, emails, people, hunter_data=None):
    """
    Returns (pattern, strength, proof). strength: 'strong' (seen with a real name) or 'weak'.
    """
    votes, proof = Counter(), {}
    personal = [e for e in emails if e.endswith("@" + domain) and not is_generic(e)]
    for e in personal:
        matched = False
        for p in people:
            pat = detect_pattern(e, p["first"], p["last"])
            if pat:
                votes[pat] += 3
                proof.setdefault(pat, f"{e} belongs to {p['name']}")
                matched = True
                break
        if not matched:
            m = re.fullmatch(r"[a-z]{2,}([._-])[a-z]{2,}", e.split("@")[0])
            if m:
                pat = "{first}" + m.group(1) + "{last}"
                votes[pat] += 1
                proof.setdefault(pat, f"{e} looks like first{m.group(1)}last")
    hp = (hunter_data or {}).get("pattern")
    if hp and hp in PATTERNS:
        votes[hp] += 3
        proof.setdefault(hp, f"Hunter knows this format for {domain}")
    if not votes:
        return None, None, None
    pat, n = votes.most_common(1)[0]
    return pat, ("strong" if n >= 3 else "weak"), proof[pat]


def generic_email(domain: str, emails) -> str:
    mine = [e for e in emails if e.endswith("@" + domain) and is_generic(e)]
    for g in GENERIC_PREFERRED:
        for e in mine:
            if e.split("@")[0] == g:
                return e
    return ""


# ------------------------------------------------------------

def find(person: dict, domain: str, pattern=None, strength=None, domain_state: dict = None) -> dict:
    """
    Returns {"email", "status", "confidence", "how", "tested"}.
    domain_state remembers per domain whether the server is catch-all (saves credits).
    """
    domain_state = domain_state if domain_state is not None else {}
    first, last = person["first"], person["last"]

    # Hunter already knows this person's email
    known = person.get("email") if (person.get("email") or "").endswith("@" + domain) else None
    if known and person.get("hunter_status") == "valid":
        return {"email": known, "status": "valid", "confidence": max(90, person.get("hunter_confidence", 0)),
                "how": "Hunter knows this person (verified)", "tested": 0}

    cands = candidates(first, last, domain, pattern)
    how_first = (f"format {pattern} learned from real emails" if pattern and strength == "strong"
                 else f"format {pattern} (weak hint)" if pattern else "most common format")
    first_conf = 75 if strength == "strong" else 55 if pattern else 40

    if known:
        cands = [known] + [c for c in cands if c != known]
        how_first, first_conf = "Hunter knows this person", max(60, person.get("hunter_confidence", 0))
    elif strength != "strong" and hunter.available():
        r = hunter.email_finder(domain, person["first"], person["last"]) or {}
        if r.get("email"):
            e = r["email"].lower()
            cands = [e] + [c for c in cands if c != e]
            how_first, first_conf = f"Hunter email finder (score {r.get('score', 0)})", max(45, r.get("score") or 0)
            if (r.get("verification") or {}).get("status") == "valid":
                return {"email": e, "status": "valid", "confidence": 95,
                        "how": "Hunter email finder (verified)", "tested": 0}

    best = cands[0]

    def result(status, conf, how, tested, email=best):
        return {"email": email, "status": status, "confidence": conf, "how": how, "tested": tested}

    if domain_state.get(domain) == "catch-all":
        return result("catch-all", first_conf, f"{how_first}; server accepts every address", 0)

    tested, unknown = 0, 0
    max_tries = config.MAX_GUESSES_FORMAT_KNOWN if strength == "strong" else config.MAX_EMAIL_GUESSES
    for e in cands[:max_tries]:
        try:
            v = hunter.verify(e)
        except hunter.Skip:
            return result("skip", 0, "person asked not to be contacted (GDPR) - skipped", tested, email="")
        st = v["status"]
        if st == "not_checked":
            break
        if not v.get("cached"):
            tested += 1
        if st == "valid":
            n = cands.index(e) + 1
            return result("valid", 95, f"verified by Hunter (variant {n} of {len(cands)})", tested, email=e)
        if st == "accept_all":
            domain_state[domain] = "catch-all"
            return result("catch-all", first_conf, f"{how_first}; server accepts every address", tested)
        if st in ("unknown", "webmail", "disposable", "blocked"):
            unknown += 1
            if unknown >= 2:
                return result("unknown", 35, f"{how_first}; mail server did not answer", tested)
    else:
        if cands:
            return result("not found", 0, f"all {min(len(cands), max_tries)} variants rejected",
                          tested, email="")

    reason = hunter.off_reason() or "not tested"
    return result("unverified", first_conf - 10, f"{how_first}; not tested ({reason})", tested)
