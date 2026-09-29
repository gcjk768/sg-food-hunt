# SG Food Hunt

Collects, ranks and keeps fresh a list of dining venues in Singapore for dates and family
occasions, and writes the results into an Obsidian vault. Everything is plain files (JSON,
JSONL, Markdown) so the project can live on a NAS share. There is no database.

**Status: all five stages built.** Sources and storage, dedup and scoring, MRT enrichment and
review analysis, the social buzz module, and the diff report, notifications, dashboard and
scheduling. The first live run against the real sites will still need selector tuning (see the
legal notes below). See [docs/DESIGN.md](docs/DESIGN.md) for the
full outline, config schema and storage schema, and the stage plan at the bottom of this file.

## Setup

```bash
git clone https://github.com/gcjk768/SG-Cafe-Food-Hunt.git
cd SG-Cafe-Food-Hunt
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"            # add ,browser for Playwright, ,dashboard for Streamlit
cp .env.example .env               # fill in keys; .env is git ignored
```

Edit `config/settings.yaml`:

- `paths.vault_dir`: the path of your Obsidian vault (absolute path on the NAS is fine)
- `paths.vault_folder`: the subfolder inside the vault the tool owns (default `SG Food Hunt`)

Then:

```bash
sgfh init                          # creates data/, logs/, the vault folder, Home.md, Sources.md
sgfh sources                       # what will run and why
sgfh categories                    # the 15 categories and their query variants
sgfh run                           # collect, dedup, score, write vault notes and exports
sgfh run -c zichar_family -s sethlui -s michelin   # one category, two sources
sgfh run --dry-run                 # cached responses only, no network
sgfh run --hide-visited            # keep venues marked `status: visited` out of the rankings
sgfh rank                          # re-score the latest run offline (after changing weights or notes)
sgfh rank --run 20260929T031500Z -c zichar_family
sgfh rank --offline               # also skip OneMap lookups (cached coordinates only)
sgfh runs                          # list past runs
sgfh show <run_id>                 # per source counts and sample candidates
sgfh cache stats | sgfh cache purge
sgfh doctor                        # deployment check: paths, keys, notification config
sgfh notify-test                   # send a test message to Telegram / email
sgfh serve                         # built-in weekly scheduler (used by the Docker image)
sgfh dashboard                     # start the Streamlit dashboard
```

`sgfh run --social-only` re-parses your Instagram / TikTok exports and secondhand mentions against
the latest run's venues without touching the network. `sgfh run --diff-only` recomputes the diff
of the latest run against the previous one, rewrites the run note and sends notifications.

### Keys

| variable | used by | how to get it |
| --- | --- | --- |
| `GOOGLE_PLACES_API_KEY` | Google Maps via Places API (New) | Google Cloud console, enable "Places API (New)", restrict the key to it |
| `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT` | Reddit | reddit.com/prefs/apps, "script" app; user agent like `sgfoodhunt/0.1 by u/yourname` |

Without a key the source is skipped and the run note says so. Blogs, Michelin, the booking
platforms and OneMap (postal code geocoding) need no keys.

## What a run produces

```
data/runs/<run_id>/run.json          manifest and stats
data/runs/<run_id>/raw/<source>.jsonl raw sightings, one per (source, query, venue)
data/runs/<run_id>/pages.jsonl        every page that produced evidence, with publication date
data/runs/<run_id>/events.jsonl       warnings, skipped sources, errors
data/runs/<run_id>/venues.json        canonical venues seen in this run (after dedup)
data/runs/<run_id>/merges.jsonl       every fuzzy / phone / booking-link merge, for review
data/runs/<run_id>/scores.json        per category scores with every weighted component
data/venues.json                      the venue registry: stable ids (v00001...) across runs
data/exports/<run_id>/raw_candidates.{csv,json}
data/exports/<run_id>/<category>.csv  ranked list per category
data/exports/<run_id>/venues_ranked.json  one merged JSON with every venue and its scores
logs/<timestamp>-run.jsonl            structured per run log
<vault>/SG Food Hunt/Runs/<run_id>.md run note: sources, categories, most surfaced names, warnings
<vault>/SG Food Hunt/Venues/<Venue>.md   one note per outlet; all data as properties
<vault>/SG Food Hunt/Categories/<Category>.md  top 15 table + full ranking + hidden + excluded
<vault>/SG Food Hunt/Home.md, Sources.md
```

Chains get one note per outlet, named `Brand (Outlet)`, with a shared `brand` property.

### Location, MRT and review analysis (stage 3)

- Coordinates come from Google Places; venues without them are geocoded by postal code through
  OneMap's public search endpoint (cached for a year). No driving distance is computed.
- The nearest MRT station and its lines (no walking time, by request) come from
  a bundled station table in `sgfoodhunt/data/mrt_stations.json` (MRT only, approximate
  coordinates). Refresh it from the data.gov.sg "LTA MRT Station Exit" GeoJSON with
  `python scripts/update_mrt_stations.py <file.geojson>`.
- Review analysis is lexicon based and runs over the stored anonymised snippets and article
  snippets: per category keyword counts, aspect scores for food, service, ambience and value
  (0 to 1, 0.5 neutral, omitted when nothing mentions the aspect), a noise level, and a rating
  trend. The trend uses the tool's own run-to-run Google rating history once two runs are at
  least 30 days apart; before that it compares the last 12 months of stored reviews with the
  overall rating.
- The `summary` and `best_for` fields are generated from structured data only (cuisine, area,
  price band, MRT, source count, Michelin, aspect strengths and weaknesses, trend). They never
  copy review or article text.

### The personal layer lives in the venue notes

Open a venue note in Obsidian and set properties:

| property | effect |
| --- | --- |
| `status: excluded` | the venue disappears from every ranking, CSV and JSON |
| `status: visited` | hidden from rankings when you run with `--hide-visited`; the note stays so Dataview can still list it |
| `status: wishlist` | no effect on scores; handy for Dataview queries |
| `my_rating: 4` | blended into the score: `(1 − p)·score + p·my_rating/5`, `p = scoring.personal_rating_weight` |
| `my_comment` | free text, preserved |

Anything you write under a heading that starts with `## My` (for example `## My notes`) is
also preserved when the note is regenerated. Everything else in the note is rewritten each run.

## Adding a category

Add an entry to `config/categories.yaml`. No code changes are needed.

```yaml
  - key: supper_date
    display_name: Best late night supper spots for a date
    group: dating              # dating | family | general (picks the keyword lexicon)
    party_size: 2
    queries:
      - "best late night supper Singapore"
      - "新加坡 宵夜 推荐"
    keywords: ["supper", "late night"]
    weights: {ambience: 0.15, quiet: 0.05}   # merged over defaults, normalised at run time
    hard_filters: {open_weekends: true}
```

## Adding a source

1. Add an entry to `config/sources.yaml` with `scraper` naming a class in
   `sgfoodhunt/scrapers/__init__.py`'s registry.
2. For a WordPress style blog, a subclass of `ListicleBlogScraper` with an `article_pattern`
   regex is usually all that is needed (or just set `options.article_pattern` and
   `options.content_selectors` in YAML on an existing class).
3. For a site with result cards, subclass `CardSearchScraper` and set `default_selectors`;
   JSON-LD `Restaurant` data is used automatically when present. Selectors can be overridden per
   site from YAML with `options.selectors`.
4. For an API, subclass `BaseScraper`, use `self.ctx.api_factory(...)` for an `AsyncApiClient`,
   and implement `missing_credentials()`.
5. Save a fixture under `tests/fixtures/` and add a test; every parser is tested offline.

Set `tos_status` honestly. `disallowed` sources never run. `unverified` sources run only after
the live robots.txt check passes.

## Ranking

For each category, every venue gets a score in `[0, 1]`:

```
score = Σ_c  w_c · component_c            (weights from categories.yaml, normalised)
      + michelin_bonus                    (if any Michelin distinction)
      + multi_source_bonus                (if ≥ multi_source_threshold independent sources)
      + min(buzz_bonus_max, buzz)         (social buzz, capped at 5% by default)
      − declining_trend_penalty           (rating trend = declining)
      − poor_hygiene_penalty              (SFA grade C or worse)
      − temporarily_closed_penalty        (business status CLOSED_TEMPORARILY)
closed permanently → excluded; hard filters → excluded; status: excluded → excluded
personal blend: score = (1 − p) · score + p · my_rating / 5    (p = personal_rating_weight)
```

Components:

- `rating`: Bayesian average `(C·m + n·r) / (C + n)` scaled to `[0, 1]`, with `C =
  bayesian_prior_reviews` and `m = bayesian_prior_rating`, so 4.9 with 12 reviews does not beat
  4.6 with 2,000.
- `recommendations`: Σ over independent sources of `0.5^(age_months / half_life)`, with anything
  older than `recommendation_stale_after_years` weighted at 0.05, normalised by the category's
  best venue.
- `keyword_match`: category keyword mentions in review text and articles, log scaled.
- `food`, `service`, `ambience`, `value`: aspect sentiment scores from review text.
- `space`, `kid_friendly`, `parking`, `weekend_open`, `quiet`: 0/1 flags (or graded where the
  data allows, e.g. parking own carpark 1.0, public nearby 0.7, street 0.4, none 0).

A component with no data for a venue is dropped and its weight is redistributed over the
components that do have data, so a blog-only venue is still scored (on fewer signals). Every
weighted component and adjustment is written to `data/runs/<run_id>/scores.json` and to the venue
note's `scores` property, so you can see why a venue ranks where it does.

**Newly opened categories** (`new_cafes`, `new_restaurants`, `new_zichar`) keep only venues whose
earliest evidence (article date, first Google review, or first sighting by this tool) is within
the last `scoring.new_within_days` days (30). Everything older is listed under "Excluded" in the
category note with the reason.

**Dedup** matches sightings by Google place id, then normalised name + postal code, then a shared
phone number or booking link (catches renamed or relocated venues), then fuzzy name
(`token_set_ratio` ≥ `scoring.fuzzy_match_threshold`) when a postal code is missing. Every
non-exact merge is logged to `merges.jsonl`. Reddit candidates (low confidence) and SFA rows never
create a venue; they only attach to one another source already found.

Tune the weights per category in `config/categories.yaml` and the global constants under
`scoring` in `config/settings.yaml`.

## Social buzz module (optional)

Off by default. Turn it on with `social.enabled: true` in `config/settings.yaml`. Instagram and
TikTok are never scraped; signals come only from these routes, each with its own parser:

1. **Your own exports.** Drop the unzipped folders under `social_exports/` (the folder name must
   start with `instagram` or `tiktok`):
   - Instagram: Settings → Accounts Center → Your information and permissions → Download your
     information → select **Saved** and **Likes**, format **JSON**. Unzip into
     `social_exports/instagram/`. The tool reads `saved_posts.json` and `liked_posts.json`
     (post URL, creator handle, date; Instagram does not export captions of others' posts, so
     matching is by creator handle).
   - TikTok: Settings → Account → Download your data → format **JSON**. Unzip into
     `social_exports/tiktok/`. The tool reads favourite and liked videos, then calls TikTok's
     public oEmbed endpoint for each video's caption and creator (cached 180 days).
2. **Secondhand mentions.** Counts "tiktok", "instagram", "viral", "insta worthy" in stored
   review snippets and article snippets per venue (`social_secondhand` property).
3. **Search engine index.** With `SERP_API_KEY` set (SerpAPI shaped endpoint,
   `social.serp_endpoint` to change), every category query runs restricted to
   `social.serp_sites`; only the result URL, title and snippet are stored. Post pages are never
   fetched.
4. **Instagram Hashtag Search API.** With `INSTAGRAM_GRAPH_TOKEN` and
   `INSTAGRAM_BUSINESS_ACCOUNT_ID` set and `social.hashtags` listed (max 25), recent media
   captions and timestamps are stored. A local log (`data/social/hashtag_log.json`) keeps you
   under the 30 unique tags per week platform limit. Not configured → skipped silently.

Mentions are matched to venues by creator handle, then by venue names or hashtags inside the
caption (fuzzy). Unmatched captions go to `data/social/unmatched.jsonl` with the best guesses;
set `resolved_venue_id` on a row and the next run treats it as a manual match. Mentions live in
`data/social/mentions.jsonl` with source, URL, caption, creator handle, post date and matched
venue id. No commenter or viewer data is ever stored.

**Buzz score:** distinct mentions in the last `social.buzz_window_months` with recency decay
(`0.5^(age_days / buzz_half_life_days)`), normalised across venues, and added to the ranking as
at most `scoring.buzz_bonus_max` (5% by default). A venue with at least
`social.trending_threshold` mentions in the window gets `trending_social: true`.

## Diff report and notifications

Every `sgfh run` (and `sgfh rank`) compares the run with the previous scored run and writes
`data/runs/<run_id>/diff.json` plus a "Diff" section in the run note:

- venues that entered or left each category's top 15
- Google rating changes of at least `notifications.rating_change_threshold` (0.2)
- newly closed (and reopened) venues
- new venues, new sources found per venue, and new social mentions

Set `notifications.telegram: true` and/or `notifications.email: true` in settings.yaml and the
matching variables in `.env` (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`; `SMTP_HOST`, `SMTP_PORT`,
`SMTP_USER`, `SMTP_PASSWORD`, `REPORT_EMAIL_TO`) to receive the diff as plain text. An empty diff
sends nothing.

## Dashboard

```bash
pip install -e ".[dashboard]"
streamlit run sgfoodhunt/dashboard/app.py -- --config config
```

Filters: category, region, budget, halal, kid friendly, nearest MRT (replaces "distance from
home" since there is no home location), trending on social, hide closed. Shows the ranked table
with booking links, a map of the filtered venues, and recent social mention counts. It reads
`data/venues.json` and the latest `scores.json`, so it works on the NAS without the vault.

## Google Sheets export (optional)

Set `exports.google_sheets: true`, install `pip install -e ".[sheets]"`, share a spreadsheet
with a service account and set `GOOGLE_SHEETS_CREDENTIALS_JSON` (path to its key file) and
`GOOGLE_SHEETS_SPREADSHEET_ID`. Each run rewrites one worksheet per category.

## Deploying on a NAS with Docker (with Telegram)

Everything the container needs is mounted from the host, so the image is stateless and the data
survives rebuilds.

1. On the NAS, clone or copy the repo into a share (for example `/volume1/docker/SG-Cafe-Food-Hunt`).
2. `cp .env.example .env` and fill in at least `GOOGLE_PLACES_API_KEY`, `TELEGRAM_BOT_TOKEN`
   and `TELEGRAM_CHAT_ID` (create the bot with @BotFather, send it a message, then read your chat
   id from `https://api.telegram.org/bot<TOKEN>/getUpdates`).
3. In `config/settings.yaml` set `notifications.telegram: true`. Leave `paths.vault_dir: vault`;
   the compose file mounts your real vault at `/app/vault`.
4. In `docker-compose.yml` change the vault volume line to your Obsidian vault folder on the NAS
   and, if you like, the `SGFH_SCHEDULE` (default Monday 03:17 Singapore time).
5. Build and check:

```bash
docker compose build
docker compose run --rm sgfoodhunt doctor          # paths writable, keys present, schedule valid
docker compose run --rm sgfoodhunt notify-test     # a test message arrives in Telegram
docker compose run --rm sgfoodhunt run -c zichar_family -s michelin -s sethlui   # small live run
docker compose up -d                               # scheduler: runs weekly, sends the diff
docker compose logs -f                             # watch it
```

`docker compose run --rm sgfoodhunt run --dry-run` reruns from the cache without network.
Set `SGFH_RUN_ON_START: "true"` in the compose file to trigger a run when the container starts.
The container's healthcheck runs `sgfh doctor --quiet` every five minutes.

Synology users: Container Manager can build from the compose file (Project → Create → choose the
folder). Make sure the shared folder holding the vault is mounted with write permission.

For the dashboard, uncomment the `ports` and `command: ["dashboard"]` lines (the image would also
need `pip install .[dashboard]`; add it to the Dockerfile's pip line) and open
`http://<nas-ip>:8501`.

## Scheduling

Weekly cron on the NAS (see `scripts/cron.example`), which keeps the vault updated in place:

```
17 3 * * 1  cd /volume1/SG-Cafe-Food-Hunt && .venv/bin/sgfh run >> logs/cron.log 2>&1
```

`.github/workflows/weekly.yml` runs the same pipeline on GitHub Actions every Monday 03:17 SGT
(or on demand), restores the cache and registry between runs, and uploads the exports, run
folders and a throwaway vault as an artifact. Add the API keys as repository secrets. Because the
vault is on your NAS, the Actions run cannot update it; use it for the notifications and CSVs, or
as a backup when the NAS is off.

## Politeness, legal and ethical notes

- robots.txt is fetched, cached for a week and obeyed for every static page and browser fetch.
  A 401/403 on robots.txt is treated as "disallow everything". `Crawl-delay` is honoured.
- Every request waits a random 2–5 s per domain, identifies itself with a descriptive
  `User-Agent`, and retries with exponential backoff only on transient errors.
- Raw responses are cached with an expiry so reruns skip unchanged pages.
- **No personal data is stored** (PDPA): review author names, profile links, Reddit usernames,
  commenter data and photo attributions are dropped on ingest. Only ratings, dates and short
  anonymised snippets (≤ 300 characters) are kept.
- **Google Maps** is only accessed through the official Places API. Popular times are not in the
  official API and are not collected. Google Reserve links are not discoverable without scraping
  Google Maps, so the tool records the `reservable` flag and the Google Maps listing link instead.
- **Instagram and TikTok** are never scraped. Social signals (stage 4) come only from your own
  data exports, the public TikTok oEmbed endpoint, a SERP API, and the official Instagram Hashtag
  Search API.
- **TripAdvisor** is disabled and marked `disallowed`: its terms prohibit automated access
  without a Content API licence.
- **Burpple, HungryGoWhere, Chope, Quandoo, TableCheck, the blogs, Michelin**: their terms were
  not verified from the build environment (outbound access to those hosts was blocked). They
  ship as `tos_status: unverified`: review each site's terms yourself before your first live
  run, and set `disallowed` for any that forbid crawling. Parsers were written against saved
  fixtures, so a live page may need selector updates; a source that parses nothing logs a
  warning in the run note rather than failing.
- **SFA hygiene grades** use data.gov.sg under the Singapore Open Data Licence; set
  `options.dataset_id` in `sources.yaml` to the current dataset (the placeholder is skipped).
- Blog text is never copied into outputs beyond short snippets; summaries (stage 3) are
  generated in the tool's own words.

## Development

```bash
ruff check . && ruff format --check .
mypy
pytest
```

CI runs the same three on every push (`.github/workflows/ci.yml`).

## Stage plan

1. Sources and storage (done)
2. Normalisation, dedup, scoring, venue and category notes (done)
3. Enrichment (OneMap geocoding, nearest MRT station) and review analysis (done)
4. Social buzz module (exports, oEmbed, SERP, hashtag API) (done)
5. Diff report, notifications, Streamlit dashboard, weekly workflow, Sheets export (done)
