"""
The database: one SQLite file (data/leads.db) shared by all steps.

Tables
  companies  one row per company, with its score
  jobs       every collected job ad
  signals    every AI clue, with the exact sentence as proof
  contacts   decision makers and their emails       (Part 2)
  email_checks / hunter_cache   results already paid for, never paid twice
  messages   drafted / approved / sent emails        (Part 3)
  llm_calls  every GPT call and its tokens (cost)    (Part 2 + 3)
"""

import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL UNIQUE,
    website TEXT,
    ats TEXT,
    ats_slug TEXT,
    score INTEGER DEFAULT 0,
    tier TEXT DEFAULT 'WATCH',
    why TEXT,
    eu_data INTEGER DEFAULT 0,
    status TEXT DEFAULT 'new',
    first_seen TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id),
    title TEXT,
    url TEXT NOT NULL UNIQUE,
    posted_date TEXT,
    source TEXT,
    description TEXT,
    collected_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id),
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    type TEXT NOT NULL,
    label TEXT,
    points INTEGER,
    evidence TEXT NOT NULL,
    url TEXT,
    found_at TEXT NOT NULL,
    UNIQUE(job_id, type)
);

CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id),
    name TEXT,
    role TEXT,
    is_decision_maker INTEGER DEFAULT 0,
    email TEXT,
    email_status TEXT,
    source TEXT,
    found_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id),
    contact_id INTEGER REFERENCES contacts(id),
    subject TEXT,
    body TEXT,
    status TEXT DEFAULT 'draft',
    created_at TEXT NOT NULL,
    sent_at TEXT,
    outcome TEXT
);

CREATE TABLE IF NOT EXISTS seen_urls (
    url TEXT PRIMARY KEY,
    seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS email_checks (
    email TEXT PRIMARY KEY,
    status TEXT,
    score INTEGER,
    checked_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hunter_cache (
    key TEXT PRIMARY KEY,
    data TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    purpose TEXT,
    model TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    created_at TEXT NOT NULL
);
"""

LEGAL_FORMS = r"\b(gmbh|ag|se|ug|kg|kgaa|ohg|gbr|mbh|co|e\.?\s?v|inc|ltd|llc|bv|haftungsbeschränkt|& co)\b"


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def name_key(name: str) -> str:
    """'Beispiel Logistik GmbH & Co. KG' -> 'beispiel logistik' (to merge duplicates)."""
    key = name.lower().replace("˚", "grad").replace("°", "grad")
    key = re.sub(LEGAL_FORMS, " ", key)
    key = re.sub(r"[^a-z0-9äöüß ]", " ", key)
    return re.sub(r"\s+", " ", key).strip()


@contextmanager
def connect():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# Columns added in Part 2 (added to old databases automatically)
NEW_COLUMNS = {
    "companies": [("domain", "TEXT"), ("website_source", "TEXT"), ("email_pattern", "TEXT"),
                  ("generic_email", "TEXT"), ("people_checked_at", "TEXT"),
                  ("ai_builds", "TEXT"), ("ai_use", "TEXT"), ("ai_spend", "TEXT"), ("ai_fit", "TEXT"),
                  ("ai_reason", "TEXT"), ("ai_evidence", "TEXT"), ("analyzed_at", "TEXT"),
                  ("company_size", "TEXT"), ("size_evidence", "TEXT"), ("web_reason", "TEXT"),
                  ("refined_at", "TEXT"), ("brief", "TEXT"), ("brief_at", "TEXT")],
    "messages": [("to_email", "TEXT"), ("to_name", "TEXT"), ("to_role", "TEXT"), ("language", "TEXT"),
                 ("written_by", "TEXT"), ("error", "TEXT"), ("updated_at", "TEXT"),
                 ("kind", "TEXT DEFAULT 'first'"), ("parent_id", "INTEGER"), ("message_id", "TEXT"),
                 ("reply_text", "TEXT"), ("reply_summary", "TEXT"), ("replied_at", "TEXT"),
                 ("outcome_by", "TEXT"), ("linkedin", "TEXT"), ("quality", "INTEGER"),
                 ("review_notes", "TEXT")],
    "contacts": [("first_name", "TEXT"), ("last_name", "TEXT"), ("rank", "INTEGER"),
                 ("confidence", "INTEGER"), ("email_source", "TEXT"), ("proof", "TEXT"),
                 ("proof_url", "TEXT"), ("linkedin_search", "TEXT"), ("why_person", "TEXT")],
}


def init():
    with connect() as conn:
        conn.executescript(SCHEMA)
        for table, cols in NEW_COLUMNS.items():
            have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            for col, typ in cols:
                if col not in have:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")


# ------------------------------------------------------------
# Companies
# ------------------------------------------------------------

def get_or_create_company(conn, name: str) -> int:
    key = name_key(name)
    row = conn.execute("SELECT id FROM companies WHERE name_key = ?", (key,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO companies (name, name_key, first_seen, updated_at) VALUES (?, ?, ?, ?)",
        (name.strip(), key, now(), now()),
    )
    return cur.lastrowid


def set_company_ats(conn, company_id: int, ats: str, slug: str):
    conn.execute(
        "UPDATE companies SET ats = COALESCE(ats, ?), ats_slug = COALESCE(ats_slug, ?) WHERE id = ?",
        (ats, slug, company_id),
    )


def update_score(conn, company_id: int, score: int, tier: str, why: str, eu_data: bool):
    conn.execute(
        "UPDATE companies SET score = ?, tier = ?, why = ?, eu_data = ?, updated_at = ? WHERE id = ?",
        (score, tier, why, int(eu_data), now(), company_id),
    )


# ------------------------------------------------------------
# Jobs + signals
# ------------------------------------------------------------

def job_exists(conn, url: str) -> bool:
    return conn.execute("SELECT 1 FROM jobs WHERE url = ?", (url,)).fetchone() is not None


def known_urls(conn) -> set:
    """Every job URL already handled (with or without AI), so it's never downloaded twice."""
    return {r[0] for r in conn.execute("SELECT url FROM seen_urls")}


def mark_seen(conn, url: str):
    conn.execute("INSERT OR IGNORE INTO seen_urls (url, seen_at) VALUES (?, ?)", (url, now()))


def add_job(conn, company_id: int, job: dict) -> int:
    cur = conn.execute(
        """INSERT OR IGNORE INTO jobs (company_id, title, url, posted_date, source, description, collected_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (company_id, job.get("title", ""), job["url"], job.get("date", ""),
         job.get("source", ""), job.get("description", "")[:20000], now()),
    )
    if cur.lastrowid and cur.rowcount:
        return cur.lastrowid
    return conn.execute("SELECT id FROM jobs WHERE url = ?", (job["url"],)).fetchone()["id"]


def add_signal(conn, company_id: int, job_id: int, signal: dict, url: str):
    conn.execute(
        """INSERT OR IGNORE INTO signals (company_id, job_id, type, label, points, evidence, url, found_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (company_id, job_id, signal["type"], signal["label"], signal["points"],
         signal["evidence"], url, now()),
    )


# ------------------------------------------------------------
# Contacts (Part 2)
# ------------------------------------------------------------

def set_company_website(conn, company_id: int, domain: str, source: str):
    conn.execute(
        "UPDATE companies SET domain = ?, website = ?, website_source = ?, updated_at = ? WHERE id = ?",
        (domain, f"https://{domain}" if domain else None, source, now(), company_id),
    )


def set_company_people_result(conn, company_id: int, pattern: str, generic_email: str, status: str):
    conn.execute(
        """UPDATE companies SET email_pattern = ?, generic_email = ?, status = ?,
           people_checked_at = ?, updated_at = ? WHERE id = ?""",
        (pattern, generic_email, status, now(), now(), company_id),
    )


def replace_contacts(conn, company_id: int, contacts: list):
    # messages keep their own copy of name + email, so old contacts can be replaced safely
    conn.execute("UPDATE messages SET contact_id = NULL WHERE contact_id IN "
                 "(SELECT id FROM contacts WHERE company_id = ?)", (company_id,))
    conn.execute("DELETE FROM contacts WHERE company_id = ?", (company_id,))
    for c in contacts:
        conn.execute(
            """INSERT INTO contacts (company_id, name, first_name, last_name, role, is_decision_maker,
                   rank, email, email_status, confidence, email_source, source, proof, proof_url,
                   linkedin_search, why_person, found_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (company_id, c.get("name"), c.get("first"), c.get("last"), c.get("role"),
             int(c.get("decision_maker", False)), c.get("rank"), c.get("email"), c.get("email_status"),
             c.get("confidence"), c.get("email_source"), c.get("source"), c.get("proof"),
             c.get("proof_url"), c.get("linkedin_search"), c.get("why_person"), now()),
        )


def contacts_for(conn, company_id: int):
    return conn.execute(
        "SELECT * FROM contacts WHERE company_id = ? ORDER BY rank, confidence DESC", (company_id,)
    ).fetchall()


def jobs_for(conn, company_id: int):
    return conn.execute("SELECT * FROM jobs WHERE company_id = ?", (company_id,)).fetchall()


def companies_for_people(conn, tiers, limit: int, redo: bool = False, name: str = None):
    if name:
        return conn.execute("SELECT * FROM companies WHERE name_key = ? OR name LIKE ?",
                            (name_key(name), f"%{name}%")).fetchall()
    marks = ",".join("?" * len(tiers))
    done = "" if redo else "AND people_checked_at IS NULL"
    skip_no = "AND COALESCE(ai_fit, '') != 'no'" if config.PEOPLE_SKIP_NO_FIT else ""
    return conn.execute(
        f"""SELECT * FROM companies WHERE tier IN ({marks}) {done} {skip_no}
            ORDER BY {FIT_ORDER}, score DESC, name LIMIT ?""",
        (*tiers, limit),
    ).fetchall()


# GPT fit first (yes, maybe, not analyzed, no), then score
FIT_ORDER = "CASE ai_fit WHEN 'yes' THEN 0 WHEN 'maybe' THEN 1 WHEN 'no' THEN 3 ELSE 2 END"


def companies_to_analyze(conn, tiers, limit: int, redo: bool = False, name: str = None):
    if name:
        return conn.execute("SELECT * FROM companies WHERE name_key = ? OR name LIKE ?",
                            (name_key(name), f"%{name}%")).fetchall()
    marks = ",".join("?" * len(tiers))
    done = "" if redo else "AND analyzed_at IS NULL"
    return conn.execute(
        f"SELECT * FROM companies WHERE tier IN ({marks}) {done} ORDER BY score DESC, name LIMIT ?",
        (*tiers, limit),
    ).fetchall()


def save_brief(conn, company_id: int, brief_json: str):
    conn.execute("UPDATE companies SET brief = ?, brief_at = ? WHERE id = ?", (brief_json, now(), company_id))


def save_refine(conn, company_id: int, r: dict):
    conn.execute(
        """UPDATE companies SET company_size = ?, size_evidence = ?, ai_fit = ?, web_reason = ?,
           refined_at = ? WHERE id = ?""",
        (r["size"], r["size_quote"], r["fit"], r["fit_reason"], now(), company_id),
    )


def save_analysis(conn, company_id: int, a: dict):
    conn.execute(
        """UPDATE companies SET ai_builds = ?, ai_use = ?, ai_spend = ?, ai_fit = ?, ai_reason = ?,
           ai_evidence = ?, analyzed_at = ? WHERE id = ?""",
        (a["ai_builds"], a["ai_use"], a["ai_spend"], a["ai_fit"], a["ai_reason"], a["ai_evidence"],
         now(), company_id),
    )


def get_email_check(conn, email: str):
    return conn.execute("SELECT * FROM email_checks WHERE email = ?", (email,)).fetchone()


def save_email_check(conn, email: str, status: str, score: int):
    conn.execute("INSERT OR REPLACE INTO email_checks (email, status, score, checked_at) VALUES (?, ?, ?, ?)",
                 (email, status, score, now()))


def get_cache(conn, key: str):
    row = conn.execute("SELECT data FROM hunter_cache WHERE key = ?", (key,)).fetchone()
    return row["data"] if row else None


def save_cache(conn, key: str, data: str):
    conn.execute("INSERT OR REPLACE INTO hunter_cache (key, data, created_at) VALUES (?, ?, ?)",
                 (key, data, now()))


def log_llm_call(conn, purpose: str, model: str, input_tokens: int, output_tokens: int):
    conn.execute(
        "INSERT INTO llm_calls (purpose, model, input_tokens, output_tokens, created_at) VALUES (?, ?, ?, ?, ?)",
        (purpose, model, input_tokens, output_tokens, now()),
    )


# ------------------------------------------------------------
# Messages (Part 3)
# ------------------------------------------------------------

def contacts_to_write(conn, statuses, limit: int, redo: bool = False, company: str = None):
    """Decision makers with a usable email that have no message yet."""
    marks = ",".join("?" * len(statuses))
    sql = f"""SELECT ct.*, c.name AS company, c.score, c.tier, c.why, c.domain, c.ai_builds, c.ai_fit, c.brief
              FROM contacts ct JOIN companies c ON c.id = ct.company_id
              WHERE ct.is_decision_maker = 1 AND ct.email IS NOT NULL AND ct.email != ''
                AND ct.email_status IN ({marks})"""
    args = list(statuses)
    if not redo:
        sql += " AND lower(ct.email) NOT IN (SELECT lower(to_email) FROM messages WHERE to_email IS NOT NULL)"
    else:
        sql += (" AND lower(ct.email) NOT IN (SELECT lower(to_email) FROM messages "
                "WHERE status IN ('approved', 'sent') AND to_email IS NOT NULL)")
    if company:
        sql += " AND (c.name_key = ? OR c.name LIKE ?)"
        args += [name_key(company), f"%{company}%"]
    if config.PEOPLE_SKIP_NO_FIT:
        sql += " AND COALESCE(c.ai_fit, '') != 'no'"
    sql += " ORDER BY " + FIT_ORDER.replace("ai_fit", "c.ai_fit") + ", c.score DESC, ct.rank, ct.confidence DESC LIMIT ?"
    return conn.execute(sql, (*args, limit)).fetchall()


def save_draft(conn, contact, subject: str, body: str, language: str, written_by: str,
               linkedin: str = None, quality: int = None, review_notes: str = None) -> int:
    conn.execute("DELETE FROM messages WHERE lower(to_email) = lower(?) AND status IN ('draft', 'skipped')",
                 (contact["email"],))
    cur = conn.execute(
        """INSERT INTO messages (company_id, contact_id, to_email, to_name, to_role, subject, body, language,
               written_by, linkedin, quality, review_notes, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?)""",
        (contact["company_id"], contact["id"], contact["email"], contact["name"], contact["role"],
         subject, body, language, written_by, linkedin, quality, review_notes, now(), now()),
    )
    return cur.lastrowid


def messages(conn, status: str = None):
    sql = """SELECT m.*, c.name AS company, c.score, c.tier, c.why, c.website, c.ai_builds, c.ai_fit,
                    c.ai_spend, c.ai_reason, c.ai_evidence
             FROM messages m JOIN companies c ON c.id = m.company_id"""
    if status:
        return conn.execute(sql + " WHERE m.status = ? ORDER BY c.score DESC, m.id", (status,)).fetchall()
    return conn.execute(sql + " ORDER BY c.score DESC, m.id").fetchall()


def update_message(conn, msg_row_id: int, **fields):
    fields["updated_at"] = now()
    cols = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE messages SET {cols} WHERE id = ?", (*fields.values(), msg_row_id))


def sent_messages(conn):
    return conn.execute("""SELECT m.*, c.name AS company FROM messages m JOIN companies c ON c.id = m.company_id
                           WHERE m.status = 'sent' ORDER BY m.sent_at""").fetchall()


def touches(conn, email: str) -> int:
    return conn.execute("SELECT COUNT(*) FROM messages WHERE status = 'sent' AND lower(to_email) = lower(?)",
                        (email,)).fetchone()[0]


def sent_today(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM messages WHERE status = 'sent' AND date(sent_at) = date('now', 'localtime')"
                        ).fetchone()[0]


def already_sent_to(conn, email: str) -> bool:
    return conn.execute("SELECT 1 FROM messages WHERE status = 'sent' AND lower(to_email) = lower(?)",
                        (email,)).fetchone() is not None


# ------------------------------------------------------------
# Reading (for scorer, report, dashboard)
# ------------------------------------------------------------

def companies_with_signals(conn):
    return conn.execute(
        "SELECT * FROM companies WHERE id IN (SELECT DISTINCT company_id FROM signals)"
    ).fetchall()


def signals_for(conn, company_id: int):
    return conn.execute(
        """SELECT s.*, j.title AS job_title, j.posted_date, j.description
           FROM signals s JOIN jobs j ON j.id = s.job_id
           WHERE s.company_id = ? ORDER BY s.points DESC, j.posted_date DESC""",
        (company_id,),
    ).fetchall()


def top_companies(conn, limit: int = 50):
    return conn.execute(
        "SELECT * FROM companies WHERE score > 0 ORDER BY score DESC, name LIMIT ?", (limit,)
    ).fetchall()


def counts(conn) -> dict:
    q = lambda sql: conn.execute(sql).fetchone()[0]
    return {
        "jobs": q("SELECT COUNT(*) FROM jobs"),
        "companies": q("SELECT COUNT(*) FROM companies"),
        "with_signals": q("SELECT COUNT(DISTINCT company_id) FROM signals"),
        "hot": q("SELECT COUNT(*) FROM companies WHERE tier = 'HOT'"),
        "warm": q("SELECT COUNT(*) FROM companies WHERE tier = 'WARM'"),
        "contacts": q("SELECT COUNT(*) FROM contacts"),
        "valid_emails": q("SELECT COUNT(*) FROM contacts WHERE email_status = 'valid'"),
        "fit_yes": q("SELECT COUNT(*) FROM companies WHERE ai_fit = 'yes'"),
        "analyzed": q("SELECT COUNT(*) FROM companies WHERE analyzed_at IS NOT NULL"),
    }
