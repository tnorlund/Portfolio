#!/bin/bash
# Start prod embedding ingestion step functions (word + line).
# Usage: ./scripts/start_ingestion_prod.sh [line|word|both]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_DIR="$(dirname "$SCRIPT_DIR")/infra"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

WORKFLOW_TYPE="${1:-both}"

case "$WORKFLOW_TYPE" in
  line|word|both) ;;
  --help|-h)
    echo "Usage: $0 [line|word|both]"
    echo "Default: both"
    exit 0
    ;;
  *)
    echo -e "${RED}Unknown argument: $WORKFLOW_TYPE${NC}" >&2
    echo "Usage: $0 [line|word|both]" >&2
    exit 1
    ;;
esac

cd "$INFRA_DIR"

echo -e "${YELLOW}Fetching prod Step Function ARNs...${NC}"

PULUMI_OUTPUTS=$(pulumi stack output --stack tnorlund/portfolio/prod --json 2>/dev/null) || PULUMI_OUTPUTS="{}"
LINE_INGEST_ARN=$(echo "$PULUMI_OUTPUTS" | python3 -c "import sys,json; d=json.load(sys.stdin); v=d.get('embedding_line_ingest_sf_arn'); print((v.get('value','') if isinstance(v,dict) else v) or '')" 2>/dev/null || echo "")
WORD_INGEST_ARN=$(echo "$PULUMI_OUTPUTS" | python3 -c "import sys,json; d=json.load(sys.stdin); v=d.get('embedding_word_ingest_sf_arn'); print((v.get('value','') if isinstance(v,dict) else v) or '')" 2>/dev/null || echo "")

# Fallback to AWS CLI
if [[ -z "$LINE_INGEST_ARN" ]]; then
  LINE_INGEST_ARN=$(aws stepfunctions list-state-machines --query 'stateMachines[?contains(name,`line-ingest`) && contains(name,`prod`)].stateMachineArn' --output text 2>/dev/null | head -1)
fi
if [[ -z "$WORD_INGEST_ARN" ]]; then
  WORD_INGEST_ARN=$(aws stepfunctions list-state-machines --query 'stateMachines[?contains(name,`word-ingest`) && contains(name,`prod`)].stateMachineArn' --output text 2>/dev/null | head -1)
fi

start_sf() {
  local ARN="$1" TYPE="$2"
  if [[ -z "$ARN" ]]; then
    echo -e "${YELLOW}Skipping $TYPE — ARN not found${NC}" >&2
    return
  fi
  local NAME="manual-run-$(date +%s)-$TYPE"
  local EXEC_ARN
  EXEC_ARN=$(aws stepfunctions start-execution --state-machine-arn "$ARN" --name "$NAME" --query 'executionArn' --output text)
  echo -e "${GREEN}Started $TYPE ingestion: $EXEC_ARN${NC}"
  echo "$EXEC_ARN"
}

case "$WORKFLOW_TYPE" in
  line)
    [[ -z "$LINE_INGEST_ARN" ]] && { echo -e "${RED}Could not find line-ingest SF${NC}" >&2; exit 1; }
    start_sf "$LINE_INGEST_ARN" "line"
    ;;
  word)
    [[ -z "$WORD_INGEST_ARN" ]] && { echo -e "${RED}Could not find word-ingest SF${NC}" >&2; exit 1; }
    start_sf "$WORD_INGEST_ARN" "word"
    ;;
  both)
    [[ -z "$LINE_INGEST_ARN" ]] && echo -e "${YELLOW}Warning: line-ingest SF not found${NC}" >&2
    [[ -z "$WORD_INGEST_ARN" ]] && echo -e "${YELLOW}Warning: word-ingest SF not found${NC}" >&2
    [[ -n "$LINE_INGEST_ARN" ]] && start_sf "$LINE_INGEST_ARN" "line"
    [[ -n "$WORD_INGEST_ARN" ]] && start_sf "$WORD_INGEST_ARN" "word"
    ;;
esac
