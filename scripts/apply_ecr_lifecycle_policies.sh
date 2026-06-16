#!/usr/bin/env bash
# apply_ecr_lifecycle_policies.sh
#
# Applies a standard ECR lifecycle policy to ALL existing repositories in the
# current AWS account/region immediately, without waiting for Pulumi to touch
# each repo.
#
# Policy:
#   Rule 1 (priority 1): expire untagged images after 1 day
#   Rule 2 (priority 2): keep only the last 10 tagged images
#
# Usage:
#   ./scripts/apply_ecr_lifecycle_policies.sh [--region us-east-1] [--dry-run]
#
# Prerequisites: aws CLI configured with sufficient permissions
#   ecr:DescribeRepositories, ecr:PutLifecyclePolicy

set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
REGION="${AWS_DEFAULT_REGION:-us-east-1}"
DRY_RUN=false

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --region)
      if [[ $# -lt 2 || -z "${2:-}" || "${2:0:1}" == "-" ]]; then
        echo "Missing value for --region" >&2
        echo "Usage: $0 [--region REGION] [--dry-run]" >&2
        exit 1
      fi
      REGION="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      echo "Usage: $0 [--region REGION] [--dry-run]" >&2
      exit 1
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Lifecycle policy JSON
# ---------------------------------------------------------------------------
LIFECYCLE_POLICY=$(cat <<'EOF'
{
  "rules": [
    {
      "rulePriority": 1,
      "description": "Expire untagged images after 1 day",
      "selection": {
        "tagStatus": "untagged",
        "countType": "sinceImagePushed",
        "countUnit": "days",
        "countNumber": 1
      },
      "action": {"type": "expire"}
    },
    {
      "rulePriority": 2,
      "description": "Keep last 10 tagged images",
      "selection": {
        "tagStatus": "tagged",
        "tagPatternList": ["*"],
        "countType": "imageCountMoreThan",
        "countNumber": 10
      },
      "action": {"type": "expire"}
    }
  ]
}
EOF
)

# ---------------------------------------------------------------------------
# Gather all repository names (handles pagination automatically via --no-paginate
# equivalent: aws ecr describe-repositories pages automatically in recent CLI)
# ---------------------------------------------------------------------------
echo "Fetching ECR repositories in region '${REGION}'..."

REPOS=$(aws ecr describe-repositories \
  --region "${REGION}" \
  --query 'repositories[*].repositoryName' \
  --output text)

if [[ -z "$REPOS" ]]; then
  echo "No ECR repositories found in region '${REGION}'."
  exit 0
fi

REPO_COUNT=$(echo "$REPOS" | wc -w | tr -d ' ')
echo "Found ${REPO_COUNT} repositories."

if [[ "$DRY_RUN" == "true" ]]; then
  echo "[DRY-RUN] Would apply lifecycle policy to:"
  for REPO in $REPOS; do
    echo "  - ${REPO}"
  done
  echo "[DRY-RUN] No changes were made."
  exit 0
fi

# ---------------------------------------------------------------------------
# Apply policy to each repo
# ---------------------------------------------------------------------------
SUCCESS=0
FAILED=0

for REPO in $REPOS; do
  echo -n "  Applying policy to '${REPO}'... "
  if aws ecr put-lifecycle-policy \
    --region "${REGION}" \
    --repository-name "${REPO}" \
    --lifecycle-policy-text "${LIFECYCLE_POLICY}" \
    --output text > /dev/null 2>&1; then
    echo "OK"
    SUCCESS=$((SUCCESS + 1))
  else
    echo "FAILED"
    FAILED=$((FAILED + 1))
  fi
done

echo ""
echo "Done. ${SUCCESS} succeeded, ${FAILED} failed (out of ${REPO_COUNT} total)."
if [[ $FAILED -gt 0 ]]; then
  exit 1
fi
