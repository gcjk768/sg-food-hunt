---
tags: [active]
updated: 2026-09-29
---
# Changelog

## 2026-09-29 (live-run fixes)
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
