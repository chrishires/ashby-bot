import json
import os
import re
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Dict, List, Set, Tuple

import requests

BOARDS_FILE = Path("boards.txt")
STATE_FILE = Path("state_seen.json")

ASHBY_URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}"

# Re-write this to tailor it to your own job search needs
TITLE_RE = re.compile(
    r"\b("
    r"data\s+scien(tist|ce)"                           
    r"|decision\s+scientist"                           
    r"|quantitative\s+(analyst|researcher|scientist)"  
    r"|forecasting\s+(analyst|scientist|engineer)"     
    r"|causal\s+(inference\s+)?scientist"              
    r"|applied\s+(data\s+)?scientist"
    r"|(?:vp|vice\s+president|head|director|lead)\s+of\s+analytics"
    r"|analytics\s+(?:lead|manager|director)"                  
    r")",
    re.IGNORECASE
)

@dataclass(frozen=True)
class JobHit:
    slug: str
    title: str
    location: str
    url: str
    updated_at: str

def load_boards() -> List[str]:
    if not BOARDS_FILE.exists():
        return []
    boards = []
    for line in BOARDS_FILE.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s:
            boards.append(s)
    return boards

def load_state() -> Set[str]:
    if not STATE_FILE.exists():
        return set()
    try:
        obj = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        seen = obj.get("seen", [])
        return set(seen) if isinstance(seen, list) else set()
    except Exception:
        return set()

def save_state(seen: Set[str]) -> None:
    STATE_FILE.write_text(json.dumps({"seen": sorted(seen)}, indent=2), encoding="utf-8")

def fetch_jobs_for_board(slug: str) -> List[dict]:
    url = ASHBY_URL.format(slug=slug)
    r = requests.get(url, timeout=30)
    if r.status_code == 404:
        return []
    r.raise_for_status()
    data = r.json()
    return data.get("jobs", []) or []

def parse_location(job: dict) -> str:
    loc = job.get("location")
    if isinstance(loc, str) and loc.strip():
        return loc.strip()
    locs = job.get("locations")
    if isinstance(locs, list) and locs:
        names = []
        for item in locs[:3]:
            if isinstance(item, dict):
                name = item.get("name") or item.get("location")
                if isinstance(name, str) and name.strip():
                    names.append(name.strip())
            elif isinstance(item, str) and item.strip():
                names.append(item.strip())
        if names:
            return " / ".join(names)
    return "Unspecified"

US_COUNTRY_TOKENS = {
    "usa", "us", "u.s.", "u.s.a.",
    "united states", "united states of america",
}

US_STATE_ABBR = {
  "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA","KS","KY","LA","ME","MD",
  "MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ","NM","NY","NC","ND","OH","OK","OR","PA","RI","SC",
  "SD","TN","TX","UT","VT","VA","WA","WV","WI","WY","DC"
}

def norm_country(x: str) -> str:
    return (x or "").strip().lower()

def country_from_job(job: dict) -> str:
    addr = job.get("address") or {}
    postal = addr.get("postalAddress") or {}
    c = postal.get("addressCountry")
    if isinstance(c, str) and c.strip():
        return c.strip()
    return ""

def countries_from_secondary(job: dict) -> list[str]:
    out = []
    sec = job.get("secondaryLocations") or []
    if not isinstance(sec, list):
        return out
    for item in sec:
        if not isinstance(item, dict):
            continue
        a = item.get("address") or {}
        c = a.get("addressCountry")
        if isinstance(c, str) and c.strip():
            out.append(c.strip())
    return out

def looks_like_us_location_string(loc: str) -> bool:
    loc = (loc or "").strip()
    if re.search(r"\bremote\b", loc, re.IGNORECASE) and re.search(r"\b(us|usa|united states)\b", loc, re.IGNORECASE):
        return True
    m = re.search(r",\s*([A-Z]{2})(\b|$)", loc)
    return bool(m and m.group(1) in US_STATE_ABBR)

# You may wish to modify or remove is_us_job and related functions if you want a different geo filter or no geo filter at all.

def is_us_job(job: dict) -> bool:
    c = norm_country(country_from_job(job))
    if c and c in US_COUNTRY_TOKENS:
        return True

    for sc in countries_from_secondary(job):
        if norm_country(sc) in US_COUNTRY_TOKENS:
            return True

    # If no country data at all, treat isRemote=True as likely US.

    if not country_from_job(job) and job.get("isRemote") is True:
        return True

    loc = job.get("location") or ""
    return looks_like_us_location_string(loc)

def extract_job_key(job: dict) -> str:
    url = job.get("jobUrl") or job.get("url") or job.get("applyUrl")
    if url:
        return f"url:{url}"

    jid = job.get("id") or job.get("_id") or job.get("jobId") or job.get("requisitionId")
    if jid:
        return f"id:{jid}"

    return f"fallback:{job.get('title','')}|{job.get('createdAt','')}"

def extract_job_url(job: dict) -> str:
    return job.get("jobUrl") or job.get("url") or job.get("applyUrl") or ""

def extract_updated_at(job: dict) -> str:
    return job.get("updatedAt") or job.get("publishedAt") or job.get("createdAt") or ""


def find_new_hits(boards: List[str], seen: Set[str]) -> Tuple[List[JobHit], Set[str]]:
    hits: List[JobHit] = []
    new_seen: Set[str] = set(seen)

    boards_total = len(boards)
    boards_ok = 0
    boards_err = 0

    jobs_total = 0
    ds_titles_unseen = 0   # passed title filter (new, not yet seen)
    ds_titles_non_us = 0   # passed title filter but dropped by geo filter
    new_hits = 0

    for slug in boards:
        try:
            jobs = fetch_jobs_for_board(slug)
            boards_ok += 1
        except Exception as e:
            boards_err += 1
            print(f"[warn] fetch failed for {slug}: {e}")
            continue

        jobs_total += len(jobs)

        for job in jobs:
            # ---- compute keys ----
            key_url = extract_job_key(job)
            jid = job.get("id") or job.get("_id") or job.get("jobId") or job.get("requisitionId")
            key_id = f"id:{jid}" if jid else None

            # ---- global dedupe ----
            if key_url in new_seen or (key_id and key_id in new_seen):
                continue

            # mark as seen immediately (even if it doesn't match DS)
            new_seen.add(key_url)
            if key_id:
                new_seen.add(key_id)

            # ---- DS title filter ----
            title = (job.get("title") or "").strip()
            if not title:
                continue

            if not TITLE_RE.search(title):
                continue

            ds_titles_unseen += 1  # passed title filter; now apply geo filter

            # ---- US-only alert filter ----
            if not is_us_job(job):
                ds_titles_non_us += 1
                continue

            url = extract_job_url(job)
            loc = parse_location(job)
            updated_at = extract_updated_at(job)

            hits.append(JobHit(
                slug=slug,
                title=title,
                location=loc,
                url=url,
                updated_at=updated_at
            ))
            new_hits += 1

    print(f"Boards total: {boards_total} | ok: {boards_ok} | err: {boards_err}")
    print(f"Jobs total fetched: {jobs_total}")
    print(f"New DS-title matches (unseen): {ds_titles_unseen}")
    print(f"  - Dropped by geo filter: {ds_titles_non_us}")
    print(f"  - Emailed as hits: {new_hits}")

    hits.sort(key=lambda x: (x.updated_at or "", x.slug, x.title), reverse=True)
    return hits, new_seen

def send_email(subject: str, body: str) -> None:
    gmail_user = os.environ["GMAIL_USER"]
    gmail_pass = os.environ["GMAIL_APP_PASSWORD"]
    alert_to = os.environ.get("ALERT_TO", gmail_user)

    msg = EmailMessage()
    msg["From"] = gmail_user
    msg["To"] = alert_to
    msg["Subject"] = subject
    msg.set_content(body)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(gmail_user, gmail_pass)
        smtp.send_message(msg)

def format_digest(hits: List[JobHit]) -> str:
    lines = []
    lines.append(f"New Ashby Data Scientist postings: {len(hits)}")
    lines.append("")
    for h in hits:
        lines.append(f"- {h.title}")
        lines.append(f"  Company board: {h.slug}")
        lines.append(f"  Location: {h.location}")
        if h.updated_at:
            lines.append(f"  Updated: {h.updated_at}")
        if h.url:
            lines.append(f"  Link: {h.url}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"

def main():
    boards = load_boards()
    print("Boards loaded:", len(boards))

    seen = load_state()
    print("Loaded state size:", len(seen))

    if not boards:
        print("No boards found. boards.txt is empty.")
        return

    hits, new_seen = find_new_hits(boards, seen)
    print("New state size:", len(new_seen))

    if hits:
        subject = f"Ashby DS jobs: {len(hits)} new"
        body = format_digest(hits)
        send_email(subject, body)
        print(f"Sent email for {len(hits)} new jobs.")
    else:
        print("No new matching jobs.")

    save_state(new_seen)
    print("Saved state size:", len(new_seen))

if __name__ == "__main__":
    main()