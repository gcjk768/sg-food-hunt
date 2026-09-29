---
tags: [active]
updated: 2026-09-29
---
# Changelog

## 2026-09-29
- fix: AI response cache used `ai:<hash>` filenames; `:` is illegal on Windows → `sgfoodhunt/http/cache.py` now maps it to `_`.
- fix: `docker/entrypoint.sh` checked out CRLF on Windows → container failed to start; added `.gitattributes` (`*.sh eol=lf`).
- feat: Telegram forum topics via `TELEGRAM_THREAD_ID` (`sgfoodhunt/notify.py`, `sgfoodhunt/config.py`); Telegram errors now include the API's description.
- chore: compose vault mount defaults to `./vault`; `mem_limit: 1536m` for the NAS.
- ops: deployed as Dockge stack on the NAS (see [[App Overview]] → NAS deployment).
