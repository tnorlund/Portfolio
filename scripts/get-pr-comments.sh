#!/bin/bash
# Get PR Comments - Fetch and display comments from the active PR for the current branch

# Check if gh CLI is available
if ! command -v gh &> /dev/null; then
    echo "Error: gh CLI is not installed. Install it from https://cli.github.com/"
    exit 1
fi

# Check if we're in a git repository
if ! git rev-parse --is-inside-work-tree &> /dev/null; then
    echo "Error: Not in a git repository"
    exit 1
fi

# Get current branch
BRANCH=$(git branch --show-current)
echo "Checking for PR on branch: $BRANCH"
echo ""

# Check if a PR exists for the current branch
PR_INFO=$(gh pr view --json number,url,title 2>/dev/null) || {
    echo "No open PR found for branch '$BRANCH'"
    exit 0
}

PR_NUMBER=$(echo "$PR_INFO" | jq -r '.number')
PR_TITLE=$(echo "$PR_INFO" | jq -r '.title')
PR_URL=$(echo "$PR_INFO" | jq -r '.url')

echo "═══════════════════════════════════════════════════════════════════"
echo "PR #$PR_NUMBER: $PR_TITLE"
echo "$PR_URL"
echo "═══════════════════════════════════════════════════════════════════"
echo ""

# Fetch all comments and reviews
PR_DATA=$(gh pr view --json comments,reviews,reviewRequests)

# Display review comments
REVIEWS=$(echo "$PR_DATA" | jq -r '.reviews')
REVIEW_COUNT=$(echo "$REVIEWS" | jq 'length')

if [ "$REVIEW_COUNT" -gt 0 ]; then
    echo "───────────────────────────────────────────────────────────────────"
    echo "REVIEWS ($REVIEW_COUNT)"
    echo "───────────────────────────────────────────────────────────────────"

    echo "$REVIEWS" | jq -r '.[] |
        "[\(.state)] \(.author.login) - \(.submittedAt | split("T")[0])\n\(.body)\n"' |
    while IFS= read -r line; do
        [ -n "$line" ] && echo "$line"
    done
    echo ""
fi

# Display general PR comments
COMMENTS=$(echo "$PR_DATA" | jq -r '.comments')
COMMENT_COUNT=$(echo "$COMMENTS" | jq 'length')

if [ "$COMMENT_COUNT" -gt 0 ]; then
    echo "───────────────────────────────────────────────────────────────────"
    echo "COMMENTS ($COMMENT_COUNT)"
    echo "───────────────────────────────────────────────────────────────────"

    echo "$COMMENTS" | jq -r '.[] |
        "\(.author.login) - \(.createdAt | split("T")[0])\n\(.body)\n"' |
    while IFS= read -r line; do
        [ -n "$line" ] && echo "$line"
    done
    echo ""
fi

# Fetch review threads (inline comments)
REVIEW_THREADS=$(gh api "repos/{owner}/{repo}/pulls/$PR_NUMBER/comments" 2>/dev/null || echo "[]")
THREAD_COUNT=$(echo "$REVIEW_THREADS" | jq 'length')

if [ "$THREAD_COUNT" -gt 0 ]; then
    echo "───────────────────────────────────────────────────────────────────"
    echo "INLINE REVIEW COMMENTS ($THREAD_COUNT)"
    echo "───────────────────────────────────────────────────────────────────"

    echo "$REVIEW_THREADS" | jq -r '.[] |
        "\(.user.login) - \(.created_at | split("T")[0]) [\(.path):\(.line // .original_line // "")]\n\(.body)\n"' |
    while IFS= read -r line; do
        [ -n "$line" ] && echo "$line"
    done
    echo ""
fi

# Summary
if [ "$REVIEW_COUNT" -eq 0 ] && [ "$COMMENT_COUNT" -eq 0 ] && [ "$THREAD_COUNT" -eq 0 ]; then
    echo "No comments or reviews on this PR yet."
fi

echo "═══════════════════════════════════════════════════════════════════"
