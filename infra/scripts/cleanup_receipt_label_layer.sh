#!/bin/bash
# Script to clean up receipt-label layer infrastructure
# This removes CodeBuild projects, CodePipelines, S3 buckets, and Lambda layers

set -e

echo "🧹 Cleaning up receipt-label layer infrastructure..."

# CodeBuild projects
echo "Deleting CodeBuild projects..."
for project in receipt-label-build-py312-dev-cb8a083 receipt-label-build-py312-prod-a001c42 receipt-label-publish-dev-9be6afc receipt-label-publish-prod-822cf39; do
    if aws codebuild get-project --name "$project" &>/dev/null; then
        echo "  Deleting $project..."
        aws codebuild delete-project --name "$project" || echo "    Failed to delete $project (may not exist)"
    else
        echo "  $project does not exist, skipping"
    fi
done

# CodePipelines
echo "Deleting CodePipelines..."
for pipeline in receipt-label-pipeline-dev-febb38e receipt-label-pipeline-prod-cd17d8f; do
    if aws codepipeline get-pipeline --name "$pipeline" &>/dev/null; then
        echo "  Deleting $pipeline..."
        aws codepipeline delete-pipeline --name "$pipeline" || echo "    Failed to delete $pipeline (may not exist)"
    else
        echo "  $pipeline does not exist, skipping"
    fi
done

# S3 buckets (need to empty first)
echo "Deleting S3 buckets..."
for bucket in receipt-label-artifacts-e9a419d receipt-label-artifacts-f87ffab; do
    if aws s3 ls "s3://$bucket" &>/dev/null; then
        echo "  Emptying $bucket..."
        aws s3 rm "s3://$bucket" --recursive || echo "    Failed to empty $bucket"
        echo "  Deleting $bucket..."
        aws s3 rb "s3://$bucket" || echo "    Failed to delete $bucket (may not be empty or may not exist)"
    else
        echo "  $bucket does not exist, skipping"
    fi
done

# Lambda layers (can't delete layers, but we can note them)
echo "Lambda layers found (cannot be deleted, but are no longer used):"
aws lambda list-layers --query "Layers[?contains(LayerName, 'receipt-label')].[LayerName, LatestMatchingVersion.LayerVersionArn]" --output table

echo ""
echo "✅ Cleanup complete!"
echo "Note: Lambda layers cannot be deleted, but they are no longer used and can be ignored."

