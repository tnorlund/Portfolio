#!/usr/bin/env bash
# Nightly loop v0 wrapper (plan humble-skipping-quilt W2). Eval-only,
# read-only. Sequence:
#   1. scripts/agent_preflight.sh   exit 2 (RED)  -> RED stub report, no agent
#   2. headless `claude -p` on docs/nightly/BRIEF.md, 4h watchdog
#   3. scripts/nightly/check_contract.py           invalid -> RED stub
#   4. publish: commit report on nightly/YYYY-MM-DD branch pushed to origin
#      (NEVER merged to main) + one-line summary appended to CAMPAIGN_LOG
#
# The wrapper ALWAYS produces a report file, even if everything fails.
#
# Flags:
#   --dry-run   skip the claude call (use the canned agent report) and skip
#               all git mutations (branch/commit/push are logged, not run).
#
# Env overrides (all optional; defaults suit the mini):
#   CLAUDE_BIN              headless claude binary (~/.local/bin/claude)
#   NIGHTLY_REPO_ROOT       Portfolio checkout (default: this script's repo)
#   NIGHTLY_DATE            YYYY-MM-DD (default: today)
#   NIGHTLY_REPORT_DIR      default $REPO_ROOT/docs/reports/nightly
#   NIGHTLY_LOG_DIR         default ~/.nightly-loop
#   NIGHTLY_CAMPAIGN_LOG    default ~/section_label_kickoff/CAMPAIGN_LOG.md
#   NIGHTLY_PREFLIGHT_BIN   default $REPO_ROOT/scripts/agent_preflight.sh
#   NIGHTLY_TIMEOUT_SECS    default 14400 (4h)
#   NIGHTLY_MAX_TURNS       default 80
#
# Exit: 0 report produced and published (any verdict); 1 only when even the
# stub/publish path failed.

set -u -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${NIGHTLY_REPO_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
RUN_DATE="${NIGHTLY_DATE:-$(date +%F)}"
REPORT_DIR="${NIGHTLY_REPORT_DIR:-$REPO_ROOT/docs/reports/nightly}"
REPORT_PATH="$REPORT_DIR/$RUN_DATE.md"
RUN_DIR="${NIGHTLY_LOG_DIR:-$HOME/.nightly-loop}/$RUN_DATE"
CAMPAIGN_LOG="${NIGHTLY_CAMPAIGN_LOG:-$HOME/section_label_kickoff/CAMPAIGN_LOG.md}"
PREFLIGHT_BIN="${NIGHTLY_PREFLIGHT_BIN:-$REPO_ROOT/scripts/agent_preflight.sh}"
TIMEOUT_SECS="${NIGHTLY_TIMEOUT_SECS:-14400}"
MAX_TURNS="${NIGHTLY_MAX_TURNS:-80}"
BRIEF="$REPO_ROOT/docs/nightly/BRIEF.md"
CANNED_REPORT="$SCRIPT_DIR/fixtures/canned_agent_report.md"
CHECKER="$SCRIPT_DIR/check_contract.py"
# Dev table pin: never inherit a prod-pointing env into the loop.
export DYNAMODB_TABLE_NAME="${DYNAMODB_TABLE_NAME:-ReceiptsTable-dc5be22}"
export PORTFOLIO_ENV=dev

# CLAUDE_BIN resolution: env override, then ~/.local/bin/claude, then PATH.
if [ -z "${CLAUDE_BIN:-}" ]; then
  if [ -x "$HOME/.local/bin/claude" ]; then
    CLAUDE_BIN="$HOME/.local/bin/claude"
  else
    CLAUDE_BIN="claude"
  fi
fi

DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    *) echo "unknown argument: $arg" >&2; exit 64 ;;
  esac
done

mkdir -p "$REPORT_DIR" "$RUN_DIR"
START_EPOCH="$(date +%s)"

log() {
  printf '[%s] %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$RUN_DIR/wrapper.log" >&2
}

elapsed() { echo "$(( $(date +%s) - START_EPOCH ))"; }

# Portable 4h watchdog: coreutils timeout, gtimeout, else the perl
# alarm+exec idiom (the alarm survives exec; SIGALRM kills the child).
run_with_timeout() {
  local secs="$1"; shift
  if command -v timeout >/dev/null 2>&1; then
    timeout "$secs" "$@"
  elif command -v gtimeout >/dev/null 2>&1; then
    gtimeout "$secs" "$@"
  else
    perl -e 'alarm shift @ARGV; exec @ARGV or die "exec: $!"' "$secs" "$@"
  fi
}

PREFLIGHT_SUMMARY="not run"

write_red_stub() {
  local reason="$1"
  log "writing RED stub report: $reason"
  cat > "$REPORT_PATH" <<EOF
# Nightly Report $RUN_DATE (RED stub)

## Verdict
**Verdict: RED** - $reason

## Budget
- wall clock: $(elapsed)s (wrapper); no completed agent run
- turns: n/a
- subagents: n/a

## Fleet
Fleet table unavailable (stub report). See the most recent prior report in
docs/reports/nightly/ for the last known fleet state.

## Tonight's Work
None - $reason

## Awaiting Owner
- Investigate the RED cause: $reason
- Logs: $RUN_DIR (wrapper.log, preflight.json, agent_stdout.log)

## Failures & Anomalies
- $reason
- preflight: $PREFLIGHT_SUMMARY

## Tomorrow's Top 3
1. Restore the nightly loop to GREEN (root-cause: $reason)
2. Re-run preflight manually: scripts/agent_preflight.sh
3. Re-attempt the skipped eval sweep once preflight is healthy
EOF
}

publish_report() {
  local branch="nightly/$RUN_DATE"
  local verdict summary_line
  verdict="$(python3 "$CHECKER" --verdict "$REPORT_PATH" 2>/dev/null || echo RED)"
  [ -n "$verdict" ] || verdict="RED"
  summary_line="$(date -u +%FT%TZ) nightly-v0 $RUN_DATE verdict=$verdict report=docs/reports/nightly/$RUN_DATE.md branch=$branch elapsed=$(elapsed)s"

  if [ "$DRY_RUN" -eq 1 ]; then
    log "[dry-run] would commit $REPORT_PATH on branch $branch and push to origin"
  else
    local wt
    wt="$(mktemp -d "${TMPDIR:-/tmp}/nightly-publish.XXXXXX")"
    if git -C "$REPO_ROOT" worktree add --detach "$wt" HEAD >/dev/null 2>&1; then
      mkdir -p "$wt/docs/reports/nightly"
      cp "$REPORT_PATH" "$wt/docs/reports/nightly/$RUN_DATE.md"
      git -C "$wt" checkout -q -B "$branch" \
        && git -C "$wt" add "docs/reports/nightly/$RUN_DATE.md" \
        && git -C "$wt" commit -q -m "nightly: morning report $RUN_DATE ($verdict)

Automated nightly loop v0 report. Not for merge to main; read the file.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>" \
        && git -C "$wt" push origin "$branch" \
        && log "published $branch to origin" \
        || log "WARNING: git publish failed; report remains at $REPORT_PATH"
      git -C "$REPO_ROOT" worktree remove --force "$wt" >/dev/null 2>&1 || true
    else
      log "WARNING: could not create publish worktree; report remains at $REPORT_PATH"
    fi
  fi

  mkdir -p "$(dirname "$CAMPAIGN_LOG")" 2>/dev/null || true
  if printf '%s\n' "$summary_line" >> "$CAMPAIGN_LOG" 2>/dev/null; then
    log "campaign log appended: $CAMPAIGN_LOG"
  else
    log "WARNING: could not append to campaign log $CAMPAIGN_LOG"
  fi
  log "morning report: $REPORT_PATH (verdict=$verdict)"
}

finish() {
  # The wrapper ALWAYS leaves a report file behind, whatever happened.
  if [ ! -s "$REPORT_PATH" ]; then
    write_red_stub "wrapper reached exit without a report (unexpected)"
  fi
  publish_report
}

# ---- 1. Preflight ---------------------------------------------------------
log "nightly v0 start date=$RUN_DATE repo=$REPO_ROOT dry_run=$DRY_RUN"
if [ -x "$PREFLIGHT_BIN" ] || [ -f "$PREFLIGHT_BIN" ]; then
  PREFLIGHT_JSON="$(bash "$PREFLIGHT_BIN" 2>"$RUN_DIR/preflight_summary.txt")"
  PF_EXIT=$?
else
  PREFLIGHT_JSON=""
  PF_EXIT=2
  echo "preflight script not found: $PREFLIGHT_BIN" > "$RUN_DIR/preflight_summary.txt"
fi
printf '%s\n' "$PREFLIGHT_JSON" > "$RUN_DIR/preflight.json"
PREFLIGHT_SUMMARY="$(tr '\n' '; ' < "$RUN_DIR/preflight_summary.txt" | cut -c1-400)"
log "preflight exit=$PF_EXIT"

if [ "$PF_EXIT" -ge 2 ]; then
  write_red_stub "preflight RED (exit $PF_EXIT); night skipped per guardrail"
  finish
  exit 0
fi
if [ "$PF_EXIT" -eq 1 ]; then
  log "preflight DEGRADED - continuing (agent runs DEGRADED, e.g. receipt-tools MCP unauthenticated)"
fi

# ---- 2. Headless agent run ------------------------------------------------
export NIGHTLY_DATE="$RUN_DATE"
export NIGHTLY_REPORT_PATH="$REPORT_PATH"
export NIGHTLY_RUN_DIR="$RUN_DIR"
export NIGHTLY_REPO_ROOT="$REPO_ROOT"

if [ "$DRY_RUN" -eq 1 ]; then
  log "[dry-run] skipping claude call; using canned agent report"
  sed "s/{{DATE}}/$RUN_DATE/g" "$CANNED_REPORT" > "$REPORT_PATH"
  AGENT_EXIT=0
else
  if [ ! -f "$BRIEF" ]; then
    write_red_stub "brief missing: $BRIEF"
    finish
    exit 0
  fi
  log "launching headless claude ($CLAUDE_BIN), timeout=${TIMEOUT_SECS}s max_turns=$MAX_TURNS"
  ( cd "$REPO_ROOT" && run_with_timeout "$TIMEOUT_SECS" \
      "$CLAUDE_BIN" -p "$(cat "$BRIEF")" \
      --permission-mode bypassPermissions \
      --model claude-fable-5 \
      --max-turns "$MAX_TURNS" \
      > "$RUN_DIR/agent_stdout.log" 2> "$RUN_DIR/agent_stderr.log" )
  AGENT_EXIT=$?
  log "agent exit=$AGENT_EXIT"
fi

# ---- 3. Contract check ----------------------------------------------------
if [ ! -s "$REPORT_PATH" ]; then
  write_red_stub "agent produced no report (agent exit $AGENT_EXIT)"
elif ! python3 "$CHECKER" "$REPORT_PATH" >> "$RUN_DIR/wrapper.log" 2>&1; then
  cp "$REPORT_PATH" "$RUN_DIR/rejected_report.md" 2>/dev/null || true
  write_red_stub "report failed contract check (agent exit $AGENT_EXIT); original preserved at $RUN_DIR/rejected_report.md"
fi

# ---- 4. Publish -----------------------------------------------------------
finish
exit 0
