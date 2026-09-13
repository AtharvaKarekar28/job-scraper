#!/usr/bin/env python3
"""
Job Radar scraper.

Polls public ATS job-board APIs (Greenhouse, Lever, Ashby), filters postings down
to early-career data / analytics / ML roles in the US, and writes the result to
docs/jobs.json for the static site to render.

Standard library only -- no pip install step, so CI stays fast and can't break on
a dependency update.

Run:  python scraper/scrape.py
"""

from __future__ import annotations

import html
import json
import re
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "scraper"
DOCS = ROOT / "docs"
DATA = ROOT / "data"

USER_AGENT = "job-radar/1.0 (+https://github.com/)"
TIMEOUT = 30
WORKERS = 12

# Matches "3 years", "3+ years", "3-5 years", "3 to 5 years", "3-5 yrs"
YEARS_RE = re.compile(
    r"(\d{1,2})\s*(?:\+|\s*(?:-|–|—|to)\s*\d{1,2}\s*\+?)?\s*(?:\+\s*)?(?:years?|yrs?)\b",
    re.I,
)
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


# --------------------------------------------------------------------------- io


def load_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as exc:
        print(f"  ! {path.name} is not valid JSON: {exc}", file=sys.stderr)
        if default is None:
            raise
        return default


def fetch(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def strip_html(raw: str) -> str:
    if not raw:
        return ""
    text = html.unescape(raw)
    text = TAG_RE.sub(" ", text)
    text = html.unescape(text)
    return WS_RE.sub(" ", text).strip()


# ---------------------------------------------------------------------- sources
# Each source function returns a list of normalised dicts:
#   id, source, company, title, location, url, posted (ISO date or ""), text


def from_greenhouse(slug: str) -> list[dict]:
    data = fetch(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
    out = []
    for j in data.get("jobs", []):
        posted = (j.get("first_published") or j.get("updated_at") or "")[:10]
        out.append(
            {
                "id": f"gh:{slug}:{j.get('id')}",
                "source": "greenhouse",
                "company": slug,
                "title": (j.get("title") or "").strip(),
                "location": ((j.get("location") or {}).get("name") or "").strip(),
                "url": j.get("absolute_url") or "",
                "posted": posted,
                "text": strip_html(j.get("content") or ""),
            }
        )
    return out


def from_lever(slug: str) -> list[dict]:
    data = fetch(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    out = []
    for j in data or []:
        cats = j.get("categories") or {}
        locs = cats.get("allLocations") or ([cats["location"]] if cats.get("location") else [])
        posted = ""
        if j.get("createdAt"):
            try:
                posted = datetime.fromtimestamp(
                    j["createdAt"] / 1000, tz=timezone.utc
                ).strftime("%Y-%m-%d")
            except (ValueError, OSError, TypeError):
                posted = ""
        body = " ".join(
            filter(None, [j.get("descriptionPlain"), j.get("additionalPlain")])
        )
        out.append(
            {
                "id": f"lv:{slug}:{j.get('id')}",
                "source": "lever",
                "company": slug,
                "title": (j.get("text") or "").strip(),
                "location": ", ".join(locs),
                "url": j.get("hostedUrl") or j.get("applyUrl") or "",
                "posted": posted,
                "text": WS_RE.sub(" ", body).strip(),
            }
        )
    return out


def from_ashby(slug: str) -> list[dict]:
    data = fetch(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    out = []
    for j in data.get("jobs", []):
        locs = [j.get("location") or ""] + [
            s.get("location", "") for s in (j.get("secondaryLocations") or [])
        ]
        out.append(
            {
                "id": f"ab:{slug}:{j.get('id')}",
                "source": "ashby",
                "company": slug,
                "title": (j.get("title") or "").strip(),
                "location": ", ".join([x for x in locs if x]),
                "url": j.get("jobUrl") or j.get("applyUrl") or "",
                "posted": (j.get("publishedAt") or "")[:10],
                "text": WS_RE.sub(" ", j.get("descriptionPlain") or "").strip(),
            }
        )
    return out


SOURCES = {"greenhouse": from_greenhouse, "lever": from_lever, "ashby": from_ashby}


def collect(boards: dict) -> tuple[list[dict], list[str]]:
    """Fetch every configured board in parallel. Returns (jobs, failed_slugs)."""
    tasks = [
        (name, slug)
        for name, slugs in boards.items()
        if name in SOURCES
        for slug in slugs
    ]

    def run(task):
        name, slug = task
        try:
            return SOURCES[name](slug), None
        except urllib.error.HTTPError as exc:
            return [], f"{name}:{slug} HTTP {exc.code}"
        except Exception as exc:  # noqa: BLE001 - one bad board must not kill the run
            return [], f"{name}:{slug} {type(exc).__name__}"

    jobs, failed = [], []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for got, err in pool.map(run, tasks):
            jobs.extend(got)
            if err:
                failed.append(err)
    return jobs, failed


# ---------------------------------------------------------------------- filters


def compile_any(patterns: list[str]) -> re.Pattern:
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.I)


def min_years(text: str) -> int | None:
    """Lowest years-of-experience figure named anywhere in the description.

    Taking the minimum is deliberate: descriptions routinely list a range or
    mention a higher figure for a stretch qualification, and the floor is what
    decides whether an early-career candidate is eligible to apply.
    """
    found = [int(m.group(1)) for m in YEARS_RE.finditer(text)]
    found = [y for y in found if 0 <= y <= 15]
    return min(found) if found else None


def main() -> int:
    boards = load_json(CONFIG_DIR / "boards.json")
    cfg = load_json(CONFIG_DIR / "filters.json")

    role_re = compile_any(cfg["role_match"])
    excl_re = compile_any(cfg["title_exclude"])
    loc_re = compile_any(cfg["location_exclude"])
    intern_re = compile_any(cfg["internship_match"])
    spons_re = compile_any(cfg["sponsorship_negative"])
    max_years = cfg["max_years_experience"]
    max_age = cfg.get("max_posting_age_days")
    oldest = (
        (datetime.now(timezone.utc) - timedelta(days=max_age)).strftime("%Y-%m-%d")
        if max_age
        else None
    )

    board_count = sum(len(v) for k, v in boards.items() if k in SOURCES)
    print(f"Polling {board_count} boards across {len(SOURCES)} sources ...")

    jobs, failed = collect(boards)
    print(f"  fetched {len(jobs)} postings ({len(failed)} boards unreachable)")
    for f in failed:
        print(f"    - {f}")

    funnel = {"scanned": len(jobs), "role": 0, "us": 0, "experience": 0, "kept": 0}
    kept: list[dict] = []

    for j in jobs:
        title = j["title"]
        if not role_re.search(title) or excl_re.search(title):
            continue
        funnel["role"] += 1

        if loc_re.search(j["location"]):
            continue
        funnel["us"] += 1

        if oldest and j["posted"] and j["posted"] < oldest:
            continue

        is_intern = bool(intern_re.search(title))
        years = min_years(j["text"])

        # Interns are exempt from the years test; everyone else must be at or
        # under the cap, or have stated nothing at all.
        if not is_intern and years is not None and years > max_years:
            continue
        funnel["experience"] += 1

        blocker = spons_re.search(j["text"])
        if blocker:
            continue
        funnel["kept"] += 1

        if is_intern:
            tier = "internship"
        elif years is None:
            tier = "unverified"
        else:
            tier = "entry"

        kept.append(
            {
                "id": j["id"],
                "source": j["source"],
                "company": j["company"],
                "title": title,
                "location": j["location"],
                "url": j["url"],
                "posted": j["posted"],
                "years": years,
                "tier": tier,
            }
        )

    # -- first-seen tracking: this is what makes "new today" meaningful -------
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    seen = load_json(DATA / "seen.json", default={}) or {}
    for job in kept:
        seen.setdefault(job["id"], today)
        job["first_seen"] = seen[job["id"]]

    # Forget ids that have not appeared for 90 days so the file cannot grow
    # without bound.
    cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%d")
    live = {j["id"] for j in kept}
    seen = {k: v for k, v in seen.items() if k in live or v >= cutoff}

    window = cfg.get("new_job_window_days", 3)
    fresh_after = (datetime.now(timezone.utc) - timedelta(days=window)).strftime("%Y-%m-%d")
    new_count = sum(1 for j in kept if j["first_seen"] >= fresh_after)

    tier_rank = {"entry": 0, "internship": 1, "unverified": 2}
    kept.sort(
        key=lambda j: (
            tier_rank.get(j["tier"], 3),
            j["years"] if j["years"] is not None else 99,
            -(j["first_seen"] <= today),
            j["company"],
            j["title"],
        )
    )

    payload = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "boards_polled": board_count,
        "boards_failed": failed,
        "funnel": funnel,
        "new_window_days": window,
        "new_count": new_count,
        "counts": {
            t: sum(1 for j in kept if j["tier"] == t)
            for t in ("entry", "internship", "unverified")
        },
        "jobs": kept,
    }

    DOCS.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)
    (DOCS / "jobs.json").write_text(
        json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    (DATA / "seen.json").write_text(
        json.dumps(seen, indent=0, sort_keys=True), encoding="utf-8"
    )

    print(
        f"  {funnel['scanned']} scanned -> {funnel['role']} role match "
        f"-> {funnel['us']} US -> {funnel['experience']} experience -> {funnel['kept']} kept"
    )
    print(f"  {new_count} new in the last {window} days")
    print("  wrote docs/jobs.json and data/seen.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
