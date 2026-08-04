#!/bin/bash
# Update the dev OCR queue workers on both Macs.
#
# The LaunchAgents run a binary built from a checkout pinned to main; nothing
# rebuilds itself. Run this after merging anything under receipt_ocr_swift/ or
# the workers keep draining the queue with stale code.
#
# See docs/line-items/agentic-review/RUNNERS.md.
set -euo pipefail

LABEL="com.tnorlund.receipt-ocr-dev"
LOCAL_CHECKOUT="/Users/tnorlund/Portfolio/.claude/worktrees/backfill-main"
MINI_CHECKOUT="/Users/tnorlund/ocr-runner-main"
MINI_SSH="mini"

HOSTS="local mini"
DRY_RUN=0
SKIP_BUILD=0

usage() {
  cat <<'USAGE'
usage: update_ocr_workers.sh [--host local|mini|both] [--dry-run] [--skip-build]

  --host        Which worker to update (default: both).
  --dry-run     Print what would run without changing anything.
  --skip-build  Refresh the checkout and kickstart, but skip `swift build`.
  -h, --help    Show this message.
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --host)
      [ $# -ge 2 ] || { echo "--host needs a value" >&2; exit 2; }
      case "$2" in
        local) HOSTS="local" ;;
        mini)  HOSTS="mini" ;;
        both)  HOSTS="local mini" ;;
        *) echo "unknown host '$2' (want local|mini|both)" >&2; exit 2 ;;
      esac
      shift 2
      ;;
    --dry-run)    DRY_RUN=1; shift ;;
    --skip-build) SKIP_BUILD=1; shift ;;
    -h|--help)    usage; exit 0 ;;
    *) echo "unknown argument '$1'" >&2; usage >&2; exit 2 ;;
  esac
done

# The body that runs on each machine. Kept as one blob so the local and remote
# paths execute byte-identical logic; it reads its settings from argv.
worker_script() {
  cat <<'REMOTE'
set -uo pipefail
CHECKOUT="$1"; LABEL="$2"; DRY_RUN="$3"; SKIP_BUILD="$4"
SWIFT_DIR="${CHECKOUT}/receipt_ocr_swift"
BIN="${SWIFT_DIR}/.build/arm64-apple-macosx/release/receipt-ocr"
HOST="$(hostname -s)"
fail() { echo "  FAIL  $*"; exit 1; }
run() {
  if [ "$DRY_RUN" = "1" ]; then echo "  dry   $*"; return 0; fi
  "$@"
}

echo "  host  ${HOST}"
echo "  path  ${CHECKOUT}"
[ -d "$SWIFT_DIR" ] || fail "no receipt_ocr_swift at ${SWIFT_DIR}"

cd "$CHECKOUT" || fail "cannot cd ${CHECKOUT}"

# These are dedicated detached worktrees pinned to main, so `pull` does not
# apply. checkout --detach keeps untracked files and refuses rather than
# clobbering tracked local edits, which is the behaviour we want on a box that
# might have been poked at by hand.
run git fetch origin main --quiet || fail "git fetch failed"
BEFORE="$(git rev-parse --short HEAD)"
run git checkout --detach origin/main --quiet || fail "git checkout failed (local edits?)"
AFTER="$(git rev-parse --short HEAD)"
if [ "$BEFORE" = "$AFTER" ]; then
  echo "  git   ${AFTER} (already current)"
else
  echo "  git   ${BEFORE} -> ${AFTER}"
fi

if [ "$SKIP_BUILD" = "1" ]; then
  echo "  build skipped (--skip-build)"
else
  cd "$SWIFT_DIR" || fail "cannot cd ${SWIFT_DIR}"
  if [ "$DRY_RUN" = "1" ]; then
    echo "  dry   swift build --configuration release"
  else
    echo "  build swift build --configuration release ..."
    BUILD_LOG="$(mktemp -t ocr-build)"
    if swift build --configuration release >"$BUILD_LOG" 2>&1; then
      echo "  build $(tail -1 "$BUILD_LOG")"
      rm -f "$BUILD_LOG"
    else
      echo "  ---- last 20 lines of build output ----"
      tail -20 "$BUILD_LOG"
      rm -f "$BUILD_LOG"
      fail "swift build failed"
    fi
  fi
fi

# A binary that cannot even print usage is worse than a stale one, so prove it
# runs before handing it back to launchd.
if [ "$DRY_RUN" = "1" ]; then
  echo "  dry   ${BIN} --help"
else
  [ -x "$BIN" ] || fail "binary missing at ${BIN}"
  "$BIN" --help >/dev/null 2>&1 || fail "binary did not run (--help failed)"
  echo "  bin   ok ($(cd "$(dirname "$BIN")" && ls -lh "$(basename "$BIN")" | awk '{print $5}'))"
fi

if ! launchctl list | grep -q "$LABEL"; then
  fail "LaunchAgent ${LABEL} is not loaded (see RUNNERS.md to install)"
fi
run launchctl kickstart -k "gui/$(id -u)/${LABEL}" || fail "kickstart failed"
if [ "$DRY_RUN" != "1" ]; then
  echo "  agent kickstarted ${LABEL}"
fi
REMOTE
}

echo "receipt-ocr worker update"
[ "$DRY_RUN" = "1" ] && echo "(dry run: nothing will be changed)"
echo

STATUS=0
for host in $HOSTS; do
  case "$host" in
    local) checkout="$LOCAL_CHECKOUT" ;;
    mini)  checkout="$MINI_CHECKOUT" ;;
  esac

  echo "=== ${host} ==="
  if [ "$host" = "local" ]; then
    if worker_script | bash -s -- "$checkout" "$LABEL" "$DRY_RUN" "$SKIP_BUILD"; then
      echo "  ==> ${host}: OK"
    else
      echo "  ==> ${host}: FAILED"
      STATUS=1
    fi
  else
    if worker_script | ssh "$MINI_SSH" bash -s -- "$checkout" "$LABEL" "$DRY_RUN" "$SKIP_BUILD"; then
      echo "  ==> ${host}: OK"
    else
      echo "  ==> ${host}: FAILED"
      STATUS=1
    fi
  fi
  echo
done

if [ "$STATUS" -eq 0 ]; then
  echo "all requested workers updated"
else
  echo "one or more workers failed; see output above" >&2
fi
exit "$STATUS"
