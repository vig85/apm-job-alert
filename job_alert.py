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
        r"performance|stack|agent|ops|specialist|analyst))",
        re.I,
    ),
]

EXCLUDE = [
    re.compile(r"\bproduct\s+design\s+intern\b", re.I),
    re.compile(r"\bproduct\s+engineer(ing)?\s+intern\b", re.I),
    re.compile(r"\bproduct\s+marketing\s+intern\b", re.I),
    re.compile(r"\bapplication\s+performance\b", re.I),
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
_LI_BASE = (
    "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
    "?location=United+States&f_TPR=r3600&start=0&count=25&keywords={kw}"
)


def fetch_linkedin() -> list[dict]:
    jobs   = []
    seen_li = set()
    for kw in ["associate product manager", "product management intern", "pm intern"]:
        url = _LI_BASE.format(kw=requests.utils.quote(kw))
        try:
            r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            if r.status_code != 200:
                log.warning("LinkedIn (%s) → HTTP %s", kw, r.status_code)
                continue
            html = r.text
            ids    = re.findall(r'data-entity-urn="urn:li:jobPosting:(\d+)"', html)
            titles = re.findall(r'class="base-search-card__title"[^>]*>\s*([^<]+?)\s*<', html)
            comps  = re.findall(r'class="hidden-nested-link"[^>]*>\s*([^<]+?)\s*<', html)
            locs   = re.findall(r'class="job-search-card__location"[^>]*>\s*([^<]+?)\s*<', html)
            for i, jid in enumerate(ids):
                if jid in seen_li:
                    continue
                title   = titles[i].strip() if i < len(titles) else ""
                company = comps[i].strip()  if i < len(comps)  else "Unknown"
                loc     = locs[i].strip()   if i < len(locs)   else ""
                if not is_match(title):
                    continue
                seen_li.add(jid)
                jobs.append({
                    "id":       f"li_{jid}",
                    "source":   "LinkedIn",
                    "title":    title,
                    "company":  company,
                    "location": loc,
                    "url":      f"https://www.linkedin.com/jobs/view/{jid}/",
                })
        except Exception as e:
            log.warning("LinkedIn error (%s): %s", kw, e)
        time.sleep(1.5)   # polite delay between LinkedIn requests
    log.info("LinkedIn: %d matches", len(jobs))
    return jobs


# ── Workable ──────────────────────────────────────────────────────────────────
# Workable has a real public search API — no company list needed.
_WK_BASE = "https://jobs.workable.com/api/v3/jobs?details=true&query={kw}"


def fetch_workable() -> list[dict]:
    jobs = []
    seen_wk = set()
    for kw in ["associate product manager", "product management intern", "pm intern"]:
        url = _WK_BASE.format(kw=requests.utils.quote(kw))
        try:
            r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            if r.status_code != 200:
                log.warning("Workable (%s) → HTTP %s", kw, r.status_code)
                continue
            for job in r.json().get("results", []):
                title     = job.get("title", "")
                shortcode = job.get("shortcode") or hashlib.md5(title.encode()).hexdigest()[:10]
                if shortcode in seen_wk or not is_match(title):
                    continue
                seen_wk.add(shortcode)
                jobs.append({
                    "id":       f"wk_{shortcode}",
                    "source":   "Workable",
                    "title":    title,
                    "company":  (job.get("company") or {}).get("name", "Unknown"),
                    "location": (job.get("location") or {}).get("city", ""),
                    "url":      job.get("url") or f"https://jobs.workable.com/view/{shortcode}",
                })
        except Exception as e:
            log.warning("Workable error (%s): %s", kw, e)
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
]


def fetch_greenhouse() -> list[dict]:
    jobs     = []
    seen_gh  = set()
    for slug in GH_SLUGS:
        url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
        try:
            r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            if r.status_code in (404, 410):
                continue   # company doesn't have this slug
            if r.status_code != 200:
                continue
            for job in r.json().get("jobs", []):
                jid   = str(job.get("id", ""))
                title = job.get("title", "")
                if jid in seen_gh or not is_match(title):
                    continue
                seen_gh.add(jid)
                jobs.append({
                    "id":       f"gh_{jid}",
                    "source":   "Greenhouse",
                    "title":    title,
                    "company":  (job.get("company") or {}).get("name") or slug.replace("-", " ").title(),
                    "location": (job.get("location") or {}).get("name", ""),
                    "url":      job.get("absolute_url") or f"https://boards.greenhouse.io/{slug}/jobs/{jid}",
                })
        except Exception as e:
            log.warning("Greenhouse error (%s): %s", slug, e)
    log.info("Greenhouse: %d matches across %d companies", len(jobs), len(GH_SLUGS))
    return jobs


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    seed_mode = "--seed" in sys.argv
    seen      = load_seen()

    log.info("Mode: %s | Previously tracked: %d jobs", "SEED" if seed_mode else "LIVE", len(seen))

    all_jobs  = fetch_linkedin() + fetch_workable() + fetch_greenhouse()
    new_ids   = {job["id"] for job in all_jobs}
    new_jobs  = [job for job in all_jobs if job["id"] not in seen]

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
