"""
GPT analysis of the best companies (after scoring).

The rules in signals.py find WORDS ("LLM", "RAG"). GPT reads the job ads and
understands the MEANING. For each hot / warm company it answers:

  1. What do they build with AI?                  (one line)
  2. How do they use AI?                          product / internal / consulting / research / unclear
  3. How big is their AI API spend probably?      high / medium / low   + why
  4. Is it a good fit for Flocta?                 yes / maybe / no      + why
  5. Proof: an exact quote from their job ad (checked by the code: must really be in the ad)

Cost control:
  - only hot / warm companies (config.ANALYZE_TIERS), max config.ANALYZE_LIMIT per run
  - only the useful sentences of each ad are sent (not the whole text)
  - every company is analyzed only once (use --redo to analyze again)
"""

import re

import config
import database as db
import llm
from signals import clean_text, sentences

AI_WORDS = re.compile(
    r"\b(?:ai|ki|llms?|gpt|genai|openai|anthropic|claude|gemini|mistral|rag|agent\w*|chatbot|assistant|"
    r"machine learning|ml|nlp|embedding\w*|vector|inference|prompt\w*|model\w*|token\w*|kosten|cost\w*|"
    r"api|copilot|chatgpt|automation|automatisier\w*|künstliche intelligenz|sprachmodell\w*|"
    r"customers?|kunden|users?|nutzer|product|produkt|platform|plattform|scale|skalier\w*|million\w*|"
    r"we are|wir sind|our mission|unsere mission|about us|über uns|employees|mitarbeiter\w*)\b",
    re.IGNORECASE)

SYSTEM = """You analyse companies for Flocta, a Berlin startup.

Flocta's product: it sits between a company's software and LLM APIs (OpenAI, Anthropic, ...) and cuts
the API bill by up to 35% with semantic caching, context compression and routing simple requests to
cheaper models. EU-hosted.

A GREAT Flocta customer:
- runs LLM API calls IN PRODUCTION, at volume: AI features in their product (agents, chatbots, RAG,
  document processing, copilots) or heavy internal automation through APIs
- is a small or mid-sized company (about 10-500 people), in Europe
NOT a fit (or weak):
- only staff using ChatGPT / Copilot seats (no API bill)
- AI labs training their own models, or companies running only self-hosted open models
- very large enterprises; public sector; IT consultancies building AI for clients (maybe as partners)
- AI only mentioned as a buzzword

You get the company's job ads (only the relevant sentences). Answer ONLY from this text.
Return JSON:
{
  "builds": "one short sentence: what they build or do with AI (or 'unclear')",
  "ai_use": "product | internal | consulting | research | unclear",
  "spend": "high | medium | low",
  "spend_reason": "max 15 words",
  "fit": "yes | maybe | no",
  "fit_reason": "max 20 words",
  "quote": "an EXACT short quote (max 25 words) copied from the text that best supports your answer"
}
Rules: never invent facts, numbers or customers. If the text does not say it, say 'unclear'.
Write in English."""


def _job_text(jobs, max_chars=6000) -> str:
    """Only the useful sentences: AI, product, customers, company intro. Saves tokens."""
    jobs = sorted(jobs, key=lambda j: -len(j["description"] or ""))[:5]
    parts = []
    per_job = max(800, max_chars // max(1, len(jobs)))
    for j in jobs:
        text = clean_text(j["description"] or "")
        keep, used = [], 0
        for s in sentences(text):
            if AI_WORDS.search(s) and used < per_job:
                keep.append(s)
                used += len(s)
        if not keep:
            keep = [text[:per_job]]
        parts.append(f"JOB: {j['title']}\n" + "\n".join(keep))
    return "\n\n".join(parts)[:max_chars]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[“”„\"'’]", "", s or "")).strip().lower()


def quote_is_real(quote: str, source: str) -> bool:
    q, src = _norm(quote).strip(" .…"), _norm(source)
    return len(q) >= 12 and (q in src or q[:60] in src)


def pick(value, allowed, default):
    v = str(value or "").strip().lower()
    return v if v in allowed else default


def analyze_company(company, jobs, max_chars: int = 6000) -> dict:
    text = _job_text(jobs, max_chars=max_chars)
    data = llm.ask_json("analyze_company", SYSTEM, f"Company: {company['name']}\n\n{text}", cheap=False)
    if not data:
        return None
    quote = str(data.get("quote", "")).strip()
    if not quote_is_real(quote, text):
        quote = ""     # GPT made it up or changed it -> not shown as proof
    return {
        "ai_builds": str(data.get("builds", "unclear")).strip()[:200],
        "ai_use": pick(data.get("ai_use"), {"product", "internal", "consulting", "research", "unclear"}, "unclear"),
        "ai_spend": pick(data.get("spend"), {"high", "medium", "low"}, "low"),
        "ai_fit": pick(data.get("fit"), {"yes", "maybe", "no"}, "maybe"),
        "ai_reason": (f"Spend: {str(data.get('spend_reason', '')).strip()[:120]} · "
                      f"Fit: {str(data.get('fit_reason', '')).strip()[:160]}"),
        "ai_evidence": quote[:300],
    }


def run(tiers=None, limit=None, redo=False, company=None):
    if not llm.available():
        print("GPT analysis skipped: no OPENAI_API_KEY in .env")
        return 0
    tiers = tiers or config.ANALYZE_TIERS
    limit = limit or config.ANALYZE_LIMIT
    with db.connect() as conn:
        rows = db.companies_to_analyze(conn, tiers, limit, redo=redo, name=company)
    if not rows:
        print("GPT analysis: nothing new to analyze.")
        return 0

    print(f"GPT analysis of {len(rows)} companies ({config.OPENAI_MODEL})...")
    done = 0
    for i, c in enumerate(rows, 1):
        with db.connect() as conn:
            jobs = db.jobs_for(conn, c["id"])
        res = analyze_company(c, jobs)
        if res is None:
            if not llm.available():
                break         # GPT switched off (key / model problem)
            continue
        with db.connect() as conn:
            db.save_analysis(conn, c["id"], res)
        done += 1
        mark = {"yes": "✅", "maybe": "🤔", "no": "❌"}[res["ai_fit"]]
        print(f"  [{i}/{len(rows)}] {mark} {c['name'][:35]:<35} spend {res['ai_spend']:<6} {res['ai_builds'][:70]}")
    print(f"Analyzed {done} companies. {llm.usage_summary()}")
    return done


# ------------------------------------------------------------
# Part 2: second look with the company WEBSITE (only for companies we look for people at)
# One GPT call: company size + better fit + the best 1-2 people to contact.
# ------------------------------------------------------------

SIZES = ["1-10", "11-50", "51-200", "201-500", "501-1000", "1000+", "unknown"]

REFINE_SYSTEM = """You help Flocta (cuts LLM API costs by up to 35%: semantic caching, context compression,
routing to cheaper models; EU-hosted; for European companies with about 10-500 employees) pick leads.

You get: what we already know from the company's job ads, text from the company's WEBSITE, and a numbered
list of people we found at the company.

Return JSON:
{
  "size": "1-10 | 11-50 | 51-200 | 201-500 | 501-1000 | 1000+ | unknown",
  "size_quote": "EXACT short quote from the website that shows the size (e.g. 'team of 40'), or empty",
  "fit": "yes | maybe | no",
  "fit_reason": "max 20 words, using job ads AND website",
  "best": [{"i": <number from the people list>, "why": "max 12 words"}]
}
Rules for "best" (max 2 people): pick who decides about LLM / AI API spend. Usually CTO / Head of AI /
Head of Engineering; in very small companies (1-50) the CEO or founder is often better. Never pick
people who work at another company (customers, investors). Recruiters only if nobody else exists.
Answer only from the text. Never invent numbers."""


def refine(company, site: dict, people: list):
    """Returns {"size", "size_quote", "fit", "fit_reason", "picked": [(person, why)]} or None."""
    if not (config.USE_GPT_REFINE and llm.available()):
        return None
    pages = [p for p in site.get("pages", []) if p["kind"] in ("home", "team", "impressum")]
    web = "\n".join(f"[{p['kind']}] {p['text'][:2500]}" for p in pages)[:7000]
    if len(web) < 100:
        return None
    candidates = [p for p in people if p["rank"] <= 5][:15] or people[:5]
    plist = "\n".join(f"{i}. {p['name']}: {p['role'] or 'no title'} (found in: {p['source']})"
                      for i, p in enumerate(candidates))
    known = (f"Job ads say: {company['ai_builds'] or 'unclear'}. Earlier fit: {company['ai_fit'] or '-'}"
             if "ai_builds" in company.keys() else "")
    data = llm.ask_json("refine_company", REFINE_SYSTEM,
                        f"Company: {company['name']}\n{known}\n\nWEBSITE:\n{web}\n\nPEOPLE:\n{plist or '(none)'}",
                        cheap=False)
    if not data:
        return None
    size = data.get("size") if data.get("size") in SIZES else "unknown"
    quote = str(data.get("size_quote") or "").strip()
    if quote and not quote_is_real(quote, web):
        quote = ""
    if size != "unknown" and not quote:
        size = "unknown"         # no proof on the website -> we don't trust the number
    picked, seen = [], set()
    for b in (data.get("best") or [])[:config.CONTACTS_PER_COMPANY]:
        try:
            i = int(b.get("i"))
        except (TypeError, ValueError, AttributeError):
            continue
        if 0 <= i < len(candidates) and i not in seen:
            seen.add(i)
            picked.append((candidates[i], str(b.get("why", "")).strip()[:120]))
    return {"size": size, "size_quote": quote[:200],
            "fit": pick(data.get("fit"), {"yes", "maybe", "no"}, company["ai_fit"] or "maybe"),
            "fit_reason": str(data.get("fit_reason", "")).strip()[:200], "picked": picked}


# ------------------------------------------------------------
# Account brief: 5 lines for Max before a call / message
# ------------------------------------------------------------

STACK_WORDS = ["OpenAI", "GPT", "Azure OpenAI", "Anthropic", "Claude", "Gemini", "Vertex AI", "Bedrock", "Mistral",
               "Llama", "Hugging Face", "LangChain", "LangGraph", "LlamaIndex", "RAG", "vector database", "Pinecone",
               "Weaviate", "Qdrant", "pgvector", "Elasticsearch", "OpenSearch", "Kubernetes", "AWS", "Azure", "GCP",
               "Python", "TypeScript", "agents", "fine-tuning", "embeddings", "Copilot", "ChatGPT", "Cohere",
               "DeepL", "Whisper", "ElevenLabs", "Databricks", "Snowflake", "MLflow", "vLLM", "Ollama"]

BRIEF_SYSTEM = """You prepare a short account brief for Max, founder of Flocta (cuts LLM API costs by up to 35%:
semantic caching, context compression, routing simple requests to cheaper models; EU-hosted).
Use ONLY the facts given. Never invent numbers, customers, funding or names.
Return JSON:
{
  "what_they_do": "1 sentence",
  "ai_use": "1 sentence: how they use LLMs / AI (from the job ads)",
  "pain": "1 sentence: where Flocta could save them money, based on the facts",
  "angle": "1 sentence: the best way to start the conversation",
  "opener": "1 friendly first sentence Max could say or write, mentioning something specific from their job ad",
  "questions": ["2 short discovery questions for a first call"]
}
Write in English."""


def _stack_in(text: str) -> list:
    """Technologies that are REALLY in the job ads (found by code, not by GPT)."""
    return [w for w in STACK_WORDS if re.search(rf"(?<![A-Za-z]){re.escape(w)}(?![A-Za-z])", text, re.I)]


def brief_for(conn, c) -> dict:
    jobs = db.jobs_for(conn, c["id"])
    signals = db.signals_for(conn, c["id"])
    contact = conn.execute("SELECT name, role FROM contacts WHERE company_id = ? ORDER BY rank LIMIT 1",
                           (c["id"],)).fetchone()
    text = _job_text(jobs, max_chars=5000)
    stack = _stack_in(" ".join((j["description"] or "") + " " + (j["title"] or "") for j in jobs))
    seen, proof = set(), []
    for s in signals:
        if s["type"] not in seen:
            seen.add(s["type"])
            proof.append(f"- {s['label']}: \"{s['evidence'][:200]}\"")
    facts = (f"Company: {c['name']}\nSize: {c['company_size'] or 'unknown'}\n"
             f"GPT analysis: {c['ai_builds'] or ''}. {c['ai_reason'] or ''} {c['web_reason'] or ''}\n"
             f"Tech in their job ads: {', '.join(stack) or 'not mentioned'}\n"
             f"Signals:\n" + "\n".join(proof) +
             (f"\nContact: {contact['name']}, {contact['role']}" if contact else "") +
             f"\n\nJob ad text:\n{text}")
    data = llm.ask_json("account_brief", BRIEF_SYSTEM, facts, cheap=False)
    if not data:
        return None
    qs = data.get("questions") or []
    return {
        "what_they_do": str(data.get("what_they_do", ""))[:250],
        "ai_use": str(data.get("ai_use", ""))[:250],
        "stack": stack,                       # from the code, not from GPT
        "pain": str(data.get("pain", ""))[:250],
        "angle": str(data.get("angle", ""))[:250],
        "opener": str(data.get("opener", ""))[:300],
        "questions": [str(q)[:200] for q in qs[:3]] if isinstance(qs, list) else [],
        "contact": f"{contact['name']}, {contact['role']}" if contact else "",
    }


def briefs(limit=100, redo=False, company=None):
    import json
    if not llm.available():
        print("Briefs skipped: no OPENAI_API_KEY in .env")
        return 0
    marks = ",".join("?" * len(config.BRIEF_FITS))
    with db.connect() as conn:
        if company:
            rows = conn.execute("SELECT * FROM companies WHERE name_key = ? OR name LIKE ?",
                                (db.name_key(company), f"%{company}%")).fetchall()
        else:
            rows = conn.execute(
                f"""SELECT * FROM companies WHERE ai_fit IN ({marks}) {'' if redo else 'AND brief IS NULL'}
                    ORDER BY score DESC LIMIT ?""", (*config.BRIEF_FITS, limit)).fetchall()
    n = 0
    for c in rows:
        with db.connect() as conn:
            b = brief_for(conn, c)
        if not b:
            continue
        with db.connect() as conn:
            db.save_brief(conn, c["id"], json.dumps(b, ensure_ascii=False))
        n += 1
        print(f"   brief: {c['name']}")
    export_briefs()
    print(f"{n} account briefs written. {llm.usage_summary()}")
    return n


def export_briefs():
    import json
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM companies WHERE brief IS NOT NULL ORDER BY score DESC").fetchall()
    lines = ["# Account briefs", ""]
    for c in rows:
        b = json.loads(c["brief"])
        lines += [f"## {c['name']}  (score {c['score']}, size {c['company_size'] or '?'}, fit {c['ai_fit']})",
                  f"- **What they do:** {b['what_they_do']}",
                  f"- **AI use:** {b['ai_use']}",
                  f"- **Tech in their job ads:** {', '.join(b['stack']) or '-'}",
                  f"- **Where Flocta helps:** {b['pain']}",
                  f"- **Angle:** {b['angle']}",
                  f"- **Opener:** {b['opener']}"]
        if b.get("contact"):
            lines.append(f"- **Contact:** {b['contact']}")
        lines += [f"- **Ask:** {q}" for q in b.get("questions", [])] + [""]
    (config.OUTPUT_DIR / "briefs.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"Briefs: {config.OUTPUT_DIR / 'briefs.md'}")
