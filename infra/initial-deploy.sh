#!/bin/bash
# Initial deployment script for fresh infrastructure
# This handles the chicken-and-egg problem where Lambda layers need S3 files
# that don't exist until CodeBuild projects run

set -e

echo "🚀 Starting initial deployment..."

# Phase 1: Create build infrastructure only
echo "📦 Phase 1: Creating build infrastructure (CodeBuild, CodePipeline)..."
pulumi up --yes \
  --target '**/*codebuild*' \
  --target '**/*codepipeline*' \
  --target '**/*pipeline*' \
  --target '**/*build*' \
  --target '**/*upload-source*' || true

echo "⏳ Waiting for pipelines to start..."
sleep 30

# Check pipeline status
echo "🔍 Checking pipeline status..."
aws codepipeline list-pipelines --query 'pipelines[].name' --output table

# Phase 2: Full deployment
echo "🚀 Phase 2: Full deployment including Lambda layers..."
pulumi up --yes

echo "✅ Initial deployment complete!"