---
tags: [active]
updated: 2026-10-01
---
# Changelog

## 2026-10-01
- feat: Telegram venue cards restyled to the HTML card standard — same fields, same order; bold name with the best rank, emoji-led lines, address in `<code>`, booking link as a "Book a table"/"Website" label, sources as domain links in an expandable quote (`_card()` in `sgfoodhunt/diff.py`). `send_telegram()` (`sgfoodhunt/notify.py`) now sends `parse_mode=HTML`, splits between blocks (`split_message()`), and resends as plain text if Telegram can't parse the HTML. All dynamic text (incl. LLM summaries) is escaped with `esc()`. `notify-test` (`sgfoodhunt/cli.py`) sends a card too; email gets plain-text cards.
- fix: sync NAS hotfix — `Dockerfile` healthcheck timeout 20s → 180s + 3m start period (doctor takes 40-90s to import on the NAS); was a NAS-only compose override.

## 2026-09-30
- feat: Telegram sends **only venue cards** (🍽 name / #rank · list / 📍 / summary / 🔗 / Sources), the owner's fixed format. `venue_messages()` (`sgfoodhunt/diff.py`) now also cards any venue that entered a top list (e.g. #15), not just new top-3 picks. `_notify()` (`sgfoodhunt/cli.py`) no longer sends the text summary to Telegram (email still gets it). Replaces the summary-first message from earlier today.
- feat: Telegram now posts on **any** change, not only new top-3 venues. `_notify()` (`sgfoodhunt/cli.py`) sends the diff summary (new venues, rating changes, closures, reopenings, social mentions, top-3 moves) first, then one card per new top pick. It stays silent only when nothing changed. The repo `docker-compose.yml` now defaults to `daily 03:17` to match the NAS.
- chore(nas): NAS stack now runs daily — `SGFH_SCHEDULE: daily 03:17` in `/volume1/docker/sg-food-hunt/docker-compose.yml` (was `mon 03:17`). Posts only venues new to a top 3, so quiet days send nothing.
- fix: month headings of monthly round-ups ("July 2026") became venues. `is_date_only()` (`sgfoodhunt/models.py`) rejects them in `VenueCandidate`; `prune_blog_evidence()` clears saved ones.
- fix: hotels ranked #1 for hawker/zi char. Registry evidence persists across runs, so articles older runs opened for every query kept every category. `prune_blog_evidence()` (`sgfoodhunt/dedup/registry.py`) re-checks stored blog evidence each run and re-derives its categories (dropped 2,652 stale entries on the NAS registry). `relevant_article()` is stricter: generic food words don't count, a 2+-word query needs 2 hits, "new/latest" queries need an openings slug.
- fix: headline names ("New restaurant: X", "X is opening at …", "New dining concept: X") → "X". `strip_news_wording()` in `sgfoodhunt/scrapers/html.py`, applied by the registry (`sgfoodhunt/dedup/registry.py`) on load + create so names saved by older runs are fixed too (the registry keeps the first name it saw).
- fix: second run crashed in `compute_diff` (`sgfoodhunt/diff.py`) — `business_status` is stored as null and `.get(k, "")` only defaults a *missing* key; now `(… or "")`.
- docs: README rewritten (highlights, flow, stack, limitations; fixed stale clone URL); architecture diagram added as `docs/architecture.drawio` + `.drawio.svg` + `.png`.
- fix: blog search pages also link sidebar/"latest" posts that ignore the query; they came back for every query (~85x), so baby classes, cruises, JB guides etc. landed as venues in all 15 categories. `relevant_article()` in `sgfoodhunt/scrapers/blogs.py` now opens a link only if its slug names food, isn't an overseas guide, and shares a word with the query (Chinese queries on these English blogs are dropped).
- fix: "New menu: X" / "New restaurant: X" headings → "X" (`NEWS_PREFIX_RE`, `sgfoodhunt/scrapers/html.py`).
- feat: Telegram gets one message per restaurant — every venue newly in a category's top 3 (all top 3s on a first run), with ranks, address/MRT, summary, link, sources (`venue_messages()` in `sgfoodhunt/diff.py`). Sends are paced ~3 s apart and retry once on 429 (`sgfoodhunt/notify.py`). Email still gets the full diff + cards.

## 2026-09-29 (live-run fixes)
- chore: Google Places + Reserve disabled (owner skipped the paid API).
- fix: container-written files were 0600 root (tempfile.mkstemp) → vault notes unreadable in Obsidian; `sgfoodhunt/http/cache.py` now applies umask perms, `docker/entrypoint.sh` sets `umask 000`.
- fix: Seth Lui (Elementor) had no content container → `.elementor-widget-theme-post-content` in `sgfoodhunt/scrapers/blogs.py`; search links scoped to `.e-loop-item`.
- fix: single-venue "Name: address | Tel | Hours" headings now parsed (`sgfoodhunt/scrapers/html.py`).
- fix: blogs only follow same-site links (Miss Tam Chiak's Facebook link hit robots.txt and skipped the source for the whole run).
- fix: Burpple and Chope selectors updated to 2026-09 markup; Chope search URL from its SearchAction JSON-LD.
- chore: disabled Quandoo (closing), TableCheck + Chope (search ignores query), HungryGoWhere (500s), Daniel Food Diary (bot challenge), TimeOut (JS search), Tatler + Michelin (robots.txt). Reasons inline in `config/sources.yaml`.
- feat: optional `WITH_BROWSER` build arg (Playwright + Chromium), off by default.
- fix: AI calls always failed with a subscription login — `--bare` only accepts ANTHROPIC_API_KEY; now passed only when that key is set (`sgfoodhunt/ai/client.py`). CLI errors now log `terminal_reason`/`result` instead of the usage preamble.

## 2026-09-29
- fix: AI response cache used `ai:<hash>` filenames; `:` is illegal on Windows → `sgfoodhunt/http/cache.py` now maps it to `_`.
- fix: `docker/entrypoint.sh` checked out CRLF on Windows → container failed to start; added `.gitattributes` (`*.sh eol=lf`).
- feat: Telegram forum topics via `TELEGRAM_THREAD_ID` (`sgfoodhunt/notify.py`, `sgfoodhunt/config.py`); Telegram errors now include the API's description.
- chore: compose vault mount defaults to `./vault`; `mem_limit: 1536m` for the NAS.
- ops: deployed as Dockge stack on the NAS (see [[App Overview]] → NAS deployment).
