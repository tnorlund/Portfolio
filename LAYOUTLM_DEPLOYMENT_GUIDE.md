# LayoutLM CLI Deployment Guide

## Quick Start

Run the deployment script from the repo root:

```bash
./deploy_layoutlm_cli.sh
```

This will:
1. ✅ Build Python wheels for `receipt_dynamo` and `receipt_layoutlm`
2. ✅ Upload wheels to S3
3. ✅ Get EC2 instance information
4. ✅ Provide SSH commands for the next steps

## Manual Steps (if script doesn't work)

### Step 1: Build and Upload Wheels

```bash
# From repo root
cd receipt_dynamo
python3 -m build --wheel
cd ../receipt_layoutlm
python3 -m build --wheel

# Get bucket name
cd ../infra
BUCKET=$(pulumi stack output layoutlm_training_bucket --stack dev)
cd ..

# Upload wheels
aws s3 cp receipt_dynamo/dist/*.whl s3://$BUCKET/wheels/
aws s3 cp receipt_layoutlm/dist/*.whl s3://$BUCKET/wheels/
```

### Step 2: SSH into EC2 Instance

```bash
# Get instance IP (replace with your instance ID or use AWS Console)
INSTANCE_IP="<your-instance-ip>"
ssh -i ~/Nov2025MacBookPro.pem ubuntu@$INSTANCE_IP
```

### Step 3: Clean Up Old Training Runs (Free Space)

```bash
# Check current disk usage
df -h /

# Remove old training runs (keep only the most recent if needed)
sudo rm -rf /tmp/receipt_layoutlm/*

# Verify space is freed
df -h /
```

### Step 4: Download and Install New Wheels

```bash
# Get bucket name (or set it manually)
BUCKET=$(aws s3 ls | grep layoutlm | awk '{print $3}' | head -1)
echo "Using bucket: $BUCKET"

# Download new wheels
sudo /usr/bin/aws s3 cp s3://$BUCKET/wheels/ /opt/wheels/ --recursive

# Install wheels (force reinstall to get latest version)
sudo pip3 install --force-reinstall /opt/wheels/receipt_dynamo-*.whl
sudo pip3 install --force-reinstall /opt/wheels/receipt_layoutlm-*.whl
```

### Step 5: Verify Installation

```bash
# Test imports
python3 -c "import receipt_dynamo; import receipt_layoutlm; print('✅ OK')"

# Test CLI
layoutlm-cli train --help | head -20

# Check version (if available)
python3 -c "import receipt_layoutlm; print(receipt_layoutlm.__version__ if hasattr(receipt_layoutlm, '__version__') else 'installed')"
```

### Step 6: Set Environment Variables

```bash
# Get DynamoDB table name
export DYNAMO_TABLE_NAME=$(aws dynamodb list-tables --query 'TableNames[?contains(@, `receipt`)]' --output text | head -1)
echo "DYNAMO_TABLE_NAME=$DYNAMO_TABLE_NAME"

# Verify it's set
echo $DYNAMO_TABLE_NAME
```

### Step 7: Start 4-Label Training

```bash
# Set job name
JOB=receipts-$(date +%F-%H%M)-4label-sroie

# Run training
layoutlm-cli train \
  --job-name "$JOB" \
  --dynamo-table "$DYNAMO_TABLE_NAME" \
  --epochs 20 \
  --batch-size 64 \
  --lr 6e-5 \
  --warmup-ratio 0.2 \
  --label-smoothing 0.1 \
  --o-entity-ratio 2.0 \
  --merge-amounts \
  --merge-date-time \
  --merge-address-phone \
  --early-stopping-patience 5 \
  --allowed-label MERCHANT_NAME \
  --allowed-label DATE \
  --allowed-label ADDRESS \
  --allowed-label AMOUNT
```

## Troubleshooting

### Wheels not installing

```bash
# Check if wheels were downloaded
ls -la /opt/wheels/

# Check pip version
pip3 --version

# Try installing with verbose output
sudo pip3 install --force-reinstall -v /opt/wheels/receipt_layoutlm-*.whl
```

### CLI not found

```bash
# Check if it's in PATH
which layoutlm-cli

# Check if it's in ~/.local/bin
ls -la ~/.local/bin/layoutlm-cli

# Add to PATH if needed
export PATH="$HOME/.local/bin:$PATH"
```

### Out of disk space

```bash
# Check disk usage
df -h /

# Find large files
sudo du -sh /tmp/receipt_layoutlm/* | sort -h

# Remove specific old runs (be careful!)
sudo rm -rf /tmp/receipt_layoutlm/receipts-2025-11-13-XXXX-*
```

### Training fails immediately

```bash
# Check DynamoDB permissions
aws dynamodb describe-table --table-name "$DYNAMO_TABLE_NAME"

# Check S3 permissions
aws s3 ls s3://$BUCKET/

# Check GPU availability
nvidia-smi

# Check Python environment
python3 --version
pip3 list | grep -E "(torch|transformers|receipt)"
```

## Expected Results

After successful deployment:
- ✅ Wheels are in S3: `s3://$BUCKET/wheels/`
- ✅ Wheels are installed on EC2: `/opt/wheels/`
- ✅ Python packages are importable
- ✅ CLI is available: `layoutlm-cli train --help`
- ✅ Training can start with 4-label setup

## Next Steps

Once training starts:
1. Monitor progress: `tail -f /tmp/receipt_layoutlm/$JOB/trainer_state.json`
2. Check logs: `tail -f /tmp/receipt_layoutlm/$JOB/training.log` (if available)
3. Model will auto-sync to S3 when training completes (if `--output-s3-path` is set)

