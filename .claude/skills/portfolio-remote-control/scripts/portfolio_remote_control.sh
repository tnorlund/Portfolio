#!/bin/bash
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLAUDE_BIN="${CLAUDE_BIN:-/opt/homebrew/bin/claude}"
PORTFOLIO_ROOT="${PORTFOLIO_ROOT:-$HOME/Portfolio}"
BASE_DIR="${PORTFOLIO_RC_BASE:-/tmp/claude-remote-screen}"
ATTACH_DIR="$BASE_DIR/attachers"
CAFFEINATE_PID_FILE="$BASE_DIR/caffeinate.pid"
PRIME_TIMEOUT="${PORTFOLIO_RC_PRIME_TIMEOUT:-180}"
CAFFEINATE_SECONDS="${PORTFOLIO_RC_CAFFEINATE_SECONDS:-86400}"

MISSION_PROMPT="${PORTFOLIO_RC_MISSION:-Read CONTEXT.md then CHARTER.md in this worktree. They hold the full context from the session that set this branch up plus your specific mission. Follow the charter milestones in order and self-review with codex along the way exactly as CONTEXT.md mandates. Begin with milestone 1 now.}"

usage() {
  cat <<'EOF'
Usage: portfolio_remote_control.sh <command>

Commands:
  launch   Clean target sessions, start screens, prime Claude, open Terminal attachers, caffeinate, verify.
  start    Clean target sessions and start screens/Terminal attachers without typing into Claude.
  prime    Attach to existing target screens through a PTY and send the startup prompt.
  status   Show processes, screens, log evidence, and caffeinate status.
  verify   Alias for status.
  cleanup  Stop only the three Portfolio remote-control Claude/screen sessions.

Environment:
  CLAUDE_BIN                         Path to claude binary. Default: /opt/homebrew/bin/claude
  PORTFOLIO_ROOT                     Portfolio checkout root. Default: $HOME/Portfolio
  PORTFOLIO_RC_OPEN_TERMINAL=0       Skip opening Terminal.app attachers.
  PORTFOLIO_RC_PRIME_TIMEOUT=180     Seconds to wait while priming each screen.
  PORTFOLIO_RC_CAFFEINATE_SECONDS    Duration for the caffeinate sleep helper. Default: 86400
EOF
}

entries() {
  cat <<EOF
merchant-intel|$PORTFOLIO_ROOT/.claude/worktrees/merchant-intel|merchant-intel|claude-merchant-intel-rc
font-render|$PORTFOLIO_ROOT/.claude/worktrees/font-render|font-render|claude-font-render-rc
orchestration|$PORTFOLIO_ROOT/.claude/worktrees/orchestration|orchestration|claude-orchestration-rc
EOF
}

quote_arg() {
  printf "%q" "$1"
}

remote_control_pids() {
  local session_name="$1"
  ps axww -o pid= -o command= | awk -v target="$session_name" '
    function strip_quotes(value) {
      gsub(/^[\"\047]+|[\"\047]+$/, "", value)
      return value
    }
    {
      pid = $1
      executable = strip_quotes($2)
      sub(/^.*\//, "", executable)
      if (executable != "claude") {
        next
      }
      for (i = 3; i <= NF; i++) {
        arg = strip_quotes($i)
        if (arg == "--remote-control" && i < NF) {
          value = strip_quotes($(i + 1))
          if (value == target) {
            print pid
            next
          }
        }
        if (arg ~ /^--remote-control=/) {
          value = strip_quotes(substr(arg, 18))
          if (value == target) {
            print pid
            next
          }
        }
      }
    }
  '
}

kill_remote_control_processes() {
  local session_name="$1"
  local pid

  while IFS= read -r pid; do
    if [[ "$pid" =~ ^[0-9]+$ ]]; then
      kill -9 "$pid" >/dev/null 2>&1 || true
    fi
  done < <(remote_control_pids "$session_name")
}

require_prereqs() {
  if [[ ! -x "$CLAUDE_BIN" ]]; then
    echo "Missing executable Claude binary: $CLAUDE_BIN" >&2
    return 1
  fi
  if [[ ! -x /usr/bin/screen ]]; then
    echo "Missing /usr/bin/screen" >&2
    return 1
  fi
  while IFS='|' read -r label worktree session_name screen_name; do
    if [[ ! -d "$worktree" ]]; then
      echo "Missing worktree for $label: $worktree" >&2
      return 1
    fi
    if [[ ! -f "$worktree/CONTEXT.md" || ! -f "$worktree/CHARTER.md" ]]; then
      echo "Missing CONTEXT.md or CHARTER.md in $worktree" >&2
      return 1
    fi
  done < <(entries)
}

cleanup_targets() {
  echo "Stopping target Portfolio remote-control sessions..."
  while IFS='|' read -r label worktree session_name screen_name; do
    /usr/bin/screen -S "$screen_name" -X quit >/dev/null 2>&1 || true
    kill_remote_control_processes "$session_name"
  done < <(entries)
  /usr/bin/screen -wipe >/dev/null 2>&1 || true
}

write_attacher() {
  local label="$1"
  local screen_name="$2"
  local file="$ATTACH_DIR/$label.command"

  mkdir -p "$ATTACH_DIR"
  cat >"$file" <<EOF
#!/bin/zsh
export TERM=xterm-256color
exec /usr/bin/screen -x $screen_name
EOF
  chmod +x "$file"
}

start_screens() {
  require_prereqs
  mkdir -p "$BASE_DIR" "$ATTACH_DIR"

  while IFS='|' read -r label worktree session_name screen_name; do
    local workdir="$BASE_DIR/$label"
    mkdir -p "$workdir"
    rm -f "$workdir/screenlog.0"
    write_attacher "$label" "$screen_name"

    echo "Starting $label in $screen_name..."
    (
      cd "$workdir"
      /usr/bin/screen -L -dmS "$screen_name" /bin/bash -lc \
        "cd $(quote_arg "$worktree") && exec $(quote_arg "$CLAUDE_BIN") --permission-mode bypassPermissions --remote-control $(quote_arg "$session_name")"
    )
    sleep 1
  done < <(entries)
}

open_attachers() {
  if [[ "${PORTFOLIO_RC_OPEN_TERMINAL:-1}" == "0" ]]; then
    return 0
  fi
  if [[ ! -x /usr/bin/open ]]; then
    return 0
  fi

  while IFS='|' read -r label worktree session_name screen_name; do
    local file="$ATTACH_DIR/$label.command"
    if [[ -x "$file" ]]; then
      /usr/bin/open -a Terminal "$file" >/dev/null 2>&1 || true
    fi
  done < <(entries)
}

prime_screens() {
  local failed=0
  local primer="$SKILL_DIR/scripts/prime_claude_screen.py"

  if [[ ! -f "$primer" ]]; then
    echo "Missing primer script: $primer" >&2
    return 1
  fi

  while IFS='|' read -r label worktree session_name screen_name; do
    echo "Priming $label..."
    if ! PORTFOLIO_RC_MISSION="$MISSION_PROMPT" python3 "$primer" --screen "$screen_name" --label "$label" --timeout "$PRIME_TIMEOUT"; then
      echo "Prime failed for $label; attach manually with: TERM=xterm-256color screen -x $screen_name" >&2
      failed=1
    fi
  done < <(entries)

  return "$failed"
}

ensure_caffeinate() {
  if [[ -s "$CAFFEINATE_PID_FILE" ]]; then
    local existing_pid
    existing_pid="$(cat "$CAFFEINATE_PID_FILE" 2>/dev/null || true)"
    if [[ "$existing_pid" =~ ^[0-9]+$ ]] && kill -0 "$existing_pid" >/dev/null 2>&1; then
      echo "Skill-managed caffeinate is already running as PID $existing_pid."
      return 0
    fi
  fi

  mkdir -p "$BASE_DIR"
  echo "Starting caffeinate for $CAFFEINATE_SECONDS seconds..."
  nohup /usr/bin/caffeinate -dimsu /bin/sleep "$CAFFEINATE_SECONDS" >"$BASE_DIR/caffeinate.log" 2>&1 &
  echo "$!" >"$CAFFEINATE_PID_FILE"
}

status() {
  local missing=0
  local screen_listing
  screen_listing="$(/usr/bin/screen -ls 2>/dev/null || true)"

  echo
  echo "Claude remote-control processes:"
  ps axww -o pid= -o command= | grep '[c]laude .*--remote-control' || true

  echo
  echo "Target screen sessions:"
  printf "%s\n" "$screen_listing" | grep -E 'claude-(merchant-intel|font-render|orchestration)-rc' || true

  echo
  while IFS='|' read -r label worktree session_name screen_name; do
    local process_ok=0
    local screen_ok=0
    local log_ok=0
    local log_file="$BASE_DIR/$label/screenlog.0"

    if remote_control_pids "$session_name" | grep -q .; then
      process_ok=1
    else
      missing=1
    fi

    if printf "%s\n" "$screen_listing" | grep -E "[0-9]+\\.$screen_name[[:space:]]" >/dev/null 2>&1; then
      screen_ok=1
    else
      missing=1
    fi

    if [[ -f "$log_file" ]] && grep -aE 'CONTEXT\.md|CHARTER\.md|Read\(|Bash\(|Edit\(|TodoWrite' "$log_file" >/dev/null 2>&1; then
      log_ok=1
    else
      missing=1
    fi

    printf "%-16s process=%s screen=%s log_evidence=%s log=%s\n" \
      "$label" "$process_ok" "$screen_ok" "$log_ok" "$log_file"
  done < <(entries)

  echo
  echo "Caffeinate:"
  if ! pgrep -fl "caffeinate.*-dimsu"; then
    missing=1
  fi

  return "$missing"
}

command="${1:-launch}"
case "$command" in
  launch)
    cleanup_targets
    start_screens
    sleep 2
    prime_failed=0
    prime_screens || prime_failed=1
    ensure_caffeinate
    open_attachers
    status_failed=0
    status || status_failed=1
    if [[ "$prime_failed" -ne 0 || "$status_failed" -ne 0 ]]; then
      exit 1
    fi
    ;;
  start)
    cleanup_targets
    start_screens
    ensure_caffeinate
    open_attachers
    status || true
    ;;
  prime)
    prime_screens
    status || true
    ;;
  status|verify)
    status
    ;;
  cleanup)
    cleanup_targets
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
