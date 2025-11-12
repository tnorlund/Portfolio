# Getting Back to LayoutLM Training

## Current Status

✅ Branch `feat/layoutlm` is checked out and merged with `main`
✅ Pulumi config has `ml-training:enable-minimal: "true"` set
✅ Infrastructure code is ready in `infra/layoutlm_training/component.py`

## Step 1: Deploy Pulumi Infrastructure

The infrastructure is conditionally enabled via config. It's already set in `Pulumi.dev.yaml`, so you just need to deploy:

```bash
cd infra
pulumi up --stack dev
```

This will create:
- S3 bucket for models/artifacts/wheels
- SQS queue for training jobs
- Auto Scaling Group (ASG) for EC2 instances (starts at 0 capacity)
- IAM roles and security groups
- Launch template with Deep Learning AMI GPU PyTorch

**Note**: The ASG starts with `desired_capacity=0`, so no instances will launch automatically.

## Step 2: Build and Upload Wheels

The EC2 instances need the `receipt_dynamo` and `receipt_layoutlm` wheels in S3. Build and upload them:

```bash
# From repo root
cd receipt_dynamo
python -m build --wheel
cd ../receipt_layoutlm
python -m build --wheel

# Get the bucket name from Pulumi outputs
cd ../infra
BUCKET=$(pulumi stack output layoutlm_training_bucket --stack dev)

# Upload wheels
aws s3 cp receipt_dynamo/dist/*.whl s3://$BUCKET/wheels/
aws s3 cp receipt_layoutlm/dist/*.whl s3://$BUCKET/wheels/
```

## Step 3: Launch Training Instance

You can manually scale the ASG or use AWS Console/CLI:

```bash
# Get ASG name
ASG_NAME=$(pulumi stack output layoutlm_training_asg_name --stack dev)

# Scale to 1 instance
aws autoscaling set-desired-capacity \
  --auto-scaling-group-name $ASG_NAME \
  --desired-capacity 1 \
  --region us-east-1
```

Or use the AWS Console to find the instance and SSH in.

**SSH Access**: The launch template uses key name `training_key`. Make sure you have this key pair in AWS or update the key name in `infra/layoutlm_training/component.py` (line 245).

## Step 4: Verify Instance Setup

Once the instance is running, SSH in and verify:

```bash
# Check if wheels were installed
ls -la /opt/wheels/

# Check if packages are installed
python3 -c "import receipt_dynamo; import receipt_layoutlm; print('OK')"

# Check CLI
layoutlm-cli train --help
```

## Step 5: Run Training

From the EC2 instance:

```bash
# Set environment variables
export DYNAMO_TABLE_NAME=<your-table-name>
export AWS_DEFAULT_REGION=us-east-1

# Run a test training job
layoutlm-cli train \
  --job-name "receipts-$(date +%F-%H%M)-test" \
  --dynamo-table "$DYNAMO_TABLE_NAME" \
  --epochs 3 \
  --batch-size 64 \
  --lr 6e-5 \
  --warmup-ratio 0.2 \
  --label-smoothing 0.1 \
  --o-entity-ratio 2.0 \
  --merge-amounts \
  --allowed-label MERCHANT_NAME \
  --allowed-label PHONE_NUMBER \
  --allowed-label ADDRESS_LINE \
  --allowed-label DATE \
  --allowed-label TIME \
  --allowed-label PRODUCT_NAME \
  --allowed-label AMOUNT
```

## Step 6: Monitor Training

Training outputs go to:
- Local: `/tmp/receipt_layoutlm/$JOB/`
- S3: `s3://$BUCKET/runs/$JOB/` (sync manually or via script)
- DynamoDB: JobMetrics and JobLogs tables

Check logs:
```bash
# On EC2 instance
tail -f /var/log/worker.log  # If using SQS worker
# Or check training output directly
tail -f /tmp/receipt_layoutlm/$JOB/trainer_state.json
```

## Troubleshooting

### Instance won't launch
- Check ASG events in CloudWatch
- Verify security group allows SSH (port 22)
- Check IAM role permissions
- Review user-data logs: `/var/log/cloud-init-output.log`

### Wheels not found
- Verify wheels are in `s3://$BUCKET/wheels/`
- Check IAM role has S3 read permissions
- Check user-data script logs

### Training fails
- Verify DynamoDB table name is correct
- Check IAM role has DynamoDB read permissions
- Review training logs in `/tmp/receipt_layoutlm/$JOB/`

### GPU not detected
- Verify instance type is `g5.xlarge` (has A10G GPU)
- Check `nvidia-smi` output
- Verify Deep Learning AMI is being used

## Next Steps from Previous Session

Based on the training notes, the main issues were:
1. **Dataset size/quality** - insufficient labeled data
2. **Subtoken supervision** - should only supervise first subtoken (set others to -100)
3. **Early stopping** - patience=2 may be too aggressive
4. **O:entity ratio** - try 1.2-1.5 for better recall

Consider implementing the subtoken fix in `receipt_layoutlm/receipt_layoutlm/data_loader.py` before training again.

## Scale Down

When done training, scale the ASG back to 0:

```bash
aws autoscaling set-desired-capacity \
  --auto-scaling-group-name $ASG_NAME \
  --desired-capacity 0 \
  --region us-east-1
```

This stops all instances and saves costs.



