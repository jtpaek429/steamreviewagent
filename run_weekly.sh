#!/usr/bin/env bash
# Wrapper called by the weekly cron job.
# Runs the multi-game digest and logs output to data/run_weekly.log.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

LOG_FILE="$SCRIPT_DIR/data/run_weekly.log"
mkdir -p "$SCRIPT_DIR/data"

{
  echo "========================================"
  echo "Run started: $(date)"
  python3 agent.py --config games.json
  echo "Run finished: $(date)"
} >> "$LOG_FILE" 2>&1
