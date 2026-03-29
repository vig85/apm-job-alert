# Job Alert — Claude Context

## What this project does
Monitors LinkedIn, Workable, and Greenhouse every 5 minutes for new Associate Product
Manager (APM) and Product Intern job postings. Sends a Telegram message immediately
when a new match is found. Runs on GitHub Actions (free, public repo) — nothing runs
locally.

## Single file: job_alert.py
Everything lives in `job_alert.py`. There is no framework, no database, no extra
libraries beyond `requests`. Keep it that way — the goal is zero dependencies and
fast cold-start (GitHub Actions runner spins up in ~10s).

## How it works
1. Fetches jobs from three sources (LinkedIn guest API, Workable public API, Greenhouse per-company API)
2. Filters titles against `INCLUDE` / `EXCLUDE` regex patterns
3. Compares matched job IDs against `seen_jobs.json` (state file)
4. Sends a Telegram message for any job ID not seen before
5. Saves updated state back to `seen_jobs.json`

State (`seen_jobs.json`) is persisted between GitHub Actions runs via the cache action
defined in `.github/workflows/job_alert.yml`.

## Key sections in job_alert.py

| Lines | What it is |
|---|---|
| `INCLUDE` list | Regex patterns a title MUST match (APM, product intern, etc.) |
| `EXCLUDE` list | Regex patterns that disqualify a match (design intern, senior PM, etc.) |
| `GH_SLUGS` list | Company slugs for Greenhouse (per-company API, no global search exists) |
| `fetch_linkedin()` | Scrapes LinkedIn guest jobs API, filtered to last hour (`f_TPR=r3600`) |
| `fetch_workable()` | Calls Workable's public search API — no company list needed |
| `fetch_greenhouse()` | Iterates `GH_SLUGS` and calls each company's Greenhouse board API |
| `send_telegram()` | POSTs to Telegram Bot API using MarkdownV2 formatting |
| `main()` | Orchestrates fetch → diff → alert → save. `--seed` flag skips alerts (first run) |

## Adding / changing things

**Add a new keyword to match:**
Add a `re.compile(...)` entry to the `INCLUDE` list. Use `\b` word boundaries to
avoid partial matches. Test with `is_match("your test title")` in a Python shell.

**Add a company to Greenhouse monitoring:**
Add its slug to `GH_SLUGS`. The slug is the path segment at `boards.greenhouse.io/{slug}`.
A 404 from the API means the slug is wrong or the company doesn't use Greenhouse — just remove it.

**Add a new job board:**
Write a `fetch_<platform>()` function that returns a list of dicts with keys:
`id` (unique string), `source`, `title`, `company`, `location`, `url`.
The `id` must be stable across runs (same job = same id). Add it to the
`all_jobs = ...` line in `main()`.

**Change poll frequency:**
Edit the cron in `.github/workflows/job_alert.yml`. Minimum on GitHub Actions is
`*/5` (every 5 minutes). For private repos, use `*/15` to stay under the 2,000
free-minutes/month limit. Public repos have unlimited free minutes.

## Constraints — do not break these
- `job_alert.py` must stay a single file with no imports beyond stdlib + `requests`
- Job IDs must be deterministic and stable (same posting = same ID across runs) to
  avoid duplicate alerts
- The `EXCLUDE` patterns must stay strict — false positives are worse than missed alerts
- Do not add `beautifulsoup4`, `lxml`, or any HTML parser — use regex on the raw response
- LinkedIn scraping uses the undocumented guest API. Do not add auth or cookies.
  If it returns non-200, log a warning and move on — do not raise.

## Environment variables (set as GitHub Secrets)
- `TELEGRAM_BOT_TOKEN` — Telegram bot token from @BotFather
- `TELEGRAM_CHAT_ID` — recipient's Telegram chat ID
- `STATE_FILE` — optional override for the state file path (default: `seen_jobs.json`)

## Running locally for testing
```bash
pip install requests
export TELEGRAM_BOT_TOKEN=...
export TELEGRAM_CHAT_ID=...
python job_alert.py --seed   # index existing jobs without sending alerts
python job_alert.py          # normal run — sends alerts for any new jobs
```

## What "Rippling" means here
Rippling is an HR platform that companies use as an ATS. Individual company job boards
are at `ats.rippling.com/{company}/jobs`. There is NO aggregate search endpoint.
To monitor Rippling jobs, add a `fetch_rippling()` function that iterates a company
slug list (same pattern as Greenhouse). The page is React-rendered — extract job data
from the `__NEXT_DATA__` script tag in the HTML.
