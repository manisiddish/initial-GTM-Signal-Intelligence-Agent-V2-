"""
Part 3c: send APPROVED emails through Gmail.

Safety rules (all checked again right before sending):
  - only messages with status 'approved' (you approved them in the dashboard)
  - only emails with a status in config.SEND_TO_STATUSES (default: valid)
  - never twice to the same address
  - never to anyone in do_not_contact.csv (emails or whole domains)
  - max config.MAX_SENDS_PER_DAY per day, with a random pause between emails

Gmail needs an App Password (Google account -> Security -> 2-Step Verification -> App passwords),
not your normal password. Put it in .env as EMAIL_APP_PASSWORD.
"""

import csv
import random
import smtplib
import time
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

import config
import database as db


def do_not_contact() -> set:
    path = config.DO_NOT_CONTACT_PATH
    if not path.exists():
        return set()
    with open(path, newline="", encoding="utf-8") as f:
        return {(r.get("email_or_domain") or "").strip().lower() for r in csv.DictReader(f)} - {""}


def add_do_not_contact(email_or_domain: str, reason: str = ""):
    path = config.DO_NOT_CONTACT_PATH
    new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["email_or_domain", "reason"])
        w.writerow([email_or_domain.strip().lower(), reason])


def blocked_reason(conn, m, blocklist: set):
    email = (m["to_email"] or "").lower()
    if not email or "@" not in email:
        return "no email"
    if email in blocklist or email.split("@")[1] in blocklist:
        return "in do_not_contact.csv"
    if (m["kind"] or "first") == "followup":
        parent = conn.execute("SELECT * FROM messages WHERE id = ?", (m["parent_id"],)).fetchone()
        if not parent or parent["status"] != "sent":
            return "follow-up without a sent first email"
        if parent["outcome"] in STOP_OUTCOMES:
            return f"they already answered ({parent['outcome']}), no follow-up"
        if db.touches(conn, email) >= config.MAX_TOUCHES:
            return f"already {config.MAX_TOUCHES} emails to this address"
    elif db.already_sent_to(conn, email):
        return "already sent to this address"
    status = conn.execute("SELECT email_status FROM contacts WHERE lower(email) = ? ORDER BY id DESC LIMIT 1",
                          (email,)).fetchone()
    if status and status["email_status"] not in config.SEND_TO_STATUSES:
        return f"email status is '{status['email_status']}' (allowed: {', '.join(config.SEND_TO_STATUSES)})"
    if "(Max: add" in config.EMAIL_FOOTER:
        return "email footer is not filled in yet (EMAIL_FOOTER in config.py)"
    return None


def full_body(body: str) -> str:
    """The email exactly as it will be sent: your text + the company footer."""
    return f"{body.rstrip()}\n\n{config.EMAIL_FOOTER}" if config.EMAIL_FOOTER else body


def build(m, parent_message_id: str = None) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = formataddr((config.SENDER_NAME or config.EMAIL_ADDRESS, config.EMAIL_ADDRESS))
    msg["To"] = formataddr((m["to_name"] or "", m["to_email"]))
    msg["Subject"] = m["subject"]
    msg["Message-ID"] = make_msgid(domain=config.EMAIL_ADDRESS.split("@")[-1] or "localhost")
    msg["List-Unsubscribe"] = f"<mailto:{config.EMAIL_ADDRESS}?subject=unsubscribe>"
    if parent_message_id:                       # follow-up: same thread in their inbox
        msg["In-Reply-To"] = parent_message_id
        msg["References"] = parent_message_id
    msg.set_content(full_body(m["body"]))
    return msg


def send(dry_run: bool = False, smtp_factory=None, sleep=time.sleep) -> dict:
    """Send approved messages. Returns {"sent", "skipped", "failed"}."""
    result = {"sent": 0, "skipped": 0, "failed": 0}
    if not dry_run and not (config.EMAIL_ADDRESS and config.EMAIL_APP_PASSWORD):
        print("EMAIL_ADDRESS / EMAIL_APP_PASSWORD missing in .env. Nothing sent.")
        return result

    with db.connect() as conn:
        approved = db.messages(conn, "approved")
        left = config.MAX_SENDS_PER_DAY - db.sent_today(conn)
    if not approved:
        print("No approved emails. Approve drafts first: streamlit run dashboard.py")
        return result
    if left <= 0:
        print(f"Daily limit reached ({config.MAX_SENDS_PER_DAY}). Try again tomorrow.")
        return result

    blocklist = do_not_contact()
    smtp = None
    try:
        for m in approved:
            if result["sent"] >= left:
                print(f"Daily limit reached ({config.MAX_SENDS_PER_DAY}). The rest stays approved for tomorrow.")
                break
            with db.connect() as conn:
                reason = blocked_reason(conn, m, blocklist)
            if reason:
                print(f"   skip {m['to_email']}: {reason}")
                with db.connect() as conn:
                    db.update_message(conn, m["id"], error=reason)
                result["skipped"] += 1
                continue

            parent_mid = None
            if m["parent_id"]:
                with db.connect() as conn:
                    p = conn.execute("SELECT message_id FROM messages WHERE id = ?", (m["parent_id"],)).fetchone()
                    parent_mid = p["message_id"] if p else None
            msg = build(m, parent_mid)
            if dry_run:
                print(f"   [dry run] would send to {m['to_email']}: {m['subject']}")
                result["sent"] += 1
                continue

            try:
                if smtp is None:
                    smtp = (smtp_factory or (lambda: smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30)))()
                    smtp.login(config.EMAIL_ADDRESS, config.EMAIL_APP_PASSWORD)
                if result["sent"]:
                    pause = random.randint(*config.SEND_PAUSE_SECONDS)
                    print(f"   waiting {pause}s ...")
                    sleep(pause)
                smtp.send_message(msg)
            except smtplib.SMTPAuthenticationError:
                print("Gmail login failed. Use a Gmail App Password in EMAIL_APP_PASSWORD (not your normal password).")
                result["failed"] += 1
                break
            except Exception as e:
                print(f"   failed {m['to_email']}: {e}")
                with db.connect() as conn:
                    db.update_message(conn, m["id"], status="failed", error=str(e)[:300])
                result["failed"] += 1
                continue

            with db.connect() as conn:
                db.update_message(conn, m["id"], status="sent", sent_at=db.now(), error=None,
                                  message_id=msg["Message-ID"])
                conn.execute("UPDATE companies SET status = 'contacted' WHERE id = ?", (m["company_id"],))
            result["sent"] += 1
            print(f"   sent to {m['to_name']} <{m['to_email']}> ({m['company']})")
    finally:
        if smtp is not None:
            try:
                smtp.quit()
            except Exception:
                pass

    print(f"\n{result['sent']} {'would be ' if dry_run else ''}sent, {result['skipped']} skipped, {result['failed']} failed.")
    return result


# ------------------------------------------------------------
# Learning: which signals lead to replies? (Max's real question)
# ------------------------------------------------------------

OUTCOMES = ["no reply", "replied", "meeting", "not interested", "out of office", "wrong person", "bounced"]
STOP_OUTCOMES = ("replied", "meeting", "not interested", "wrong person", "bounced")   # no follow-up after these


def results_by_signal(conn):
    """For every signal type: how many contacted companies had it, and how many replied / booked a meeting."""
    rows = conn.execute("""
        SELECT s.type, s.label, m.company_id, MAX(CASE WHEN m.outcome IN ('replied', 'meeting') THEN 1 ELSE 0 END) AS good,
               MAX(CASE WHEN m.outcome = 'meeting' THEN 1 ELSE 0 END) AS meeting
        FROM messages m JOIN signals s ON s.company_id = m.company_id
        WHERE m.status = 'sent'
        GROUP BY s.type, m.company_id""").fetchall()
    stats = {}
    for r in rows:
        st = stats.setdefault(r["type"], {"signal": r["label"], "companies": 0, "replied": 0, "meetings": 0})
        st["companies"] += 1
        st["replied"] += r["good"]
        st["meetings"] += r["meeting"]
    out = []
    for st in stats.values():
        st["reply_rate"] = f"{100 * st['replied'] / st['companies']:.0f}%" if st["companies"] else "-"
        out.append(st)
    return sorted(out, key=lambda s: -s["companies"])


def print_results():
    with db.connect() as conn:
        res = results_by_signal(conn)
        sent = conn.execute("SELECT COUNT(*) FROM messages WHERE status = 'sent'").fetchone()[0]
    if not res:
        print("No sent emails yet. After sending, mark replies in the dashboard; this shows which signals work.")
        return
    print(f"{sent} emails sent. Reply rate per signal:\n")
    print(f"{'Signal':<32}{'Companies':>10}{'Replied':>9}{'Meetings':>10}{'Rate':>7}")
    for s in res:
        print(f"{s['signal']:<32}{s['companies']:>10}{s['replied']:>9}{s['meetings']:>10}{s['reply_rate']:>7}")
