# Design: project structure, config schema, storage schema

This is the outline for the whole project. Stage one (sources and storage) is implemented; the
rest of the modules are listed so the structure is fixed up front.

There is **no database**. The project lives on a NAS share, so every piece of state is a plain
file (JSON, JSONL, Markdown). SQLite over SMB/NFS is unreliable because of file locking, and
plain files are what Obsidian reads anyway. All human facing output goes into an Obsidian vault
folder; all machine state goes into `data/`.

## Project structure

```
SG-Cafe-Food-Hunt/
├── config/
│   ├── settings.yaml          home postal code, paths, HTTP politeness, scoring globals, flags
│   ├── categories.yaml        one entry per category (queries, party size, weights, filters)
│   └── sources.yaml           one entry per source (scraper class, URLs, ToS status, options)
├── sgfoodhunt/
│   ├── cli.py                 `sgfh` entry point (typer)
│   ├── config.py              pydantic models for the three YAML files + secrets from .env
│   ├── models.py              SearchQuery, VenueCandidate, SourcePage, ScrapeResult
│   ├── logging_setup.py       console + per run JSONL log file
│   ├── pipeline.py            Collector: runs sources × categories × queries, stores results
│   ├── http/
│   │   ├── cache.py           file based response cache with expiry (+ atomic write helpers)
│   │   ├── robots.py          robots.txt lookup, cached, RFC 9309 semantics
│   │   ├── ratelimit.py       per domain polite delay; per API requests-per-minute limiter
│   │   ├── client.py          PoliteClient (requests) and AsyncApiClient (httpx) with retries
│   │   └── browser.py         optional Playwright fetcher for `fetch: browser` sources
│   ├── scrapers/
│   │   ├── base.py            BaseScraper interface + ScraperContext
│   │   ├── html.py            listicle extraction, JSON-LD, result card parsing
│   │   ├── google_places.py   Places API (New) text search
│   │   ├── booking.py         Chope, Quandoo, TableCheck, Google Reserve
│   │   ├── aggregators.py     Burpple, HungryGoWhere
│   │   ├── blogs.py           Eatbook, Seth Lui, Daniel Food Diary, Miss Tam Chiak, Time Out,
│   │   │                      Honeycombers, The Smart Local, Sassy Mama, Tatler Dining
│   │   ├── michelin.py        Michelin Guide Singapore listing (catalogue source)
│   │   ├── reddit.py          Reddit official API (r/singaporeeats, r/singapore)
│   │   ├── sfa.py             SFA hygiene grades via data.gov.sg (catalogue, enrich only)
│   │   └── tripadvisor.py     disabled by default (ToS)
│   ├── storage/
│   │   ├── runs.py            RunStore: data/runs/<run_id>/{run.json, raw/*.jsonl, pages.jsonl, events.jsonl}
│   │   ├── frontmatter.py     Markdown + YAML frontmatter round trip, user edit preservation
│   │   └── vault.py           Vault: paths and note writing inside the Obsidian folder
│   ├── reporting/
│   │   ├── notes.py           Home, Sources and Runs/<run_id> notes
│   │   ├── venue_notes.py     Venues/<Venue>.md and Categories/<Category>.md, personal layer reader
│   │   └── exports.py         CSV / JSON exports (raw, per category, merged)
│   ├── ranking.py             stage 2 driver: raw rows -> venues -> scores -> notes and exports
│   ├── normalise/             names, price levels, cuisine labels, opening hours, region from postal code
│   ├── dedup/                 venue model + registry (place id, name+postal, phone/booking link, fuzzy)
│   ├── scoring/               Bayesian rating, recency-decayed recommendations, weights, filters, penalties
│   ├── enrich/                OneMap postal geocoding, nearest MRT station and lines
│   ├── reviews/               keyword counts, aspect scores, noise, rating trend, summary, best-for
│   ├── data/mrt_stations.json bundled MRT station table (refresh: scripts/update_mrt_stations.py)
│   ├── social/                exports.py (IG/TikTok parsers), oembed.py, serp.py, hashtags.py,
│   │                          secondhand.py, matcher.py, store.py, buzz.py, pipeline.py
│   ├── diff.py                run-to-run diff (top list churn, ratings, closures, new venues/sources/mentions)
│   ├── notify.py              Telegram bot and SMTP email delivery of the diff
│   ├── sheets.py              optional Google Sheets export (gspread)
│   └── dashboard/             Streamlit app (app.py) over data/venues.json + latest scores.json
├── tests/                     fixtures/ (saved HTML and JSON) + one test module per area
├── scripts/cron.example       weekly cron line
└── .github/workflows/ci.yml   ruff, mypy, pytest on every push
```

## Config schema

### `settings.yaml`

| key | purpose |
| --- | --- |
| `paths.vault_dir`, `paths.vault_folder` | Obsidian vault and the subfolder this tool owns |
| `paths.data_dir`, `paths.cache_dir`, `paths.exports_dir`, `paths.logs_dir` | machine state |
| `http.*` | user agent, polite delay range (2–5 s), timeout, retries, backoff, robots TTL, cache TTL |
| `api_rate_limits` | requests per minute per official API |
| `scoring.*` | top_n, fuzzy threshold, per-pax prices, new-venue window, Bayesian prior, recency half-life, bonuses and penalties, buzz cap, personal weight |
| `social.*` | `enabled` flag, exports dir, buzz window, hashtags (max 25), SERP sites |
| `exports.*`, `notifications.*` | CSV/JSON/Sheets flags, Telegram/email flags, rating change threshold |

### `categories.yaml`

`defaults` (weights, hard filters, per group keyword lexicons) plus a `categories` list. Each
category: `key`, `display_name`, `group` (`dating` | `family` | `general`), `party_size`,
`queries` (English and Chinese variants), `keywords`, `weights`, `hard_filters`.
Weights are merged over the defaults and normalised at run time. Recognised weight components:
`rating, recommendations, keyword_match, food, service, value, ambience, space, kid_friendly,
parking, weekend_open, quiet`. Hard filters: `exclude_closed, open_weekends,
requires_private_room, halal_only, max_price_level, opened_within_months`.

Fifteen categories ship: the twelve from the brief plus `new_cafes`, `new_restaurants` and
`new_zichar` ("what's new" lists that weight recent recommendations over review volume and keep
only venues whose earliest evidence is within the last 30 days).

### `sources.yaml`

Each source: `key`, `name`, `kind`, `scraper` (registry class name), `enabled`, `priority`,
`base_url`, `search_url` (with `{query}`), `fetch` (`static` | `browser` | `api`),
`independent` (counts towards the multi-source bonus), `tos_status`
(`verified_ok` | `unverified` | `disallowed`), `cache_ttl_hours`, `notes`, `options`.
Sources with `tos_status: disallowed` never run, whatever `enabled` says. robots.txt is checked
live on every fetch.

### `.env`

`GOOGLE_PLACES_API_KEY`, `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT`, and
later `ONEMAP_*`, `SERP_API_KEY`, `INSTAGRAM_*`, `TELEGRAM_*`, `SMTP_*`, `GOOGLE_SHEETS_*`.

## Storage schema (files, no database)

### Machine state: `data/`

```
data/
├── runs/<run_id>/            run_id = UTC timestamp, e.g. 20260929T031500Z
│   ├── run.json              {run_id, mode, categories, sources, started_at, finished_at, status, stats}
│   ├── raw/<source>.jsonl    one VenueCandidate sighting per line (see fields below)
│   ├── pages.jsonl           {source_key, url, title, published_at, content_hash, fetched_at, query, category_key}
│   ├── events.jsonl          {at, level, component, message, data}
│   ├── venues.json           canonical venues seen in this run (id, fields, evidence[], aliases[])
│   ├── merges.jsonl          every non-exact merge for review
│   ├── scores.json           {category: [ {venue_id, score, rank, components, adjustments, excluded_reason} ]}
│   └── diff.json             diff against the previous scored run
├── venues.json               the registry: every venue ever seen, stable ids v00001..., next_id
├── cache/
│   ├── http/<2 hex>/<sha256>.meta.json + .body   raw responses with expires_at
│   └── robots/<host>.json
├── social/                   mentions.jsonl (deduplicated), unmatched.jsonl (review + manual resolve), hashtag_log.json
└── exports/<run_id>/         CSV per category, merged JSON
```

Raw candidate row (`raw/<source>.jsonl`): `run_id, source_key, category_key, category_group,
party_size, query, page_url, source_ref, name, name_zh, brand, address, postal_code, lat, lng,
phone, website, booking_url, rating, review_count, price_level, price_text, cuisine[],
opening_hours{}, business_status, michelin, hygiene_grade, snippet (≤300 chars, anonymised),
confidence (0–1), extra{}, captured_at`.

### Human facing: the Obsidian vault folder

```
<vault>/SG Food Hunt/
├── Home.md                   links, latest run, Dataview examples
├── Sources.md                source registry with ToS and robots status
├── Runs/<run_id>.md          run summary; diff report from stage 5
├── Categories/<Category>.md  stage 2: top 15 table + full ranked list, wikilinks to venues
└── Venues/<Venue>.md         stage 2: one note per venue
```

Venue note frontmatter (properties, so Obsidian Bases / Dataview can filter):

```yaml
type: venue
name, name_zh, brand, outlet
address, postal_code, district, region, lat, lng
nearest_mrt, mrt_lines
cuisine: [..], halal, vegetarian_options, kid_friendly, pet_friendly
price_level: "$$", price_per_pax_sgd, bill_estimate: {cafes_date: 60, family_weekend: 180}
opening_hours: {mon: ["11:00-22:00"], ...}, open_weekends, late_night, ph_closed
booking_url, phone, walk_in_only
private_room, outdoor_seating, high_chairs, wheelchair, large_group
ambience: [romantic, quiet], noise_level
google_rating, google_reviews, ratings: {burpple: 4.2, ...}, hygiene_grade, michelin
sources: [{url, source, published_at}], source_count, independent_sources
keyword_counts: {romantic: 12, ...}, aspect_food, aspect_service, aspect_ambience, aspect_value
rating_trend: improving|stable|declining, buzz_score, trending_social
scores: {cafes_date: 0.81, ...}, ranks: {cafes_date: 3}
best_for: "quiet anniversary dinner"
first_seen, last_seen, business_status
# user owned (never overwritten):
status: visited | wishlist | excluded
my_rating, my_comment, tags
```

Body sections: `## Summary` (own words), `## Best for`, `## Signature dishes`, `## Evidence`
(source links with dates), `## Recent social mentions`, and `## My notes` (preserved verbatim).
The personal layer of the brief is therefore the venue notes themselves: `status: excluded`
removes a venue from every output, `status: visited` can be hidden with `--hide-visited`, and
`my_rating` is blended into the score with `scoring.personal_rating_weight`.

## Pipeline stages

1. **Sources and storage** (done): `sgfh run` → `Collector` → `RunStore` → run note.
2. **Dedup and scoring** (done): normalise → registry match (place id, name+postal, phone or
   booking link, fuzzy with log) → `data/venues.json` with stable ids → score per category →
   `Categories/*.md`, `Venues/*.md`, per category CSV and merged JSON. `sgfh rank` re-runs it offline.
3. **Enrichment and review analysis** (done): nearest MRT station and lines from a bundled
   station table (OneMap geocodes postal codes when Google gave no coordinates);
   no driving distance by request. Keyword counts, aspect scores, noise level, rating trend from
   run-to-run history, generated summary and best-for line, all persisted in the registry.
4. **Social buzz** (done, behind `social.enabled`): export parsers, TikTok oEmbed, SERP, hashtag
   API, handle/caption matcher, mention store, buzz score capped at 5% of the ranking.
5. **Dashboard, diff and scheduling** (done): Streamlit app, diff report in the run note and
   diff.json, Telegram/email, cron example and GitHub Actions weekly workflow, optional Google
   Sheets export.
