#!/bin/bash
# Drain the dev OCR job queue with the Swift Vision/LayoutLM worker.
# Installed at ~/receipt_ocr_runner/run-dev.sh and started by
# com.tnorlund.receipt-ocr-dev.plist. See
# docs/line-items/agentic-review/RUNNERS.md.
set -uo pipefail

# Each machine pins its own checkout to main. The first candidate that exists
# wins, so this script is byte-identical on every machine; set
# RECEIPT_OCR_SWIFT_DIR to override.
SWIFT_DIR_CANDIDATES="
/Users/tnorlund/ocr-runner-main/receipt_ocr_swift
/Users/tnorlund/Portfolio/.claude/worktrees/backfill-main/receipt_ocr_swift
"

SWIFT_DIR="${RECEIPT_OCR_SWIFT_DIR:-}"
if [ -z "$SWIFT_DIR" ]; then
  for candidate in $SWIFT_DIR_CANDIDATES; do
    if [ -d "$candidate" ]; then
      SWIFT_DIR="$candidate"
      break
    fi
  done
fi

LOCK="/tmp/receipt-ocr-dev.lock"

# `--env dev` shells out to `/usr/bin/env pulumi` to read stack outputs, and
# launchd's default PATH does not include the pulumi install. PulumiLoader
# returns an empty config on a non-zero exit rather than raising, so without
# this the worker starts with no queue URL and quietly does nothing. pulumi
# lives in ~/.pulumi/bin on the MacBook and /opt/homebrew/bin on the mini.
export PATH="/Users/tnorlund/.pulumi/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

if [ -z "$SWIFT_DIR" ]; then
  echo "$(date -u +%FT%TZ) ERROR: no receipt_ocr_swift checkout found on this host"
  exit 1
fi

BIN="${SWIFT_DIR}/.build/arm64-apple-macosx/release/receipt-ocr"

# mkdir is atomic; keeps a slow drain from overlapping the next scheduled start.
if ! mkdir "$LOCK" 2>/dev/null; then
  echo "$(date -u +%FT%TZ) skip: previous run still holding $LOCK"
  exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

if [ ! -x "$BIN" ]; then
  echo "$(date -u +%FT%TZ) ERROR: worker binary missing at $BIN"
  echo "  rebuild with: cd $SWIFT_DIR && swift build --configuration release"
  exit 1
fi

# The LayoutLM CoreML bundle caches to the relative path .models/layoutlm, so
# cd first or the ~220 MB model is refetched into whatever directory launchd
# happened to pick.
cd "$SWIFT_DIR" || exit 1
echo "$(date -u +%FT%TZ) starting drain on $(hostname -s)"
"$BIN" --env dev --continuous --log-level info
echo "$(date -u +%FT%TZ) drain exited rc=$?"
