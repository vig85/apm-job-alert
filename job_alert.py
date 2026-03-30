#!/usr/bin/env python3
"""
APM & Product Intern Job Alert
Monitors LinkedIn, Workable, and Greenhouse for new roles.
Sends a Telegram message the moment a match is found.

Usage:
  python job_alert.py          # normal run (alerts on new jobs)
  python job_alert.py --seed   # first run: index existing jobs WITHOUT alerting
"""

import os
import sys
import json
import re
import time
import logging
import hashlib
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

# ── Config ────────────────────────────────────────────────────────────────────
BOT_TOKEN  = os.environ["TELEGRAM_BOT_TOKEN"]   # from @BotFather
CHAT_ID    = os.environ["TELEGRAM_CHAT_ID"]     # your personal chat ID

STATE_FILE      = Path(os.environ.get("STATE_FILE", "seen_jobs.json"))
REQUEST_TIMEOUT = 15   # seconds per HTTP call

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# ── Keyword matching ──────────────────────────────────────────────────────────
# A title must match at least one INCLUDE pattern and zero EXCLUDE patterns.

INCLUDE = [
    re.compile(r"\bassociate\s+product\s+manager\b", re.I),
    re.compile(r"\bproduct\s+management\s+intern(ship)?\b", re.I),
    re.compile(r"\bproduct\s+manager\s+intern(ship)?\b", re.I),
    re.compile(r"\bpm\s+intern(ship)?\b", re.I),
    # "APM" alone — but NOT when followed by words that signal App Perf Monitoring
    re.compile(
        r"\bapm\b(?!\s*[-–]?\s*(engineer|developer|tool|platform|monitor|"
        r"performance|stack|agent|ops|specialist|analyst|retrieval|"
        r"ingestion|pipeline|tracing|infrastructure|backend|data))",
        re.I,
    ),
]

EXCLUDE = [
    re.compile(r"\bproduct\s+design\s+intern\b", re.I),
    re.compile(r"\bproduct\s+engineer(ing)?\s+intern\b", re.I),
    re.compile(r"\bproduct\s+marketing\s+intern\b", re.I),
    re.compile(r"\bapplication\s+performance\b", re.I),
    re.compile(r"\bengineering\b.{0,30}\bapm\b", re.I),   # "Engineering - APM ..."
    re.compile(r"\bsenior\s+(associate\s+)?product\s+manager\b", re.I),  # skip senior roles
    re.compile(r"\bdirector\b", re.I),
    re.compile(r"\bvp\b", re.I),
    re.compile(r"\bhead\s+of\b", re.I),
]


def is_match(title: str) -> bool:
    if not title:
        return False
    if any(p.search(title) for p in EXCLUDE):
        return False
    return any(p.search(title) for p in INCLUDE)


# ── Cross-platform deduplication ──────────────────────────────────────────────
def _fingerprint(job: dict) -> str:
    """Stable title+company key for cross-platform dedup within a single run.
    Prevents alerting twice if the same role appears on LinkedIn AND Greenhouse."""
    title   = re.sub(r'\s+', ' ', job.get("title",   "").lower().strip())
    company = re.sub(r'\s+', ' ', job.get("company", "").lower().strip())
    # Strip common suffixes that vary across platforms
    company = re.sub(r'\s*(inc\.?|llc\.?|ltd\.?|corp\.?|co\.?)$', '', company)
    return f"{title}|{company}"


def dedup_across_platforms(jobs: list[dict]) -> list[dict]:
    """Keep only the first occurrence of each title+company combination.
    Prefers LinkedIn (direct apply link) > Workable > Greenhouse ordering."""
    seen_fp : set  = set()
    result  : list = []
    for job in jobs:
        fp = _fingerprint(job)
        if fp not in seen_fp:
            seen_fp.add(fp)
            result.append(job)
    return result


# ── State ─────────────────────────────────────────────────────────────────────
def load_seen() -> set:
    if STATE_FILE.exists():
        try:
            return set(json.loads(STATE_FILE.read_text()))
        except Exception:
            return set()
    return set()


def save_seen(seen: set):
    STATE_FILE.write_text(json.dumps(sorted(seen), indent=2))


# ── Telegram ──────────────────────────────────────────────────────────────────
def _escape(text: str) -> str:
    """Escape MarkdownV2 special characters."""
    for ch in r"\_*[]()~`>#+-=|{}.!":
        text = text.replace(ch, f"\\{ch}")
    return text


def send_telegram(job: dict):
    title    = _escape(job["title"])
    company  = _escape(job["company"])
    location = _escape(job.get("location") or "Remote / Not specified")
    source   = _escape(job["source"])
    url      = job["url"]

    text = (
        f"*{title}*\n"
        f"🏢 {company}\n"
        f"📍 {location}\n"
        f"[Apply →]({url})\n"
        f"_via {source}_"
    )
    resp = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={
            "chat_id":                  CHAT_ID,
            "text":                     text,
            "parse_mode":               "MarkdownV2",
            "disable_web_page_preview": False,
        },
        timeout=10,
    )
    if not resp.ok:
        log.error("Telegram error: %s — %s", resp.status_code, resp.text[:200])
        resp.raise_for_status()
    log.info("Sent: %s @ %s", job["title"], job["company"])


# ── LinkedIn ──────────────────────────────────────────────────────────────────
# Paginates ALL results. Parses each card individually to enforce the
# <200 applicants rule. Cards without an applicant count (new postings) pass.
_LI_BASE = (
    "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
    "?location=United+States&f_TPR=r86400&count=25&keywords={kw}&start={start}"
)
# f_TPR=r86400 = last 24 hours. Wider than 1 hour to never miss a posting.
# seen_jobs.json dedup ensures we never re-alert on the same job.
_LI_MAX_PAGES      = 20    # 20 × 25 = 500 results per keyword max
_LI_MAX_APPLICANTS = 200   # skip jobs at or above this threshold


def _li_applicant_count(card_html: str) -> int:
    """Return applicant count from a single LinkedIn card's HTML.
    Returns 0 if not shown (new posting — treat as low applicants)."""
    # "Over 200 applicants" → 201
    if re.search(r'over\s+200\s+applicants?', card_html, re.I):
        return 201
    # "47 applicants" or "1,234 applicants"
    m = re.search(r'([\d,]+)\s+applicants?', card_html, re.I)
    if m:
        return int(m.group(1).replace(',', ''))
    return 0   # not shown → new job, include it


def fetch_linkedin() -> list[dict]:
    jobs    = []
    seen_li = set()
    for kw in ["associate product manager", "product management intern", "pm intern"]:
        for page in range(_LI_MAX_PAGES):
            start = page * 25
            url   = _LI_BASE.format(kw=requests.utils.quote(kw), start=start)
            try:
                r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
                if r.status_code != 200:
                    log.warning("LinkedIn (%s) p%d → HTTP %s", kw, page, r.status_code)
                    break
                html = r.text
                # Split into per-card chunks so applicant count aligns with each job
                cards = re.split(r'(?=data-entity-urn="urn:li:jobPosting:)', html)
                if len(cards) <= 1:
                    break   # no more results
                new_on_page = 0
                for card in cards:
                    jid_m = re.search(r'data-entity-urn="urn:li:jobPosting:(\d+)"', card)
                    if not jid_m:
                        continue
                    jid = jid_m.group(1)
                    if jid in seen_li:
                        continue
                    # ── Applicant count filter ──
                    count = _li_applicant_count(card)
                    if count >= _LI_MAX_APPLICANTS:
                        seen_li.add(jid)   # track it so we don't recheck
                        continue
                    title_m = re.search(r'class="base-search-card__title"[^>]*>\s*([^<]+?)\s*<', card)
                    comp_m  = re.search(r'class="hidden-nested-link"[^>]*>\s*([^<]+?)\s*<', card)
                    loc_m   = re.search(r'class="job-search-card__location"[^>]*>\s*([^<]+?)\s*<', card)
                    title   = title_m.group(1).strip() if title_m else ""
                    company = comp_m.group(1).strip()  if comp_m  else "Unknown"
                    loc     = loc_m.group(1).strip()   if loc_m   else ""
                    if not is_match(title):
                        continue
                    seen_li.add(jid)
                    new_on_page += 1
                    jobs.append({
                        "id":       f"li_{jid}",
                        "source":   "LinkedIn",
                        "title":    title,
                        "company":  company,
                        "location": loc,
                        "url":      f"https://www.linkedin.com/jobs/view/{jid}/",
                    })
                if len(cards) < 26:   # < 25 cards + 1 leading empty = last page
                    break
            except Exception as e:
                log.warning("LinkedIn error (%s) p%d: %s", kw, page, e)
                break
            time.sleep(1)    # polite delay between pages
        time.sleep(1.5)      # polite delay between keywords
    log.info("LinkedIn: %d matches (filtered <200 applicants)", len(jobs))
    return jobs


# ── Workable ──────────────────────────────────────────────────────────────────
# Public API v1 — confirmed working without auth (v3 is dead as of 2026).
# Paginates through ALL results using nextPageToken until exhausted.
_WK_BASE    = "https://jobs.workable.com/api/v1/jobs?location=USA&query={kw}"
_WK_MAX_PAGES = 20   # safety cap — 20 pages × ~20 results = ~400 per keyword max


def fetch_workable() -> list[dict]:
    jobs    = []
    seen_wk = set()
    for kw in ["associate product manager", "product management intern", "pm intern"]:
        page_token = None
        for page in range(_WK_MAX_PAGES):
            url = _WK_BASE.format(kw=requests.utils.quote(kw))
            if page_token:
                url += f"&pageToken={requests.utils.quote(page_token)}"
            try:
                r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
                if r.status_code != 200:
                    log.warning("Workable (%s) p%d → HTTP %s", kw, page, r.status_code)
                    break
                data       = r.json()
                page_token = data.get("nextPageToken")
                for job in data.get("jobs", []):
                    title = job.get("title", "")
                    jid   = job.get("id", "") or hashlib.md5(title.encode()).hexdigest()[:10]
                    if jid in seen_wk or not is_match(title):
                        continue
                    seen_wk.add(jid)
                    loc = job.get("location") or ""
                    if isinstance(loc, dict):
                        loc = loc.get("city") or loc.get("country") or ""
                    jobs.append({
                        "id":       f"wk_{jid}",
                        "source":   "Workable",
                        "title":    title,
                        "company":  (job.get("company") or {}).get("title", "Unknown"),
                        "location": loc,
                        "url":      job.get("url") or "https://jobs.workable.com/",
                    })
                if not page_token:
                    break   # no more pages
            except Exception as e:
                log.warning("Workable error (%s) p%d: %s", kw, page, e)
                break
    log.info("Workable: %d matches", len(jobs))
    return jobs


# ── Greenhouse ────────────────────────────────────────────────────────────────
# Greenhouse has NO global search — must query per company.
# This list covers companies known to run APM programs or hire PM interns.
# Slugs come from: https://boards.greenhouse.io/{slug}

GH_SLUGS = [
    # ── Big Tech / FAANG-adjacent ──
    "google", "meta", "apple", "netflix", "snap", "twitter", "pinterest",
    "dropbox", "box", "zendesk", "hubspot", "salesforce", "atlassian",
    "datadog", "pagerduty", "cloudflare", "hashicorp", "mongodb",
    "elastic", "databricks", "snowflake", "confluent",
    # ── High-growth SaaS / productivity ──
    "uber", "lyft", "airbnb", "doordash", "stripe", "coinbase",
    "robinhood", "figma", "notion", "brex", "plaid", "ramp",
    "airtable", "lattice", "gusto", "rippling", "deel", "mercury",
    "linear", "retool", "dbtlabs", "coda", "loom", "calendly",
    "superhuman", "asana", "mondaydotcom", "miro", "clickup",
    # ── Fintech ──
    "chime", "klarna", "affirm", "marqeta", "adyen",
    "checkout", "nerdwallet", "creditsesame",
    # ── Healthcare tech ──
    "oscar", "devoted", "cityblock", "zocdoc", "hims",
    # ── Enterprise / infra ──
    "vercel", "samsara", "twilio", "sendgrid", "segment",
    "amplitude", "mixpanel", "fullstory", "heap",
    # ── Consumer / marketplace ──
    "instacart", "postmates", "shipt", "opendoor", "compass",
    "zillow", "redfin", "rover", "faire", "stitch-fix",

    # ── SF Tech Startups ──
    "openai", "anthropic", "scaleai", "glean", "perplexityai",
    "verkada", "ironclad", "carta", "okta", "splunk",
    "newrelic", "wandb", "workato", "tripactions", "flexport",
    "gong-io", "lob", "pilot", "mosaic", "census",
    "retool", "watershed", "vanta", "drata", "secureframe",
    "persona", "sardine", "lithic", "unit", "increase",

    # ── Dallas / DFW Companies ──
    "matchgroup", "capitalone", "sabre", "toyotaconnected",
    "mckesson", "moneylion", "carvana", "peloton", "slalom",
    "dialexa", "sendbird", "keurig", "nortelinc", "aecom",
    "hilton", "nokia", "ericsson", "tenet", "jacobs",
    "atandt", "americanairlines", "southwestairlines", "7eleven",
    "goldmansachs", "jpmorganchase", "fidelity", "williamsonema",
]

GH_WORKERS = 20   # concurrent Greenhouse requests — fast without hammering


def _fetch_one_greenhouse(slug: str) -> list[dict]:
    """Fetch jobs for a single Greenhouse company slug. Returns list of matched jobs."""
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    try:
        r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        if r.status_code in (404, 410):
            return []
        if r.status_code != 200:
            return []
        results = []
        for job in r.json().get("jobs", []):
            title = job.get("title", "")
            if not is_match(title):
                continue
            jid = str(job.get("id", ""))
            results.append({
                "id":       f"gh_{jid}",
                "source":   "Greenhouse",
                "title":    title,
                "company":  (job.get("company") or {}).get("name") or slug.replace("-", " ").title(),
                "location": (job.get("location") or {}).get("name", ""),
                "url":      job.get("absolute_url") or f"https://boards.greenhouse.io/{slug}/jobs/{jid}",
            })
        return results
    except Exception as e:
        log.warning("Greenhouse error (%s): %s", slug, e)
        return []


def fetch_greenhouse() -> list[dict]:
    jobs    = []
    seen_gh = set()
    # Fetch all companies in parallel — 20 workers turns ~100s into ~8s
    with ThreadPoolExecutor(max_workers=GH_WORKERS) as pool:
        futures = {pool.submit(_fetch_one_greenhouse, slug): slug for slug in GH_SLUGS}
        for future in as_completed(futures):
            for job in future.result():
                if job["id"] not in seen_gh:
                    seen_gh.add(job["id"])
                    jobs.append(job)
    log.info("Greenhouse: %d matches across %d companies", len(jobs), len(GH_SLUGS))
    return jobs


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    seed_mode = "--seed" in sys.argv
    seen      = load_seen()

    log.info("Mode: %s | Previously tracked: %d jobs", "SEED" if seed_mode else "LIVE", len(seen))

    # Order matters: LinkedIn first → preferred source when cross-platform dedup fires
    all_jobs = fetch_linkedin() + fetch_workable() + fetch_greenhouse()
    new_ids  = {job["id"] for job in all_jobs}

    # Step 1 — filter to jobs not seen in previous runs
    new_jobs = [job for job in all_jobs if job["id"] not in seen]

    # Step 2 — cross-platform dedup: same title+company from multiple sources = 1 alert
    new_jobs = dedup_across_platforms(new_jobs)

    if seed_mode:
        log.info("Seed complete. Indexed %d jobs. No alerts sent.", len(new_ids))
    else:
        log.info("%d new job(s) to alert on.", len(new_jobs))
        for job in new_jobs:
            try:
                send_telegram(job)
                time.sleep(0.3)   # avoid Telegram rate limit (30 msg/s)
            except Exception as e:
                log.error("Failed to send alert for %s: %s", job["title"], e)

    save_seen(seen | new_ids)


if __name__ == "__main__":
    main()
