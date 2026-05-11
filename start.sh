#!/usr/bin/env bash
# Entry point for all Railway services in this repo.
# Each service sets RAILWAY_SERVICE_NAME automatically — we branch on it here
# so both services can share the same railway.toml startCommand.
set -euo pipefail

case "${RAILWAY_SERVICE_NAME:-}" in
  weekly-digest)
    # Cron job: hit the Flask webhook and exit. Railway runs this on schedule.
    echo "[cron] Triggering weekly job at $(date -u)..."
    curl -f -s -X POST "https://reviews.jonathanpaek.com/admin/run-weekly" \
      -H "Authorization: Bearer ${WEEKLY_SECRET}"
    echo "[cron] Done."
    ;;
  *)
    # Web service (steamreviewagent): start the gunicorn server.
    exec gunicorn app:app --workers 1 --bind "0.0.0.0:${PORT}"
    ;;
esac
