"""
Run the agent.

    python run.py              collect + find signals + score + report (Part 1)
    python run.py collect      only collect job ads and find signals
    python run.py score        only (re)score companies
    python run.py report       only write the report
    python run.py rescan       re-check saved job ads with the latest signal rules (no download)
    python run.py analyze      GPT reads the hot/warm companies: what they build, AI spend, fit
                               (also runs inside 'python run.py' when OPENAI_API_KEY is set)
    python run.py learn        GPT suggests new signal phrases, the code tests them (--apply to use them)
    python run.py --days 14    look back 14 days instead of 7
    python run.py --sources arbeitnow,watchlist

Part 2 (people + emails):
    python run.py people                   hot + warm companies, best first (25 per run)
    python run.py people --limit 5         only the top 5
    python run.py people --tier HOT        only hot companies
    python run.py people --company Bliro   one company
    python run.py people --redo            check companies again
    python run.py contacts                 only rewrite output/contacts.csv
    python run.py credits                  Hunter credits left + GPT tokens used

Part 3 (write -> approve -> send):
    python run.py brief                    GPT account brief for every ✅ company -> output/briefs.md
                                           (also runs after 'people')
    python run.py write                    GPT writes email + LinkedIn note, a 2nd GPT pass reviews it
    streamlit run dashboard.py             review, edit, approve, send, mark replies
    python run.py send --dry-run           show what would be sent (sends nothing)
    python run.py send                     send APPROVED emails only
    python run.py replies                  read replies from Gmail, GPT sorts them (meeting / not interested ...)
    python run.py followup                 follow-up drafts for people who didn't answer (approve in dashboard)
    python run.py results                  reply rate per signal (which signals work?)

Output in output/:
    report.md                ranked companies with proof (show this to Max)
    leads.csv                the same as a table (Excel / Numbers)
    watchlist_suggestions.csv companies with a known job tool, to watch daily
    contacts.csv             Part 2: decision makers, emails, proof
    drafts.md                Part 3: all email drafts
"""

import argparse
import csv
from datetime import date

import config
import database as db
import signals
import scorer
import analyzer
import contacts
import followups
import hunter
import learn
import replies
import llm
import sender
import writer
from collectors import arbeitnow, ats_feeds, bundesagentur

ALL_SOURCES = ["bundesagentur", "arbeitnow", "watchlist"]


# ------------------------------------------------------------
# Step 1 + 2: collect job ads and find signals
# ------------------------------------------------------------

def collect(sources, days):
    with db.connect() as conn:
        known = db.known_urls(conn)

    print("Collecting job ads...")
    jobs = []
    if "bundesagentur" in sources:
        jobs += bundesagentur.collect(days=days, known_urls=known)
    if "arbeitnow" in sources:
        jobs += arbeitnow.collect(known_urls=known)
    if "watchlist" in sources:
        jobs += ats_feeds.collect(known_urls=known)

    stats = {"new_ads": len(jobs), "recruiters": 0, "no_ai": 0, "ai_ads": 0}
    with db.connect() as conn:
        for job in jobs:
            company = (job.get("company") or "").strip()
            if not company or not job.get("url"):
                continue
            db.mark_seen(conn, job["url"])
            if scorer.looks_like_job_title(company):
                stats["recruiters"] += 1
                continue
            if signals.is_recruiter(company, job.get("description", "")):
                stats["recruiters"] += 1
                continue
            found = signals.find(job.get("title", ""), job.get("description", ""))
            if not found:
                stats["no_ai"] += 1
                continue

            stats["ai_ads"] += 1
            company_id = db.get_or_create_company(conn, company)
            job_id = db.add_job(conn, company_id, job)
            for s in found:
                db.add_signal(conn, company_id, job_id, s, job["url"])

            ats, slug = ats_feeds.detect_ats(f"{job['url']} {job.get('description', '')}")
            if ats:
                db.set_company_ats(conn, company_id, ats, slug)

    print(f"\n{stats['new_ads']} new job ads: {stats['ai_ads']} with AI signals, "
          f"{stats['no_ai']} without, {stats['recruiters']} from recruitment agencies or with a broken company name (skipped).")
    return stats


# ------------------------------------------------------------
# Re-check saved job ads (after changing signals.py or config.py)
# ------------------------------------------------------------

def rescan():
    with db.connect() as conn:
        jobs = conn.execute("SELECT j.*, c.name AS company FROM jobs j JOIN companies c ON c.id = j.company_id").fetchall()
        conn.execute("DELETE FROM signals")
        kept = 0
        for j in jobs:
            for s in signals.find(j["title"] or "", j["description"] or ""):
                db.add_signal(conn, j["company_id"], j["id"], s, j["url"])
                kept += 1
    print(f"Re-checked {len(jobs)} saved job ads ({kept} signals).")


# ------------------------------------------------------------
# Step 4: report
# ------------------------------------------------------------

def report(limit=None):
    with db.connect() as conn:
        top = db.top_companies(conn, limit=500)
        n = db.counts(conn)
        details = {c["id"]: db.signals_for(conn, c["id"]) for c in top[:25]}
        watch = conn.execute(
            "SELECT name, ats, ats_slug FROM companies WHERE ats IS NOT NULL ORDER BY score DESC"
        ).fetchall()

    # CSV with every scored company
    with open(config.OUTPUT_DIR / "leads.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["rank", "company", "score", "tier", "gpt_fit", "gpt_ai_spend", "what_they_build", "gpt_reason",
                    "gpt_quote", "why", "website", "best_proof", "proof_link"])
        with db.connect() as conn:
            for i, c in enumerate(top, 1):
                rows = db.signals_for(conn, c["id"])
                best = rows[0] if rows else None
                w.writerow([i, c["name"], c["score"], c["tier"], c["ai_fit"] or "", c["ai_spend"] or "",
                            c["ai_builds"] or "", c["ai_reason"] or "", c["ai_evidence"] or "",
                            c["why"], c["website"] or "", best["evidence"] if best else "", best["url"] if best else ""])

    # Watchlist suggestions
    with open(config.OUTPUT_DIR / "watchlist_suggestions.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["company", "ats", "slug"])
        for r in watch:
            w.writerow([r["name"], r["ats"], r["ats_slug"]])

    # Markdown report
    lines = [
        f"# Companies hiring for AI roles ({date.today().isoformat()})",
        "",
        f"{n['jobs']} AI-related job ads from {n['with_signals']} companies. "
        f"**{n['hot']} hot**, **{n['warm']} warm**. Recruitment agencies are excluded.",
        "",
        "Score: hiring for an AI role 30 · worries about AI costs 40 · builds AI into its product 25 · "
        "staff use AI daily 10 · mentions AI 5 · +5 per extra AI role (max 15) · +5 EU data rules. "
        "Only companies hiring for AI roles (or mentioning AI costs) are kept. Hot = 65+, warm = 40+. "
        "Job boards, recruitment agencies and data errors are removed. "
        + ("All other companies are kept; big companies, public sector and consultancies are labelled."
           if config.KEEP_ALL_COMPANIES else
           "Big companies, AI labs and public sector are excluded; consultancies count half."),
        "",
        "| # | Company | Score | GPT fit | What they build with AI | Why (signals) |",
        "|---|---|---|---|---|---|",
    ]
    fit_icon = {"yes": "✅ yes", "maybe": "🤔 maybe", "no": "❌ no"}
    for i, c in enumerate(top if limit is None else top[:limit], 1):
        lines.append(f"| {i} | {c['name']} | {c['score']} {c['tier']} | {fit_icon.get(c['ai_fit'], '')} | "
                     f"{(c['ai_builds'] or '').replace('|', '/')} | {c['why']} |")

    lines += ["", "## Proof for the top companies", ""]
    for i, c in enumerate(top[:25], 1):
        lines.append(f"### {i}. {c['name']} ({c['score']}, {c['tier']})")
        if c["ai_fit"]:
            lines.append(f"- **GPT**: fit **{c['ai_fit']}**, AI spend **{c['ai_spend']}**. {c['ai_builds']}  ")
            lines.append(f"  {c['ai_reason']}" + (f"  \n  Quote: \"{c['ai_evidence']}\"" if c["ai_evidence"] else ""))
        shown = set()
        with db.connect() as conn:
            people = db.contacts_for(conn, c["id"])
        for p in people[:2]:
            mail = f"{p['email']} ({p['email_status']})" if p["email"] else p["email_status"]
            lines.append(f"- **Contact**: {p['name']}, {p['role']}: {mail}")
        for s in details[c["id"]]:
            if s["type"] in shown:
                continue
            shown.add(s["type"])
            date_txt = f", {s['posted_date'][:10]}" if s["posted_date"] else ""
            lines.append(f"- **{s['label']}**: \"{s['evidence']}\"  ")
            lines.append(f"  [{s['job_title']}]({s['url']}){date_txt}")
        lines.append("")

    (config.OUTPUT_DIR / "report.md").write_text("\n".join(lines), encoding="utf-8")

    # Console summary
    print(f"\nTop companies ({n['hot']} hot, {n['warm']} warm"
          + (f"; GPT: {n['fit_yes']} good fit of {n['analyzed']} analyzed" if n["analyzed"] else "") + "):")
    fit_icon = {"yes": "✅", "maybe": "🤔", "no": "❌"}
    for i, c in enumerate(top[:15], 1):
        info = c["ai_builds"] or c["why"] or ""
        print(f"{i:>3}. {c['score']:>3} {c['tier']:<5} {fit_icon.get(c['ai_fit'], '  ')} {c['name'][:38]:<38} {info[:70]}")
    print(f"\nReport: {config.OUTPUT_DIR / 'report.md'}")
    print(f"Table:  {config.OUTPUT_DIR / 'leads.csv'}")


# ------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Flocta lead agent, Part 1")
    parser.add_argument("step", nargs="?", default="all",
                        choices=["all", "collect", "score", "report", "rescan", "analyze", "people", "contacts",
                                 "credits", "write", "send", "replies", "followup", "results",
                                 "brief", "learn"])
    parser.add_argument("--days", type=int, default=config.DAYS_BACK)
    parser.add_argument("--sources", default=",".join(ALL_SOURCES))
    parser.add_argument("--tier", default=",".join(config.PEOPLE_TIERS), help="Part 2: HOT,WARM")
    parser.add_argument("--limit", type=int, default=config.PEOPLE_LIMIT, help="Part 2: companies per run")
    parser.add_argument("--company", default=None, help="Part 2: only this company")
    parser.add_argument("--redo", action="store_true", help="Part 2/3: do companies / drafts again")
    parser.add_argument("--dry-run", action="store_true", help="Part 3: show what would be sent")
    parser.add_argument("--apply", action="store_true", help="learn: add the good phrases to custom_signals.csv")
    args = parser.parse_args()

    db.init()
    sources = {s.strip() for s in args.sources.split(",")}

    if args.step in ("all", "collect"):
        collect(sources, args.days)
    if args.step == "rescan":
        rescan()
    if args.step in ("all", "collect", "score", "rescan"):
        scorer.score_all()
    custom_limit = None if args.limit == config.PEOPLE_LIMIT else args.limit
    if args.step == "analyze" or (args.step == "all" and llm.available()):
        analyzer.run(limit=custom_limit, redo=args.redo, company=args.company)
    if args.step == "learn":
        learn.run(apply=args.apply)
    if args.step in ("all", "score", "report", "rescan", "analyze"):
        report()
    if args.step == "people":
        contacts.run(tiers=[t.strip().upper() for t in args.tier.split(",")], limit=args.limit,
                     redo=args.redo, company=args.company)
        if llm.available():
            analyzer.briefs(company=args.company)
    if args.step == "brief":
        analyzer.briefs(limit=args.limit if args.limit != config.PEOPLE_LIMIT else 100,
                        redo=args.redo, company=args.company)
        report()
    if args.step == "contacts":
        contacts.export()
    if args.step == "write":
        writer.write(limit=args.limit if args.limit != config.PEOPLE_LIMIT else config.WRITE_LIMIT,
                     redo=args.redo, company=args.company)
    if args.step == "send":
        sender.send(dry_run=args.dry_run)
    if args.step == "replies":
        replies.check()
    if args.step == "followup":
        followups.write()
    if args.step == "results":
        sender.print_results()
    if args.step == "credits":
        print(hunter.account())
        print(llm.usage_summary())


if __name__ == "__main__":
    main()
