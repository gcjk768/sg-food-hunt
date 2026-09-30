---
tags: [active]
updated: 2026-09-29
---
# App Overview

Pipeline: scrape (`sgfoodhunt/scrapers/`) → dedup (`sgfoodhunt/dedup/registry.py`) → enrich (`sgfoodhunt/enrich/`) → rank (`sgfoodhunt/ranking.py`) → vault notes (`sgfoodhunt/reporting/`) → diff (`sgfoodhunt/diff.py`) → notify (`sgfoodhunt/notify.py`). CLI in `sgfoodhunt/cli.py` (`sgfh`); scheduler `sgfoodhunt/schedule.py`. Full design: `docs/DESIGN.md`.

## Docker
`Dockerfile` + `docker-compose.yml`; `docker/entrypoint.sh` (`serve` = weekly scheduler, default `mon 03:17` SGT; the NAS runs `daily 03:17`). State in bind mounts: `config/ data/ logs/ vault/ social_exports/ claude-config/`. `.gitattributes` forces LF on `*.sh` so Windows builds work.

## Sources (live status 2026-09-29)
Working: Eatbook, Honeycombers, The Smart Local, Sassy Mama (family only), Seth Lui, Miss Tam Chiak, Burpple. Google Places/Reserve disabled by choice (paid API; ~370 req/month at `max_pages: 1` would fit the free cap if ever wanted). Reddit needs keys. SFA needs a real `dataset_id`. Everything else is disabled in `config/sources.yaml` with the reason next to it — re-check those before re-enabling.

## Telegram
`.env`: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID=<TELEGRAM_CHAT_ID>` (the owner Channel), `TELEGRAM_THREAD_ID=<THREAD_ID>` (topic "Food Hunt"; 2765 is the recipe bot's "Recipe" topic) → `message_thread_id` in `sgfoodhunt/notify.py`. Needs `notifications.telegram: true` in `config/settings.yaml`. Test: `docker compose run --rm sgfoodhunt notify-test`. Format: one message per venue newly in a category's top 3 (`venue_messages()` in `sgfoodhunt/diff.py`), not the diff text.

## AI layer
`sgfoodhunt/ai/client.py` shells out to `claude -p`; needs `CLAUDE_CODE_OAUTH_TOKEN` or a CLI login in `claude-config/`. Falls back to rules without it.

## NAS deployment
Dockge stack `sg-food-hunt` at `/volume1/docker/sg-food-hunt` (UI http://<LAN_IP>:5001/compose/sg-food-hunt). The NAS `docker-compose.yml` differs from the repo one: chat/topic set in `environment`, vault = `/volume1/<USER>/Obsidian` (notes in `SG Food Hunt/`), Claude = trading-desk's login, shared by mounting `/volume1/docker/trading-desk/.home/.claude` at `/app/claude-config` (same Max account; `claude -p` verified 2026-09-29). `config/settings.yaml` there has `notifications.telegram: true`. Code updates (since 2026-09-30): push to GitHub; in the container terminal (Dockge → sgfoodhunt → Bash) download `https://github.com/gcjk768/sg-food-hunt/archive/<sha>.tar.gz` into `/app/data/_src` (= `./data/_src` on the NAS; the compose `build.context`); then in Dockge edit `image: sgfoodhunt:<sha>` and Deploy (new tag → rebuild). NAS Docker has no git, so a GitHub URL as build context fails. Never overwrite the NAS `config/` (Telegram is on there).
