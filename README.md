# Flocta Lead Agent

Finds European companies that are hiring for AI jobs (so they are paying for AI), then finds the right person there and their email. Built for [Flocta](https://flocta.com).

```
Part 1 (ready)   job ads → AI signals → score companies           python run.py
Part 2 (ready)   website → decision maker → email (tested)         python run.py people
Part 3 (ready)   GPT drafts the email → you approve → send          streamlit run dashboard.py
```

## Setup

```bash
cd flocta-gtm-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then put your keys in .env
```

Keys in `.env`:

| Key | Needed for | Where to get it |
|---|---|---|
| `HUNTER_API_KEY` | testing emails (Part 2) | hunter.io → API. Free: 25 searches + 50 tests per month |
| `OPENAI_API_KEY` | GPT analysis of companies, reads team pages (Part 2), writes emails (Part 3) | platform.openai.com |
| `OPENAI_MODEL` / `OPENAI_MODEL_CHEAP` | which GPT model (e.g. `gpt-6-luna`) | names you have: see platform.openai.com → Models |

Part 2 also works **without** keys. Emails are then marked `unverified`.

## Part 1: find companies

```bash
python run.py                 # collect job ads + score (about 30-60 min the first time)
python run.py rescan          # re-check saved ads after changing signals.py (seconds)
python run.py --days 14       # look back 14 days
```

Sources: Bundesagentur für Arbeit, Arbeitnow, and the company job pages in `watchlist.csv`.

### GPT analysis (after scoring)

```bash
python run.py analyze              # GPT reads the hot + warm companies (also part of 'python run.py')
python run.py analyze --redo       # analyze again
```

The rules find words ("LLM", "RAG"). GPT reads the job ads and understands what they mean. For each hot or warm company, it gives:

- **What they build with AI** (one line)
- **How they use AI**: product / internal / consulting / research
- **AI spend**: high / medium / low, with the reason
- **Fit for Flocta**: yes / maybe / no, with the reason
- **Quote** from their job ad. The code checks it is really in the ad, and invented quotes are removed.

Cost control: only hot and warm companies, max 200 per run (`ANALYZE_LIMIT`), only the useful sentences are sent, and each company is analyzed only once. Part 2 and 3 start with "yes" companies and skip "no" companies (`PEOPLE_SKIP_NO_FIT`). The score itself stays pure math.

| Signal (found in the job ad) | Points |
|---|---|
| Hiring for an AI role (job title) | 30 |
| Worries about AI costs ("Kosten je Token", "model routing") | 40 |
| Builds AI into its product (LLM, RAG, OpenAI API) | 25 |
| Staff use AI tools daily (ChatGPT, Copilot) | 10 |
| Mentions AI | 5 |

+5 per extra AI job (max 15), +5 for EU data rules. Hot = 65+, warm = 40+.

## Part 2: find the person and their email

```bash
python run.py people --limit 5          # start small: top 5 companies
python run.py people                    # hot + warm companies, 25 per run
python run.py people --company "Bliro"  # one company
python run.py people --redo             # check again
python run.py credits                   # Hunter credits left + GPT tokens used
```

For each company:

1. **Website**: from links/emails in its job ads, then a **GPT web search** that knows the context (what they do, the job ad), then smart guesses (bliro.com, bliro.ai …), then Hunter. Every site is checked by the code: the company name must be on it, and it must be active in Germany/Europe. Short names like "Flip" need the full name ("Flip GmbH") on the site. Wrong? Write it in `websites.csv`.
2. **People**: reads Impressum (German law: must name the Geschäftsführer), team and about pages. GPT helps when no tech leader is found. Names GPT invents are thrown away. Also reads contact persons in the job ads, and people Hunter knows.
3. **Decision maker**: 1) CTO / Head of AI / Engineering, 2) CEO / founder / Geschäftsführer, 3) other leaders. The recruiter is only a fallback.
4. **Email format**: learned from real addresses (website, job ads, Hunter), e.g. `lisa.hoffmann@` + "Lisa Hoffmann" → `{first}.{last}`.
5. **Email test**: builds about 20 variants (format first), Hunter tests them one by one, stops at the first `valid`.

| Email status | Meaning | What to do |
|---|---|---|
| `valid` | Mailbox exists (Hunter checked) | Safe to send |
| `catch-all` | Server accepts every address, so this is the best guess | Send carefully |
| `unverified` | Not tested (no key / no credits) | Test before sending |
| `unknown` | Mail server did not answer | Try again later |
| `not found` | All variants rejected | Use LinkedIn link or company email |

Saving credits: Hunter answers are stored in the database (never paid twice). When the format is known for sure, only 5 variants are tested instead of 20. Run limits are in `config.py`.

Result: **`output/contacts.csv`**, one row per person: email, status, confidence, how it was found, where the person was found (with link), LinkedIn search link, and the job ad proof.

## Part 3: write, approve, send

```bash
python run.py write              # drafts for every decision maker with a valid / catch-all email
streamlit run dashboard.py       # review, edit, approve, send, mark replies
python run.py send --dry-run     # what would be sent (sends nothing)
python run.py send               # send APPROVED emails only
python run.py replies            # read replies from Gmail, GPT sorts them
python run.py followup           # follow-up drafts for people who didn't answer
python run.py results            # reply rate per signal
```

**Replies** (`replies.py`): reads your Gmail inbox (read-only, IMAP must be on in Gmail settings) and finds answers from people you emailed, plus bounces. GPT sorts each reply: meeting / replied / not interested / out of office / wrong person, with a one-line summary. "Not interested" and bounces go to `do_not_contact.csv`. Outcomes you set by hand are never overwritten.

**Follow-ups** (`followups.py`): after `FOLLOWUP_AFTER_DAYS` (6) days with no answer, GPT writes one short follow-up with a new angle. You approve it, and it's sent in the same email thread. Max `MAX_TOUCHES` (2) emails per person, and none after any real answer.

**Writing:** each draft uses only facts. That means their own job ad (title and the signal sentence), the person's name and role, and `FLOCTA_FACTS` in `config.py`. GPT writes it in the language of their job ad (German with "du", or English). The code then rejects drafts with placeholders, a missing name, links, or **numbers that aren't in the facts**. Without GPT, a template is used.

**Dashboard:**
1. **Review**: draft next to its proof (job ad quote + links, where the person was found, email status). Edit it, then approve, skip or block it ("never contact").
2. **Send**: test run first, then send. Tick "I have read these emails and Max agreed" to enable the button.
3. **Replies**: mark each sent email: no reply / replied / meeting / not interested / bounced. "Not interested" and "bounced" go to `do_not_contact.csv` automatically.
4. **What works**: reply rate per signal. Over time this tells Max which signals really find customers.

**Safety rules when sending:**
- Only approved emails, and only `valid` addresses (add `catch-all` to `SEND_TO_STATUSES` if Max agrees).
- Never twice to the same address. Never to `do_not_contact.csv`.
- Max 20 per day, with a random pause of 45–120 s between emails.
- Sending is blocked until `EMAIL_FOOTER` in `config.py` has Flocta's company details (required for German GmbH business emails).
- Every email has a one-line opt-out and an unsubscribe header.

Gmail: use an **App Password** (Google account → Security → 2-Step Verification → App passwords). Put `EMAIL_ADDRESS`, `EMAIL_APP_PASSWORD` and `SENDER_NAME` in `.env`.

## Where GPT is used

| # | Command | File | What GPT does | Checked by code |
|---|---|---|---|---|
| 1 | `analyze` | `analyzer.py` | Hot + warm companies: what they build, AI spend, fit | Quote must be in the ad |
| 2 | `learn` | `learn.py` | Suggests new signal phrases | The code tests each phrase on all saved ads (✅ vs ❌) |
| 3 | `people` | `website_finder.py` | Web search for the official website | Name on site + active in Europe |
| 4 | `people` | `analyzer.py` (refine) | Website: company size, fit, best person to contact | Size quote must be on the site |
| 5 | `people` | `person_finder.py` | Names on messy team pages | Name must be on the page; no "… at other company" |
| 6 | `brief` | `analyzer.py` | Account brief for each ✅ company (`output/briefs.md`) | Tech list comes from the ads, not from GPT |
| 7 | `write` | `writer.py` | Email + LinkedIn note | No invented numbers, placeholders, links; note ≤ 300 characters |
| 8 | `write` | `writer.py` (review) | Rates each email 1–10, rewrites weak ones | Rewrites with new numbers or claims are rejected |
| 9 | `replies` | `replies.py` | Sorts replies | Rules as backup; your own choice is never overwritten |
| 10 | `followup` | `followups.py` | Short second email | Same checks as 7 |

Not GPT (Python / Hunter): collecting job ads, filtering, scoring, guessing and checking emails, sending. See the cost anytime with `python run.py credits`.

**Cost control:** `GPT_TOKEN_BUDGET_PER_RUN` (400,000) stops GPT when a run used that many tokens. `GPT_REASONING_EFFORT = "low"` cuts hidden "thinking" tokens. `OPENAI_MODEL_CHEAP` (e.g. `gpt-5.4-mini`) is used for simple jobs: screening, reading team pages, sorting replies. Every result is saved, so nothing is paid twice.

### Self-improving signals

```bash
python run.py learn            # GPT suggests phrases, the code measures them -> output/signal_suggestions.csv
python run.py learn --apply    # add the good ones to custom_signals.csv
python run.py rescan           # re-score all saved ads with the new phrases
```

A phrase is only kept if most companies it matches are rated ✅ by GPT and not more ❌ than ✅. You can also add your own phrases to `custom_signals.csv` (`type,phrase,note`; types: `builds_with_ai`, `cost_pain`, `daily_ai_use`, `hires_ai_role`).

## Output

| File | What |
|---|---|
| `output/report.md` | Ranked companies with proof (+ contacts) |
| `output/leads.csv` | All companies as a table |
| `output/contacts.csv` | Part 2: people + emails |
| `output/drafts.md` | Part 3: all drafts |
| `output/watchlist_suggestions.csv` | Copy rows into `watchlist.csv` to check those job pages every run |

## Project structure

```
config.py            all settings
database.py          SQLite (data/leads.db)
collectors/          job sources (Part 1)
signals.py           AI signals with proof (Part 1)
scorer.py            scores (Part 1)
analyzer.py          GPT: analysis, website check, account briefs
learn.py             GPT teaches the word rules (self-improving signals)
custom_signals.csv   extra signal phrases (learned or your own)
website_finder.py    company website (Part 2)
site_reader.py       reads websites politely, finds emails (Part 2)
person_finder.py     people + roles + decision maker (Part 2)
email_finder.py      email format + variants + test (Part 2)
hunter.py            Hunter API with cache (Part 2)
llm.py               GPT with token log
contacts.py          runs Part 2
writer.py            email drafts from evidence (Part 3)
replies.py           reads + sorts replies from Gmail (Part 3)
followups.py         follow-up drafts (Part 3)
sender.py            sends approved emails, tracks replies (Part 3)
dashboard.py         review + approve + send (Part 3)
do_not_contact.csv   emails / domains never to contact
run.py               start everything here
```

## Rules

- `.env` and `data/` are never uploaded (`.gitignore`).
- No scraping of LinkedIn, Indeed or StepStone. The LinkedIn column is only a search link for you to click.
- Company websites: robots.txt is respected, max 8 pages per site, 1 s between requests.
- Emails are tested through Hunter, never by connecting to mail servers from your computer.
- If a person asked Hunter not to be processed (GDPR), they are skipped.
- **Nothing is sent without your approval.** In Germany, cold advertising emails need prior consent (UWG §7), also B2B. Max decides who may be emailed. Use the drafts for LinkedIn messages or calls too.
