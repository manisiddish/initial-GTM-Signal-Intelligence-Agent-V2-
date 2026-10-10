"""Arbeitnow: startup job board for Germany. Free API, no key."""

from datetime import datetime, timezone

import config
from .http import get

API = "https://www.arbeitnow.com/api/job-board-api"


def collect(pages=None, known_urls=frozenset()) -> list:
    pages = pages or config.ARBEITNOW_PAGES
    jobs = []
    for page in range(1, pages + 1):
        try:
            items = get(API, params={"page": page}).json().get("data", [])
        except Exception as e:
            print(f"  ! Arbeitnow page {page}: {e}")
            break
        if not items:
            break
        for item in items:
            url = item.get("url", "")
            if not url or url in known_urls:
                continue
            created = item.get("created_at")
            date = ""
            if isinstance(created, (int, float)):
                date = datetime.fromtimestamp(created, tz=timezone.utc).date().isoformat()
            jobs.append({
                "company": (item.get("company_name") or "").strip(),
                "title": item.get("title", ""),
                "description": f"{item.get('description', '')} {' '.join(item.get('tags') or [])}",
                "url": url,
                "date": date,
                "source": "arbeitnow",
                "location": item.get("location", ""),
            })
    print(f"  Arbeitnow: {len(jobs)} new ads")
    return jobs
