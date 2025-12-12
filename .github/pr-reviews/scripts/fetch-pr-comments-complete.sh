#!/bin/bash
# Fetch ALL PR comments from GitHub with pagination
# Includes: review summaries, inline comments, PR comments, and issue comments
# Usage: ./scripts/fetch-pr-comments-complete.sh <PR_NUMBER>

set -e

PR_NUMBER="${1:-502}"
OUTPUT_DIR="$(dirname "$0")/../pr-review-data"
OUTPUT_FILE="$OUTPUT_DIR/pr-${PR_NUMBER}-complete.json"

mkdir -p "$OUTPUT_DIR"

echo "🔍 Fetching complete PR data for #${PR_NUMBER}..."
echo ""

# Function to fetch paginated data
fetch_paginated_reviews() {
    local pr=$1
    local temp_file=$(mktemp)
    echo "[]" > "$temp_file"
    local cursor=""
    local has_next=true
    local page=1

    while [ "$has_next" = "true" ]; do
        echo "  📄 Fetching reviews page $page..."

        local query='
query($owner: String!, $repo: String!, $pr: Int!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $pr) {
      reviews(first: 50, after: $cursor) {
        totalCount
        pageInfo {
          hasNextPage
          endCursor
        }
        nodes {
          id
          author { login }
          authorAssociation
          body
          submittedAt
          state
          commit { oid }
          comments(first: 100) {
            totalCount
            pageInfo {
              hasNextPage
              endCursor
            }
            nodes {
              id
              path
              position
              line
              body
              createdAt
              diffHunk
            }
          }
        }
      }
    }
  }
}'

        local result
        if [ -z "$cursor" ] || [ "$cursor" = "null" ]; then
            result=$(gh api graphql -f query="$query" -f owner=tnorlund -f repo=Portfolio -F pr="$pr")
        else
            result=$(gh api graphql -f query="$query" -f owner=tnorlund -f repo=Portfolio -F pr="$pr" -f cursor="$cursor")
        fi

        local page_reviews=$(echo "$result" | jq '.data.repository.pullRequest.reviews.nodes')

        # Append to temp file
        jq --argjson new "$page_reviews" '. + $new' "$temp_file" > "${temp_file}.tmp"
        mv "${temp_file}.tmp" "$temp_file"

        has_next=$(echo "$result" | jq -r '.data.repository.pullRequest.reviews.pageInfo.hasNextPage')
        cursor=$(echo "$result" | jq -r '.data.repository.pullRequest.reviews.pageInfo.endCursor')

        local count=$(echo "$page_reviews" | jq 'length')
        echo "     ✓ Got $count reviews"

        page=$((page + 1))
    done

    cat "$temp_file"
    rm -f "$temp_file"
}

# Fetch basic PR info
echo "1️⃣  Basic PR information..."
PR_INFO=$(gh pr view "$PR_NUMBER" --json number,title,url,state,author,createdAt,updatedAt,baseRefName,headRefName,commits,additions,deletions,changedFiles)

echo "   ✓ Title: $(echo "$PR_INFO" | jq -r '.title')"
echo "   ✓ State: $(echo "$PR_INFO" | jq -r '.state')"
echo "   ✓ Commits: $(echo "$PR_INFO" | jq '.commits | length')"
echo ""

# Fetch reviews with pagination
echo "2️⃣  Reviews with inline comments..."
REVIEWS=$(fetch_paginated_reviews "$PR_NUMBER")
REVIEW_COUNT=$(echo "$REVIEWS" | jq 'length')
INLINE_COUNT=$(echo "$REVIEWS" | jq '[.[].comments.nodes[]] | length')
echo "   ✓ Total reviews: $REVIEW_COUNT"
echo "   ✓ Total inline comments: $INLINE_COUNT"
echo ""

# Fetch PR-level comments (not review comments)
echo "3️⃣  PR-level comments..."
COMMENTS=$(gh api graphql -f query='
query($owner: String!, $repo: String!, $pr: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $pr) {
      comments(first: 100) {
        totalCount
        nodes {
          id
          author { login }
          body
          createdAt
          isMinimized
        }
      }
    }
  }
}' -f owner=tnorlund -f repo=Portfolio -F pr="$PR_NUMBER" | jq '.data.repository.pullRequest.comments.nodes')

COMMENT_COUNT=$(echo "$COMMENTS" | jq 'length')
echo "   ✓ Total PR comments: $COMMENT_COUNT"
echo ""

# Combine all data
echo "4️⃣  Combining all data..."
COMBINED=$(jq -n \
    --argjson pr "$PR_INFO" \
    --argjson reviews "$REVIEWS" \
    --argjson comments "$COMMENTS" \
    '{
        pr_info: $pr,
        reviews: $reviews,
        pr_comments: $comments,
        summary: {
            total_reviews: ($reviews | length),
            total_inline_comments: ($reviews | [.[].comments.nodes[]] | length),
            total_pr_comments: ($comments | length),
            reviews_by_author: ($reviews | group_by(.author.login) | map({
                author: .[0].author.login,
                count: length
            })),
            latest_review: ($reviews | max_by(.submittedAt) | .submittedAt),
            coderabbit_reviews: ($reviews | map(select(.author.login == "coderabbitai")) | length)
        },
        fetched_at: now | strftime("%Y-%m-%dT%H:%M:%SZ")
    }')

# Save to file
echo "$COMBINED" > "$OUTPUT_FILE"
echo "   ✓ Saved to: $OUTPUT_FILE"
echo ""

# Display summary
echo "📊 Summary:"
echo "$COMBINED" | jq -r '
"   Reviews: \(.summary.total_reviews)
   Inline comments: \(.summary.total_inline_comments)
   PR comments: \(.summary.total_pr_comments)
   CodeRabbit reviews: \(.summary.coderabbit_reviews)
   Latest review: \(.summary.latest_review)
   Latest commit: \(.pr_info.commits[-1].oid)

   By author:"
'
echo "$COMBINED" | jq -r '.summary.reviews_by_author[] | "     \(.author): \(.count) reviews"'

echo ""
echo "✅ Complete! Use this file with the parser:"
echo "   python3 scripts/parse-coderabbit-reviews.py $OUTPUT_FILE"

