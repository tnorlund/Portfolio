#!/bin/bash
# Fetch PR comments from GitHub CLI for tracking
# Usage: ./scripts/fetch-pr-comments.sh <PR_NUMBER>

set -e

PR_NUMBER="${1:-502}"
OUTPUT_DIR="$(dirname "$0")/../pr-review-data"
OUTPUT_FILE="$OUTPUT_DIR/pr-${PR_NUMBER}-comments.json"

mkdir -p "$OUTPUT_DIR"

echo "Fetching comments for PR #${PR_NUMBER}..."

# Fetch all review data
gh pr view "$PR_NUMBER" --json \
  number,title,url,state,reviews,comments,latestReviews,commits \
  > "$OUTPUT_FILE"

echo "✅ Saved to: $OUTPUT_FILE"

# Show summary
echo ""
echo "Summary:"
jq -r '
  .reviews |
  group_by(.author.login) |
  map({
    author: .[0].author.login,
    count: length,
    latest: (map(.submittedAt) | max)
  }) |
  .[] |
  "\(.author): \(.count) reviews (latest: \(.latest))"
' "$OUTPUT_FILE"

echo ""
echo "Latest commit: $(jq -r '.commits[-1].oid' "$OUTPUT_FILE")"
echo "PR state: $(jq -r '.state' "$OUTPUT_FILE")"

