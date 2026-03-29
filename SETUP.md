# APM & Product Intern Job Alert — Setup Guide

This script monitors LinkedIn, Workable, and Greenhouse every 5 minutes and sends
a Telegram message the moment a new Associate Product Manager or Product Intern role
is posted. It runs on GitHub's free servers — nothing needs to run on your computer.

---

## What you need before starting

- A **GitHub account** (free) → github.com
- A **Telegram account** (free) → telegram.org or the Telegram app
- **Git** installed on your computer → git-scm.com/downloads
- **Python 3.8+** installed → python.org/downloads (only needed for the one-time seed step)

---

## Step 1 — Get the code onto your computer

Download this folder (`job-alert/`) and open a terminal inside it.

```bash
cd path/to/job-alert
```

Verify you're in the right place:
```bash
ls
# Should show: job_alert.py  requirements.txt  .github/  .env.example
```

---

## Step 2 — Create your Telegram bot (5 minutes)

You'll get two things from this step: a **bot token** and a **chat ID**.

### 2a. Create the bot
1. Open Telegram (phone or desktop)
2. Search for `@BotFather` and open the chat
3. Send the message: `/newbot`
4. When asked for a name, type anything — e.g. `Job Alert`
5. When asked for a username, type anything ending in `bot` — e.g. `my_apm_alert_bot`
6. BotFather will reply with your **bot token** — it looks like:
   ```
   1234567890:ABCDefghIJKLmnoPQRstuvWXyz
   ```
   Copy and save this — you'll need it in Step 5.

### 2b. Get your chat ID
1. In Telegram, search for the bot you just created (e.g. `@my_apm_alert_bot`) and open it
2. Send it any message — just type `hi` and hit send
3. Open this URL in your browser (replace `YOUR_TOKEN` with your actual token):
   ```
   https://api.telegram.org/botYOUR_TOKEN/getUpdates
   ```
4. You'll see JSON. Find the part that says `"chat":{"id":` — the number after it is your **chat ID**:
   ```json
   "chat": { "id": 987654321, ... }
   ```
   Copy and save that number — you'll need it in Step 5.

> If the JSON shows `"result":[]` (empty), go back to Telegram and send another message to your bot, then refresh the URL.

---

## Step 3 — Push the code to a public GitHub repo

Run these commands in your terminal (inside the `job-alert/` folder):

```bash
git init
git add .
git commit -m "Initial commit — APM job alert"
```

Now create a public GitHub repo and push to it. Two options:

### Option A — using GitHub CLI (easiest)
```bash
gh auth login        # only needed once, follow the prompts
gh repo create apm-job-alert --public --push --source .
```

### Option B — manually on github.com
1. Go to github.com → click the **+** icon → **New repository**
2. Name it `apm-job-alert`
3. Set visibility to **Public**
4. Do NOT initialize with README (the code is already local)
5. Click **Create repository**
6. GitHub will show you commands — run the ones under "push an existing repository":
   ```bash
   git remote add origin https://github.com/YOUR_USERNAME/apm-job-alert.git
   git branch -M main
   git push -u origin main
   ```

After this, you should see the files at `github.com/YOUR_USERNAME/apm-job-alert`.

---

## Step 4 — Add your secrets to GitHub

The bot token and chat ID are stored as encrypted secrets — never visible to anyone.

1. Go to your repo on GitHub: `github.com/YOUR_USERNAME/apm-job-alert`
2. Click **Settings** (top tab)
3. In the left sidebar, click **Secrets and variables** → **Actions**
4. Click **New repository secret** and add these two secrets:

| Secret name | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Your bot token from Step 2a (e.g. `1234567890:ABCDef...`) |
| `TELEGRAM_CHAT_ID` | Your chat ID number from Step 2b (e.g. `987654321`) |

Make sure the names are spelled exactly as shown above (all caps, underscores).

---

## Step 5 — Enable GitHub Actions

1. In your repo, click the **Actions** tab (top navigation)
2. If you see a banner saying "Workflows aren't being run on this repository", click **I understand my workflows, go ahead and enable them**
3. You should now see **Job Alert — APM & Product Intern** in the left sidebar

---

## Step 6 — Do a test run

1. In the Actions tab, click **Job Alert — APM & Product Intern**
2. Click the **Run workflow** button (top right) → **Run workflow**
3. A new run will appear — click it to watch the logs in real time
4. Within 30 seconds, check Telegram — you should receive messages for any matching jobs posted in the last hour

If you receive messages, everything is working. The script will now run automatically every 5 minutes forever (for free).

> **Note on the first run:** The first run alerts on jobs posted in the last hour. After that, only truly new postings trigger alerts — no repeats.

---

## Step 7 — Done

From now on:
- GitHub runs the script every 5 minutes on their servers
- Your computer doesn't need to be on
- You'll get a Telegram message within 5 minutes of a new APM or Product Intern role being posted on LinkedIn, Workable, or Greenhouse (65+ companies)
- No cost — GitHub Actions is free for public repos, Telegram is free

---

## Customizing the company list (optional)

The Greenhouse company list is in `job_alert.py` at the variable `GH_SLUGS`.
To add a company, find their Greenhouse slug:
- If their jobs are at `boards.greenhouse.io/stripe`, the slug is `stripe`
- Add it to the list, commit, and push — the change goes live on the next run

---

## Troubleshooting

| Problem | Fix |
|---|---|
| No Telegram message on test run | Double-check the secret names match exactly (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`) |
| `"chat not found"` error in logs | Make sure you sent at least one message to your bot before getting the chat ID |
| Workflow doesn't appear in Actions tab | Make sure the file `.github/workflows/job_alert.yml` was committed and pushed |
| `HTTP 429` in logs | LinkedIn rate-limited — this is intermittent and self-resolving |

---

## File overview

```
job-alert/
  job_alert.py                    ← the script (edit this to add companies or change keywords)
  requirements.txt                ← Python dependencies (just "requests")
  .env.example                    ← template if you want to run locally
  .github/
    workflows/
      job_alert.yml               ← GitHub Actions schedule (runs every 5 min)
```
