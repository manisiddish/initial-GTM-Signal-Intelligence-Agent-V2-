"""
Self-improving signals: GPT teaches the free word rules.

    python run.py learn            suggestions -> output/signal_suggestions.csv
    python run.py learn --apply    also add the good ones to custom_signals.csv, then: python run.py rescan

How it works:
  1. Compare GPT's verdict (✅ / ❌) with the rule score:
       missed      = GPT says ✅ but the rules scored low  (the rules missed a good company)
       false alarm = GPT says ❌ but the rules scored high (the rules were fooled)
  2. GPT reads examples of both and suggests short phrases that separate good from bad companies.
  3. The CODE tests every phrase on all saved job ads and measures it:
       how many companies it matches, and how many of them GPT rated ✅ vs ❌.
     Only phrases that really point to good companies are kept.
  4. With --apply they go into custom_signals.csv, which signals.py reads automatically.
"""

import csv
import re

import config
import database as db
import llm
from analyzer import _job_text

SYSTEM = """You improve keyword rules that find companies paying for LLM APIs (good leads for Flocta,
an LLM cost-reduction product). You get job ad excerpts from:
 A) MISSED: good companies the keyword rules scored too low
 B) FALSE ALARMS: companies the rules scored high, but that are NOT good leads
Suggest short phrases (2-5 words, German or English, exactly as they appear in job ads) that
appear in A-type ads and would catch more companies like A, without catching B.
Return JSON: {"phrases": [{"phrase": "...", "type": "builds_with_ai | cost_pain | daily_ai_use", "why": "max 10 words"}]}
Max 15 phrases. Only phrases you actually saw in the excerpts. No single generic words like "AI"."""


def _examples(conn, missed: bool, n: int = 8) -> str:
    if missed:
        rows = conn.execute("""SELECT * FROM companies WHERE ai_fit = 'yes' AND tier NOT IN ('HOT')
                               ORDER BY score LIMIT ?""", (n,)).fetchall()
    else:
        rows = conn.execute("""SELECT * FROM companies WHERE ai_fit = 'no' AND tier IN ('HOT', 'WARM')
                               ORDER BY score DESC LIMIT ?""", (n,)).fetchall()
    parts = []
    for c in rows:
        parts.append(f"### {c['name']} (rule score {c['score']}; GPT: {c['ai_builds']})\n"
                     + _job_text(db.jobs_for(conn, c["id"]), max_chars=1200))
    return "\n\n".join(parts)


def measure(conn, phrase: str) -> dict:
    """How many saved companies does this phrase match, and how good are they (by GPT's verdict)?"""
    rx = re.compile(rf"(?<![\w-]){re.escape(phrase)}(?![\w-])", re.I)
    hit = set()
    for j in conn.execute("SELECT company_id, title, description FROM jobs"):
        if rx.search(f"{j['title'] or ''} {j['description'] or ''}"):
            hit.add(j["company_id"])
    if not hit:
        return {"companies": 0, "yes": 0, "maybe": 0, "no": 0, "unrated": 0}
    marks = ",".join("?" * len(hit))
    fits = [r["ai_fit"] for r in conn.execute(f"SELECT ai_fit FROM companies WHERE id IN ({marks})", tuple(hit))]
    return {"companies": len(hit), "yes": fits.count("yes"), "maybe": fits.count("maybe"),
            "no": fits.count("no"), "unrated": sum(1 for f in fits if not f)}


def existing_phrases() -> set:
    if not config.CUSTOM_SIGNALS_PATH.exists():
        return set()
    with open(config.CUSTOM_SIGNALS_PATH, newline="", encoding="utf-8") as f:
        return {(r.get("phrase") or "").strip().lower() for r in csv.DictReader(f)}


def run(apply: bool = False):
    if not llm.available():
        print("Learning needs GPT (OPENAI_API_KEY in .env).")
        return []
    with db.connect() as conn:
        rated = conn.execute("SELECT COUNT(*) FROM companies WHERE ai_fit IS NOT NULL").fetchone()[0]
        missed, false_alarms = _examples(conn, True), _examples(conn, False)
    if rated < 20 or not missed:
        print("Not enough GPT ratings yet. Run 'python run.py analyze' first.")
        return []

    data = llm.ask_json("learn_signals", SYSTEM, f"A) MISSED:\n{missed}\n\nB) FALSE ALARMS:\n{false_alarms or '(none)'}",
                        cheap=False) or {}
    have = existing_phrases()
    results = []
    with db.connect() as conn:
        for p in (data.get("phrases") or [])[:15]:
            phrase, kind = str(p.get("phrase", "")).strip(), str(p.get("type", "")).strip()
            if len(phrase) < 4 or kind not in ("builds_with_ai", "cost_pain", "daily_ai_use") or phrase.lower() in have:
                continue
            m = measure(conn, phrase)
            rated_hits = m["yes"] + m["maybe"] + m["no"]
            precision = (m["yes"] + 0.5 * m["maybe"]) / rated_hits if rated_hits else 0
            keep = m["companies"] >= 2 and rated_hits >= 2 and precision >= 0.6 and m["no"] <= m["yes"]
            results.append(dict(p, phrase=phrase, type=kind, precision=round(precision, 2), keep=keep, **m))

    results.sort(key=lambda r: (-r["keep"], -r["precision"], -r["companies"]))
    out = config.OUTPUT_DIR / "signal_suggestions.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["keep", "type", "phrase", "companies", "gpt_yes", "gpt_maybe", "gpt_no", "precision", "why"])
        for r in results:
            w.writerow(["yes" if r["keep"] else "no", r["type"], r["phrase"], r["companies"], r["yes"], r["maybe"],
                        r["no"], r["precision"], r.get("why", "")])

    print(f"{'Keep':<5} {'Type':<15} {'Phrase':<35} {'Companies':>9} {'✅':>3} {'🤔':>3} {'❌':>3}")
    for r in results:
        print(f"{'yes' if r['keep'] else 'no':<5} {r['type']:<15} {r['phrase'][:35]:<35} {r['companies']:>9} "
              f"{r['yes']:>3} {r['maybe']:>3} {r['no']:>3}")
    print(f"\nSuggestions: {out}")

    good = [r for r in results if r["keep"]]
    if apply and good:
        new = not config.CUSTOM_SIGNALS_PATH.exists()
        with open(config.CUSTOM_SIGNALS_PATH, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["type", "phrase", "note"])
            for r in good:
                w.writerow([r["type"], r["phrase"], f"learned: {r['yes']} yes / {r['no']} no of {r['companies']}"])
        print(f"Added {len(good)} phrases to {config.CUSTOM_SIGNALS_PATH.name}. Now run: python run.py rescan")
    elif good:
        print(f"{len(good)} good phrases. Add them with: python run.py learn --apply")
    return results
