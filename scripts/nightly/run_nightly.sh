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
#   NIGHTLY_TOKEN_BUDGET    default 2000000 (H2 token circuit breaker ceiling;
#                           breach -> watchdog exit 123 -> RED stub)
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
# H2 token circuit breaker ceiling (conservative ~2M/run, raisable as trust
# builds). Exported so it is visible to the loop and any child process.
export NIGHTLY_TOKEN_BUDGET="${NIGHTLY_TOKEN_BUDGET:-2000000}"
# Must match watchdog.py TOKEN_EXIT (exit code on a token-budget group-kill).
TOKEN_EXIT_CODE=123
BRIEF="$REPO_ROOT/docs/nightly/BRIEF.md"
CANNED_REPORT="$SCRIPT_DIR/fixtures/canned_agent_report.md"
CHECKER="$SCRIPT_DIR/check_contract.py"
TRAJECTORY_TOOLS="$SCRIPT_DIR/trajectory_tools.py"
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

# ---- Log retention ---------------------------------------------------------
# Per-date run dirs older than 14 days are swept; the launchd stdout/stderr
# logs are truncated IN PLACE (cat >, preserving the inode launchd holds
# open) to their last 5MB when they exceed it.
LOG_BASE="$(dirname "$RUN_DIR")"
find "$LOG_BASE" -mindepth 1 -maxdepth 1 -type d -mtime +14 -exec rm -rf {} + 2>/dev/null
LAUNCHD_LOG_CAP=5242880
for launchd_log in "$HOME/Library/Logs/receipt-nightly.out.log" \
                   "$HOME/Library/Logs/receipt-nightly.err.log"; do
  if [ -f "$launchd_log" ]; then
    log_size="$(stat -f%z "$launchd_log" 2>/dev/null || stat -c%s "$launchd_log" 2>/dev/null || echo 0)"
    if [ "${log_size:-0}" -gt "$LAUNCHD_LOG_CAP" ]; then
      tail -c "$LAUNCHD_LOG_CAP" "$launchd_log" > "$launchd_log.trunc.$$" \
        && cat "$launchd_log.trunc.$$" > "$launchd_log"
      rm -f "$launchd_log.trunc.$$"
    fi
  fi
done

log() {
  printf '[%s] %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$RUN_DIR/wrapper.log" >&2
}

elapsed() { echo "$(( $(date +%s) - START_EPOCH ))"; }

# H2: actual tokens consumed this run, summed from run_metrics.json the same
# way the breaker counts (all four usage types, deduped per message). Echoes an
# integer, or nothing on any error (fail quiet; a missing number never breaks
# the report).
read_token_total() {
  [ -s "$RUN_DIR/run_metrics.json" ] || return 0
  python3 - "$RUN_DIR/run_metrics.json" <<'PY' 2>/dev/null || true
import json, sys
try:
    m = json.load(open(sys.argv[1]))
    t = m.get("token_totals") or {}
    print(sum(v for v in t.values() if isinstance(v, int)))
except Exception:
    pass
PY
}

# H2: wire actual-vs-ceiling into a healthy report's Budget section, inserted
# right after the "## Budget" heading so the contract check still passes.
inject_budget_token_line() {
  local line="$1" tmp
  [ -s "$REPORT_PATH" ] || return 0
  tmp="$(mktemp "${TMPDIR:-/tmp}/nightly-budget.XXXXXX")" || return 0
  awk -v ins="$line" '
    { print }
    /^## Budget[[:space:]]*$/ && !done { print ins; done=1 }
  ' "$REPORT_PATH" > "$tmp" && cat "$tmp" > "$REPORT_PATH"
  rm -f "$tmp"
}

# 4h watchdog: scripts/nightly/watchdog.py owns setsid + group-kill
# (SIGTERM to -PGID, 30s grace, then SIGKILL to -PGID) on every path. The
# old timeout/gtimeout/perl-alarm chain is gone: the perl leg killed only
# the direct child, leaving grandchildren (stdio MCP servers, subagents)
# alive past the deadline.
WATCHDOG="$SCRIPT_DIR/watchdog.py"

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
      # Same-date re-run: refresh the remote-tracking ref, then push with
      # --force-with-lease so the re-run's report wins (the branch is never
      # merged; last-run-wins is the intended semantics) without blind force.
      git -C "$wt" fetch -q origin "+refs/heads/$branch:refs/remotes/origin/$branch" 2>/dev/null || true
      git -C "$wt" checkout -q -B "$branch" \
        && git -C "$wt" add "docs/reports/nightly/$RUN_DATE.md" \
        && git -C "$wt" commit -q -m "nightly: morning report $RUN_DATE ($verdict)

Automated nightly loop v0 report. Not for merge to main; read the file.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>" \
        && git -C "$wt" push --force-with-lease origin "$branch" \
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
  # Preflight extension: prove group-kill works on THIS host before a real
  # launch; otherwise a hung agent would outlive its 4h deadline via
  # surviving grandchildren. Dry-run never launches, so it skips the probe.
  if ! python3 "$WATCHDOG" --self-test >> "$RUN_DIR/wrapper.log" 2>&1; then
    write_red_stub "watchdog group-kill self-test failed on this host; refusing real agent launch"
    finish
    exit 0
  fi
  log "watchdog self-test OK (setsid + group-kill)"
  log "launching headless claude ($CLAUDE_BIN), timeout=${TIMEOUT_SECS}s max_turns=$MAX_TURNS"
  # H1: stream-json + --verbose captures the full tool-call/turn trajectory
  # AND final usage/cost. stdout is the JSONL event stream -> trajectory.jsonl
  # (this is what watchdogs H2/H3 poll). The old plain-text agent_stdout.log is
  # regenerated below from the trajectory's final result event so the human
  # continuity (and any log reader) still sees a readable final message.
  # H2: --token-budget + --trajectory arm the token circuit breaker. The
  # watchdog polls trajectory.jsonl (~15s) and group-kills with exit 123 if
  # the deduped token sum breaches NIGHTLY_TOKEN_BUDGET.
  ( cd "$REPO_ROOT" && python3 "$WATCHDOG" --grace 30 \
      --token-budget "$NIGHTLY_TOKEN_BUDGET" \
      --trajectory "$RUN_DIR/trajectory.jsonl" \
      "$TIMEOUT_SECS" -- \
      "$CLAUDE_BIN" -p "$(cat "$BRIEF")" \
      --output-format stream-json --verbose \
      --permission-mode bypassPermissions \
      --model claude-fable-5 \
      --max-turns "$MAX_TURNS" \
      > "$RUN_DIR/trajectory.jsonl" 2> "$RUN_DIR/agent_stderr.log" )
  AGENT_EXIT=$?
  log "agent exit=$AGENT_EXIT"

  # Regenerate agent_stdout.log (continuity) from the trajectory's final
  # result event; never fatal (the parser degrades on a truncated trace).
  python3 "$TRAJECTORY_TOOLS" extract-result "$RUN_DIR/trajectory.jsonl" \
    > "$RUN_DIR/agent_stdout.log" 2>> "$RUN_DIR/wrapper.log" \
    || log "WARNING: extract-result failed; agent_stdout.log may be empty"

  # Record run_metrics.json. claude --version is captured HERE by the wrapper
  # (a stream-json shape-drift canary per the plan's Cross-cutting note).
  CLAUDE_VERSION="$("$CLAUDE_BIN" --version 2>/dev/null | head -1)"
  python3 "$TRAJECTORY_TOOLS" metrics "$RUN_DIR/trajectory.jsonl" \
    --claude-version "$CLAUDE_VERSION" \
    --duration-secs "$(elapsed)" \
    -o "$RUN_DIR/run_metrics.json" 2>> "$RUN_DIR/wrapper.log" \
    && log "run_metrics.json written (claude $CLAUDE_VERSION)" \
    || log "WARNING: run_metrics.json generation failed"

  # H2: map the token circuit breaker's exit 123 to its own RED stub, and on
  # healthy nights record actual-vs-ceiling in the report's Budget section.
  TOKEN_TOTAL="$(read_token_total)"
  if [ "$AGENT_EXIT" -eq "$TOKEN_EXIT_CODE" ]; then
    log "token budget breached: ${TOKEN_TOTAL:-unknown} of $NIGHTLY_TOKEN_BUDGET tokens (watchdog exit 123)"
    write_red_stub "token budget exceeded (${TOKEN_TOTAL:-unknown} of $NIGHTLY_TOKEN_BUDGET tokens)"
  elif [ -s "$REPORT_PATH" ]; then
    inject_budget_token_line "- tokens: ${TOKEN_TOTAL:-n/a} of $NIGHTLY_TOKEN_BUDGET (budget)"
  fi
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
