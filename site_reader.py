"""
Reads a company website the polite way:
  - respects robots.txt
  - waits between requests to the same site
  - reads only a few useful pages: home, Impressum, team/about, contact

Also finds email addresses on pages, including hidden ones like
"anna [at] firma [dot] de" and Cloudflare-protected addresses.
"""

import html as html_lib
import re
import time
from urllib import robotparser
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

import config

UA = "Mozilla/5.0 (compatible; FloctaLeadAgent/1.0; research)"
HEADERS = {"User-Agent": UA, "Accept-Language": "de-DE,de;q=0.9,en;q=0.8"}
SITE_PAUSE = 1.0

_robots = {}
_last_hit = {}

PAGE_KINDS = {
    "impressum": r"impressum|imprint|legal[- ]?notice|legal|anbieterkennzeichnung|rechtliche[sn]? hinweise|site[- ]?notice|mentions[- ]l[ée]gales",
    "team": r"\bteam\b|about|über[- ]?uns|ueber[- ]?uns|wir[- ]über[- ]uns|unternehmen|company|who[- ]we[- ]are|management|leadership|founders|gründer|people|story|mission",
    "contact": r"kontakt|contact",
}
DEFAULT_PATHS = {
    "impressum": ["/impressum", "/imprint", "/legal-notice", "/de/impressum", "/en/imprint", "/legal"],
    "team": ["/team", "/about", "/ueber-uns", "/about-us", "/unternehmen", "/company"],
    "contact": ["/kontakt", "/contact"],
}

EMAIL_RE = re.compile(r"[a-z0-9][a-z0-9._%+\-]{0,63}@[a-z0-9.\-]+\.[a-z]{2,24}", re.IGNORECASE)
BAD_EMAIL_END = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js", ".ico")
BAD_EMAIL_PARTS = ("sentry", "wixpress", "example.", "domain.", "@email.", "yourname", "name@", "max.mustermann",
                   "beispiel", "@2x", "@3x")


def registrable(host: str) -> str:
    """www.jobs.firma.co.uk -> firma.co.uk ; www.firma.de -> firma.de"""
    host = (host or "").lower().split(":")[0].strip(".")
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "net", "gv", "ac") and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _allowed(url: str) -> bool:
    p = urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    if base not in _robots:
        rp = robotparser.RobotFileParser()
        try:
            r = requests.get(base + "/robots.txt", headers=HEADERS, timeout=8)
            rp.parse(r.text.splitlines() if r.status_code == 200 else [])
        except requests.RequestException:
            rp.parse([])
        _robots[base] = rp
    return _robots[base].can_fetch(UA, url)


def fetch(url: str, timeout: int = 12):
    """Returns (final_url, html) or (None, None). Never raises."""
    try:
        if not _allowed(url):
            return None, None
        host = urlparse(url).netloc
        wait = SITE_PAUSE - (time.time() - _last_hit.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        _last_hit[host] = time.time()
        r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        if r.status_code != 200 or "html" not in r.headers.get("Content-Type", "text/html"):
            return None, None
        r.encoding = r.encoding if r.encoding and r.encoding.lower() != "iso-8859-1" else r.apparent_encoding
        return r.url, r.text
    except Exception:
        return None, None


def page_text(html: str) -> str:
    """Visible text, one line per block (keeps 'Name' and 'Role' on separate lines)."""
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "iframe", "template"]):
        tag.decompose()
    for br in soup.find_all("br"):
        br.replace_with("\n")
    text = soup.get_text("\n")
    lines = [re.sub(r"[ \t ]+", " ", l).strip() for l in text.splitlines()]
    return "\n".join(l for l in lines if l)


def title_of(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    parts = []
    if soup.title and soup.title.string:
        parts.append(soup.title.string)
    for prop in ("og:site_name", "og:title", "application-name"):
        tag = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
        if tag and tag.get("content"):
            parts.append(tag["content"])
    return " | ".join(parts)


def _cf_decode(hexstr: str) -> str:
    try:
        key = int(hexstr[:2], 16)
        return "".join(chr(int(hexstr[i:i + 2], 16) ^ key) for i in range(2, len(hexstr), 2))
    except ValueError:
        return ""


def find_emails(html_or_text: str) -> set:
    raw = html_lib.unescape(html_or_text or "")
    found = set()
    # Cloudflare email protection
    for h in re.findall(r'data-cfemail="([0-9a-fA-F]+)"', raw):
        found.add(_cf_decode(h))
    for h in re.findall(r"/cdn-cgi/l/email-protection#([0-9a-fA-F]+)", raw):
        found.add(_cf_decode(h))
    # "anna [at] firma [dot] de", "anna(at)firma.de", "anna at firma dot de"
    text = re.sub(r"\s*[\[\(\{]\s*(?:at|ät|@)\s*[\]\)\}]\s*", "@", raw, flags=re.IGNORECASE)
    text = re.sub(r"\s*[\[\(\{]\s*(?:dot|punkt|\.)\s*[\]\)\}]\s*", ".", text, flags=re.IGNORECASE)
    text = re.sub(r"(\w)\s+(?:at)\s+(\w[\w-]*)\s+(?:dot|punkt)\s+(\w{2,6})\b", r"\1@\2.\3", text)
    found.update(EMAIL_RE.findall(text))
    out = set()
    for e in found:
        e = e.lower().strip(".-_ ")
        if not EMAIL_RE.fullmatch(e) or e.endswith(BAD_EMAIL_END) or any(b in e for b in BAD_EMAIL_PARTS):
            continue
        out.add(e)
    return out


def _links(html: str, base_url: str):
    soup = BeautifulSoup(html or "", "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        yield urljoin(base_url, href), " ".join(a.get_text(" ").split())[:80]


def read_site(domain: str) -> dict:
    """
    {"domain": final domain, "home_url", "title",
     "pages": [{"kind", "url", "text", "emails"}], "emails": set()}
    """
    result = {"domain": domain, "home_url": None, "title": "", "pages": [], "emails": set()}
    home_url, home = None, None
    for start in (f"https://{domain}", f"https://www.{domain}", f"http://{domain}"):
        home_url, home = fetch(start)
        if home:
            break
    if not home:
        return result

    final_domain = registrable(urlparse(home_url).netloc)
    result.update(domain=final_domain, home_url=home_url, title=title_of(home))
    result["pages"].append({"kind": "home", "url": home_url, "text": page_text(home),
                            "emails": find_emails(home)})

    # Pick the best link for each kind of page (same website only)
    chosen = {}
    for url, text in _links(home, home_url):
        if registrable(urlparse(url).netloc) != final_domain:
            continue
        path = urlparse(url).path.lower()
        for kind, pattern in PAGE_KINDS.items():
            if kind in chosen and kind != "team":
                continue
            if re.search(pattern, f"{text} {path}", re.IGNORECASE):
                chosen.setdefault(kind, [])
                if url not in chosen[kind]:
                    chosen[kind].append(url)
                break

    root = f"{urlparse(home_url).scheme}://{urlparse(home_url).netloc}"
    queue = []
    for kind in ("impressum", "team", "contact"):
        urls = chosen.get(kind, [])[:3 if kind == "team" else 1]
        queue += [(kind, u) for u in urls] or [(kind, root + p) for p in DEFAULT_PATHS[kind]]

    seen = {home_url.rstrip("/")}
    got = {"impressum": 0, "team": 0, "contact": 0}
    for kind, url in queue:
        if len(result["pages"]) >= config.MAX_PAGES_PER_SITE:
            break
        if url.rstrip("/") in seen or (kind != "team" and got[kind]) or got.get(kind, 0) >= 3:
            continue
        seen.add(url.rstrip("/"))
        final, html = fetch(url)
        if not html:
            continue
        got[kind] += 1
        result["pages"].append({"kind": kind, "url": final, "text": page_text(html),
                                "emails": find_emails(html)})

    for p in result["pages"]:
        result["emails"] |= p["emails"]
    return result
