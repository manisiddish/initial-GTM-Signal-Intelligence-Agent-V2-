"""
All settings in one place.

Secrets (API keys) come from the .env file.
Everything else (keywords, points, limits) can be changed here.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

# ------------------------------------------------------------
# Paths
# ------------------------------------------------------------
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"
DB_PATH = DATA_DIR / "leads.db"
WATCHLIST_PATH = ROOT / "watchlist.csv"

DATA_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

# ------------------------------------------------------------
# Secrets (Part 2 + 3)
# ------------------------------------------------------------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-6-luna").strip()
OPENAI_MODEL_CHEAP = os.getenv("OPENAI_MODEL_CHEAP", "gpt-6-luna").strip()
OPENAI_SEARCH_MODEL = os.getenv("OPENAI_SEARCH_MODEL", "gpt-5-search-api").strip()  # backup for web search
GPT_TOKEN_BUDGET_PER_RUN = 400_000   # safety stop: GPT switches off when one run used this many tokens
GPT_REASONING_EFFORT = "low"         # less hidden "thinking" = far fewer output tokens ("" = model default)
HUNTER_API_KEY = os.getenv("HUNTER_API_KEY", "").strip()
EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS", "").strip()
EMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD", "").replace(" ", "").strip()
SENDER_NAME = os.getenv("SENDER_NAME", "").strip()        # e.g. "Shashank Reddy"
SENDER_ROLE = os.getenv("SENDER_ROLE", "Flocta").strip()   # e.g. "Growth, Flocta"

# ------------------------------------------------------------
# Collecting job ads
# ------------------------------------------------------------
DAYS_BACK = 7                 # only job ads from the last N days
REQUEST_PAUSE = 1.5           # seconds between requests (be polite)
ARBEITNOW_PAGES = 30          # pages to read from Arbeitnow

# Words searched at the Bundesagentur für Arbeit (German national job board)
BA_SEARCH_WORDS = [
    # AI roles
    "AI Engineer", "KI Engineer", "KI-Entwickler", "AI Developer", "Machine Learning Engineer",
    "ML Engineer", "LLM Engineer", "Prompt Engineer", "MLOps", "Data Scientist KI",
    "Computer Vision", "NLP", "Deep Learning", "AI Architect", "KI Architekt",
    "Head of AI", "AI Product Manager", "KI Manager", "KI Spezialist", "AI Specialist",
    "Conversational AI", "AI Researcher", "Applied Scientist", "Werkstudent KI",
    # AI topics
    "LLM", "Generative AI", "Generative KI", "GenAI", "Künstliche Intelligenz",
    "ChatGPT", "OpenAI", "RAG", "AI Agents", "KI-Agenten",
]
BA_PAGE_SIZE = 50
BA_MAX_PAGES = 4            # up to 200 ads per search word

# ------------------------------------------------------------
# Scoring
# ------------------------------------------------------------
# Points per signal type (each type counts once per company)
SIGNAL_POINTS = {
    "hires_ai_role": 30,    # job TITLE is an AI role -> definitely building with AI
    "cost_pain": 40,        # worries about AI costs  -> Flocta's exact problem
    "builds_with_ai": 25,   # builds AI into its product -> pays an API bill
    "daily_ai_use": 10,     # staff use AI tools daily -> many seats
    "mentions_ai": 5,       # mentions AI in general   -> weak alone
}
# Only keep companies that hire for an AI role (or mention AI costs)
AI_ROLES_ONLY = True

EXTRA_JOB_POINTS = 5        # per extra AI role
EXTRA_JOB_MAX = 15
EU_DATA_POINTS = 5          # mentions DSGVO / GDPR / EU hosting
OLD_SIGNAL_DAYS = 30        # signals older than this count half

# Hot = hires for an AI role AND builds AI into its product, plus more (65+),
#       or worries about AI costs
HOT_SCORE = 65
WARM_SCORE = 40

# ------------------------------------------------------------
# Which companies to keep
# ------------------------------------------------------------
# True  = keep EVERY company hiring for AI roles. Big companies, public sector and
#         consultancies get a label but are NOT removed.
# False = remove big companies / public sector, count consultancies half.
# Job boards, recruitment agencies and data errors are always removed
# (they are not the company that is hiring).
KEEP_ALL_COMPANIES = True

# ------------------------------------------------------------
# Company lists
# ------------------------------------------------------------
# Flocta itself and its investors
EXCLUDE_COMPANIES = ["flocta", "atlantic.vc", "atlantic labs", "atlantic ventures", "index ventures"]

# Job boards / aggregators that re-post other companies' jobs
JOB_BOARDS = [
    "jobgether", "remotive", "jooble", "himalayas", "remoteok", "working nomads",
    "wellfound", "arc.dev", "toptal", "turing", "crossover", "braintrust", "honeypot",
    "landing.jobs", "stepstone", "indeed", "glassdoor", "kununu", "join.com", "jobot",
    "talent.io", "hired", "dice", "ziprecruiter", "workana", "malt", "freelance",
]

# Too big for Flocta (target: 10-500 employees) or AI labs that run their own models.
# Add names here when you spot them in the report.
TOO_BIG_OR_AI_LAB = [
    "gitlab", "intercom", "pipedrive", "kraken", "elevenlabs", "deepl", "aleph alpha",
    "mistral", "openai", "anthropic", "google", "microsoft", "amazon", "meta", "apple",
    "sap", "siemens", "zalando", "delivery hero", "hellofresh", "n26", "celonis",
    "personio", "spotify", "booking", "adyen", "klarna", "revolut", "wise", "bosch",
    "deutsche telekom", "telekom", "allianz", "bmw", "mercedes", "volkswagen", "porsche",
    "accenture", "capgemini", "deloitte", "pwc", "kpmg", "ibm", "oracle", "salesforce",
    "bumble", "bumble inc", "crypto.com", "coinbase", "binance", "uber", "airbnb", "netflix",
    "tiktok", "bytedance", "shopify", "stripe", "canva", "atlassian", "datadog", "cloudflare",
    "mongodb", "hubspot", "zendesk", "dropbox", "twilio", "paypal", "ebay", "nvidia", "intel",
    "samsung", "sony", "philips", "ericsson", "nokia", "spotify", "trade republic", "flix",
    "auto1", "check24", "otto", "lidl", "aldi", "rewe", "dhl", "deutsche bahn", "lufthansa",
]

# Public sector, universities, research institutes: not customers (word anywhere in the name)
PUBLIC_SECTOR_WORDS = [
    "universität", "universitaet", "university", "hochschule", "fachhochschule", "rechenzentrum",
    "institut", "institute", "fraunhofer", "max planck", "max-planck", "helmholtz", "leibniz",
    "forschungszentrum", "bundesamt", "bundesagentur", "ministerium", "landesamt", "stadt ",
    "landkreis", "kommune", "behörde", "klinikum", "universitätsklinikum", "dlr", "lrz",
]

# A company with this many AI job ads is probably large -> score lowered
MANY_JOBS_LIMIT = 12

# ------------------------------------------------------------
# GPT analysis of the best companies (after scoring)
# ------------------------------------------------------------
ANALYZE_TIERS = ["HOT", "WARM"]   # which companies GPT reads
ANALYZE_LIMIT = 200               # max companies per run (cost limit); each company only once
PEOPLE_SKIP_NO_FIT = True         # Part 2 + 3 skip companies GPT marked "no fit" (saves Hunter credits)
BRIEF_FITS = ["yes"]              # account briefs for these GPT fits (add "maybe" for more)
USE_GPT_REVIEW = True             # a second GPT pass rates every email draft (1-10) and improves weak ones
REVIEW_MIN_SCORE = 7              # drafts below this score get rewritten by the reviewer
CUSTOM_SIGNALS_PATH = ROOT / "custom_signals.csv"   # extra signal phrases (from 'python run.py learn')

# ------------------------------------------------------------
# Part 2: website -> people -> email
# ------------------------------------------------------------
PEOPLE_TIERS = ["HOT", "WARM"]   # which companies get Part 2
PEOPLE_LIMIT = 25                # companies per run (best scores first)
CONTACTS_PER_COMPANY = 2         # decision makers to keep per company
MAX_PAGES_PER_SITE = 8           # pages read per company website
WEBSITES_PATH = ROOT / "websites.csv"   # fix a wrong website by hand: company,website
USE_GPT_WEB_SEARCH = True        # GPT searches the web for a company's official website (small cost per company)

# Email guessing (Max: "guess about 20 variants and test which one works")
MAX_EMAIL_GUESSES = 20           # variants tested per person, stops at the first valid one
MAX_GUESSES_FORMAT_KNOWN = 5     # if the company's format is known for sure and fails, test only a few more
HUNTER_MAX_VERIFICATIONS_PER_RUN = 150  # safety limit for your Hunter credits
HUNTER_MAX_SEARCHES_PER_RUN = 25        # domain searches / email finder (also credits)
USE_HUNTER_DOMAIN_SEARCH = True  # ask Hunter which emails it knows for the domain (1 search)
USE_HUNTER_EMAIL_FINDER = True   # ask Hunter for a person's email if no format is known (1 search)
USE_GPT_FOR_PEOPLE = True        # GPT reads team/about pages (only if OPENAI_API_KEY is set)
USE_GPT_REFINE = True            # GPT reads the website: company size, better fit, best person to contact

# ------------------------------------------------------------
# Part 3: write -> approve -> send
# ------------------------------------------------------------
# What Flocta does. GPT may ONLY use these facts (ask Max to check the wording).
FLOCTA_FACTS = [
    "Flocta reduces the cost of LLM / AI API calls by up to 35%.",
    "How: semantic caching (similar questions are answered from cache), context compression "
    "(shorter prompts), and routing simple requests to cheaper models.",
    "Hosted in the EU (GDPR).",
    "Built for European small and mid-sized companies.",
    "Based in Berlin.",
]
WRITE_LIMIT = 30                  # drafts per run
WRITE_TO_STATUSES = ["valid", "catch-all"]   # which emails get a draft
SEND_TO_STATUSES = ["valid"]      # which emails may be SENT (add "catch-all" if Max agrees)
MAX_SENDS_PER_DAY = 20            # stay far below Gmail limits and look human
SEND_PAUSE_SECONDS = (45, 120)    # random pause between two emails
DO_NOT_CONTACT_PATH = ROOT / "do_not_contact.csv"   # emails or domains never to contact
# Footer under every email. German companies (GmbH) must show these details in business emails.
# Replies + follow-ups
FOLLOWUP_AFTER_DAYS = 6          # write a follow-up draft if no reply after this many days
MAX_TOUCHES = 2                  # first email + 1 follow-up, never more
IMAP_HOST = "imap.gmail.com"     # where replies are read (same Gmail + App Password)
EMAIL_FOOTER = """--
Flocta GmbH · Berlin
(Max: add Registergericht, HRB number and Geschäftsführer here)"""

# Consultancies / agencies build AI for clients: partners, not direct customers
CONSULTANCY_WORDS = [
    "consulting", "consultancy", "beratung", "berater", "agentur", "agency",
    "sfeir", "isolutions", "valtech", "adesso", "msg systems", "netlight", "thoughtworks",
    "endava", "epam", "globant", "sopra", "atos", "cgi", "tietoevry", "reply",
]
