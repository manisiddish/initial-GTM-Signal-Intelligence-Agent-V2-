"""
Part 3a: write a short, personal email draft for each decision maker.

The email is built from FACTS only:
  - the company's own job ad (title + the exact sentence that is the signal)
  - the person's name and role
  - Flocta's facts from config.FLOCTA_FACTS

GPT writes the draft (if OPENAI_API_KEY is set). The code then checks it:
  - no placeholders like [Name] or {company}
  - the right name and company are in it
  - no numbers that are not in the facts (GPT must not invent "40% savings")
  - not too long
If a check fails, or there is no GPT key, a simple template is used instead.

Drafts are never sent from here. You approve them first (dashboard or run.py).
"""

import json
import re

import config
import database as db
import llm

GERMAN = re.compile(r"\b(?:und|der|die|das|wir|mit|für|du|ihr|ihre[nm]?|sie|bei|eine?[nm]?|sind|unser\w*|deine?[nm]?|"
                    r"ist|auf|zu[rm]?|im|dem|den|des|von|über|unter|oder|auch|als|wie|werden|nicht|sowie|"
                    r"kenntnisse|erfahrung|aufgaben|anforderungen|wir bieten)\b", re.I)
ENGLISH = re.compile(r"\b(?:and|the|we|with|for|you|our|your|are|is|to|of|in|on|as|will|what|who|"
                     r"experience|requirements|responsibilities)\b", re.I)
GERMAN_MARKS = re.compile(r"\((?:m|w|d|f)\s*/\s*(?:m|w|d|f)|[äöüß]|-entwickler|stellenangebot|bewerbung", re.I)


def language_of(texts, domain: str = "") -> str:
    """'de' or 'en': the language of the company's own job ads."""
    text = " ".join(texts)[:8000]
    de = len(GERMAN.findall(text)) + 2 * len(GERMAN_MARKS.findall(text))
    en = len(ENGLISH.findall(text))
    if de == en:
        return "de" if (domain or "").endswith((".de", ".at", ".ch")) else "en"
    return "de" if de > en else "en"


def short(text: str, n: int = 110) -> str:
    text = re.sub(r"\s+", " ", (text or "")).strip(" .;:")
    if len(text) <= n:
        return text
    return text[:n].rsplit(" ", 1)[0] + " …"


LEGAL = re.compile(r"\s*(?:gmbh\s*&\s*co\.?\s*kg(?:aa)?|gmbh|ag|se|ug(?: \(haftungsbeschränkt\))?|kg|e\.?\s?v\.?|"
                   r"inc\.?|ltd\.?|llc|b\.?v\.?|s\.?a\.?s?\.?|& co\.? kg)\s*$", re.I)


def short_company(name: str) -> str:
    """'Finanz Informatik GmbH & Co. KG' -> 'Finanz Informatik'"""
    out = name or ""
    for _ in range(2):
        out = LEGAL.sub("", out).strip(" ,")
    return out or name


def clean_job_title(title: str) -> str:
    return re.sub(r"\s*\((?:[mwfdx]\s*/\s*)+[mwfdx]\)|\s*\((?:all genders?|w/m/d|m/w/d|f/m/d|m/f/d)\)", "",
                  title or "", flags=re.I).strip(" -–")


def facts_for(conn, contact) -> dict:
    signals = db.signals_for(conn, contact["company_id"])
    jobs = db.jobs_for(conn, contact["company_id"])
    by_type = {}
    for s in signals:
        by_type.setdefault(s["type"], s)
    ai_job = by_type.get("hires_ai_role") or (signals[0] if signals else None)
    proof = by_type.get("cost_pain") or by_type.get("builds_with_ai") or by_type.get("daily_ai_use")
    return {
        "company": short_company(contact["company"]),
        "first": contact["first_name"] or (contact["name"] or "").split(" ")[0],
        "last": contact["last_name"] or "",
        "name": contact["name"],
        "role": contact["role"],
        "job_title": clean_job_title(ai_job["job_title"]) if ai_job else "",
        "ai_jobs": len({s["job_id"] for s in signals if s["type"] == "hires_ai_role"}),
        "proof_type": proof["type"] if proof else "",
        "proof_label": proof["label"] if proof else "",
        "proof": short(proof["evidence"], 160) if proof else "",
        "builds": (contact["ai_builds"] or "") if "ai_builds" in contact.keys() else "",
        "brief": json.loads(contact["brief"]) if "brief" in contact.keys() and contact["brief"] else {},
        "language": language_of([j["description"] or "" for j in jobs] + [j["title"] or "" for j in jobs],
                                contact["domain"] or ""),
    }


# ------------------------------------------------------------
# Template (no GPT needed)
# ------------------------------------------------------------

def template(f: dict) -> tuple:
    sender = config.SENDER_NAME or "Shashank"
    role = config.SENDER_ROLE or "Flocta"
    job = f"„{f['job_title']}“" if f["language"] == "de" else f"\"{f['job_title']}\""
    if f["language"] == "de":
        subject = f"KI-Kosten bei {f['company']}"
        if f["proof_type"] == "cost_pain":
            hook = f"In der Stellenanzeige steht: „{short(f['proof'])}“. Die Kosten pro Anfrage sind bei euch also schon ein Thema."
        elif f["proof_type"] == "builds_with_ai":
            hook = f"Ihr baut KI direkt ins Produkt („{short(f['proof'])}“). Mit mehr Nutzern wächst die API-Rechnung meist schneller als gedacht."
        else:
            hook = "Mit jedem neuen KI-Feature wächst meist auch die API-Rechnung."
        body = (f"Hallo {f['first']},\n\n"
                f"ich habe gesehen, dass {f['company']} gerade {job} sucht. {hook}\n\n"
                f"Flocta senkt die Kosten für LLM-APIs um bis zu 35 % – mit semantischem Caching, "
                f"Kontext-Kompression und Routing einfacher Anfragen zu günstigeren Modellen. Gehostet in der EU.\n\n"
                f"Hättest du nächste Woche 15 Minuten, um kurz zu schauen, ob das bei euch passt?\n\n"
                f"Viele Grüße\n{sender}\n{role}\n\n"
                f"P.S. Kein Interesse? Eine kurze Antwort genügt, dann melde ich mich nicht mehr.")
    else:
        subject = f"AI costs at {f['company']}"
        if f["proof_type"] == "cost_pain":
            hook = f"Your job ad says: \"{short(f['proof'])}\", so cost per request is already on your radar."
        elif f["proof_type"] == "builds_with_ai":
            hook = f"You're building AI into the product (\"{short(f['proof'])}\"). With more users, the API bill usually grows faster than expected."
        else:
            hook = "With every new AI feature, the API bill usually grows too."
        body = (f"Hi {f['first']},\n\n"
                f"I saw that {f['company']} is hiring for {job}. {hook}\n\n"
                f"Flocta cuts LLM API costs by up to 35% with semantic caching, context compression and routing "
                f"simple requests to cheaper models. Hosted in the EU.\n\n"
                f"Would you have 15 minutes next week to see if it fits your setup?\n\n"
                f"Best,\n{sender}\n{role}\n\n"
                f"P.S. Not interested? A short reply is enough and I won't follow up.")
    return subject, body


# ------------------------------------------------------------
# GPT
# ------------------------------------------------------------

SYSTEM = """You write short first emails for Flocta to a decision maker at a company.
Return JSON: {"subject": "...", "body": "...", "linkedin": "..."}

Rules:
- Language: {lang}. German: use "du" (startup tone), no "Sehr geehrte".
- Max 110 words in the body. Plain text. No links, no emojis, no bullet points.
- Start with "Hallo {first}," (German) or "Hi {first}," (English).
- First sentence: the specific thing from THEIR job ad (quote it short). This shows we did research.
- Then ONE sentence on why that matters for them (AI API costs grow with usage). If "What they build"
  is given, connect it to that (e.g. their agents / chatbot / document AI), in their words.
- Then what Flocta does, using ONLY the Flocta facts given. Never invent numbers, customers or results.
- End with a soft question for a 15-minute call.
- Sign with the sender name and role exactly as given.
- Last line: a short P.S. that they can just reply if they are not interested, and we won't follow up.
- Subject: max 6 words, specific to them, no clickbait, no "Re:".
- Never use placeholders like [Name].
- "linkedin": a LinkedIn connection note in the same language, max 280 characters, friendly, mentions
  their job ad in a few words, no pitch-heavy wording, no links, ends with the sender's first name."""

REVIEW_SYSTEM = """You review a cold email from Flocta before a human sends it. Be strict.
Score 1-10: would a busy CTO / founder read it and maybe reply? Check: specific to them (not generic),
short, clear value, no hype, natural tone, correct language, soft ask.
Return JSON: {"score": <1-10>, "problems": ["max 3 short points"], "subject": "...", "body": "..."}
"subject" and "body" = your improved version (same language, same facts, same greeting and signature,
same P.S.). Use ONLY facts from the original email and the facts given. Never add numbers or claims."""


def template_linkedin(f: dict) -> str:
    sender = (config.SENDER_NAME or "Shashank").split(" ")[0]
    if f["language"] == "de":
        txt = (f"Hallo {f['first']}, ich habe eure Stelle „{f['job_title']}“ gesehen. Ich arbeite an Flocta: "
               f"wir senken LLM-API-Kosten (EU-gehostet). Würde mich freuen, uns zu vernetzen! {sender}")
    else:
        txt = (f"Hi {f['first']}, saw your \"{f['job_title']}\" opening. I work on Flocta: we cut LLM API costs "
               f"(EU-hosted). Would be great to connect! {sender}")
    return txt[:300]


def _numbers(text: str) -> set:
    return set(re.findall(r"\d+(?:[.,]\d+)?", text or ""))


def check(subject: str, body: str, f: dict, facts_text: str) -> list:
    problems = []
    whole = f"{subject}\n{body}"
    if re.search(r"\[[^\]]*\]|\{[^}]*\}|<[^>]+>|TODO|XXX|lorem", whole, re.I):
        problems.append("placeholder")
    if f["first"] and f["first"] not in body:
        problems.append("first name missing")
    if f["company"].split()[0].lower() not in whole.lower():
        problems.append("company name missing")
    invented = _numbers(whole) - _numbers(facts_text) - {"15"}
    if invented:
        problems.append(f"invented numbers {sorted(invented)}")
    if len(body.split()) > 170 or len(subject) > 80:
        problems.append("too long")
    if re.search(r"https?://|www\.", body):
        problems.append("link")
    return problems


def facts_text(f: dict) -> str:
    sender = config.SENDER_NAME or "Shashank"
    b = f.get("brief") or {}
    return (f"Company: {f['company']}\nPerson: {f['name']}, {f['role']}\n"
            f"Their AI job ad: {f['job_title']} ({f['ai_jobs']} AI role(s) open)\n"
            f"Signal from the job ad ({f['proof_label']}): \"{f['proof']}\"\n"
            + (f"What they build with AI (our research): {f['builds']}\n" if f.get("builds") and f["builds"] != "unclear" else "")
            + (f"Best angle (account brief): {b.get('angle', '')}\nWhere Flocta helps them: {b.get('pain', '')}\n"
               if b else "")
            + "\n"
            f"Flocta facts:\n- " + "\n- ".join(config.FLOCTA_FACTS) +
            f"\n\nSender: {sender}, {config.SENDER_ROLE}")


def check_linkedin(text: str, f: dict, facts: str) -> bool:
    return (bool(text) and len(text) <= 300 and not re.search(r"https?://|www\.|\[[^\]]*\]|\{[^}]*\}", text)
            and not (_numbers(text) - _numbers(facts)))


def review(subject: str, body: str, f: dict, facts: str):
    """Second GPT pass: score 1-10, and a better version if the score is low. Returns (subject, body, score, notes)."""
    if not (config.USE_GPT_REVIEW and llm.available()):
        return subject, body, None, ""
    data = llm.ask_json("review_email", REVIEW_SYSTEM,
                        f"FACTS:\n{facts}\n\nEMAIL:\nSubject: {subject}\n\n{body}", cheap=False)
    if not data:
        return subject, body, None, ""
    try:
        score = max(1, min(10, int(data.get("score"))))
    except (TypeError, ValueError):
        return subject, body, None, ""
    notes = "; ".join(str(p) for p in (data.get("problems") or [])[:3])[:300]
    if score < config.REVIEW_MIN_SCORE:
        s2, b2 = str(data.get("subject", "")).strip(), str(data.get("body", "")).strip()
        if s2 and b2 and not check(s2, b2, f, facts + "\n" + body):
            return s2, b2, score, f"improved by reviewer (was {score}/10): {notes}"
    return subject, body, score, notes


def gpt_draft(f: dict):
    if not llm.available():
        return None
    facts = facts_text(f)
    system = (SYSTEM.replace("{lang}", "German" if f["language"] == "de" else "English")
              .replace("{first}", f["first"]))
    for _ in range(2):          # one retry if the check fails
        data = llm.ask_json("write_email", system, facts, cheap=False)
        if not data:
            return None
        subject, body = str(data.get("subject", "")).strip(), str(data.get("body", "")).strip()
        linkedin = str(data.get("linkedin", "")).strip()
        problems = check(subject, body, f, facts)
        if not problems:
            if not check_linkedin(linkedin, f, facts):
                linkedin = template_linkedin(f)
            return subject, body, linkedin
        print(f"   GPT draft rejected ({', '.join(problems)}), trying again")
    return None


# ------------------------------------------------------------

def write(limit=None, redo=False, company=None):
    limit = limit or config.WRITE_LIMIT
    with db.connect() as conn:
        todo = db.contacts_to_write(conn, config.WRITE_TO_STATUSES, limit, redo=redo, company=company)
        facts = [(c, facts_for(conn, c)) for c in todo]
    if not todo:
        print("No new contacts to write to. (Run 'python run.py people' first, or use --redo.)")
        return 0

    print(f"Writing {len(todo)} drafts (GPT: {'on' if llm.available() else 'off, using the template'})\n")
    for c, f in facts:
        got = gpt_draft(f)
        if got:
            subject, body, linkedin = got
        else:
            (subject, body), linkedin = template(f), template_linkedin(f)
        subject, body, score, notes = review(subject, body, f, facts_text(f))
        with db.connect() as conn:
            db.save_draft(conn, c, subject, body, f["language"], "gpt" if got else "template",
                          linkedin=linkedin, quality=score, review_notes=notes)
        q = f", review {score}/10" + (" → improved" if notes.startswith("improved") else "") if score else ""
        print(f"   {c['company']}: {c['name']} <{c['email']}>  [{'GPT' if got else 'template'}, {f['language']}{q}]")
    print(f"\n{len(todo)} drafts ready. Review them: streamlit run dashboard.py")
    print(llm.usage_summary())
    export()
    return len(todo)


def export():
    """output/drafts.md: all drafts to read (or send to Max)."""
    with db.connect() as conn:
        rows = db.messages(conn)
    lines = ["# Email drafts", ""]
    for m in rows:
        lines += [f"## {m['company']}: {m['to_name']} ({m['to_role']})",
                  f"To: {m['to_email']} · status: **{m['status']}** · written by: {m['written_by']}", "",
                  f"**Subject:** {m['subject']}", "", "```", m["body"] or "", "```", ""]
        if m["quality"]:
            lines += [f"Review: {m['quality']}/10. {m['review_notes'] or ''}", ""]
        if m["linkedin"]:
            lines += ["**LinkedIn note:**", "", "```", m["linkedin"], "```", ""]
    (config.OUTPUT_DIR / "drafts.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"Drafts: {config.OUTPUT_DIR / 'drafts.md'}")
