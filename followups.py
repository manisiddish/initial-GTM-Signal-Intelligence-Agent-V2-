"""
Follow-up drafts (GPT): one short second email when there was no answer.

    python run.py followup

For every first email that was sent at least config.FOLLOWUP_AFTER_DAYS days ago and got
no reply (or only an out-of-office), a follow-up DRAFT is written. You approve it in the
dashboard like any other email. It is sent in the same email thread ("Re: ...").
Never more than config.MAX_TOUCHES emails per person. Run 'python run.py replies' first,
so people who answered don't get a follow-up.
"""

from datetime import datetime, timedelta

import config
import database as db
import llm
import sender
import writer

SYSTEM = """You write a short follow-up to a first sales email from Flocta that got no answer.
Return JSON: {"body": "..."}
Rules:
- Language: {lang}. German: "du". Max 60 words. Plain text, no links, no emojis.
- Start with "Hallo {first}," (German) or "Hi {first}," (English).
- Don't repeat the first email. Add ONE new, useful angle that fits what they build
  (e.g. repeated prompts can be cached, simple requests can go to cheaper models, EU hosting).
- Use only the Flocta facts given. Never invent numbers, customers or results.
- End with an easy yes/no question. Sign with the sender name exactly as given.
- Last line: P.S. if it's not relevant, a one-word reply is enough and we won't write again."""


def _template(f: dict) -> str:
    sender_name = config.SENDER_NAME or "Shashank"
    if f["language"] == "de":
        return (f"Hallo {f['first']},\n\nkurz nachgehakt: Oft wiederholen sich viele Anfragen an das LLM. "
                f"Flocta beantwortet solche Anfragen aus dem Cache und schickt einfache an günstigere Modelle, "
                f"ohne dass ihr euren Code groß ändern müsst.\n\nWäre ein kurzer Call dazu interessant?\n\n"
                f"Viele Grüße\n{sender_name}\n\nP.S. Nicht relevant? Ein kurzes „nein“ genügt, dann melde ich mich nicht mehr.")
    return (f"Hi {f['first']},\n\nquick follow-up: many LLM requests are repeats or simple ones. Flocta answers "
            f"repeats from cache and sends simple requests to cheaper models.\n\nWould a short call be useful?\n\n"
            f"Best,\n{sender_name}\n\nP.S. Not relevant? A one-word reply is enough and I won't write again.")


def candidates(conn):
    cutoff = (datetime.now() - timedelta(days=config.FOLLOWUP_AFTER_DAYS)).isoformat(timespec="seconds")
    return conn.execute("""
        SELECT m.*, c.name AS company, c.ai_builds, c.domain
        FROM messages m JOIN companies c ON c.id = m.company_id
        WHERE m.status = 'sent' AND COALESCE(m.kind, 'first') = 'first' AND m.sent_at <= ?
          AND COALESCE(m.outcome, 'no reply') IN ('no reply', 'out of office')
          AND NOT EXISTS (SELECT 1 FROM messages f WHERE f.parent_id = m.id)
        ORDER BY m.sent_at""", (cutoff,)).fetchall()


def write(limit: int = 50) -> int:
    blocked = sender.do_not_contact()
    with db.connect() as conn:
        todo = [m for m in candidates(conn)
                if db.touches(conn, m["to_email"]) < config.MAX_TOUCHES
                and m["to_email"].lower() not in blocked and m["to_email"].split("@")[1].lower() not in blocked][:limit]
    if not todo:
        print(f"No follow-ups due (first emails need {config.FOLLOWUP_AFTER_DAYS}+ days without a reply). "
              f"Run 'python run.py replies' first to catch answers.")
        return 0

    n = 0
    for m in todo:
        first = (m["to_name"] or "").split(" ")[0] or "there"
        f = {"first": first, "company": writer.short_company(m["company"]), "language": m["language"] or "en"}
        body, by = None, "template"
        if llm.available():
            facts = (f"First email we sent (subject: {m['subject']}):\n{m['body']}\n\n"
                     f"What they build with AI: {m['ai_builds'] or 'unclear'}\n\nFlocta facts:\n- "
                     + "\n- ".join(config.FLOCTA_FACTS) + f"\n\nSender: {config.SENDER_NAME or 'Shashank'}")
            system = SYSTEM.replace("{lang}", "German" if f["language"] == "de" else "English").replace("{first}", first)
            data = llm.ask_json("write_followup", system, facts, cheap=False)
            cand = str((data or {}).get("body", "")).strip()
            problems = [x for x in writer.check("Re", cand, f, facts) if x != "company name missing"]
            if cand and not problems:
                body, by = cand, "gpt"
        body = body or _template(f)
        subject = m["subject"] if m["subject"].lower().startswith("re:") else f"Re: {m['subject']}"
        with db.connect() as conn:
            conn.execute(
                """INSERT INTO messages (company_id, contact_id, to_email, to_name, to_role, subject, body, language,
                       written_by, status, kind, parent_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', 'followup', ?, ?, ?)""",
                (m["company_id"], m["contact_id"], m["to_email"], m["to_name"], m["to_role"], subject, body,
                 m["language"], by, m["id"], db.now(), db.now()))
        n += 1
        print(f"   follow-up for {m['company']}: {m['to_name']} <{m['to_email']}>  [{by}]")
    print(f"\n{n} follow-up drafts. Approve them in the dashboard.")
    return n
