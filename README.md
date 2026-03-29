# Ashby Bot

A job board scraper that sends email alerts for newly posted roles across hundreds of companies that use [Ashby](https://www.ashbyhq.com/) as their ATS.

## How it works

Two GitHub Actions workflows run on a schedule:

**`discover.yml`** — runs daily. Queries the [Common Crawl](https://commoncrawl.org/) index for new `jobs.ashbyhq.com` URLs, validates each discovered slug against the Ashby API, and appends new boards to `boards.txt`.

**`poll.yml`** — runs every 3 hours. Fetches all open jobs from every board in `boards.txt`, filters by title and US location, and emails a digest of any jobs not seen in previous runs. Seen job URLs are persisted in `state_seen.json` to avoid duplicate alerts.

**Note:** The first time you run the script, you'll get an email with a very large number of listings, because `state_seen.json` will be empty. This may or may not be useful to you. If not, just ignore the email and you'll only receive new listings going forward.

## Configuration

### Title filtering

Edit `TITLE_RE` in `scripts/poll_and_email.py` to match the roles you're targeting. The default is tuned for Data Science roles:

```python
TITLE_RE = re.compile(
    r"\b("
    r"data\s+scien(tist|ce)"
    r"|decision\s+scientist"
    r"|quantitative\s+(analyst|researcher|scientist)"
    r"|forecasting\s+(analyst|scientist|engineer)"
    r"|causal\s+(inference\s+)?scientist"
    r"|applied\s+(data\s+)?scientist"
    r")",
    re.IGNORECASE
)
```

### Adding boards manually

Append Ashby board slugs (one per line) to `boards.txt`. The slug is the path segment from any Ashby job board URL — e.g. for `jobs.ashbyhq.com/ramp`, the slug is `ramp`.

## Setup

### 1. Fork this repo

### 2. Add GitHub Actions secrets

Go to **Settings → Secrets and variables → Actions** and add:

| Secret | Description |
|---|---|
| `GMAIL_USER` | Your Gmail address |
| `GMAIL_APP_PASSWORD` | A [Gmail App Password](https://support.google.com/accounts/answer/185833) (not your account password) |
| `ALERT_TO` | Email address to send alerts to (can be the same as `GMAIL_USER`) |

### 3. Enable Actions

GitHub Actions should be enabled by default on forked repos. Trigger either workflow manually via **Actions → Run workflow** to test before the scheduled runs kick in.

## Local development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Test discovery
python scripts/discover_boards.py

# Test polling (requires env vars)
GMAIL_USER=you@gmail.com \
GMAIL_APP_PASSWORD=your-app-password \
ALERT_TO=you@gmail.com \
python scripts/poll_and_email.py
```