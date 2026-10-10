"""
Part 2: for each hot/warm company
    website  ->  people  ->  decision maker  ->  email (format + ~20 variants + Hunter check)

Results are saved in the contacts table and in output/contacts.csv.
"""

import csv
from collections import Counter
from urllib.parse import quote_plus

import analyzer
import config
import database as db
import email_finder
import hunter
import llm
import person_finder
import site_reader
import website_finder


def linkedin_search(name: str, company: str, role: str = "") -> str:
    """A search link for YOU to click (the agent never opens LinkedIn itself)."""
    words = f"{name} {company}" if name else f"{role or 'CTO'} {company}"
    return f"https://www.linkedin.com/search/results/people/?keywords={quote_plus(words)}"


def _email_domain(site_domain: str, emails, company: str) -> str:
    """Emails are usually @website-domain, but not always (website flix.com, emails @flix.com / @flixbus.com)."""
    counts = Counter(e.split("@")[1] for e in emails if not email_finder.is_generic(e))
    counts.update(e.split("@")[1] for e in emails)
    for d, _ in counts.most_common():
        if d == site_domain:
            return d
        if website_finder.name_match(d, company) >= 60 and not website_finder._is_never(d):
            return d
    return site_domain


def process(row, domain_state: dict) -> dict:
    name = row["name"]
    with db.connect() as conn:
        jobs = db.jobs_for(conn, row["id"])

    # 1. Website
    domain, how_site = website_finder.find(row, jobs)
    with db.connect() as conn:
        db.set_company_website(conn, row["id"], domain, how_site)

    job_text = " ".join(f"{j['description'] or ''}" for j in jobs)
    job_emails = site_reader.find_emails(job_text)

    if not domain:
        people = person_finder.merge(person_finder.from_job_ads(jobs))
        contacts = [dict(p, email="", email_status="no website", confidence=0, email_source="",
                         linkedin_search=linkedin_search(p["name"], name)) for p in people[:1]]
        with db.connect() as conn:
            db.replace_contacts(conn, row["id"], contacts)
            db.set_company_people_result(conn, row["id"], None, "", "no_website")
        return {"company": name, "domain": None, "how_site": how_site, "people": 0, "contacts": contacts}

    # 2. Read the website
    site = site_reader.read_site(domain)
    all_emails = set(site["emails"]) | job_emails
    mail_domain = _email_domain(site.get("domain") or domain, all_emails, name)

    # 3. Hunter: emails + format it already knows for this domain (1 search, cached)
    hunter_data = hunter.domain_search(mail_domain) if hunter.available() else None
    if hunter_data:
        all_emails |= {(e.get("value") or "").lower() for e in hunter_data.get("emails", []) if e.get("value")}
        if hunter_data.get("accept_all"):
            domain_state[mail_domain] = "catch-all"

    # 4. People
    people = person_finder.find(name, site, jobs, hunter_data)

    # 5. Email format
    pattern, strength, proof = email_finder.learn_pattern(mail_domain, all_emails, people, hunter_data)
    generic = email_finder.generic_email(mail_domain, all_emails)

    # 6. GPT reads the website: company size, fit, and who is the best person to contact
    ref = analyzer.refine(row, site, people)
    if ref:
        with db.connect() as conn:
            db.save_refine(conn, row["id"], ref)

    # 7. Choose who to contact: GPT's pick, otherwise the rules (CTO > CEO > ...; fallback: job ad contact)
    if ref and ref["picked"]:
        chosen = [dict(p, why_person=f"GPT: {why}") for p, why in ref["picked"]]
    else:
        chosen = [dict(p, why_person=person_finder.RANK_LABEL.get(p["rank"], ""))
                  for p in ([p for p in people if p["decision_maker"]][:config.CONTACTS_PER_COMPANY] or people[:1])]
    contacts = []
    for p in chosen:
        r = email_finder.find(p, mail_domain, pattern, strength, domain_state)
        if r["status"] == "valid" and r["email"] and strength != "strong":
            # a confirmed email teaches us the format -> the next person costs fewer credits
            learned = email_finder.detect_pattern(r["email"], p["first"], p["last"])
            if learned:
                pattern, strength, proof = learned, "strong", f"{r['email']} confirmed for {p['name']}"
        how = r["how"] + (f" [{proof}]" if pattern and proof and "format" in r["how"] else "")
        contacts.append(dict(p, email=r["email"], email_status=r["status"], confidence=r["confidence"],
                             email_source=how, linkedin_search=linkedin_search(p["name"], name)))

    good = [c for c in contacts if c["decision_maker"] and c["email_status"] in ("valid", "catch-all")]
    status = ("contact_ready" if good else
              "contact_unverified" if any(c["email"] for c in contacts) else
              "no_person" if not people else "no_email")
    with db.connect() as conn:
        db.replace_contacts(conn, row["id"], contacts)
        db.set_company_people_result(conn, row["id"], pattern, generic, status)
    return {"company": name, "domain": domain, "how_site": how_site, "people": len(people),
            "pattern": pattern, "generic": generic, "contacts": contacts, "refine": ref}


def run(tiers=None, limit=None, redo=False, company=None):
    tiers = tiers or config.PEOPLE_TIERS
    limit = limit or config.PEOPLE_LIMIT
    with db.connect() as conn:
        rows = db.companies_for_people(conn, tiers, limit, redo=redo, name=company)
    if not rows:
        print("No companies to check. (Already done? Use --redo, or --company \"Name\".)")
        return

    print(f"Finding people + emails for {len(rows)} companies "
          f"(Hunter: {'on' if hunter.available() else 'off - ' + hunter.off_reason()}, "
          f"GPT: {'on' if llm.available() else 'off'})\n")
    domain_state = {}
    for i, row in enumerate(rows, 1):
        print(f"[{i}/{len(rows)}] {row['name']} ({row['score']} {row['tier']})")
        try:
            res = process(row, domain_state)
        except Exception as e:      # one broken website must not stop the run
            print(f"   error: {e}")
            continue
        print(f"   website: {res['domain'] or '-'}  ({res['how_site']})")
        if res.get("domain"):
            print(f"   people found: {res['people']}, email format: {res.get('pattern') or 'unknown'}"
                  + (f", company email: {res['generic']}" if res.get("generic") else ""))
        if res.get("refine"):
            r = res["refine"]
            icon = {"yes": "✅", "maybe": "🤔", "no": "❌"}.get(r["fit"], "")
            print(f"   GPT website check: {icon} {r['fit']}, size {r['size']}"
                  + (f' ("{r["size_quote"][:50]}")' if r["size_quote"] else "") + f": {r['fit_reason'][:80]}")
        for c in res["contacts"]:
            print(f"   -> {c['name']} ({c['role'][:40]}): {c['email'] or '-'}  [{c['email_status']}]"
                  + (f"  {c['why_person']}" if (c.get("why_person") or "").startswith("GPT") else ""))
        if not res["contacts"]:
            print("   -> no person found (try the LinkedIn search link in contacts.csv)")

    print(f"\nHunter used this run: {hunter.used['searches']} searches, {hunter.used['verifications']} verifications")
    print(llm.usage_summary())
    export()


def export():
    path = config.OUTPUT_DIR / "contacts.csv"
    with db.connect() as conn:
        companies = conn.execute(
            f"SELECT * FROM companies WHERE people_checked_at IS NOT NULL ORDER BY {db.FIT_ORDER}, score DESC, name"
        ).fetchall()
        rows = []
        for c in companies:
            sig = db.signals_for(conn, c["id"])
            best = sig[0] if sig else None
            base = {
                "company": c["name"], "score": c["score"], "tier": c["tier"],
                "company_email_format": c["email_pattern"] or "", "generic_email": c["generic_email"] or "",
                "website": c["website"] or "", "website_found_by": c["website_source"] or "",
                "gpt_fit": c["ai_fit"] or "", "what_they_build": c["ai_builds"] or "",
                "company_size": c["company_size"] or "", "size_proof": c["size_evidence"] or "",
                "why_company": c["why"], "job_proof": best["evidence"] if best else "",
                "job_link": best["url"] if best else "",
            }
            people = db.contacts_for(conn, c["id"])
            if not people:
                rows.append(dict(base, person="", role="", decision_maker="", email="", email_status="no person found",
                                 confidence=0, how_email_found="", person_found_in="", person_proof="",
                                 person_proof_link="", linkedin_search=linkedin_search("", c["name"], "CTO")))
            for p in people:
                rows.append(dict(base, person=p["name"], role=p["role"],
                                 decision_maker="yes" if p["is_decision_maker"] else "no",
                                 email=p["email"] or "", email_status=p["email_status"], confidence=p["confidence"],
                                 how_email_found=p["email_source"] or "", person_found_in=p["source"],
                                 person_proof=p["proof"], person_proof_link=p["proof_url"] or "",
                                 why_this_person=p["why_person"] or "",
                                 linkedin_search=p["linkedin_search"]))

    cols = ["company", "score", "tier", "gpt_fit", "company_size", "what_they_build", "person", "role",
            "why_this_person", "decision_maker", "email", "email_status", "confidence",
            "how_email_found", "person_found_in", "person_proof", "person_proof_link", "company_email_format",
            "generic_email", "website", "website_found_by", "linkedin_search", "why_company", "job_proof", "job_link"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in cols})

    ready = sum(1 for r in rows if r["decision_maker"] == "yes" and r["email_status"] == "valid")
    catch = sum(1 for r in rows if r["decision_maker"] == "yes" and r["email_status"] == "catch-all")
    print(f"\n{len(companies)} companies checked: {ready} decision makers with a VALID email, "
          f"{catch} with a catch-all email (best guess).")
    print(f"Contacts: {path}")
