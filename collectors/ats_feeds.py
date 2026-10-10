"""
Company job pages from hiring tools (ATS). Free public feeds, no key.

Add companies to watchlist.csv (company,ats,slug). The agent also
suggests new entries automatically when it sees these links in job ads.
"""

import csv
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import config
from .http import get


def _personio(slug):
    for domain in ("de", "com"):
        try:
            root = ET.fromstring(get(f"https://{slug}.jobs.personio.{domain}/xml").content)
        except Exception:
            continue
        out = []
        for pos in root.iter("position"):
            pid = pos.findtext("id", "")
            text = " ".join(d.findtext("value", "") for d in pos.iter("jobDescription"))
            out.append({"title": pos.findtext("name", ""), "description": text,
                        "url": f"https://{slug}.jobs.personio.{domain}/job/{pid}",
                        "date": (pos.findtext("createdAt", "") or "")[:10]})
        return out
    return []


def _greenhouse(slug):
    data = get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", params={"content": "true"}).json()
    return [{"title": j.get("title", ""), "description": j.get("content", ""),
             "url": j.get("absolute_url", ""), "date": (j.get("updated_at") or "")[:10]}
            for j in data.get("jobs", [])]


def _lever(slug):
    for host in ("api.lever.co", "api.eu.lever.co"):
        try:
            data = get(f"https://{host}/v0/postings/{slug}", params={"mode": "json"}).json()
        except Exception:
            continue
        out = []
        for j in data:
            lists = " ".join(f"{l.get('text', '')} {l.get('content', '')}" for l in j.get("lists", []))
            ts = j.get("createdAt")
            date = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).date().isoformat() if ts else ""
            out.append({"title": j.get("text", ""),
                        "description": f"{j.get('descriptionPlain', '')} {lists} {j.get('additionalPlain', '')}",
                        "url": j.get("hostedUrl", ""), "date": date})
        return out
    return []


def _ashby(slug):
    data = get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}").json()
    return [{"title": j.get("title", ""), "description": j.get("descriptionPlain") or j.get("descriptionHtml", ""),
             "url": j.get("jobUrl", ""), "date": (j.get("publishedAt") or "")[:10]}
            for j in data.get("jobs", []) if j.get("isListed", True)]


def _workable(slug):
    data = get(f"https://www.workable.com/api/accounts/{slug}", params={"details": "true"}).json()
    return [{"title": j.get("title", ""),
             "description": f"{j.get('description', '')} {j.get('requirements', '')}",
             "url": j.get("url") or j.get("shortlink", ""),
             "date": (j.get("published_on") or j.get("created_at") or "")[:10]}
            for j in data.get("jobs", [])]


def _recruitee(slug):
    data = get(f"https://{slug}.recruitee.com/api/offers/").json()
    return [{"title": j.get("title", ""), "description": f"{j.get('description', '')} {j.get('requirements', '')}",
             "url": j.get("careers_url", ""), "date": (j.get("published_at") or j.get("created_at") or "")[:10]}
            for j in data.get("offers", [])]


READERS = {"personio": _personio, "greenhouse": _greenhouse, "lever": _lever,
           "ashby": _ashby, "workable": _workable, "recruitee": _recruitee}

LINK_RULES = [
    ("personio", r"https?://([\w-]+)\.jobs\.personio\.(?:de|com)"),
    ("greenhouse", r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/([\w-]+)"),
    ("lever", r"jobs(?:\.eu)?\.lever\.co/([\w-]+)"),
    ("ashby", r"jobs\.ashbyhq\.com/([\w.-]+)"),
    ("workable", r"apply\.workable\.com/([\w-]+)"),
    ("recruitee", r"https?://([\w-]+)\.recruitee\.com"),
]


def detect_ats(text: str):
    """Find a hiring-tool link (in a job URL or description) -> (ats, slug)."""
    for ats, pattern in LINK_RULES:
        m = re.search(pattern, text or "")
        if m and m.group(1).lower() not in {"www", "api", "jobs", "boards"}:
            return ats, m.group(1)
    return None, None


def load_watchlist() -> list:
    if not config.WATCHLIST_PATH.exists():
        return []
    with open(config.WATCHLIST_PATH, newline="", encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if r.get("company") and r.get("ats") and r.get("slug")]


def collect(watchlist=None, known_urls=frozenset()) -> list:
    watchlist = load_watchlist() if watchlist is None else watchlist
    jobs = []
    for entry in watchlist:
        reader = READERS.get(entry["ats"].strip().lower())
        if not reader:
            print(f"  ! Unknown hiring tool for {entry['company']}: {entry['ats']}")
            continue
        try:
            found = reader(entry["slug"].strip())
        except Exception as e:
            print(f"  ! {entry['company']} ({entry['ats']}): {e}")
            continue
        new = [j for j in found if j.get("url") and j["url"] not in known_urls]
        for j in new:
            j.update({"company": entry["company"], "source": entry["ats"]})
        jobs += new
    print(f"  Company job pages: {len(jobs)} new ads from {len(watchlist)} companies")
    return jobs
