#!/usr/bin/env bash
# Manage the weekly cron job for steam-review-agent.
#
# Usage:
#   ./schedule.sh --enable    Install cron job (Mondays at 8 AM local time)
#   ./schedule.sh --disable   Remove cron job
#   ./schedule.sh --status    Show whether the cron job is active
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNNER="$SCRIPT_DIR/run_weekly.sh"
# Marker lets us find and remove our entry precisely without touching other cron jobs.
CRON_MARKER="steam-review-agent"
CRON_SCHEDULE="0 8 * * 1"  # every Monday at 08:00 local time
CRON_LINE="$CRON_SCHEDULE $RUNNER  # $CRON_MARKER"

enable_cron() {
  if crontab -l 2>/dev/null | grep -q "$CRON_MARKER"; then
    echo "Already enabled. Run --status to confirm."
    exit 0
  fi
  chmod +x "$RUNNER"
  # Append to existing crontab (or create one if empty)
  (crontab -l 2>/dev/null; echo "$CRON_LINE") | crontab -
  echo "Cron job installed. The digest will run every Monday at 08:00."
  echo "  Entry: $CRON_LINE"
  echo "  Logs:  $SCRIPT_DIR/data/run_weekly.log"
}

disable_cron() {
  if ! crontab -l 2>/dev/null | grep -q "$CRON_MARKER"; then
    echo "No cron job found — nothing to remove."
    exit 0
  fi
  crontab -l 2>/dev/null | grep -v "$CRON_MARKER" | crontab -
  echo "Cron job removed."
}

status_cron() {
  if crontab -l 2>/dev/null | grep -q "$CRON_MARKER"; then
    echo "Status: ENABLED"
    crontab -l 2>/dev/null | grep "$CRON_MARKER"
  else
    echo "Status: DISABLED (no cron job installed)"
  fi
}

case "${1:-}" in
  --enable)  enable_cron ;;
  --disable) disable_cron ;;
  --status)  status_cron ;;
  *)
    echo "Usage: $0 --enable | --disable | --status"
    exit 1
    ;;
esac
