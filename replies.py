"""
Read replies from your Gmail inbox and sort them (GPT).

    python run.py replies

For every email the agent sent, it looks for an answer from that address (and for bounces).
GPT reads each reply and decides:
    meeting          they want to talk / book a call
    replied          they answered (question, "send more info", ...)
    not interested   no / unsubscribe / remove me   -> added to do_not_contact.csv
    out of office    auto-reply                      -> still gets a follow-up later
    wrong person     "ask my colleague X"            -> you check by hand
    bounced          the address does not exist      -> added to do_not_contact.csv
Without GPT, simple word rules are used.

Uses the same Gmail + App Password as sending (IMAP must be on: Gmail settings -> Forwarding and POP/IMAP).
Only READS mail, never deletes or changes anything.
"""

import email
import hashlib
import imaplib
import re
from datetime import datetime, timedelta
from email.header import decode_header, make_header
from email.utils import parseaddr

import config
import database as db
import llm
import sender

OUTCOMES = ["meeting", "replied", "not interested", "out of office", "wrong person", "bounced"]

SYSTEM = """You sort replies to a sales email from Flocta (AI API cost reduction).
Return JSON: {"outcome": "meeting | replied | not interested | out of office | wrong person",
              "summary": "max 15 words, what they said", "next_step": "max 12 words, what we should do"}
meeting = they want a call/demo or propose a time. replied = a real answer that is not a clear yes/no
(question, 'send more info', 'maybe later'). not interested = no, unsubscribe, don't contact.
out of office = automatic absence reply. wrong person = they point to someone else."""

RULES = [
    ("out of office", r"out of (?:the )?office|abwesen|nicht im büro|urlaub|vacation|automatic reply|automatische antwort|autoreply"),
    ("not interested", r"kein interesse|not interested|no interest|unsubscribe|abmelden|remove me|nicht mehr kontaktieren|don'?t contact|bitte keine"),
    ("meeting", r"calendly|cal\.com|termin|meeting|let'?s talk|gerne (?:einen )?call|happy to (?:chat|talk)|book a|demo"),
    ("wrong person", r"falsche[rn]? ansprechp|wrong person|not the right person|wenden sie sich an|reach out to my colleague|zuständig ist"),
]


def _decode(value) -> str:
    try:
        return str(make_header(decode_header(value or "")))
    except Exception:
        return value or ""


def _body(msg) -> str:
    parts = []
    for part in (msg.walk() if msg.is_multipart() else [msg]):
        if part.get_content_type() == "text/plain" and not part.get_filename():
            try:
                parts.append(part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace"))
            except Exception:
                pass
    if not parts:
        for part in (msg.walk() if msg.is_multipart() else [msg]):
            if part.get_content_type() == "text/html":
                try:
                    html = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
                    parts.append(re.sub(r"<[^>]+>", " ", html))
                except Exception:
                    pass
    return "\n".join(parts)


def strip_quote(text: str) -> str:
    """Only the new answer, not our own email quoted below it."""
    cut = re.split(r"\n\s*(?:On .{5,80} wrote:|Am .{5,80} schrieb .{0,80}:|-{2,}\s*Original|Von: |From: |>)", text,
                   maxsplit=1)
    return re.sub(r"\s+\n", "\n", cut[0]).strip()[:3000]


def classify(text: str) -> dict:
    if llm.available():
        data = llm.ask_json("classify_reply", SYSTEM, text[:3000], cheap=True)
        if data and data.get("outcome") in OUTCOMES:
            return {"outcome": data["outcome"], "summary": str(data.get("summary", ""))[:200],
                    "next_step": str(data.get("next_step", ""))[:150], "by": "gpt"}
    for outcome, rx in RULES:
        if re.search(rx, text, re.I):
            return {"outcome": outcome, "summary": text[:120], "next_step": "", "by": "rules"}
    return {"outcome": "replied", "summary": text[:120], "next_step": "read and answer", "by": "rules"}


def fetch_inbox(since: datetime, imap_factory=None):
    """[(from_address, subject, date, body)] for all mails since a date. Read-only."""
    box = (imap_factory or (lambda: imaplib.IMAP4_SSL(config.IMAP_HOST)))()
    box.login(config.EMAIL_ADDRESS, config.EMAIL_APP_PASSWORD)
    try:
        box.select("INBOX", readonly=True)
        typ, data = box.search(None, "SINCE", since.strftime("%d-%b-%Y"))
        out = []
        for num in (data[0].split() if data and data[0] else [])[-500:]:
            typ, msg_data = box.fetch(num, "(BODY.PEEK[])")
            raw = next((p[1] for p in msg_data if isinstance(p, tuple)), None)
            if not raw:
                continue
            msg = email.message_from_bytes(raw)
            out.append((parseaddr(msg.get("From", ""))[1].lower(), _decode(msg.get("Subject")),
                        msg.get("Date", ""), _body(msg)))
        return out
    finally:
        try:
            box.logout()
        except Exception:
            pass


def check(imap_factory=None) -> dict:
    if not (config.EMAIL_ADDRESS and config.EMAIL_APP_PASSWORD):
        print("EMAIL_ADDRESS / EMAIL_APP_PASSWORD missing in .env")
        return {}
    with db.connect() as conn:
        sent = db.sent_messages(conn)
    if not sent:
        print("No sent emails yet.")
        return {}
    first = min(datetime.fromisoformat(m["sent_at"]) for m in sent) - timedelta(days=1)
    try:
        inbox = fetch_inbox(first, imap_factory)
    except Exception as e:
        print(f"Could not read Gmail: {e}\n(Turn on IMAP in Gmail settings, and use the App Password.)")
        return {}

    by_addr = {}
    for m in sent:
        by_addr.setdefault(m["to_email"].lower(), []).append(m)
    found = {}
    for frm, subject, date, body in inbox:
        key = "reply:" + hashlib.sha1(f"{frm}|{date}|{subject}".encode()).hexdigest()
        with db.connect() as conn:
            if db.get_cache(conn, key) is not None:          # already read before -> no GPT cost again
                continue
        if frm in by_addr:                                   # a real reply
            m = by_addr[frm][-1]
            res = classify(f"Subject: {subject}\n\n{strip_quote(body)}")
            text = strip_quote(body)
        elif re.search(r"mailer-daemon|postmaster|mail delivery", frm, re.I):   # a bounce
            hit = next((a for a in by_addr if a in body.lower()), None)
            if not hit:
                continue
            m = by_addr[hit][-1]
            res, text = {"outcome": "bounced", "summary": "address does not exist", "next_step": "", "by": "rules"}, ""
        else:
            continue
        # don't overwrite what you set by hand, and keep the strongest answer
        if m["outcome_by"] == "you" or found.get(m["id"], {}).get("outcome") == "meeting":
            continue
        found[m["id"]] = res
        with db.connect() as conn:
            db.save_cache(conn, key, res["outcome"])
        with db.connect() as conn:
            db.update_message(conn, m["id"], outcome=res["outcome"], outcome_by=res["by"],
                              reply_text=text[:3000],
                              reply_summary=(res["summary"] + (f" → {res['next_step']}" if res["next_step"] else "")),
                              replied_at=db.now())
        if res["outcome"] in ("not interested", "bounced"):
            sender.add_do_not_contact(m["to_email"], res["outcome"])
        print(f"   {m['company']}: {m['to_name']} -> {res['outcome'].upper()}  {res['summary'][:80]}")

    print(f"\n{len(found)} replies/bounces found in {len(inbox)} inbox emails.")
    return found
