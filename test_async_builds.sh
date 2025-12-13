#!/bin/bash
# Quick verification script for async build system

set -e

echo "================================================================================"
echo "Async Build System Status"
echo "================================================================================"

echo -e "\n📦 BASE IMAGE URIs (with content-based tags):"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
pulumi stack output dynamo_base_image_uri 2>/dev/null || echo "  dynamo_base_image_uri: Not yet deployed"
pulumi stack output label_base_image_uri 2>/dev/null || echo "  label_base_image_uri: Not yet deployed"
pulumi stack output agent_base_image_uri 2>/dev/null || echo "  agent_base_image_uri: Not yet deployed"

echo -e "\n🏗️  RECENT BASE IMAGE BUILDS (last 3):"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
for base in "base-receipt-dynamo-dev" "base-receipt-label-dev" "base-receipt-agent-dev"; do
    echo -e "\n  $base:"
    aws codebuild list-builds-for-project \
      --project-name "${base}-img-builder" \
      --sort-order DESCENDING \
      --max-items 3 \
      --query 'ids' \
      --output table 2>/dev/null || echo "    No builds yet or project doesn't exist"
done

echo -e "\n🔧 RECENT LAMBDA BUILDS (last 3):"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
for lambda in "metadata-harmonizer-dev-harmonize" "create-labels-dev-create"; do
    echo -e "\n  $lambda:"
    aws codebuild list-builds-for-project \
      --project-name "${lambda}-img-builder" \
      --sort-order DESCENDING \
      --max-items 3 \
      --query 'ids' \
      --output table 2>/dev/null || echo "    No builds yet or project doesn't exist"
done

echo -e "\n📸 CURRENT LAMBDA IMAGE:"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
aws lambda get-function \
  --function-name metadata-harmonizer-dev-harmonize-metadata \
  --query 'Code.ImageUri' \
  --output text 2>/dev/null || echo "  Lambda not found or not yet deployed"

echo -e "\n================================================================================"
echo "Tip: Run './test_async_builds.sh' after each pulumi up to track changes"
echo "================================================================================"

