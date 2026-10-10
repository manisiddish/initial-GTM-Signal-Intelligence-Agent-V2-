"""One shared, polite way to download pages."""

import time

import requests

import config

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; FloctaLeadAgent/1.0; research)",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
}

_last_request = 0.0


def get(url, headers=None, params=None, timeout=30, retries=3):
    """
    GET with a pause between requests. Raises on HTTP errors.
    If the site says "too many requests" (429) or is briefly down (502/503),
    wait and try again instead of giving up.
    """
    global _last_request
    all_headers = dict(HEADERS)
    all_headers.update(headers or {})

    for attempt in range(retries + 1):
        wait = config.REQUEST_PAUSE - (time.time() - _last_request)
        if wait > 0:
            time.sleep(wait)
        try:
            response = requests.get(url, headers=all_headers, params=params, timeout=timeout)
        finally:
            _last_request = time.time()

        if response.status_code in (429, 502, 503) and attempt < retries:
            retry_after = response.headers.get("Retry-After", "")
            pause = int(retry_after) if retry_after.isdigit() else 15 * (attempt + 1)
            print(f"  ... {response.status_code} from {url.split('/')[2]}, waiting {pause}s and trying again")
            time.sleep(min(pause, 120))
            continue

        response.raise_for_status()
        return response


def all_strings(obj) -> str:
    """Join every text value inside a JSON object (for job details)."""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        return " ".join(all_strings(v) for v in obj.values())
    if isinstance(obj, list):
        return " ".join(all_strings(v) for v in obj)
    return ""
