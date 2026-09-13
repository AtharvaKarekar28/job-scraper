# Job Radar

A self-updating job board for early-career data, analytics and ML roles in the US.

Every morning a GitHub Action polls ~100 company job boards through the public
Greenhouse, Lever and Ashby APIs, filters the postings, and commits the result to
`docs/jobs.json`. GitHub Pages serves the site from the same folder. No server, no
database, no cost.

## Setup (about five minutes)

1. **Create the repo.** Make a new GitHub repository and push these files to `main`.

   ```bash
   cd job-radar
   git init && git add . && git commit -m "Initial commit"
   git branch -M main
   git remote add origin https://github.com/<you>/job-radar.git
   git push -u origin main
   ```

2. **Allow the Action to commit.** Settings → Actions → General → *Workflow
   permissions* → select **Read and write permissions** → Save. Without this the
   daily run scrapes fine but cannot push its results.

3. **Turn on Pages.** Settings → Pages → Source: *Deploy from a branch* →
   Branch `main`, folder `/docs` → Save. Your site appears at
   `https://<you>.github.io/job-radar/` within a minute or two.

4. **Run it once now.** Actions tab → *Scrape jobs* → **Run workflow**. The
   schedule alone would leave the site on yesterday's seed data until tomorrow.

Private repos work fine for all of this on the free tier, if you'd rather your job
search not be public.

## How the filtering works

The filter runs in four stages, and the site shows the count surviving each one.

| Stage | What it does |
| --- | --- |
| **Title match** | Keeps titles matching `role_match`, drops anything matching `title_exclude` (senior, staff, manager, clearance…). |
| **Location** | Drops postings whose location matches `location_exclude`. This is a denylist of non-US places, so an unfamiliar US city is kept rather than silently lost. |
| **Experience** | Reads the **full job description** and finds the lowest years-of-experience figure in it. Drops the posting if that floor is above `max_years_experience`. |
| **Sponsorship** | Drops postings whose description explicitly refuses sponsorship or demands a clearance. |

Reading the description is what makes this useful. Only about one early-career
posting in ten says so in its title, so a title-only filter throws away most of
what you want and keeps senior roles whose titles happen to look junior.

Surviving jobs land in one of three groups:

- **entry** — a stated floor at or under the cap. Highest confidence.
- **internship** — explicit intern, co-op, new grad or class-year language.
- **unverified** — the description never names a number, so nothing could be
  tested. These are kept on purpose: dropping them would silently hide roles that
  simply don't state a requirement. Skim them, don't trust them.

## Retuning it

Everything you'd want to change lives in two files. Edit, commit, push — the next
run picks it up.

**`scraper/filters.json`**

- `role_match` — titles you want. Keep these specific: a bare `data` matches
  "Data Center Technician", and a bare `analyst` matches every treasury,
  compliance and complaints role on the internet.
- `title_exclude` — instant disqualifiers, tested against the title.
- `location_exclude` — non-US places to drop.
- `max_years_experience` — currently `2`.
- `max_posting_age_days` — off by default. Set it to e.g. `365` if stale evergreen
  requisitions bother you; be aware it discards genuinely open old roles too.
- `new_job_window_days` — how long a role keeps its **NEW** badge.

**`scraper/boards.json`** — the company boards to poll, grouped by ATS. Coverage
equals this list, so growing it is the main way to see more jobs. To find a
company's slug, open its careers page and look at the URL:

| ATS | Careers URL looks like | Slug | Verify with |
| --- | --- | --- | --- |
| Greenhouse | `job-boards.greenhouse.io/**figma**` | `figma` | `boards-api.greenhouse.io/v1/boards/figma/jobs` |
| Lever | `jobs.lever.co/**palantir**` | `palantir` | `api.lever.co/v0/postings/palantir?mode=json` |
| Ashby | `jobs.ashbyhq.com/**ramp**` | `ramp` | `api.ashbyhq.com/posting-api/job-board/ramp` |

Paste the verify URL into a browser. JSON means the slug works; a 404 means it
doesn't. Bad slugs are harmless — the scraper skips them, notes them in the run
log, and shows a count in the site footer.

## Running it locally

```bash
python scraper/scrape.py      # writes docs/jobs.json and data/seen.json
cd docs && python -m http.server 8000
```

Then open `http://localhost:8000`. Opening `index.html` as a `file://` path will
not work — the browser blocks the `jobs.json` fetch.

Python 3.9 or newer, standard library only.

## What it can't do

- **Coverage is the board list, nothing more.** These APIs are per-company; there
  is no global search endpoint. A company missing from `boards.json` is invisible
  no matter how well it matches.
- **LinkedIn and Indeed are not here.** Both block automated access and their
  terms forbid it. Getting them means a paid API.
- **The sponsorship filter is weak, by nature.** It removed one posting out of
  ~12,900 on the first run — not because employers sponsor, but because almost
  none discuss it in the job text. It catches explicit refusals and clearance
  requirements. It is not a sponsor-friendly signal, and shouldn't be read as one.
- **Years parsing is a heuristic.** It takes the lowest figure in the text, which
  is right far more often than not, but a description mentioning "10 years of
  industry change" in its intro can skew a posting either way.

## Layout

```
.github/workflows/scrape.yml   daily cron, commits results
scraper/scrape.py              fetch, filter, classify, write
scraper/boards.json            company slugs per ATS
scraper/filters.json           all filter rules
docs/index.html                the site
docs/jobs.json                 generated each run (seeded with a real 2026-09-13 run)
data/seen.json                 first-seen dates, powers the NEW badge
```
