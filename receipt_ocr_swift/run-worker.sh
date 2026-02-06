#!/usr/bin/env bash
#
# run-worker.sh — Auto-restarting wrapper for the receipt OCR Swift worker.
#
# Usage:
#   ./run-worker.sh [--env dev|prod] [-- extra-flags...]
#
# Behaviour:
#   • Builds the Swift binary (release) if it doesn't exist.
#   • Runs the worker with --continuous.
#   • On clean exit (queue empty), waits IDLE_BACKOFF seconds before restarting.
#   • On crash, waits CRASH_BACKOFF seconds (with exponential backoff, capped).
#   • Ctrl-C stops the loop.
#
set -euo pipefail

# ---------- configuration ----------
ENV="${1:-dev}"
if [[ "$ENV" == "--env" ]]; then
  ENV="${2:-dev}"
  shift 2
else
  shift 1 2>/dev/null || true
fi

# Strip leading "--" separator if present
if [[ "${1:-}" == "--" ]]; then shift; fi

IDLE_BACKOFF=10          # seconds to wait after clean exit (queue was empty)
CRASH_BACKOFF_INIT=5     # initial crash backoff seconds
CRASH_BACKOFF_MAX=120    # max crash backoff seconds

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BINARY="$SCRIPT_DIR/.build/arm64-apple-macosx/release/receipt-ocr"

# ---------- build if needed ----------
if [[ ! -x "$BINARY" ]]; then
  echo "[run-worker] Building Swift binary (release)..."
  (cd "$SCRIPT_DIR" && swift build --configuration release)
fi

# ---------- run loop ----------
crash_backoff=$CRASH_BACKOFF_INIT
consecutive_crashes=0

cleanup() {
  echo ""
  echo "[run-worker] Shutting down."
  exit 0
}
trap cleanup SIGINT SIGTERM

echo "[run-worker] Starting OCR worker (env=$ENV). Press Ctrl-C to stop."

while true; do
  echo "[run-worker] $(date '+%H:%M:%S') — Launching worker..."
  set +e
  "$BINARY" --env "$ENV" --continuous "$@"
  exit_code=$?
  set -e

  if [[ $exit_code -eq 0 ]]; then
    # Clean exit — queue was empty. Reset crash counter.
    consecutive_crashes=0
    crash_backoff=$CRASH_BACKOFF_INIT
    echo "[run-worker] Queue empty. Waiting ${IDLE_BACKOFF}s before polling again..."
    sleep "$IDLE_BACKOFF"
  else
    # Crash — exponential backoff
    consecutive_crashes=$((consecutive_crashes + 1))
    echo "[run-worker] Worker exited with code $exit_code (crash #$consecutive_crashes). Waiting ${crash_backoff}s..."
    sleep "$crash_backoff"
    crash_backoff=$((crash_backoff * 2))
    if [[ $crash_backoff -gt $CRASH_BACKOFF_MAX ]]; then
      crash_backoff=$CRASH_BACKOFF_MAX
    fi
  fi
done
