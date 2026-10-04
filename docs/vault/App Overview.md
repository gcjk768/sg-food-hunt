---
tags: [active]
updated: 2026-10-04
---
# App Overview

Pipeline: scrape (`sgfoodhunt/scrapers/`) → dedup (`sgfoodhunt/dedup/registry.py`) → enrich (`sgfoodhunt/enrich/`) → rank (`sgfoodhunt/ranking.py`) → vault notes (`sgfoodhunt/reporting/`) → diff (`sgfoodhunt/diff.py`) → notify (`sgfoodhunt/notify.py`). CLI in `sgfoodhunt/cli.py` (`sgfh`); scheduler `sgfoodhunt/schedule.py`. Full design: `docs/DESIGN.md`.

## Docker
`Dockerfile` + `docker-compose.yml`; `docker/entrypoint.sh` (`serve` = weekly scheduler, `sgfh serve` default `mon 03:17`; `docker-compose.yml` and the NAS run `daily 03:17`; `_notify()` in `sgfoodhunt/cli.py` posts one card per venue that entered a top list or is new to a top 3, built by `venue_messages()` in `sgfoodhunt/diff.py`; no summary text on Telegram). State in bind mounts: `config/ data/ logs/ vault/ social_exports/ claude-config/`. `.gitattributes` forces LF on `*.sh` so Windows builds work.

## Sources (live status 2026-09-29)
Working: Eatbook, Honeycombers, The Smart Local, Sassy Mama (family only), Seth Lui, Miss Tam Chiak, Burpple. Google Places/Reserve disabled by choice (paid API; ~370 req/month at `max_pages: 1` would fit the free cap if ever wanted). Reddit needs keys. SFA needs a real `dataset_id`. Everything else is disabled in `config/sources.yaml` with the reason next to it — re-check those before re-enabling.

## Telegram
`.env`: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID=<TELEGRAM_CHAT_ID>` (the owner Channel), `TELEGRAM_THREAD_ID=<THREAD_ID>` (topic "Food Hunt"; 2765 is the recipe bot's "Recipe" topic) → `message_thread_id` in `sgfoodhunt/notify.py`. Needs `notifications.telegram: true` in `config/settings.yaml`. Test: `docker compose run --rm sgfoodhunt notify-test`. Format: one HTML card per venue that entered a top list or is new to a top 3 (`venue_messages()` / `_card()` in `sgfoodhunt/diff.py`), not the diff text. Card (2026-10-04, simplified): `🍽 <b>name</b> 🆕 <b>NEW</b>` / `✅ Worth going · reason` or `❌ Not worth going · reason` (`verdict()` in `sgfoodhunt/diff.py`) / `📍 <code>address</code> · 🚇 MRT` / `🍴` cuisine · price · ★ / `💬` summary / `🔗` link. Only cafes (`cafes_date`, `brunch_date`, `new_cafes`) zi char (`zichar_family`, `new_zichar`) restaurants for 4 under S$200 (`restaurants_4pax`) and hawker stalls (`hawker_family`, Michelin Bib Gourmand included) are enabled; the rest have `enabled: false`. All sends go through `send_telegram()` in `sgfoodhunt/notify.py`: `parse_mode=HTML`, no link previews, `split_message()` splits between blocks (never inside a tag), and a 400 "can't parse entities" resends as plain text (`html_to_plain()`, links kept as `label (url)`). Escape every dynamic value with `esc()` (`sgfoodhunt/notify.py`). Email gets the plain-text version of the cards.

## Special occasion list
`sgfoodhunt/occasion.py`: one message every 14 days (after a run) of restaurants over S$200 for 4, Michelin first (⭐ per star), `Worth` and `Buffet` tags. `sgfh occasion --force` sends it now. State: `data/occasion_state.json`.

## Promotions
`sgfoodhunt/promos.py`: weekly 🎁 PROMOS message (one Telegram message, `sgfh promos --force` sends now) of current deals at the top venues of every enabled list, found with Claude web search (`AiClient.ask(web=True)`, the only AI task with tools). Each deal needs a source link; expired deals are dropped. The special occasion list gets the same 🎁/🎄 line per venue. State: `data/promos_state.json`. Switch off with `ai.tasks.promo_search: false`.

## Vault memory (movement log)
`sgfoodhunt/reporting/memory.py`, per the NAS vault standard. **Write:** `log_activity()` → `SG Food Hunt/Activity/YYYY-MM-DD.md`, one line per event in SGT (run started/finished/failed, new venues, top-15 entered/left/moved, card sent/skipped, send failed). Venue notes keep an append-only `## History` (`history_entry()`, carried over by `write_venue_notes()` in `sgfoodhunt/reporting/venue_notes.py`; `append_history()` for cards). **Read:** `_memory()` in `sgfoodhunt/ranking.py` builds a ≤4,000-char excerpt (current + last-run ranks, History, 30-day Activity lines for the venue, newest first) for top-list venues only and passes it to `analyse_reviews()` (`sgfoodhunt/ai/tasks.py`). `_notify()` (`sgfoodhunt/cli.py`) skips a card when `last_card()` shows it went out at the same ranks (`card_ranks()` in `sgfoodhunt/diff.py`). Everything is best-effort: errors are logged, never raised. Files are 0666 via the image's `umask 000`.

## AI layer
`sgfoodhunt/ai/client.py` shells out to `claude -p`; needs `CLAUDE_CODE_OAUTH_TOKEN` or a CLI login in `claude-config/`. Falls back to rules without it.

## NAS deployment
Dockge stack `sg-food-hunt` at `/volume1/docker/sg-food-hunt` (UI http://<LAN_IP>:5001/compose/sg-food-hunt). The NAS `docker-compose.yml` differs from the repo one: chat/topic set in `environment`, vault = `/volume1/<USER>/Obsidian` (notes in `SG Food Hunt/`; can be narrowed to `"/volume1/<USER>/Obsidian/SG Food Hunt:/app/vault/SG Food Hunt"`, no code change), Claude = trading-desk's login, shared by mounting `/volume1/docker/trading-desk/.home/.claude` at `/app/claude-config` (same Max account; `claude -p` verified 2026-09-29). `config/settings.yaml` there has `notifications.telegram: true`. Code updates (since 2026-09-30): push to GitHub; in the container terminal (Dockge → sgfoodhunt → Bash) download `https://github.com/gcjk768/sg-food-hunt/archive/<sha>.tar.gz` into `/app/data/_src` (= `./data/_src` on the NAS; the compose `build.context`); then in Dockge edit `image: sgfoodhunt:<sha>` and Deploy (new tag → rebuild). NAS Docker has no git, so a GitHub URL as build context fails. Never overwrite the NAS `config/` (Telegram is on there).
