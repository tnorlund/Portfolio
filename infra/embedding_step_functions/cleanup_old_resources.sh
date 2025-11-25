#!/bin/bash
# Cleanup script for old embedding Lambda functions and ECR repos after rename

set -e

echo "🧹 Cleaning up old embedding resources..."

# Old Lambda functions to delete
OLD_LAMBDAS=(
    "embedding-line-poll-lambda-dev-17afd14"
    "embedding-line-poll-lambda-dev-8c6e936"
    "embedding-word-poll-lambda-dev-3744e80"
    "embedding-word-poll-lambda-dev-a18caf9"
    "embedding-vector-compact-lambda-dev-377c856"
    "embedding-vector-compact-lambda-dev-7194324"
)

# Old ECR repositories to delete
OLD_ECR_REPOS=(
    "embedding-line-poll-docker-repo-02fded9"
    "embedding-word-poll-docker-repo-b7c3b31"
    "embedding-vector-compact-docker-repo-ecf1810"
)

echo ""
echo "📋 Lambda Functions to delete:"
for lambda in "${OLD_LAMBDAS[@]}"; do
    echo "  - $lambda"
done

echo ""
echo "📋 ECR Repositories to delete:"
for repo in "${OLD_ECR_REPOS[@]}"; do
    echo "  - $repo"
done

echo ""
read -p "⚠️  Are you sure you want to delete these resources? (yes/no): " confirm

if [ "$confirm" != "yes" ]; then
    echo "❌ Cancelled."
    exit 1
fi

echo ""
echo "🗑️  Deleting Lambda functions..."
for lambda in "${OLD_LAMBDAS[@]}"; do
    if aws lambda get-function --function-name "$lambda" >/dev/null 2>&1; then
        echo "  Deleting $lambda..."
        aws lambda delete-function --function-name "$lambda" 2>/dev/null || echo "    ⚠️  Failed to delete (may already be deleted)"
    else
        echo "  ⏭️  $lambda not found, skipping"
    fi
done

echo ""
echo "🗑️  Deleting ECR repositories..."
for repo in "${OLD_ECR_REPOS[@]}"; do
    if aws ecr describe-repositories --repository-names "$repo" >/dev/null 2>&1; then
        echo "  Deleting $repo..."
        # Delete all images first (using --force will delete images automatically)
        # Delete the repository (--force deletes all images)
        aws ecr delete-repository --repository-name "$repo" --force 2>/dev/null || echo "    ⚠️  Failed to delete (may already be deleted)"
    else
        echo "  ⏭️  $repo not found, skipping"
    fi
done

echo ""
echo "✅ Cleanup complete!"

