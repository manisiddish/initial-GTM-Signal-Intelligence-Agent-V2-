"""
Find the right PERSON at a company: who decides about AI spending?

Best for Flocta (rank 1): CTO, Head of AI / Engineering / Data, VP Engineering
Then (rank 2):            CEO, founder, Geschäftsführer (in small companies they decide everything)
Then (rank 3):            other leaders (CFO, COO, Head of Product ...)
Not a decision maker (9): the recruiter / contact person from the job ad.
                          Their email is still useful: it shows the company's email format.

Where people are found (free first):
  1. Impressum: German law requires it to name the managing directors (Geschäftsführer)
  2. Team / about pages: "Anna Schmidt - CTO"
  3. GPT reads the team pages if the rules found no tech leader (only if OPENAI_API_KEY is set)
  4. Job ads: "Ihre Ansprechpartnerin: Frau Anna Schmidt"
  5. Hunter: people Hunter knows at that domain (with their job title)

Every person keeps the line where they were found (proof) and the page link.
"""

import re
import unicodedata

import config
import llm

# ------------------------------------------------------------
# Names
# ------------------------------------------------------------

TITLES = re.compile(
    r"^(?:prof\.?|dr\.?(?:\s*-?\s*ing\.?|\s*rer\.?\s*\w+\.?|\s*med\.?)?|dipl\.?\s*-?\s*[\w]+\.?|mag\.?|"
    r"m\.?\s?sc\.?|b\.?\s?sc\.?|m\.?\s?a\.?|mba|ll\.?\s?m\.?|herrn?|frau|hr\.|fr\.|mr\.?|mrs\.?|ms\.?)\s+",
    re.IGNORECASE)
PARTICLES = {"von", "van", "de", "der", "den", "zu", "di", "da", "le", "la", "du", "del", "ten", "ter",
             "vom", "zum", "zur", "dos", "das", "el", "al", "bin", "ben", "mc"}
NAME_WORD = re.compile(r"[A-ZÄÖÜÉÈÁÀÓÚÍÇŠŽČŁØÅÑ][a-zäöüßéèêáàâóòôúùûíìîçšžčćłøåëïñ'’]+"
                       r"(?:-[A-ZÄÖÜa-zäöü][a-zäöüßéèêáàâóòôúùûíìîçšžčćłøåëïñ]+)*")
NOT_NAME = set("""
gmbh ag ug kg se co mbh ohg gbr ev inc ltd llc sa sas bv
straße strasse str platz weg allee ring gasse damm ufer chaussee
berlin münchen munich hamburg köln cologne frankfurt stuttgart düsseldorf leipzig dresden hannover
nürnberg bremen essen dortmund bonn mannheim karlsruhe wien zürich deutschland germany austria
telefon phone tel fax email mail kontakt contact impressum imprint team sitz registergericht
amtsgericht handelsregister hrb hra ust umsatzsteuer vat datenschutz privacy verantwortlich
geschäftsführer geschäftsführerin geschäftsführung managing director directors ceo cto cfo coo
founder gründer gründerin vorstand inhaber inhaberin und and the our unser unsere über about home
jobs karriere careers news blog mehr more lesen read cookie cookies menu verwaltungs beteiligungs
holding technologies solutions software digital consulting services vertreten durch represented by
informationen angaben gemäß tmg ddg aufsichtsbehörde steuernummer haftung inhalte links urheberrecht
head lead chief officer engineer engineering manager partner partners product sales marketing
support service operations finance people talent recruiting recruiter senior junior principal
data science ai ki machine learning platform technology tech development entwicklung
mitglied member board aufsichtsrat vorsitzender vorsitz chairman chair
anschrift address adresse postfach registernummer identifikationsnummer wirtschafts
dr prof herr herrn frau mr mrs ms dipl ing
""".split())

ROLE_WORDS = re.compile(
    r"\b(?:ceo|cto|cfo|coo|cpo|cio|cmo|caio|cdo|co-?founder|founder|gründer(?:in)?|mitgründer(?:in)?|"
    r"geschäftsführer(?:in)?|geschäftsführende[rn]?|managing (?:director|partner)|head of [\w &/+-]{2,40}|"
    r"vp(?: of)? [\w &/+-]{2,30}|vice president[\w &/+-]{0,30}|director(?: of)? [\w &/+-]{2,30}|"
    r"chief [\w ]{2,30}officer|leiter(?:in)? [\w &/+-]{2,30}|[\w-]+ lead\b|lead [\w-]+ engineer|"
    r"vorstand\w*|inhaber(?:in)?|owner|engineering manager|team ?lead|partner)\b",
    re.IGNORECASE)


def _ascii(s: str) -> str:
    s = s.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss").replace("ø", "o")
    s = s.replace("Ä", "Ae").replace("Ö", "Oe").replace("Ü", "Ue")
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def person_key(first: str, last: str) -> str:
    return re.sub(r"[^a-z]", "", _ascii(f"{first}{last}").lower())


def parse_name(raw: str):
    """'Dr. Anna-Lena von der Heide' -> ('Anna-Lena von der Heide', 'Anna-Lena', 'von der Heide') or None"""
    s = (raw or "").strip(" \t:,;.-–—|•·*")
    s = re.sub(r"\(.*?\)", "", s).strip()
    for _ in range(4):
        s2 = TITLES.sub("", s)
        if s2 == s:
            break
        s = s2
    s = re.sub(r"\s+", " ", s).strip(" ,.;:")
    tokens = s.split(" ")
    if not 2 <= len(tokens) <= 5 or len(s) > 50:
        return None
    fixed = []
    for t in tokens:
        if t.isupper() and len(t) > 2:
            t = "-".join(p.capitalize() for p in t.split("-"))
        fixed.append(t)
    tokens = fixed
    for i, t in enumerate(tokens):
        low = t.lower().strip(".")
        if low in NOT_NAME:
            return None
        if low in PARTICLES and 0 < i < len(tokens) - 1:
            continue
        if not NAME_WORD.fullmatch(t):
            return None
    first = tokens[0]
    rest = tokens[1:]
    particle_at = next((i for i, t in enumerate(rest) if t.lower() in PARTICLES), None)
    last = " ".join(rest[particle_at:]) if particle_at is not None else rest[-1]
    return " ".join(tokens), first, last


# ------------------------------------------------------------
# Rank: who decides about AI spending?
# ------------------------------------------------------------

RANKS = [
    (1, re.compile(
        r"\bcto\b|chief technology|technische[rn]? geschäftsf|chief (?:ai|data|information|digital|product and technology) officer|"
        r"\bc(?:ai|i|d)o\b|head of (?:ai|ki|ml|machine learning|data|engineering|technology|tech\b|it\b|platform|"
        r"r&d|development|entwicklung|software|product engineering|artificial)|"
        r"vp(?: of)? (?:engineering|technology|ai|data|platform)|vice president[\w ]*(?:engineering|technology|ai)|"
        r"director[\w ]*(?:engineering|technology|\bai\b|data|it\b)|(?:\bai|\bml|tech(?:nical)?|engineering) lead|"
        r"lead (?:ai|ml) |leiter(?:in)? (?:der )?(?:it|technik|entwicklung|software|ki)|it-?leiter|bereichsleiter(?:in)? it",
        re.IGNORECASE)),
    (2, re.compile(r"\bceo\b|chief executive|founder|gründer|geschäftsf|managing (?:director|partner)|vorstand|"
                   r"inhaber|\bowner\b|\bpresident\b|\bpartner\b|board member", re.IGNORECASE)),
    (3, re.compile(r"\bcfo\b|\bcoo\b|\bcpo\b|\bcmo\b|chief|head of|\bvp\b|vice president|director|leiter|"
                   r"engineering manager|team ?lead", re.IGNORECASE)),
    (9, re.compile(r"recruit|talent|\bhr\b|human resources|people|personal|ansprechp|contact|kontakt", re.IGNORECASE)),
]
RANK_LABEL = {1: "tech / AI leader", 2: "founder / CEO / managing director", 3: "other leader",
              5: "employee", 9: "job ad contact (not a decision maker)"}


def rank_of(role: str) -> int:
    for rank, rx in RANKS:
        if rx.search(role or ""):
            return rank
    return 5


def _person(name_parts, role, source, proof, url, **extra):
    full, first, last = name_parts
    p = {"name": full, "first": first, "last": last, "role": (role or "").strip()[:120],
         "rank": rank_of(role), "source": source, "proof": (proof or "").strip()[:300], "proof_url": url}
    p.update(extra)
    return p


# ------------------------------------------------------------
# 1. Impressum
# ------------------------------------------------------------

IMPRESSUM_LABELS = [
    (re.compile(r"technische[rn]? geschäftsführ\w*|\bcto\b|chief technology officer", re.I), "CTO"),
    (re.compile(r"geschäftsführ\w*|vertretungsberechtigt\w*(?: geschäftsführer\w*)?|vertreten durch|"
                r"managing directors?|represented by|authori[sz]ed (?:to represent|representatives?)|"
                r"geschäftsleitung|\bdirectors?\b", re.I), "Managing Director (Geschäftsführer)"),
    (re.compile(r"vorstand(?:svorsitzende[rn]?)?|executive board|management board", re.I), "Board (Vorstand)"),
    (re.compile(r"inhaber(?:in)?|\bowner\b|proprietor", re.I), "Owner (Inhaber)"),
    (re.compile(r"\bceo\b|chief executive officer", re.I), "CEO"),
]
STOP = re.compile(r"registergericht|handelsregister|amtsgericht|\bsitz\b|ust\.?-?id|umsatzsteuer|telefon|\btel\b|"
                  r"phone|\bfax\b|e-?mail|kontakt|contact|\bhrb\b|\bhra\b|registernummer|anschrift|address|"
                  r"\bvat\b|steuer|verantwortlich|aufsichtsbeh|postanschrift|www\.|https?://", re.I)
SPLIT = re.compile(r",|;|\s+und\s+|\s+and\s+|\s+&\s+|\s+/\s+|\s+\|\s+|\s+sowie\s+")
INLINE_ROLE = re.compile(r"\(([^)]{2,40})\)")


def _names_in(chunk: str):
    out = []
    for part in SPLIT.split(chunk):
        role = None
        m = INLINE_ROLE.search(part)
        if m:
            role = m.group(1)
        part = re.sub(r"^(?:die|der|den|des|the|our|unsere[n]?|durch|by|ihre[n]?)\s+", "", part.strip(), flags=re.I)
        part = re.sub(r"^(?:geschäftsführ\w*|managing directors?|vorstand\w*|ceo|cto)\s*:?\s*", "", part, flags=re.I)
        parsed = parse_name(part)
        if parsed:
            out.append((parsed, role))
    return out


def from_impressum(text: str, url: str) -> list:
    people = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if len(line) > 400:
            continue
        for rx, role in IMPRESSUM_LABELS:
            m = rx.search(line)
            if not m:
                continue
            rest = line[m.end():]
            stop = STOP.search(rest)
            if stop:
                rest = rest[:stop.start()]
            rest = rest.strip(" :.-–—")
            found = _names_in(rest) if rest else []
            if not found and len(rest) < 3:
                for nxt in lines[i + 1:i + 5]:
                    got = _names_in(nxt)
                    if not got:
                        break
                    found += got
            for parsed, inline in found:
                people.append(_person(parsed, f"{role}: {inline}" if inline else role, "Impressum", line if rest else
                                      f"{line} {parsed[0]}", url))
            if found:
                break
    return people


# ------------------------------------------------------------
# 2. Team / about pages (rules)
# ------------------------------------------------------------

SAME_LINE = [
    re.compile(r"^(?P<name>[^,:–—|()]{4,50}?)\s*(?:,|–|—|-|\||:|\()\s*(?P<role>[^()]{2,80}?)\)?$"),
    re.compile(r"^(?P<role>[^,:–—|()]{2,60}?)\s*(?::|–|—|-|\|)\s*(?P<name>[^,:–—|()]{4,50})$"),
]


def _name_role_line(line: str):
    """'Anna Schmidt - CTO' -> (parsed name, role) or None"""
    for rx in SAME_LINE:
        m = rx.match(line)
        if m and ROLE_WORDS.search(m.group("role")):
            parsed = parse_name(m.group("name"))
            if parsed:
                return parsed, m.group("role")
    return None


def _is_role_line(line: str) -> bool:
    return (len(line) <= 80 and bool(ROLE_WORDS.search(line))
            and not parse_name(line) and not _name_role_line(line))


def from_team_page(text: str, url: str, source: str = "Team page") -> list:
    people = []
    lines = [l for l in text.splitlines() if len(l) <= 120]
    for i, line in enumerate(lines):
        # "Anna Schmidt - CTO" / "CTO: Anna Schmidt" / "Anna Schmidt (Co-Founder)"
        same = _name_role_line(line)
        if same:
            people.append(_person(same[0], same[1], source, line, url))
            continue
        parsed = parse_name(line)
        if not parsed:
            continue
        # name on one line, role on the next (or the line before)
        for j in (i + 1, i - 1):
            if 0 <= j < len(lines) and _is_role_line(lines[j]):
                people.append(_person(parsed, lines[j], source, f"{line} | {lines[j]}", url))
                break
    return people


# ------------------------------------------------------------
# 3. GPT reads team pages (only when the rules found no tech leader)
# ------------------------------------------------------------

GPT_SYSTEM = """You extract people who work at a company from website text.
Return JSON: {"people": [{"name": "First Last", "role": "their job title"}]}
Rules:
- Only people who WORK AT this company (not customers, investors, testimonials or authors of quotes from other companies).
- If a role says "... at <another company>" (e.g. "Head of Data at Malt"), that person is a customer: skip them.
- Copy names and roles exactly as written in the text. Never invent or guess a name.
- Skip people without a job title. Return {"people": []} if there are none."""


def _gpt_input(pages: list) -> str:
    keep = []
    for p in pages:
        lines = p["text"].splitlines()
        for i, l in enumerate(lines):
            if ROLE_WORDS.search(l) or parse_name(l):
                keep.extend(lines[max(0, i - 1):i + 2])
    out, seen = [], set()
    for l in keep:
        if l not in seen:
            seen.add(l)
            out.append(l)
    return "\n".join(out)[:6000]


def from_gpt(company: str, pages: list) -> list:
    if not (config.USE_GPT_FOR_PEOPLE and llm.available()):
        return []
    text = _gpt_input(pages)
    if len(text) < 30:
        return []
    data = llm.ask_json("find_people", GPT_SYSTEM, f"Company: {company}\n\nText from its website:\n{text}")
    people = []
    whole = " ".join(p["text"] for p in pages)
    for item in (data or {}).get("people", [])[:20]:
        name, role = str(item.get("name", "")), str(item.get("role", ""))
        parsed = parse_name(name)
        # anti-hallucination: the name must really be on the website
        if not parsed or parsed[1] not in whole or parsed[2].split()[-1] not in whole:
            continue
        url = next((p["url"] for p in pages if parsed[2].split()[-1] in p["text"]), pages[0]["url"])
        people.append(_person(parsed, role, "Website (read by GPT)", f"{name} - {role}", url))
    return people


# ------------------------------------------------------------
# 4. Job ads
# ------------------------------------------------------------

JOB_CONTACT = re.compile(
    r"(?:ansprechpartner(?:in)?|ansprechperson|kontaktperson|ihr(?:e)? kontakt|dein(?:e)? kontakt|"
    r"contact person|your contact|point of contact|recruiter(?:in)?|talent acquisition(?: partner| manager)?|"
    r"fragen\??[^.]{0,30}?(?:an|bei)|questions\??[^.]{0,30}?(?:to|reach out to|contact))"
    r"[\s:,-]{1,6}((?:(?:frau|herrn?|mr\.?|ms\.?|dr\.?)\s+)*"
    r"[A-ZÄÖÜ][\w'’-]+(?:\s+(?:von|van|de|der|zu)?\s*[A-ZÄÖÜ][\w'’-]+){1,2})",
    re.IGNORECASE)
JOB_FRAU_HERR = re.compile(r"\b(?:Frau|Herrn?)\s+((?:Dr\.\s+)?[A-ZÄÖÜ][a-zäöüß-]+\s+[A-ZÄÖÜ][a-zäöüß-]+)")


def from_job_ads(jobs) -> list:
    people = []
    for j in jobs:
        text = re.sub(r"<[^>]+>", " ", j["description"] or "")
        text = re.sub(r"\s+", " ", text)
        for rx in (JOB_CONTACT, JOB_FRAU_HERR):
            for m in rx.finditer(text):
                cand = re.sub(r"\s+(?:unter|at|per|via|from|aus|vom|von der|telefon|tel|e-mail|mail)\b.*$", "",
                              m.group(1), flags=re.I)
                parsed = parse_name(cand)
                if parsed:
                    start = max(0, m.start() - 40)
                    people.append(_person(parsed, "Contact person in job ad", "Job ad",
                                          text[start:m.end() + 40], j["url"]))
    return people


# ------------------------------------------------------------
# 5. Hunter
# ------------------------------------------------------------

def from_hunter(data) -> list:
    people = []
    for e in (data or {}).get("emails", []) or []:
        parsed = parse_name(f"{e.get('first_name') or ''} {e.get('last_name') or ''}")
        if not parsed:
            continue
        role = e.get("position") or ("Executive" if e.get("seniority") == "executive" else "")
        status = ((e.get("verification") or {}).get("status")) or ""
        people.append(_person(parsed, role, "Hunter", f"{e.get('value')} ({role or 'no title'})", "",
                              email=(e.get("value") or "").lower(), hunter_confidence=e.get("confidence") or 0,
                              hunter_status=status))
    return people


# ------------------------------------------------------------
# Merge + choose
# ------------------------------------------------------------

SOURCE_ORDER = {"Impressum": 0, "Team page": 1, "Website": 1, "Website (read by GPT)": 2, "Hunter": 3, "Job ad": 4}


def merge(people: list) -> list:
    """Same person found twice -> one entry with the best role and all sources."""
    by_key = {}
    for p in people:
        k = person_key(p["first"], p["last"])
        if k not in by_key:
            by_key[k] = dict(p, sources={p["source"]})
            continue
        cur = by_key[k]
        cur["sources"].add(p["source"])
        better = (p["rank"], SOURCE_ORDER.get(p["source"], 5)) < (cur["rank"], SOURCE_ORDER.get(cur["source"], 5))
        if better:
            for f in ("role", "rank", "source", "proof", "proof_url", "name", "first", "last"):
                cur[f] = p[f]
        for f in ("email", "hunter_confidence", "hunter_status"):
            if p.get(f) and not cur.get(f):
                cur[f] = p[f]
    out = list(by_key.values())
    for p in out:
        p["source"] = " + ".join(sorted(p["sources"], key=lambda s: SOURCE_ORDER.get(s, 5)))
        p["decision_maker"] = p["rank"] <= 3
        del p["sources"]
    return sorted(out, key=lambda p: (p["rank"], SOURCE_ORDER.get(p["source"].split(" + ")[0], 5), -len(p["source"])))


ROLE_AT = re.compile(r"\b(?:at|bei|chez|@)\b\s*([^,;|()]*)", re.I)


def works_elsewhere(role: str, company: str) -> bool:
    """'Head of Data Platform at Malt' on dust.tt -> a customer quote, not an employee.
    'CTO at ' (company name missing) -> also not trusted."""
    m = ROLE_AT.search(role or "")
    if not m:
        return False
    other = m.group(1).strip(" .-–")
    if not other:
        return True
    import database as db
    mine = {t for t in db.name_key(company).split() if len(t) >= 3}
    theirs = {t for t in db.name_key(other).split() if len(t) >= 3}
    flat_mine, flat_theirs = "".join(sorted(mine)), "".join(db.name_key(other).split())
    return not (mine & theirs or (flat_mine and flat_mine in flat_theirs) or (flat_theirs and flat_theirs in flat_mine))


def find(company: str, site: dict, jobs, hunter_data=None) -> list:
    pages = site.get("pages", [])
    people = []
    for p in pages:
        if p["kind"] == "impressum":
            people += from_impressum(p["text"], p["url"])
    for p in pages:
        if p["kind"] in ("team", "home", "contact"):
            people += from_team_page(p["text"], p["url"], "Website" if p["kind"] != "team" else "Team page")
    if not any(p["rank"] == 1 for p in people):
        people += from_gpt(company, [p for p in pages if p["kind"] in ("team", "home")])
    people += from_job_ads(jobs)
    people += from_hunter(hunter_data)
    people = [p for p in people if not works_elsewhere(p["role"], company)]
    return merge(people)
