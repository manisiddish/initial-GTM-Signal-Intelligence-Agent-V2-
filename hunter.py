"""
Hunter.io: the service that checks whether an email address really exists.

We never test email servers ourselves (from a home internet connection that gets
you blocked and looks like spam). Hunter does it for us.

Free plan: 25 searches + 50 verifications per month (paid plans have more).
Every answer is saved in the database, so the same question is never paid twice.

  verify(email)                -> valid / invalid / accept_all / unknown ...
  domain_search(domain)        -> email format of the company + emails Hunter knows
  email_finder(domain, f, l)   -> Hunter's best guess for one person
  find_domain(company_name)    -> website of a company
"""

import json
import time

import requests

import config
import database as db

BASE = "https://api.hunter.io/v2"

used = {"verifications": 0, "searches": 0}
_off_reason = None


class Skip(Exception):
    """The person asked Hunter not to process their data (GDPR). We respect that."""


def available() -> bool:
    return bool(config.HUNTER_API_KEY) and _off_reason is None


def off_reason() -> str:
    if not config.HUNTER_API_KEY:
        return "no HUNTER_API_KEY in .env"
    return _off_reason or ""


def _switch_off(reason):
    global _off_reason
    if _off_reason is None:
        print(f"  Hunter switched off for this run: {reason}")
    _off_reason = reason


def _call(endpoint: str, params: dict):
    """Returns the 'data' part of Hunter's answer, or None."""
    if not available():
        return None
    params = dict(params, api_key=config.HUNTER_API_KEY)
    for attempt in range(4):
        try:
            r = requests.get(f"{BASE}/{endpoint}", params=params, timeout=40)
        except requests.RequestException as e:
            print(f"  Hunter network error: {e}")
            time.sleep(3)
            continue
        if r.status_code == 200:
            return (r.json() or {}).get("data")
        if r.status_code == 202:            # verification still running
            time.sleep(4)
            continue
        if r.status_code == 222:            # mail server did not answer
            return {"status": "unknown"}
        if r.status_code == 451:            # person asked not to be processed
            raise Skip()
        if r.status_code == 401:
            _switch_off("API key is wrong (check HUNTER_API_KEY in .env)")
            return None
        if r.status_code == 429:
            _switch_off("no credits left this month")
            return None
        if r.status_code == 403:            # too many requests per second
            time.sleep(2)
            continue
        print(f"  Hunter answered {r.status_code}: {r.text[:150]}")
        return None
    return None


def _cached(key: str, fetch):
    with db.connect() as conn:
        hit = db.get_cache(conn, key)
    if hit is not None:
        return json.loads(hit)
    data = fetch()
    if data is not None:
        with db.connect() as conn:
            db.save_cache(conn, key, json.dumps(data))
    return data


def _search_budget_ok() -> bool:
    if used["searches"] >= config.HUNTER_MAX_SEARCHES_PER_RUN:
        _switch_off(f"search limit for this run reached ({config.HUNTER_MAX_SEARCHES_PER_RUN}, see config.py)")
        return False
    return True


# ------------------------------------------------------------

def verify(email: str) -> dict:
    """
    {"status": valid | invalid | accept_all | unknown | webmail | disposable | blocked, "score": 0-100}
    accept_all = the company's server says "yes" to every address (catch-all), so a
                 "yes" proves nothing.
    """
    email = email.lower().strip()
    with db.connect() as conn:
        row = db.get_email_check(conn, email)
    if row:
        return {"status": row["status"], "score": row["score"] or 0, "cached": True}
    if not available():
        return {"status": "not_checked", "score": 0}
    if used["verifications"] >= config.HUNTER_MAX_VERIFICATIONS_PER_RUN:
        _switch_off(f"verification limit for this run reached "
                    f"({config.HUNTER_MAX_VERIFICATIONS_PER_RUN}, see config.py)")
        return {"status": "not_checked", "score": 0}

    used["verifications"] += 1
    data = _call("email-verifier", {"email": email})
    if data is None:
        return {"status": "not_checked", "score": 0}
    status = data.get("status")
    if not status:   # older answer format
        status = {"deliverable": "valid", "undeliverable": "invalid"}.get(data.get("result"), "unknown")
    out = {"status": status, "score": int(data.get("score") or 0)}
    with db.connect() as conn:
        db.save_email_check(conn, email, out["status"], out["score"])
    return out


def domain_search(domain: str):
    """{"pattern": "{first}.{last}", "accept_all": bool, "emails": [{value, first_name, last_name, position, ...}]}"""
    if not config.USE_HUNTER_DOMAIN_SEARCH:
        return None

    def fetch():
        if not available() or not _search_budget_ok():
            return None
        used["searches"] += 1
        return _call("domain-search", {"domain": domain, "limit": 10})

    return _cached(f"domain:{domain}", fetch)


def email_finder(domain: str, first: str, last: str):
    """{"email": ..., "score": 0-100, "verification": {"status": ...}} or None"""
    if not config.USE_HUNTER_EMAIL_FINDER:
        return None

    def fetch():
        if not available() or not _search_budget_ok():
            return None
        used["searches"] += 1
        data = _call("email-finder", {"domain": domain, "first_name": first, "last_name": last})
        return data if data is not None else ({} if available() else None)

    return _cached(f"finder:{domain}:{first.lower()}:{last.lower()}", fetch)


def find_domain(company: str):
    """Ask Hunter for a company's website (costs 1 search). Returns 'example.com' or None."""
    def fetch():
        if not available() or not _search_budget_ok():
            return None
        used["searches"] += 1
        data = _call("domain-search", {"company": company, "limit": 10})
        return data if data is not None else ({} if available() else None)

    data = _cached(f"company:{db.name_key(company)}", fetch)
    return (data or {}).get("domain") or None, data


def account() -> str:
    if not config.HUNTER_API_KEY:
        return "Hunter: no HUNTER_API_KEY in .env"
    data = _call("account", {})
    if not data:
        return f"Hunter: could not read account ({off_reason() or 'unknown error'})"
    req = data.get("requests", {})
    s, v = req.get("searches", {}), req.get("verifications", {})
    return (f"Hunter ({data.get('plan_name', '?')} plan): "
            f"searches {s.get('used', '?')}/{s.get('available', '?')}, "
            f"verifications {v.get('used', '?')}/{v.get('available', '?')} used this month")
