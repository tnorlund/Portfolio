#!/usr/bin/env bash
# Agent preflight for headless runs (plan humble-skipping-quilt W1).
#
# Composes read-only health checks a headless agent session depends on:
#   1. MCP auth healthcheck        scripts/check_mcp_auth.py
#   2. AWS dev-table readability   aws dynamodb describe-table (READ-ONLY)
#      table = $DYNAMODB_TABLE_NAME, default dev ReceiptsTable-dc5be22.
#      Never touches prod: default is the dev table and the only call made
#      is describe-table.
#   3. Git state                   branch, clean/dirty, origin reachability
#   4. Disk free                   >= 10 GB required on the repo volume
#
# Output: one JSON document on stdout, human summary on stderr.
# Exit:   0 healthy / 1 degraded / 2 red.
# Safe to run anywhere (laptop or mini); mutates nothing.

set -u -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# AGENT_PREFLIGHT_REPO_ROOT lets the preflight be staged outside the repo
# (e.g. copied to /tmp on a remote host) while still checking the real repo.
REPO_ROOT="${AGENT_PREFLIGHT_REPO_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
TABLE_NAME="${DYNAMODB_TABLE_NAME:-ReceiptsTable-dc5be22}"
DISK_MIN_GB=10

# ---- 1. MCP auth healthcheck ---------------------------------------------
MCP_JSON="$(python3 "$SCRIPT_DIR/check_mcp_auth.py" --repo-root "$REPO_ROOT" 2>/dev/null)"
MCP_EXIT=$?
if [ -z "$MCP_JSON" ]; then
  MCP_JSON='{"overall":"red","checks":[],"error":"check_mcp_auth.py produced no output"}'
  MCP_EXIT=2
fi

# ---- 2. AWS dev-table readability (describe-table is read-only) ----------
AWS_STATUS="red"
AWS_DETAIL=""
if command -v aws >/dev/null 2>&1; then
  AWS_OUT="$(aws dynamodb describe-table \
      --table-name "$TABLE_NAME" \
      --cli-connect-timeout 10 --cli-read-timeout 15 \
      --query 'Table.{name:TableName,status:TableStatus,items:ItemCount}' \
      --output json 2>&1)"
  if [ $? -eq 0 ]; then
    AWS_STATUS="ok"
    AWS_DETAIL="$(printf '%s' "$AWS_OUT" | tr -d '\n' | tr -s ' ')"
  else
    AWS_DETAIL="describe-table failed: $(printf '%s' "$AWS_OUT" | head -2 | tr '\n' ' ')"
  fi
else
  AWS_DETAIL="aws CLI not installed"
fi

# ---- 3. Git state (all read-only; ls-remote makes no changes) ------------
GIT_BRANCH="$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
GIT_DIRTY_COUNT="$(git -C "$REPO_ROOT" status --porcelain 2>/dev/null | wc -l | tr -d ' ')"
if [ "${GIT_DIRTY_COUNT:-0}" -eq 0 ]; then GIT_TREE="clean"; else GIT_TREE="dirty(${GIT_DIRTY_COUNT})"; fi
if GIT_TERMINAL_PROMPT=0 GIT_SSH_COMMAND="ssh -o BatchMode=yes -o ConnectTimeout=10" \
   git -C "$REPO_ROOT" -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=15 \
   ls-remote --exit-code --heads origin >/dev/null 2>&1; then
  GIT_ORIGIN="reachable"
  GIT_STATUS="ok"
else
  GIT_ORIGIN="unreachable"
  GIT_STATUS="degraded"
fi
GIT_DETAIL="branch=$GIT_BRANCH tree=$GIT_TREE origin=$GIT_ORIGIN"

# ---- 4. Disk free --------------------------------------------------------
# >= 10 GB ok; below that degraded; below 2 GB red (a render/eval night
# cannot run and even logs/artifacts may fail to write).
DISK_AVAIL_KB="$(df -k "$REPO_ROOT" | awk 'NR==2 {print $4}')"
DISK_AVAIL_GB=$(( ${DISK_AVAIL_KB:-0} / 1048576 ))
if [ "$DISK_AVAIL_GB" -ge "$DISK_MIN_GB" ]; then
  DISK_STATUS="ok"
elif [ "$DISK_AVAIL_GB" -ge 2 ]; then
  DISK_STATUS="degraded"
else
  DISK_STATUS="red"
fi
DISK_DETAIL="${DISK_AVAIL_GB}GB free (need >= ${DISK_MIN_GB}GB, red < 2GB)"

# ---- Compose -------------------------------------------------------------
export MCP_JSON MCP_EXIT AWS_STATUS AWS_DETAIL TABLE_NAME \
       GIT_STATUS GIT_DETAIL DISK_STATUS DISK_DETAIL DISK_AVAIL_GB

REPORT="$(python3 - <<'PYEOF'
import json, os, socket
from datetime import datetime, timezone

sev = {"ok": 0, "healthy": 0, "degraded": 1, "red": 2}

try:
    mcp = json.loads(os.environ["MCP_JSON"])
except json.JSONDecodeError:
    mcp = {"overall": "red", "error": "unparseable check_mcp_auth output"}

checks = {
    "mcp_auth": {"status": mcp.get("overall", "red"), "report": mcp},
    "aws_dev_table": {
        "status": os.environ["AWS_STATUS"],
        "table": os.environ["TABLE_NAME"],
        "detail": os.environ["AWS_DETAIL"],
    },
    "git": {"status": os.environ["GIT_STATUS"], "detail": os.environ["GIT_DETAIL"]},
    "disk": {"status": os.environ["DISK_STATUS"], "detail": os.environ["DISK_DETAIL"]},
}
worst = max(sev.get(c["status"], 2) for c in checks.values())
overall = ["healthy", "degraded", "red"][worst]
doc = {
    "overall": overall,
    "host": socket.gethostname(),
    "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "checks": checks,
}
print(json.dumps(doc, indent=2))
PYEOF
)"

OVERALL="$(printf '%s' "$REPORT" | python3 -c 'import json,sys; print(json.load(sys.stdin)["overall"])')"

# JSON to stdout, human summary to stderr.
printf '%s\n' "$REPORT"
{
  echo "== agent preflight: $OVERALL =="
  echo "  mcp_auth : $(printf '%s' "$MCP_JSON" | python3 -c 'import json,sys
d=json.load(sys.stdin)
parts = "; ".join("%s=%s" % (c["name"], c["status"]) for c in d.get("checks", []))
print(d.get("overall", "red"), "|", parts)')"
  echo "  aws      : $AWS_STATUS | table=$TABLE_NAME $AWS_DETAIL"
  echo "  git      : $GIT_STATUS | $GIT_DETAIL"
  echo "  disk     : $DISK_STATUS | $DISK_DETAIL"
} >&2

case "$OVERALL" in
  healthy)  exit 0 ;;
  degraded) exit 1 ;;
  *)        exit 2 ;;
esac
