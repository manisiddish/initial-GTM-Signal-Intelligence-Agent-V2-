"""
Find a company's website (domain), in this order:

  1. websites.csv           you fixed it by hand
  2. the job ads            links and email addresses in the company's own ads
  3. GPT web search         GPT searches with the context of the job ad (what they do, the ad link)
  4. smart guesses          bliro.com, bliro.ai, bliro.de ... (the page must show the company name)
  5. Hunter                 asks Hunter by company name (costs 1 search)

A domain is only accepted when it clearly belongs to the company:
its name is in the domain, or the company name is on the website.
"""

import csv
import re
from collections import Counter
from urllib.parse import urlparse

import config
import database as db
import hunter
import llm
import site_reader

# Never a company's own website
NEVER = {
    "arbeitnow.com", "arbeitnow.fr", "arbeitnow.de", "arbeitsagentur.de", "linkedin.com", "xing.com",
    "facebook.com", "instagram.com", "twitter.com", "x.com", "youtube.com", "youtu.be", "tiktok.com",
    "github.com", "gitlab.com", "medium.com", "glassdoor.com", "glassdoor.de", "kununu.com",
    "indeed.com", "indeed.de", "stepstone.de", "monster.de", "join.com", "jobgether.com",
    "personio.de", "personio.com", "greenhouse.io", "lever.co", "ashbyhq.com", "workable.com",
    "recruitee.com", "smartrecruiters.com", "softgarden.io", "softgarden.de", "myworkdayjobs.com",
    "workday.com", "successfactors.com", "successfactors.eu", "bamboohr.com", "teamtailor.com",
    "d.vinci.de", "dvinci-easy.com", "onlyfy.jobs", "jobteaser.com", "welcometothejungle.com",
    "gmail.com", "googlemail.com", "gmx.de", "gmx.net", "web.de", "outlook.com", "hotmail.com",
    "yahoo.com", "t-online.de", "icloud.com", "proton.me", "protonmail.com", "mail.de",
    "bit.ly", "goo.gl", "t.co", "lnkd.in", "calendly.com", "typeform.com", "forms.gle",
    "google.com", "goo.gle", "w3.org", "schema.org", "wikipedia.org", "europa.eu", "bund.de",
    "gesetze-im-internet.de", "notion.site", "notion.so", "hubspot.com", "hs-sites.com",
    "openai.com", "anthropic.com", "microsoft.com", "azure.com", "amazon.com", "aws.amazon.com",
    "apple.com", "python.org", "pytorch.org", "tensorflow.org", "huggingface.co", "langchain.com",
    "kubernetes.io", "docker.com", "atlassian.com", "slack.com", "zoom.us",
}

# Words that don't help to match a name with a domain
FILLER = {"group", "gruppe", "holding", "digital", "technologies", "technology", "tech", "solutions",
          "software", "systems", "services", "service", "deutschland", "germany", "europe", "international",
          "the", "und", "and", "company", "labs", "lab", "ai", "io", "de", "berlin", "münchen", "munich",
          "hamburg", "consulting", "partners", "partner"}

TLDS = [".com", ".de", ".ai", ".io", ".eu", ".tech", ".co", ".app", ".net", ".org"]
PARKED = re.compile(r"domain (?:is )?for sale|diese domain (?:kaufen|steht zum verkauf)|sedo|parkingcrew|"
                    r"this domain (?:may be|is) for sale|domain kaufen|buy this domain|hugedomains|dan\.com",
                    re.IGNORECASE)

# Signs that a website belongs to a company active in Germany / Europe (our job ads are from there)
EUROPE_HINTS = re.compile(
    r"impressum|imprint|gmbh|\bug\b|\bag\b|deutschland|germany|german|europe|european|\beu\b|dsgvo|gdpr|"
    r"berlin|münchen|munich|hamburg|köln|cologne|frankfurt|stuttgart|düsseldorf|leipzig|dresden|hannover|"
    r"nürnberg|bremen|essen|dortmund|karlsruhe|mannheim|bonn|wien|vienna|zürich|zurich|amsterdam|paris|"
    r"london|madrid|barcelona|lisbon|lisboa|stockholm|copenhagen|warsaw|prague|\+49|\+43|\+41",
    re.IGNORECASE)
EUROPE_TLDS = (".de", ".at", ".ch", ".eu", ".fr", ".nl", ".es", ".it", ".pl", ".se", ".dk", ".fi", ".be",
               ".pt", ".cz", ".uk", ".ie", ".lu", ".no")

URL_RE = re.compile(r"(?:https?://|www\.)[^\s\"'<>()\[\]{},]+", re.IGNORECASE)


def _ascii(s: str) -> str:
    return (s.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss"))


def name_tokens(company: str):
    tokens = [t for t in db.name_key(company).split() if t not in FILLER]
    return tokens or db.name_key(company).split()


def name_match(domain: str, company: str) -> int:
    """0-100: how clearly does the domain belong to this company?"""
    label = _ascii(registrable_label(domain))
    label_flat = label.replace("-", "")
    tokens = [_ascii(t) for t in name_tokens(company)]
    if not tokens:
        return 0
    compact = "".join(tokens)
    if label_flat == compact:
        return 100
    if len(label_flat) >= 4 and (compact.startswith(label_flat) or label_flat.startswith(compact)):
        return 85
    if len(tokens[0]) >= 4 and label_flat.startswith(tokens[0]):
        return 70
    if any(len(t) >= 5 and t in label_flat for t in tokens):
        return 60
    # initials: "Finanz Informatik" -> f-i.de / fi.de
    initials = "".join(t[0] for t in tokens)
    if len(tokens) >= 2 and label_flat == initials:
        return 50
    return 0


def registrable_label(domain: str) -> str:
    return site_reader.registrable(domain).split(".")[0]


def page_shows_name(html_title: str, text: str, company: str) -> bool:
    tokens = [t for t in name_tokens(company) if len(t) >= 3]
    hay = _ascii(f"{html_title}\n{text[:6000]}").lower()
    hay_flat = re.sub(r"[^a-z0-9]", "", hay)
    compact = "".join(_ascii(t) for t in tokens)
    if compact and len(compact) >= 4 and compact in hay_flat:
        return True
    return bool(tokens) and all(re.search(rf"(?<![a-z0-9]){re.escape(_ascii(t))}(?![a-z0-9])", hay) for t in tokens)


def _domain_of(url_or_email: str) -> str:
    if "@" in url_or_email and "/" not in url_or_email:
        host = url_or_email.split("@")[1]
    else:
        u = url_or_email if url_or_email.lower().startswith("http") else "https://" + url_or_email
        host = urlparse(u).netloc
    return site_reader.registrable(host.strip(".,;:"))


def _is_never(domain: str) -> bool:
    return domain in NEVER or any(domain.endswith("." + n) for n in NEVER)


def _check_live(domain: str, company: str, need: str = None):
    """
    Open the homepage. Returns the final domain if it is a real site of this company.
    need=None: just has to be a real site; "page": company name somewhere on the page;
    "title": company name in the page title (strictest, used for guesses).
    """
    final_url, html = site_reader.fetch(f"https://{domain}", timeout=8)
    if not html:
        final_url, html = site_reader.fetch(f"https://www.{domain}", timeout=8)
    if not html:
        return None
    text = site_reader.page_text(html)
    title = site_reader.title_of(html)
    if PARKED.search(f"{title} {text[:3000]}") or (len(text) < 20 and not title):
        return None
    final = site_reader.registrable(urlparse(final_url).netloc)
    if _is_never(final):
        return None
    if need == "page" and not page_shows_name(title, text, company):
        return None
    if need == "title" and not page_shows_name(title, "", company):
        return None
    return final


def _in_europe(domain: str) -> bool:
    """Is this site from a company active in Germany / Europe? (homepage or Impressum shows it)"""
    if domain.endswith(EUROPE_TLDS):
        return True
    for path in ("", "/impressum", "/imprint", "/legal-notice", "/about"):
        _, html = site_reader.fetch(f"https://{domain}{path}", timeout=8)
        if html and (path in ("/impressum", "/imprint") or
                     EUROPE_HINTS.search(f"{site_reader.title_of(html)} {site_reader.page_text(html)[:20000]}")):
            return True
    return False


def _manual(company: str):
    path = config.WEBSITES_PATH
    if not path.exists():
        return None
    key = db.name_key(company)
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if db.name_key(row.get("company", "")) == key and row.get("website", "").strip():
                return _domain_of(row["website"].strip())
    return None


def from_job_ads(company: str, jobs) -> list:
    """Domains in the company's own job ads, best first: [(domain, match, count)]"""
    counts = Counter()
    for j in jobs:
        text = f"{j['description'] or ''} {j['url'] or ''}"
        for u in URL_RE.findall(text):
            counts[_domain_of(u)] += 1
        for e in site_reader.find_emails(text):
            counts[_domain_of(e)] += 2          # an email address of the company is a strong hint
    out = []
    for d, n in counts.items():
        if not d or "." not in d or _is_never(d):
            continue
        out.append((d, name_match(d, company), n))
    return sorted(out, key=lambda x: (-x[1], -x[2]))


def guesses(company: str, slug: str = None) -> list:
    plain = re.sub(r"\(.*?\)", " ", company)          # "Lime (Connect & CRM)" -> "Lime"
    tokens = [_ascii(t) for t in name_tokens(plain)]
    tokens = [re.sub(r"[^a-z0-9]", "", t) for t in tokens if re.sub(r"[^a-z0-9]", "", t)]
    extra = [re.sub(r"[^a-z0-9]", "", _ascii(t)) for t in name_tokens(company)]
    if not tokens:
        return []
    plan = [("".join(tokens), TLDS)]
    if len(tokens) > 1:
        plan += [("-".join(tokens), TLDS[:4]), (tokens[0], TLDS[:4])]
    for t in extra:                                     # "lime-crm.com", "limecrm.de"
        if t and t not in tokens:
            plan += [(f"{tokens[0]}-{t}", [".com", ".de"]), (f"{tokens[0]}{t}", [".com", ".de"])]
    if slug:
        plan.insert(0, (re.sub(r"[^a-z0-9-]", "", slug.lower()), TLDS[:4]))
    seen, out = set(), []
    for base, tlds in plan:
        for tld in tlds:
            d = base + tld
            if len(base) >= 3 and d not in seen:
                seen.add(d)
                out.append(d)
    return out[:24]


LEGAL_FORM = re.compile(r"\b(?:gmbh|ag|se|ug|kg|ohg|gbr|e\.?\s?v|inc|ltd|llc|b\.?v|s\.?a\.?s?)\b", re.I)


def _legal_name_on_site(domain: str, company: str) -> bool:
    """'Flip GmbH' must appear on the homepage or Impressum (for short, common names)."""
    want = re.sub(r"\s+", " ", company.lower()).strip()
    for path in ("", "/impressum", "/imprint", "/legal-notice"):
        _, html = site_reader.fetch(f"https://{domain}{path}", timeout=8)
        if html and want in re.sub(r"\s+", " ", site_reader.page_text(html).lower()):
            return True
    return False


GPT_SITE_PROMPT = """Find the official website of this company. It posted the job ad(s) below in Germany/Europe.
Company name: {name}
Job title(s): {titles}
What they do (from the job ad): {about}
Job ad link(s): {links}

Search the web. Make sure it is the SAME company (same business, active in Germany/Europe), not another
company with a similar name. Answer only with JSON:
{{"domain": "example.com or empty if not sure", "why": "max 15 words"}}"""


def from_gpt_search(company_row, jobs):
    """GPT searches the web for the official website (cached, verified afterwards by the code)."""
    if not (config.USE_GPT_WEB_SEARCH and llm.available()):
        return None
    key = f"gptsite:{db.name_key(company_row['name'])}"
    with db.connect() as conn:
        cached = db.get_cache(conn, key)
    if cached is not None:
        return cached or None
    about = ""
    if "ai_builds" in company_row.keys() and company_row["ai_builds"]:
        about = company_row["ai_builds"]
    elif jobs:
        about = re.sub(r"<[^>]+>|\s+", " ", jobs[0]["description"] or "")[:300]
    prompt = GPT_SITE_PROMPT.format(
        name=company_row["name"], titles="; ".join({j["title"] for j in jobs[:3]}),
        about=about, links=" ".join(j["url"] for j in jobs[:2]))
    data = llm.web_search_json("find_website", prompt) or {}
    raw = str(data.get("domain") or "").strip()
    domain = _domain_of(raw) if raw and "." in raw else ""
    if llm.available() and llm._search_mode["value"] and llm._search_mode["value"][0] != "off":
        with db.connect() as conn:
            db.save_cache(conn, key, domain)
    return domain or None


def find(company_row, jobs) -> tuple:
    """Returns (domain, how_found) or (None, reason)."""
    name = company_row["name"]

    d = _manual(name)
    if d:
        return d, "websites.csv (set by hand)"

    source = company_row["website_source"] or ""
    if company_row["domain"] and not source.startswith(("Hunter", "guessed")):
        return company_row["domain"], source or "saved"

    # 2. job ads
    for domain, match, n in from_job_ads(name, jobs)[:5]:
        if match >= 85:
            live = _check_live(domain, name)
            if live:
                return live, f"link in job ad ({domain})"
        elif match >= 50 or n >= 2:
            live = _check_live(domain, name, need="page")
            if live:
                return live, f"link in job ad ({domain}), name found on site"

    # 3. GPT web search (knows the context: what they do, the job ad) -> the code checks the site
    domain = from_gpt_search(company_row, jobs)
    if domain and not _is_never(domain):
        live = _check_live(domain, name, need="page")
        if live and _in_europe(live):
            return live, "GPT web search, name + Germany/Europe found on site"

    # 4. guesses (the page must show the company name)
    #    + must be active in Germany/Europe (avoids a same-name company in another country)
    #    Short, common names (Flip, Lime, MAIA) match many companies: then the full legal name
    #    ("Flip GmbH") must be on the site, and without a legal form we don't guess at all.
    compact = "".join(re.sub(r"[^a-z0-9]", "", _ascii(t)) for t in name_tokens(re.sub(r"\(.*?\)", " ", name)))
    short = len(compact) <= 4
    if not short or LEGAL_FORM.search(name):
        for domain in guesses(name, company_row["ats_slug"]):
            live = _check_live(domain, name, need="title")
            if not live or not _in_europe(live):
                continue
            if short and not _legal_name_on_site(live, name):
                continue
            return live, f"guessed {domain}, name in page title"

    # 5. Hunter: only matches by NAME, so it can find a different company with the same name.
    #    Accept only if the name is on the site AND the site is active in Germany/Europe.
    domain, _ = hunter.find_domain(name)
    if domain and not _is_never(domain):
        live = _check_live(domain, name, need="page")
        if live and _in_europe(live):
            return live, "Hunter company search, name + Germany/Europe found on site"
        return None, (f"website not found. Hunter suggested {domain}, but it doesn't look like the same "
                      f"company (add the right one to websites.csv)")
    return None, "website not found (add it to websites.csv)"
