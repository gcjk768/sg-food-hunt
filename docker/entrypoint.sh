#!/bin/sh
# Entrypoint: "serve" keeps the container alive and runs sgfh on the weekly schedule;
# any other arguments are passed straight to sgfh (e.g. "run --dry-run", "doctor", "notify-test").
set -e
cd /app
if [ ! -f /app/config/settings.yaml ]; then
  echo "config/settings.yaml is missing: mount your config folder at /app/config" >&2
  exit 1
fi
case "$1" in
  serve) shift; exec sgfh serve -C /app/config "$@" ;;
  *) exec sgfh "$@" ;;
esac
