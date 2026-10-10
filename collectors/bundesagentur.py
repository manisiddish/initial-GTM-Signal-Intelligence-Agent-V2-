"""
Bundesagentur für Arbeit: the German national job board.

Public (unofficial) interface, free, documented at
https://github.com/bundesAPI/jobsuche-api

The address has changed over time (v4 -> v6), so we try the known
addresses in order and remember the first one that works.
"""

import base64

import config
from .http import get, all_strings

BASE = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service"
SEARCH_PATHS = ["/pc/v6/jobs", "/pc/v4/app/jobs", "/pc/v4/jobs"]
DETAIL_PATHS = ["/pc/v6/jobdetails/", "/pc/v4/jobdetails/", "/pc/v3/jobdetails/"]
KEYS = [{"X-API-Key": "jobboerse-jobsuche"}, {"X-API-KEY": "jobboerse-jobsuche"}]
PUBLIC_LINK = "https://www.arbeitsagentur.de/jobsuche/jobdetail/"

_working = {"search": None, "details": None}

# kept for tests / backwards compatibility
SEARCH = BASE + SEARCH_PATHS[0]
DETAILS = BASE + DETAIL_PATHS[0]


def _search(params):
    """Try the known search addresses until one works."""
    options = [_working["search"]] if _working["search"] else [
        (path, key) for path in SEARCH_PATHS for key in KEYS]
    last_error = None
    for path, key in options:
        try:
            data = get(BASE + path, headers=key, params=params, timeout=60).json()
            _working["search"] = (path, key)
            return data
        except Exception as e:
            last_error = e
    raise last_error


def _details(ref):
    code = base64.b64encode(ref.encode()).decode()
    options = [_working["details"]] if _working["details"] else [
        (path, key) for path in DETAIL_PATHS for key in KEYS]
    for path, key in options:
        try:
            data = get(BASE + path + code, headers=key, timeout=60).json()
            _working["details"] = (path, key)
            return all_strings(data)
        except Exception:
            continue
    return ""


def collect(days=None, words=None, known_urls=frozenset()) -> list:
    days = days or config.DAYS_BACK
    words = words or config.BA_SEARCH_WORDS
    jobs, seen = [], set()
    failures = 0

    for word in words:
        found = []
        for page in range(1, config.BA_MAX_PAGES + 1):
            params = {"was": word, "veroeffentlichtseit": days,
                      "size": config.BA_PAGE_SIZE, "page": page, "angebotsart": 1}
            try:
                data = _search(params)
            except Exception as e:
                print(f"  ! Bundesagentur '{word}': {e}")
                failures += 1
                break
            # v6 answers with "ergebnisliste", older versions with "stellenangebote"
            batch = data.get("ergebnisliste") or data.get("stellenangebote") or []
            found += batch
            if len(batch) < config.BA_PAGE_SIZE:
                break

        if failures >= 3 and not _working["search"]:
            print("  ! Bundesagentur is blocking every request right now. Skipping it for this run.")
            break

        print(f"  Bundesagentur '{word}': {len(found)} ads")

        for item in found:
            ref = item.get("referenznummer") or item.get("refnr")
            url = PUBLIC_LINK + ref if ref else None
            if not ref or ref in seen or url in known_urls:
                continue
            seen.add(ref)
            places = item.get("stellenlokationen") or []
            place = ((places[0].get("adresse") or {}).get("ort", "") if places
                     else (item.get("arbeitsort") or {}).get("ort", ""))
            external = item.get("externeURL") or item.get("externeUrl") or ""
            # details may be blocked; the title still counts, and the external link
            # helps detect the company's hiring tool
            description = f"{_details(ref)} {external}".strip()
            jobs.append({
                "company": (item.get("firma") or item.get("arbeitgeber") or "").strip(),
                "title": item.get("stellenangebotsTitel") or item.get("titel") or item.get("beruf") or "",
                "description": description,
                "url": url,
                "date": item.get("datumErsteVeroeffentlichung") or item.get("aktuelleVeroeffentlichungsdatum") or "",
                "source": "bundesagentur",
                "location": place,
            })
    return jobs
