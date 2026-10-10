"""
Part 3b: the dashboard where YOU decide what gets sent.

    streamlit run dashboard.py

1. Review   read each draft next to its proof, edit it, approve or skip
2. Send     send the approved emails (test run first)
3. Replies  mark who replied -> the agent learns which signals work
4. Results  reply rate per signal (answers Max's question: which signals predict customers?)
"""

import io
from contextlib import redirect_stdout

import streamlit as st

import config
import database as db
import json

import analyzer
import followups
import replies
import sender
import writer

st.set_page_config(page_title="Flocta Lead Agent", page_icon="📬", layout="wide")
db.init()


def run_and_show(fn, *args, **kwargs):
    buf = io.StringIO()
    with redirect_stdout(buf):
        out = fn(*args, **kwargs)
    st.code(buf.getvalue() or "done")
    return out


def proof_block(conn, m):
    st.markdown(f"**{m['company']}** · score {m['score']} {m['tier']}")
    st.caption(m["why"] or "")
    comp = conn.execute("SELECT company_size, size_evidence, web_reason FROM companies WHERE id = ?",
                        (m["company_id"],)).fetchone()
    if comp and comp["company_size"]:
        st.markdown(f"**Size:** {comp['company_size']}" + (f" (“{comp['size_evidence']}”)" if comp["size_evidence"] else ""))
    if comp and comp["web_reason"]:
        st.caption(f"Website check: {comp['web_reason']}")
    brief = conn.execute("SELECT brief FROM companies WHERE id = ?", (m["company_id"],)).fetchone()
    if brief and brief["brief"]:
        b = json.loads(brief["brief"])
        # (no nested expander: older Streamlit versions don't allow it)
        st.markdown("**📝 Account brief**  \n"
                    f"<small>**What they do:** {b['what_they_do']}<br>**AI use:** {b['ai_use']}<br>"
                    f"**Tech in their ads:** {', '.join(b['stack']) or '-'}<br>**Where Flocta helps:** {b['pain']}<br>"
                    f"**Angle:** {b['angle']}<br>**Opener:** {b['opener']}<br>"
                    + "".join(f"• {q}<br>" for q in b.get("questions", [])) + "</small>", unsafe_allow_html=True)
    if m["ai_fit"]:
        icon = {"yes": "✅", "maybe": "🤔", "no": "❌"}.get(m["ai_fit"], "")
        st.markdown(f"**GPT:** {icon} fit **{m['ai_fit']}**, AI spend **{m['ai_spend']}**: {m['ai_builds']}  \n"
                    f"<small>{m['ai_reason']}</small>", unsafe_allow_html=True)
        if m["ai_evidence"]:
            st.markdown(f"> {m['ai_evidence']}")
    shown = set()
    for s in db.signals_for(conn, m["company_id"]):
        if s["type"] in shown:
            continue
        shown.add(s["type"])
        st.markdown(f"- **{s['label']}**: “{s['evidence'][:200]}” · [job ad]({s['url']})")
    ct = conn.execute("SELECT * FROM contacts WHERE lower(email) = lower(?) ORDER BY id DESC LIMIT 1",
                      (m["to_email"],)).fetchone()
    if ct:
        link = f" · [source]({ct['proof_url']})" if ct["proof_url"] else ""
        st.markdown(f"**Person:** {ct['name']}, {ct['role']} (found in {ct['source']}{link})"
                    + (f"  \n*Why this person: {ct['why_person']}*" if ct["why_person"] else ""))
        st.markdown(f"**Email:** `{ct['email']}`: **{ct['email_status']}**, confidence {ct['confidence']}  \n"
                    f"<small>{ct['email_source']}</small>", unsafe_allow_html=True)
        if ct["linkedin_search"]:
            st.markdown(f"[Search on LinkedIn]({ct['linkedin_search']})")


# ------------------------------------------------------------
with db.connect() as conn:
    n = db.counts(conn)
    by_status = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) n FROM messages GROUP BY status")}
    today = db.sent_today(conn)
    n_replies = conn.execute("SELECT COUNT(*) FROM messages WHERE outcome IN ('replied','meeting')").fetchone()[0]

st.title("📬 Flocta Lead Agent")
c = st.columns(6)
c[0].metric("Hot / warm (GPT fit)", f"{n['hot']} / {n['warm']} ({n['fit_yes']})")
c[1].metric("Valid emails", n["valid_emails"])
c[2].metric("Drafts", by_status.get("draft", 0))
c[3].metric("Approved", by_status.get("approved", 0))
c[4].metric("Sent today", f"{today} / {config.MAX_SENDS_PER_DAY}")
c[5].metric("Replies", n_replies)

with st.sidebar:
    st.header("Write drafts")
    limit = st.number_input("How many", 1, 100, 10)
    if st.button("✍️ Write new drafts"):
        run_and_show(writer.write, limit=int(limit))
    if st.button("📝 Write account briefs (GPT)"):
        run_and_show(analyzer.briefs)
    if st.button("🔁 Write follow-ups"):
        run_and_show(followups.write)
    if st.button("📥 Check inbox for replies"):
        run_and_show(replies.check)
    st.caption("Uses GPT if OPENAI_API_KEY is set, otherwise a template.")
    st.divider()
    st.caption(f"Sending to: {', '.join(config.SEND_TO_STATUSES)} emails · max {config.MAX_SENDS_PER_DAY}/day")
    if "(Max: add" in config.EMAIL_FOOTER:
        st.warning("Fill in EMAIL_FOOTER in config.py (company details) before sending.")

tab_review, tab_send, tab_sent, tab_results = st.tabs(["1. Review drafts", "2. Send", "3. Replies", "4. What works"])

# ---------------- 1. Review
with tab_review:
    with db.connect() as conn:
        drafts = db.messages(conn, "draft")
    if not drafts:
        st.info("No drafts. Click 'Write new drafts' on the left (or run: python run.py write).")
    for m in drafts:
        tag = "🔁 FOLLOW-UP · " if (m["kind"] or "first") == "followup" else ""
        tag += f"⭐ {m['quality']}/10 · " if m["quality"] else ""
        with st.expander(f"{tag}{m['company']} · {m['to_name']} ({m['to_role']}) · {m['to_email']}", expanded=False):
            left, right = st.columns([2, 3])
            with left:
                with db.connect() as conn:
                    proof_block(conn, m)
            with right:
                subject = st.text_input("Subject", m["subject"], key=f"s{m['id']}")
                body = st.text_area("Email", m["body"], height=330, key=f"b{m['id']}")
                st.caption("Added when sending:\n\n" + config.EMAIL_FOOTER)
                if m["review_notes"]:
                    st.caption(f"GPT review: {m['review_notes']}")
                if m["linkedin"]:
                    st.markdown("**LinkedIn note** (copy it, send it yourself on LinkedIn):")
                    st.code(m["linkedin"], language=None)
                b1, b2, b3, b4 = st.columns(4)
                if b1.button("✅ Approve", key=f"a{m['id']}"):
                    with db.connect() as conn:
                        db.update_message(conn, m["id"], subject=subject, body=body, status="approved")
                    st.rerun()
                if b2.button("💾 Save", key=f"v{m['id']}"):
                    with db.connect() as conn:
                        db.update_message(conn, m["id"], subject=subject, body=body)
                    st.rerun()
                if b3.button("⏭ Skip", key=f"k{m['id']}"):
                    with db.connect() as conn:
                        db.update_message(conn, m["id"], status="skipped")
                    st.rerun()
                if b4.button("🚫 Never contact", key=f"n{m['id']}"):
                    sender.add_do_not_contact(m["to_email"], "set in dashboard")
                    with db.connect() as conn:
                        db.update_message(conn, m["id"], status="skipped", error="do not contact")
                    st.rerun()

# ---------------- 2. Send
with tab_send:
    with db.connect() as conn:
        approved = db.messages(conn, "approved")
    if not approved:
        st.info("Nothing approved yet.")
    else:
        st.write(f"**{len(approved)} approved emails:**")
        for m in approved:
            cols = st.columns([4, 1])
            cols[0].markdown(f"- {m['company']}: {m['to_name']} `{m['to_email']}`: *{m['subject']}*"
                             + (f"  \n  ⚠️ {m['error']}" if m["error"] else ""))
            if cols[1].button("Back to draft", key=f"d{m['id']}"):
                with db.connect() as conn:
                    db.update_message(conn, m["id"], status="draft")
                st.rerun()
        if st.button("🧪 Test run (sends nothing)"):
            run_and_show(sender.send, dry_run=True)
        sure = st.checkbox("I have read these emails and Max agreed to send them.")
        if st.button("📤 Send now", disabled=not sure, type="primary"):
            with st.spinner("Sending (with pauses between emails)..."):
                run_and_show(sender.send)

# ---------------- 3. Replies
with tab_sent:
    with db.connect() as conn:
        sent = db.messages(conn, "sent")
    if not sent:
        st.info("No emails sent yet.")
    for m in sent:
        cols = st.columns([4, 2, 1])
        reply = (f"  \n💬 **{m['outcome']}** ({m['outcome_by']}): {m['reply_summary']}" if m["reply_summary"] else "")
        cols[0].markdown(f"**{m['company']}**: {m['to_name']} `{m['to_email']}`  \n"
                         f"<small>sent {str(m['sent_at'])[:16]} · {m['subject']}</small>{reply}", unsafe_allow_html=True)
        if m["reply_text"]:
            with cols[0].expander("Their reply"):
                st.text(m["reply_text"])
        current = m["outcome"] if m["outcome"] in sender.OUTCOMES else "no reply"
        choice = cols[1].selectbox("Outcome", sender.OUTCOMES, index=sender.OUTCOMES.index(current),
                                   key=f"o{m['id']}", label_visibility="collapsed")
        if cols[2].button("Save", key=f"os{m['id']}"):
            with db.connect() as conn:
                db.update_message(conn, m["id"], outcome=choice, outcome_by="you")
            if choice in ("not interested", "bounced"):
                sender.add_do_not_contact(m["to_email"], choice)
            st.rerun()

# ---------------- 4. Results
with tab_results:
    st.write("Which signals lead to replies? Mark replies in tab 3, then check here. "
             "This tells Max which signals really predict customers.")
    with db.connect() as conn:
        res = sender.results_by_signal(conn)
    if res:
        st.dataframe(res, hide_index=True)
    else:
        st.info("No sent emails yet.")
