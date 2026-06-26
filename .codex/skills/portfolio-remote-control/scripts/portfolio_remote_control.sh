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
DIRTY_WARN="${PORTFOLIO_RC_DIRTY_WARN:-8}"
LOG_EVIDENCE_PATTERN='Read\(|Bash\(|Edit\(|MultiEdit\(|Write\(|TodoWrite|Grep\(|Glob\(|LS\(|Reading\s*[0-9]+\s*files?'

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
  PORTFOLIO_RC_CAFFEINATE_SECONDS    Duration for the caffeinate helper. Default: 86400
EOF
}

entries() {
  cat <<EOF
merchant-intel|$PORTFOLIO_ROOT/.claude/worktrees/merchant-intel|merchant-intel|claude-merchant-intel-rc|feat/merchant-intelligence-agents
font-render|$PORTFOLIO_ROOT/.claude/worktrees/font-render|font-render|claude-font-render-rc|feat/receipt-font-render
orchestration|$PORTFOLIO_ROOT/.claude/worktrees/orchestration|orchestration|claude-orchestration-rc|feat/synthesis-orchestration
EOF
}

quote_arg() {
  printf "%q" "$1"
}

canonical_dir() {
  cd "$1" 2>/dev/null && pwd -P
}

process_cwd() {
  local pid="$1"
  /usr/sbin/lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | awk '
    /^n/ {
      print substr($0, 2)
      exit
    }
  '
}

process_cwd_matches() {
  local pid="$1"
  local expected_dir="$2"
  local expected actual

  expected="$(canonical_dir "$expected_dir" || true)"
  actual="$(process_cwd "$pid")"
  [[ -n "$expected" && -n "$actual" && "$actual" == "$expected" ]]
}

remote_control_candidates() {
  ps axww -o pid= -o command= | awk '
    function strip_quotes(value) {
      gsub(/^[\"\047]+|[\"\047]+$/, "", value)
      return value
    }
    function print_candidate(value) {
      value = strip_quotes(value)
      if (value != "") {
        print pid "|" value
      }
    }
    {
      pid = $1
      executable = strip_quotes($2)
      sub(/^.*\//, "", executable)
      if (executable == "SCREEN" || executable == "screen" || executable == "login" || executable == "awk") {
        next
      }
      for (i = 3; i <= NF; i++) {
        arg = strip_quotes($i)
        if (arg == "--remote-control" && i < NF) {
          print_candidate($(i + 1))
          next
        }
        if (arg ~ /^--remote-control=/) {
          print_candidate(substr(arg, 18))
          next
        }
      }
    }
  '
}

remote_control_pids() {
  local session_name="$1"
  local worktree="$2"
  local pid found_session

  while IFS='|' read -r pid found_session; do
    if [[ "$found_session" == "$session_name" ]] && process_cwd_matches "$pid" "$worktree"; then
      printf "%s\n" "$pid"
    fi
  done < <(remote_control_candidates)
}

kill_remote_control_processes() {
  local session_name="$1"
  local worktree="$2"
  local pid

  while IFS= read -r pid; do
    if [[ "$pid" =~ ^[0-9]+$ ]]; then
      kill -9 "$pid" >/dev/null 2>&1 || true
    fi
  done < <(remote_control_pids "$session_name" "$worktree")
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
  while IFS='|' read -r label worktree session_name screen_name expected_branch; do
    if [[ ! -d "$worktree" ]]; then
      echo "Missing worktree for $label: $worktree" >&2
      return 1
    fi
    if [[ ! -f "$worktree/CONTEXT.md" || ! -f "$worktree/CHARTER.md" ]]; then
      echo "Missing CONTEXT.md or CHARTER.md in $worktree" >&2
      return 1
    fi
    local branch
    branch="$(git -C "$worktree" branch --show-current 2>/dev/null || true)"
    if [[ "$branch" != "$expected_branch" ]]; then
      echo "Wrong branch for $label: expected $expected_branch, got ${branch:-unknown}" >&2
      return 1
    fi
  done < <(entries)
}

cleanup_targets() {
  echo "Stopping target Portfolio remote-control sessions..."
  while IFS='|' read -r label worktree session_name screen_name expected_branch; do
    /usr/bin/screen -S "$screen_name" -X quit >/dev/null 2>&1 || true
    kill_remote_control_processes "$session_name" "$worktree"
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

  while IFS='|' read -r label worktree session_name screen_name expected_branch; do
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

  while IFS='|' read -r label worktree session_name screen_name expected_branch; do
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

  while IFS='|' read -r label worktree session_name screen_name expected_branch; do
    echo "Priming $label..."
    if ! PORTFOLIO_RC_MISSION="$MISSION_PROMPT" python3 "$primer" --screen "$screen_name" --label "$label" --timeout "$PRIME_TIMEOUT"; then
      echo "Prime failed for $label; attach manually with: TERM=xterm-256color screen -x $screen_name" >&2
      failed=1
    fi
  done < <(entries)

  return "$failed"
}

log_has_evidence() {
  local log_file="$1"
  python3 - "$log_file" "$LOG_EVIDENCE_PATTERN" <<'PY'
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
pattern = sys.argv[2]
data = path.read_bytes()
data = re.sub(
    rb"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)|\x1b[@-_]",
    b"",
    data,
)
text = data.replace(b"\x00", b"").decode("utf-8", errors="ignore")
raise SystemExit(0 if re.search(pattern, text) else 1)
PY
}

caffeinate_pid_is_managed() {
  local pid="$1"
  local command

  command="$(ps -p "$pid" -o command= 2>/dev/null || true)"
  [[ "$command" == *caffeinate* && "$command" == *-dimsu* ]]
}

ensure_caffeinate() {
  if [[ -s "$CAFFEINATE_PID_FILE" ]]; then
    local existing_pid
    existing_pid="$(cat "$CAFFEINATE_PID_FILE" 2>/dev/null || true)"
    if [[ "$existing_pid" =~ ^[0-9]+$ ]] && caffeinate_pid_is_managed "$existing_pid"; then
      echo "Skill-managed caffeinate is already running as PID $existing_pid."
      return 0
    fi
    rm -f "$CAFFEINATE_PID_FILE"
  fi

  mkdir -p "$BASE_DIR"
  echo "Starting caffeinate for $CAFFEINATE_SECONDS seconds..."
  nohup /usr/bin/caffeinate -dimsu -t "$CAFFEINATE_SECONDS" >"$BASE_DIR/caffeinate.log" 2>&1 &
  echo "$!" >"$CAFFEINATE_PID_FILE"
}

# Agents are useless if they cannot push: the mini's gh token expires and origin
# can be a dead SSH remote. Verify a real authenticated remote op works.
check_push_auth() {
  local failed=0

  if ! command -v gh >/dev/null 2>&1 || ! gh auth status -h github.com >/dev/null 2>&1; then
    echo "WARNING: gh is not authenticated for GitHub; agents may not be able to push." >&2
    failed=1
  elif ! gh api repos/tnorlund/Portfolio --jq '.permissions.push' 2>/dev/null | grep -q '^true$'; then
    echo "WARNING: gh token does not report push permission for tnorlund/Portfolio." >&2
    failed=1
  fi

  while IFS='|' read -r label worktree session_name screen_name expected_branch; do
    local branch
    branch="$(git -C "$worktree" branch --show-current 2>/dev/null || true)"
    if [[ -z "$branch" ]] || ! git -C "$worktree" push --dry-run origin "HEAD:refs/heads/$branch" >/dev/null 2>&1; then
      echo "WARNING: dry-run push failed for $label from $worktree." >&2
      failed=1
    fi
  done < <(entries)

  if [[ "$failed" -ne 0 ]]; then
    echo "  Fix on the mini, then relaunch:" >&2
    echo "    gh auth status            # if invalid, re-auth (e.g. from a good machine:" >&2
    echo "    #   gh auth token | ssh <mini> 'gh auth login --with-token')" >&2
    echo "    gh auth setup-git" >&2
    echo "    git -C $PORTFOLIO_ROOT remote set-url origin https://github.com/tnorlund/Portfolio.git  # if origin is a dead SSH remote" >&2
    return 1
  fi

  return 0
}

# Remote-control sessions outside the expected trio and worktrees.
stray_sessions() {
  local pid found_session expected_worktree cwd

  while IFS='|' read -r pid found_session; do
    expected_worktree=""
    while IFS='|' read -r label worktree session_name screen_name expected_branch; do
      if [[ "$found_session" == "$session_name" ]]; then
        expected_worktree="$worktree"
        break
      fi
    done < <(entries)

    if [[ -n "$expected_worktree" ]] && process_cwd_matches "$pid" "$expected_worktree"; then
      continue
    fi

    cwd="$(process_cwd "$pid")"
    if [[ -n "$cwd" ]]; then
      printf "%s pid=%s cwd=%s\n" "$found_session" "$pid" "$cwd"
    else
      printf "%s pid=%s\n" "$found_session" "$pid"
    fi
  done < <(remote_control_candidates) | sort -u || true
}

dirty_count() {
  local worktree="$1"
  local count

  if count="$(git -C "$worktree" status --porcelain 2>/dev/null | wc -l | tr -d ' ')"; then
    echo "${count:-0}"
  else
    echo 0
  fi
}

unpushed_count() {
  local worktree="$1"
  local count

  if count="$(git -C "$worktree" rev-list --count '@{u}..HEAD' 2>/dev/null)"; then
    echo "${count:-0}"
  else
    echo unknown
  fi
}

caffeinate_processes() {
  ps axww -o pid= -o command= | awk '
    {
      executable = $2
      sub(/^.*\//, "", executable)
      if (executable == "caffeinate" && $0 ~ /-dimsu/) {
        print
      }
    }
  '
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
  while IFS='|' read -r label worktree session_name screen_name expected_branch; do
    local process_ok=0
    local screen_ok=0
    local log_ok=0
    local log_file="$BASE_DIR/$label/screenlog.0"

    if remote_control_pids "$session_name" "$worktree" | grep -q .; then
      process_ok=1
    else
      missing=1
    fi

    if printf "%s\n" "$screen_listing" | grep -E "[0-9]+\\.$screen_name[[:space:]]" >/dev/null 2>&1; then
      screen_ok=1
    else
      missing=1
    fi

    if [[ -f "$log_file" ]] && log_has_evidence "$log_file"; then
      log_ok=1
    else
      missing=1
    fi

    local dirty unpushed cadence
    dirty="$(dirty_count "$worktree")"
    unpushed="$(unpushed_count "$worktree")"
    cadence=ok
    if [[ "${dirty:-0}" -gt "$DIRTY_WARN" || "$unpushed" == "unknown" || "$unpushed" -gt 0 ]]; then
      cadence=DRIFT
    fi

    printf "%-16s process=%s screen=%s log_evidence=%s dirty=%s unpushed=%s cadence=%s log=%s\n" \
      "$label" "$process_ok" "$screen_ok" "$log_ok" "$dirty" "$unpushed" "$cadence" "$log_file"
  done < <(entries)

  echo
  echo "Unexpected remote-control sessions:"
  local strays; strays="$(stray_sessions)"
  if [[ -n "$strays" ]]; then
    printf "%s\n" "$strays" | sed 's/^/  /'
    echo "  (not part of the trio -- close with: screen -S <name> -X quit, or kill the claude pid)"
  else
    echo "  none"
  fi

  echo
  echo "Caffeinate:"
  local caffeinate_lines; caffeinate_lines="$(caffeinate_processes)"
  if [[ -n "$caffeinate_lines" ]]; then
    printf "%s\n" "$caffeinate_lines"
  else
    missing=1
  fi

  return "$missing"
}

command="${1:-launch}"
case "$command" in
  launch)
    require_prereqs
    cleanup_targets
    check_push_auth || echo "  (continuing launch; fix push auth before the agents try to push)" >&2
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
    require_prereqs
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
