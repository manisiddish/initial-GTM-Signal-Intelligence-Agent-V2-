"""
Score every company from 0 to 100. Simple math anyone can check.

  + points for each kind of signal (each kind counts once)
  + 5 for every extra AI job ad (max +15)
  + 5 if the ads mention EU data rules (DSGVO / GDPR)
  - signals older than 30 days count half
  - only "mentions AI" and nothing else = weak, max 10
  x 0   Flocta / investors, job boards, big companies, AI labs (excluded)
  x 0.5 consultancies / agencies, and companies with very many AI job ads
"""

from datetime import date, datetime

import re

import config
import database as db
from signals import LABELS, mentions_eu_data


def _matches(name: str, words, anywhere: bool = False) -> bool:
    """
    anywhere=False: the company name IS the word or starts with it
                    ("GitLab Inc" matches "gitlab"; "Meta Design GmbH" does not match "meta")
    anywhere=True:  the word appears anywhere in the name ("XY Consulting GmbH")
    """
    key = db.name_key(name)
    raw = (name or "").lower().strip()
    for w in words:
        w = w.lower()
        wk = db.name_key(w) or w
        if anywhere:
            if re.search(rf"(?<![a-z0-9]){re.escape(wk)}(?![a-z0-9])", key):
                return True
        elif key == wk or raw == w or raw.startswith(w + ".") or (len(wk) > 5 and key.startswith(wk + " ")):
            return True
    return False


JOB_TITLE_WORDS = re.compile(
    r"\((?:m|w|f|d)\s*/|\b(?:engineer|developer|entwickler|manager|werkstudent|praktikant|"
    r"intern|senior|junior|teamlead|specialist|consultant)\b", re.IGNORECASE)


def looks_like_job_title(name: str) -> bool:
    """Some sources put the job title in the company field, e.g. 'Senior Software Engineer (Typescript)'."""
    return bool(JOB_TITLE_WORDS.search(name or "")) and not re.search(
        r"\b(gmbh|ag|se|inc|ltd|llc|ug|kg|bv|sas|sarl|oy|ab)\b", name or "", re.IGNORECASE)


def company_flag(name: str):
    """Returns (flag, factor). factor 0 = excluded, 0.5 = weak lead, 1 = normal."""
    # Always removed: not the company that is hiring
    if looks_like_job_title(name):
        return "Not a company name (data error)", 0
    if _matches(name, config.JOB_BOARDS):
        return "Job board, not an employer (excluded)", 0
    if _matches(name, config.EXCLUDE_COMPANIES):
        return "Flocta / investor (excluded)", 0

    keep = config.KEEP_ALL_COMPANIES
    if _matches(name, config.PUBLIC_SECTOR_WORDS, anywhere=True):
        return ("Public sector / research", 1) if keep else ("Public sector / research (excluded)", 0)
    if _matches(name, config.TOO_BIG_OR_AI_LAB):
        return ("Big company or AI lab", 1) if keep else ("Too big or AI lab (excluded)", 0)
    if _matches(name, config.CONSULTANCY_WORDS, anywhere=True):
        return ("Consultancy / agency", 1) if keep else ("Consultancy / agency: partner, not customer", 0.5)
    return None, 1

ORDER = ["hires_ai_role", "cost_pain", "builds_with_ai", "daily_ai_use", "mentions_ai"]


def _age_days(posted: str):
    if not posted:
        return None
    try:
        d = datetime.fromisoformat(posted[:10]).date()
        return (date.today() - d).days
    except ValueError:
        return None


def score_company(rows, name: str = "") -> tuple:
    """rows = signals_for(company). Returns (score, tier, why, eu_data)."""
    best = {}
    jobs = set()
    ai_roles = set()
    eu = False
    for r in rows:
        jobs.add(r["job_id"])
        if r["type"] == "hires_ai_role":
            ai_roles.add(r["job_id"])
        age = _age_days(r["posted_date"])
        pts = r["points"] / 2 if (age is not None and age > config.OLD_SIGNAL_DAYS) else r["points"]
        best[r["type"]] = max(best.get(r["type"], 0), pts)
        if not eu and mentions_eu_data(r["description"] or ""):
            eu = True

    counted = ai_roles if config.AI_ROLES_ONLY else jobs
    if config.AI_ROLES_ONLY and not ai_roles and "cost_pain" not in best:
        score = 0
    elif set(best) == {"mentions_ai"}:
        score = min(10, 5 * len(jobs))
    else:
        score = sum(best.values())
        score += min(config.EXTRA_JOB_MAX, config.EXTRA_JOB_POINTS * max(0, len(counted) - 1))
        score += config.EU_DATA_POINTS if eu else 0
    flag, factor = company_flag(name)
    if len(counted) > config.MANY_JOBS_LIMIT and factor == 1 and not flag:
        if config.KEEP_ALL_COMPANIES:
            flag = f"{len(counted)} AI roles open: probably a large company"
        else:
            flag, factor = f"{len(counted)} AI roles open: probably a large company", 0.5
    score = int(round(min(100, score) * factor))

    tier = "HOT" if score >= config.HOT_SCORE else "WARM" if score >= config.WARM_SCORE else "WATCH"
    why = ", ".join(LABELS[t] for t in ORDER if t in best)
    if len(ai_roles) > 1:
        why += f"; {len(ai_roles)} AI roles open"
    if score == 0 and config.AI_ROLES_ONLY:
        tier = "NO AI ROLE"
    if eu:
        why += "; mentions EU data rules"
    if flag:
        why = f"[{flag}] {why}"
        if factor == 0:
            tier = "EXCLUDED"
    return score, tier, why, eu


def score_all():
    with db.connect() as conn:
        companies = db.companies_with_signals(conn)
        for c in companies:
            score, tier, why, eu = score_company(db.signals_for(conn, c["id"]), c["name"])
            db.update_score(conn, c["id"], score, tier, why, eu)
    print(f"Scored {len(companies)} companies.")
