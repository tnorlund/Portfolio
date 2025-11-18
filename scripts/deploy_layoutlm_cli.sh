#!/bin/bash
# Script to build, upload wheels, and prepare EC2 for LayoutLM training

set -e

echo "🚀 LayoutLM CLI Deployment Script"
echo "=================================="
echo ""

# Step 1: Get bucket name from Pulumi
echo "📦 Step 1: Getting S3 bucket name from Pulumi..."
cd infra
BUCKET=$(pulumi stack output layoutlm_training_bucket --stack dev 2>/dev/null || echo "")
cd ..

if [ -z "$BUCKET" ]; then
    echo "❌ Error: Could not get bucket name from Pulumi. Make sure you're in the infra directory and the stack is deployed."
    exit 1
fi

echo "✅ Found bucket: $BUCKET"
echo ""

# Step 2: Build wheels
echo "🔨 Step 2: Building Python wheels..."
echo ""

echo "Building receipt_dynamo..."
cd receipt_dynamo
python3 -m build --wheel
cd ..

echo "Building receipt_layoutlm..."
cd receipt_layoutlm
python3 -m build --wheel
cd ..

echo "✅ Wheels built successfully"
echo ""

# Step 3: Upload wheels to S3
echo "📤 Step 3: Uploading wheels to S3..."
echo ""

DYNAMO_WHEEL=$(ls -t receipt_dynamo/dist/*.whl 2>/dev/null | head -1)
LAYOUTLM_WHEEL=$(ls -t receipt_layoutlm/dist/*.whl 2>/dev/null | head -1)

if [ -z "$DYNAMO_WHEEL" ] || [ -z "$LAYOUTLM_WHEEL" ]; then
    echo "❌ Error: Could not find wheel files"
    exit 1
fi

echo "Uploading $DYNAMO_WHEEL..."
aws s3 cp "$DYNAMO_WHEEL" "s3://$BUCKET/wheels/" --quiet

echo "Uploading $LAYOUTLM_WHEEL..."
aws s3 cp "$LAYOUTLM_WHEEL" "s3://$BUCKET/wheels/" --quiet

echo "✅ Wheels uploaded to s3://$BUCKET/wheels/"
echo ""

# Step 4: Get EC2 instance info
echo "🖥️  Step 4: Getting EC2 instance information..."
echo ""

cd infra
ASG_NAME=$(pulumi stack output layoutlm_training_asg_name --stack dev 2>/dev/null || echo "")
cd ..

if [ -z "$ASG_NAME" ]; then
    echo "⚠️  Warning: Could not get ASG name. You may need to manually find the instance."
    echo ""
    echo "To find the instance manually:"
    echo "  1. Go to AWS Console > EC2 > Auto Scaling Groups"
    echo "  2. Find the group with 'layoutlm' in the name"
    echo "  3. Check the 'Instances' tab for the instance ID"
    echo ""
else
    echo "✅ Found ASG: $ASG_NAME"
    echo ""

    # Get instance ID from ASG
    INSTANCE_ID=$(aws autoscaling describe-auto-scaling-groups \
        --auto-scaling-group-names "$ASG_NAME" \
        --region us-east-1 \
        --query 'AutoScalingGroups[0].Instances[0].InstanceId' \
        --output text 2>/dev/null || echo "")

    if [ -n "$INSTANCE_ID" ] && [ "$INSTANCE_ID" != "None" ]; then
        echo "✅ Found running instance: $INSTANCE_ID"
        echo ""

        # Get instance IP
        INSTANCE_IP=$(aws ec2 describe-instances \
            --instance-ids "$INSTANCE_ID" \
            --region us-east-1 \
            --query 'Reservations[0].Instances[0].PublicIpAddress' \
            --output text 2>/dev/null || echo "")

        if [ -n "$INSTANCE_IP" ] && [ "$INSTANCE_IP" != "None" ]; then
            echo "✅ Instance IP: $INSTANCE_IP"
            echo ""
            echo "📋 Next steps (run these on EC2 via SSH):"
            echo "=========================================="
            echo ""
            echo "1. SSH into the instance:"
            echo "   ssh -i ~/Nov2025MacBookPro.pem ubuntu@$INSTANCE_IP"
            echo ""
            echo "2. Clean up old training runs (free space):"
            echo "   sudo rm -rf /tmp/receipt_layoutlm/*"
            echo "   df -h /"
            echo ""
            echo "3. Download and install new wheels:"
            echo "   mkdir -p ~/wheels"
            echo "   aws s3 cp s3://$BUCKET/wheels/ ~/wheels/ --recursive"
            echo "   pip install --user --force-reinstall --no-deps ~/wheels/receipt_dynamo-*.whl"
            echo "   pip install --user --force-reinstall --no-deps ~/wheels/receipt_layoutlm-*.whl"
            echo "   export PATH=\"\$HOME/.local/bin:\$PATH\""
            echo ""
            echo "4. Verify installation:"
            echo "   python3 -c 'import receipt_dynamo; import receipt_layoutlm; print(\"✅ OK\")'"
            echo "   layoutlm-cli train --help | head -20"
            echo ""
            echo "5. Set environment variables:"
            echo "   export DYNAMO_TABLE_NAME=\$(aws dynamodb list-tables --query 'TableNames[?contains(@, \\\"receipt\\\")]' --output text | head -1)"
            echo "   echo \"DYNAMO_TABLE_NAME=\$DYNAMO_TABLE_NAME\""
            echo ""
            echo "6. Start 4-label training (see LAYOUTLM_4_LABEL_TRAINING_COMMAND.md):"
            echo "   JOB=receipts-\$(date +%F-%H%M)-4label-sroie"
            echo "   layoutlm-cli train \\"
            echo "     --job-name \"\$JOB\" \\"
            echo "     --dynamo-table \"\$DYNAMO_TABLE_NAME\" \\"
            echo "     --epochs 20 \\"
            echo "     --batch-size 64 \\"
            echo "     --lr 6e-5 \\"
            echo "     --warmup-ratio 0.2 \\"
            echo "     --label-smoothing 0.1 \\"
            echo "     --o-entity-ratio 2.0 \\"
            echo "     --merge-amounts \\"
            echo "     --merge-date-time \\"
            echo "     --merge-address-phone \\"
            echo "     --early-stopping-patience 5 \\"
            echo "     --allowed-label MERCHANT_NAME \\"
            echo "     --allowed-label DATE \\"
            echo "     --allowed-label ADDRESS \\"
            echo "     --allowed-label AMOUNT"
            echo ""
        else
            echo "⚠️  Instance is not running or doesn't have a public IP yet."
            echo "   You may need to wait for it to start, or check the AWS Console."
            echo ""
        fi
    else
        echo "⚠️  No running instances found in ASG."
        echo "   To start an instance, run:"
        echo "   aws autoscaling set-desired-capacity \\"
        echo "     --auto-scaling-group-name $ASG_NAME \\"
        echo "     --desired-capacity 1 \\"
        echo "     --region us-east-1"
        echo ""
    fi
fi

echo "✅ Deployment script completed!"
echo ""

