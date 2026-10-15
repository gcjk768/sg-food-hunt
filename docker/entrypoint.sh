#!/bin/sh
# Entrypoint: "serve" keeps the container alive and runs sgfh on the weekly schedule;
# any other arguments are passed straight to sgfh (e.g. "run --dry-run", "doctor", "notify-test").
set -e
cd /app
# Files land on a NAS share edited from Obsidian by uid 1000 (group admin, gid 10). The container
# runs as uid 0 with gid 10 (compose `user: "0:10"`), so new files are root:admin 664 / dirs 775.
umask 002
if [ ! -f /app/config/settings.yaml ]; then
  echo "config/settings.yaml is missing: mount your config folder at /app/config" >&2
  exit 1
fi
case "$1" in
  serve) shift; exec sgfh serve -C /app/config "$@" ;;
  claude) shift; exec claude "$@" ;;          # e.g. `docker compose run --rm -it sgfoodhunt claude` to log in
  shell|sh|bash) exec /bin/sh ;;
  *) exec sgfh "$@" ;;
esac
