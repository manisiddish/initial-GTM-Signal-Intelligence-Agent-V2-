"""
Find AI signals in job ad text. No GPT needed, so it's free and fast.

Every signal keeps the exact sentence it was found in, as proof.

Signal types (points are in config.py):
  cost_pain       worries about AI costs        "Inference-Kosten senken"
  builds_with_ai  builds AI into its product    "RAG pipeline on GPT-4o"
  daily_ai_use    staff use AI tools daily      "you use ChatGPT every day"
  mentions_ai     mentions AI in general        "KI", "machine learning"
"""

import html
import re

import config

LABELS = {
    "hires_ai_role": "Hiring for an AI role",
    "cost_pain": "Worries about AI costs",
    "builds_with_ai": "Builds AI into its product",
    "daily_ai_use": "Staff use AI tools daily",
    "mentions_ai": "Mentions AI",
}

AI = (r"(?:\bllm|\bki\b|\bai\b|\bgpt|inference|\bprompt|sprachmodell|"
      r"(?:ai|ml|ki|language|llm|foundation)[- ]models?)")
SAVE = r"(?:optimi[sz]|reduc|senk|effizien|einspar|spar|lower|cut)"

# AI roles, read from the JOB TITLE only
AI_ROLE_TOPIC = (r"(?:\bai\b|\bki\b|\bllms?\b|\bgen ?ai\b|generative|machine learning|\bml\b|"
                 r"deep learning|\bnlp\b|natural language|prompt|mlops|llmops|künstliche intelligenz|"
                 r"computer vision|conversational|\brag\b|agentic|\bagents?\b)")
AI_ROLE_JOB = (r"(?:engineer|entwickler|developer|scientist|researcher|architect|lead|head|manager|"
               r"director|specialist|spezialist|expert|experte|consultant|berater|owner|vp|cto|"
               r"werkstudent|praktikant|intern|trainee|analyst|designer|programmierer|ingenieur)")
AI_ROLE_TITLE = [
    rf"{AI_ROLE_TOPIC}[^,/|()]{{0,40}}{AI_ROLE_JOB}",
    rf"{AI_ROLE_JOB}[^,|()]{{0,30}}(?:for|für|im bereich|in|of)?[^,|()]{{0,15}}{AI_ROLE_TOPIC}",
    r"head of (?:ai|ml|machine learning|data (?:&|and) ai)",
    r"\b(?:ai|ki|ml)[- ](?:engineer|entwickler|developer|lead)",
    r"prompt[- ]engineer",
]

PATTERNS = {
    "cost_pain": [
        r"inference[- ]?(?:cost|kosten)",
        r"token[- ]?(?:cost|usage|kosten|verbrauch)",
        r"cost per (?:request|call|query|token)",
        r"kosten (?:pro|je) (?:anfrage|request|aufruf|abfrage|token)",
        r"\b(?:llm|ai|ki|gpt)[- ]?(?:api[- ]?)?(?:cost|kosten)",
        rf"(?:cost|kosten)\w*[- ]?{SAVE}\w*[^.]{{0,60}}{AI}",
        rf"{AI}[^.]{{0,60}}(?:cost|kosten)\w*[- ]?{SAVE}",
        rf"{SAVE}\w*[^.]{{0,30}}(?:cost|kosten)[^.]{{0,40}}{AI}",
        r"latency and cost|cost and latency|kosten und latenz",
        r"finops[^.]{0,40}(?:\bai\b|\bki\b|llm)",
        r"model routing|semantic cach|prompt cach",
    ],
    "builds_with_ai": [
        r"\bllms?\b", r"large language model", r"sprachmodell",
        r"\brag\b", r"retrieval[- ]augmented",
        r"vector (?:database|db|store|search)", r"vektor(?:datenbank|suche)",
        r"\bembeddings?\b", r"fine[- ]?tun",
        r"openai[- ]?api", r"(?:anthropic|claude)[- ]?api", r"azure openai",
        r"\bgpt-?\d", r"langchain|llamaindex|langgraph|hugging ?face",
        r"(?:ai|ki)[- ]agent", r"agentic",
        r"generative (?:ai|ki)|\bgenai\b|generative[nr]? ki",
        r"prompt engineer", r"llmops",
    ],
    "daily_ai_use": [
        r"chatgpt", r"\bcopilot\b", r"\bclaude\b(?![- ]?api)", r"\bgemini\b", r"\bperplexity\b",
        r"\bdeepl\b[^.]{0,40}(?:ki|ai)",
        r"(?<![\w-])(?:ai|ki)[- ]?(?:tools?|werkzeuge)\b",
        r"(?:ai|ki)[- ]first", r"\bprompting\b",
        r"(?:täglich|daily|every day|jeden tag|routiniert)[^.]{0,60}(?:\bki\b|\bai\b|chatgpt|claude|copilot)",
        r"(?:\bki\b|\bai\b|chatgpt|claude|copilot)[^.]{0,60}(?:täglich|daily|every day|jeden tag)",
    ],
    "mentions_ai": [
        r"(?-i:\bKI\b)", r"künstliche[nr]? intelligenz", r"artificial intelligence",
        r"(?-i:\bAI\b)", r"machine learning", r"maschinelle[sn]? lernen",
    ],
}

EU_DATA = [r"dsgvo", r"gdpr", r"eu[- ]?ai[- ]?act", r"data residency", r"eu[- ]hosting",
           r"server(?:n|standort)? in (?:der )?(?:eu|deutschland)", r"datensouveränität"]

# Recruitment agencies post for other companies -> not our customer
RECRUITER_NAMES = [
    r"personalvermittlung", r"personaldienstleist", r"zeitarbeit", r"personalberatung",
    r"personal ?service", r"staffing", r"headhunt", r"recruiting gmbh", r"\brecruitment\b",
    r"\bhays\b", r"\brandstad\b", r"\badecco\b", r"\bmanpower", r"\bakkodis\b", r"\bgulp\b",
    r"\bamadeus fire\b", r"\bpage personnel\b", r"\bmichael page\b", r"\bduerenhoff\b",
    r"\bavantgarde experts\b", r"\bfreelancermap\b", r"\bdis ag\b", r"\btimepartner\b",
]
RECRUITER_TEXT = [
    r"für unseren kunden", r"im auftrag unsere[sr] kunden", r"unser(?:e)? kunde(?:n)?\b",
    r"on behalf of (?:our|a) client", r"our client is", r"arbeitnehmerüberlassung",
    r"im rahmen der personalvermittlung",
]


def _compile(patterns):
    return [re.compile(p) if p.startswith("(?-i:") else re.compile(p, re.IGNORECASE) for p in patterns]


COMPILED = {k: _compile(v) for k, v in PATTERNS.items()}
AI_ROLE_RE = _compile(AI_ROLE_TITLE)


def _load_custom():
    """Extra phrases from custom_signals.csv (written by you or by 'python run.py learn').
    Phrases are matched as plain text (not regex), so a bad line can't break anything."""
    import csv
    path = config.CUSTOM_SIGNALS_PATH
    if not path.exists():
        return 0
    n = 0
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            kind, phrase = (row.get("type") or "").strip(), (row.get("phrase") or "").strip()
            if len(phrase) < 3:
                continue
            rx = re.compile(rf"(?<![\w-]){re.escape(phrase)}(?![\w-])", re.IGNORECASE)
            if kind in COMPILED:
                COMPILED[kind].append(rx)
                n += 1
            elif kind == "hires_ai_role":
                AI_ROLE_RE.append(rx)
                n += 1
    return n


CUSTOM_COUNT = _load_custom()
EU_RE = _compile(EU_DATA)
REC_NAME_RE = _compile(RECRUITER_NAMES)
REC_TEXT_RE = _compile(RECRUITER_TEXT)


# ------------------------------------------------------------
# Text helpers
# ------------------------------------------------------------

def clean_text(raw: str) -> str:
    if not raw:
        return ""
    text = html.unescape(html.unescape(str(raw)))
    text = re.sub(r"<(?:br|/p|/li|li|/h\d|/div|/ul)[^>]*>", ". ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"(?:\.\s*){2,}", ". ", text)
    return text.strip()


def sentences(text: str) -> list:
    parts = re.split(r"(?<=[.!?;:])\s+(?=[A-ZÄÖÜ0-9•\-])|\s+[•·▪–]\s+|\s+-\s+(?=[A-ZÄÖÜ])", text)
    return [p.strip(" .-") for p in parts if len(p.strip()) > 3]


# ------------------------------------------------------------
# Main functions
# ------------------------------------------------------------

def is_recruiter(company: str, description: str = "") -> bool:
    if any(p.search(company or "") for p in REC_NAME_RE):
        return True
    return any(p.search((description or "")[:1500]) for p in REC_TEXT_RE)


def find(title: str, description: str) -> list:
    """
    Signals in one job ad: [{"type", "label", "points", "evidence"}].
    One signal per type, with the most specific sentence as proof.
    """
    found = []
    title_clean = clean_text(title)

    # 1. Is the job itself an AI role? (title only)
    if title_clean and any(p.search(title_clean) for p in AI_ROLE_RE):
        found.append({"type": "hires_ai_role", "label": LABELS["hires_ai_role"],
                      "points": config.SIGNAL_POINTS["hires_ai_role"], "evidence": title_clean[:300]})

    body = sentences(clean_text(description))
    candidates = [title_clean] + body if title_clean else body

    for kind, patterns in COMPILED.items():
        best = None
        for s in candidates:
            if any(p.search(s) for p in patterns):
                # prefer a real sentence over the bare job title
                if best is None or (best == title_clean and s != title_clean):
                    best = s
                if best != title_clean:
                    break
        if best:
            found.append({"type": kind, "label": LABELS[kind],
                          "points": config.SIGNAL_POINTS[kind], "evidence": best[:300]})
    return found


def mentions_eu_data(description: str) -> bool:
    text = clean_text(description)
    return any(p.search(text) for p in EU_RE)


# Quick self-test: python signals.py
EXAMPLES = [
    ("Inference-Kosten um 30% senken bei gleicher Qualität", "cost_pain"),
    ("Du senkst die Kosten pro Anfrage unserer LLM-Features", "cost_pain"),
    ("Help us reduce the cost of our GPT-based assistant", "cost_pain"),
    ("Wir nutzen die Claude API und OpenAI API", "builds_with_ai"),
    ("You will build our RAG pipeline on GPT-4o", "builds_with_ai"),
    ("Du arbeitest täglich mit ChatGPT und Claude", "daily_ai_use"),
    ("Erfahrung mit KI-Tools ist ein Plus", "daily_ai_use"),
    ("Kenntnisse in Machine Learning", "mentions_ai"),
    ("Du verantwortest unseren KI-Assistenten auf Basis von GPT-4o", "builds_with_ai"),
    ("Wir senken Kosten im Einkauf", None),
    ("Sachbearbeiter Kfz-Versicherung", None),
    ("Organisation des Büros und Empfang", None),
    ("Kosten senken durch neue Geschäftsmodelle", None),
    ("Mitarbeit im Mai Kosten planen", None),
    # real cost signals from the first full run
    ("Benchmark models on quality, latency and cost", "cost_pain"),
    ("Verständnis von RAG, Agenten, Halluzinationen und Kosten je Token", "cost_pain"),
    ("Memory and context, retrieval, model routing, fallbacks and rollback", "cost_pain"),
    # false cost signals from the first full run
    ("What a given architecture or operating model costs and enables", "not:cost_pain"),
    ("Set up and maintain MLOps / LLMOps pipelines", "not:cost_pain"),
    ("Large token budget, real freedom to experiment", "not:cost_pain"),
]

if __name__ == "__main__":
    ok = 0
    titles = [("Senior AI Engineer (m/w/d)", True), ("Machine Learning Engineer", True),
              ("Head of AI", True), ("KI-Entwickler (w/m/d)", True), ("LLM Engineer - NLP", True),
              ("Werkstudent Künstliche Intelligenz", True), ("Product Manager AI Agents", True),
              ("Backend Engineer (Python)", False), ("Content Marketing Manager", False),
              ("Sales Manager Air & Sea", False), ("Mailroom Clerk", False), ("Maintenance Engineer", False)]
    for t, expected in titles:
        got = any(s["type"] == "hires_ai_role" for s in find(t, ""))
        ok += got == expected
        print(("PASS " if got == expected else "FAIL ") + f"[title] {t}  ->  {got}")
    for text, expected in EXAMPLES:
        got = [s["type"] for s in find("", text)]
        if expected and expected.startswith("not:"):
            passed = expected[4:] not in got
        else:
            passed = (expected in got) if expected else not got
        ok += passed
        print(("PASS " if passed else "FAIL ") + f"{text}  ->  {got}")
    print(f"\n{ok}/{len(EXAMPLES) + len(titles)} passed")
