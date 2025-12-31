import json
import re
from pathlib import Path
from typing import Iterable, Set
import requests

BOARDS_FILE = Path("boards.txt")

SLUG_RE = re.compile(r"^https?://jobs\.ashbyhq\.com/([^/?#]+)/?$", re.IGNORECASE)
ASHBY_API = "https://api.ashbyhq.com/posting-api/job-board/{slug}"

CC_COLLINFO = "https://index.commoncrawl.org/collinfo.json"

def get_latest_cc_index_api() -> str:
    resp = requests.get(CC_COLLINFO, timeout=30)
    resp.raise_for_status()
    colls = resp.json()
    def key_fn(x):
        m = re.search(r"CC-MAIN-(\d{4})-(\d+)", x.get("id", ""))
        return (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    latest = max(colls, key=key_fn)
    return latest["cdx-api"]

def read_existing_boards() -> Set[str]:
    if not BOARDS_FILE.exists():
        return set()
    return {line.strip() for line in BOARDS_FILE.read_text().splitlines() if line.strip()}

def append_boards(new_slugs: Iterable[str]) -> int:
    existing = read_existing_boards()
    to_add = sorted({s for s in new_slugs if s not in existing})
    if not to_add:
        return 0
    with BOARDS_FILE.open("a", encoding="utf-8") as f:
        for slug in to_add:
            f.write(slug + "\n")
    return len(to_add)

def iter_cc_matches(cdx_api: str) -> Iterable[str]:
    query_url = (
        f"{cdx_api}"
        f"?url=jobs.ashbyhq.com/*"
        f"&output=json"
        f"&fl=url"
        f"&collapse=urlkey"
    )
    with requests.get(query_url, stream=True, timeout=60) as r:
        r.raise_for_status()
        for line in r.iter_lines(decode_unicode=True):
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            url = obj.get("url")
            if url:
                yield url

def extract_slugs(urls: Iterable[str]) -> Set[str]:
    slugs = set()
    for url in urls:
        m = SLUG_RE.match(url.strip())
        if not m:
            continue
        slug = m.group(1).strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}", slug):
            continue
        slugs.add(slug)
    return slugs

def is_valid_ashby_board(slug: str) -> bool:
    try:
        r = requests.get(ASHBY_API.format(slug=slug), timeout=20)
        if r.status_code != 200:
            return False
        data = r.json()
        # Ashby docs show: {"apiVersion":"1","jobs":[...]}  [oai_citation:2‡Ashby](https://developers.ashbyhq.com/docs/public-job-posting-api)
        return isinstance(data, dict) and isinstance(data.get("jobs"), list) and "apiVersion" in data
    except Exception:
        return False

def main():
    latest_cdx = get_latest_cc_index_api()
    existing = read_existing_boards()

    urls = iter_cc_matches(latest_cdx)
    slugs = extract_slugs(urls)

    candidates = sorted(slugs - existing)
    valid_new = []

    # Validate newly discovered slugs against Ashby API
    for slug in candidates:
        if is_valid_ashby_board(slug):
            valid_new.append(slug)

    added = append_boards(valid_new)

    print(f"Latest CC index: {latest_cdx}")
    print(f"Found slugs: {len(slugs)} | Candidates: {len(candidates)} | Valid new: {len(valid_new)} | Added: {added}")

if __name__ == "__main__":
    main()