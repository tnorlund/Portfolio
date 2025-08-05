# Deployment Guide

## Prerequisites

- AWS CLI configured with appropriate credentials
- Pulumi CLI installed and authenticated
- Node.js 18+ and Python 3.12+ installed
- Access to production AWS account

## Deployment Environments

### Development
- **Purpose**: Testing and development
- **URL**: https://dev.tylernorlund.com
- **AWS Account**: Development
- **Pulumi Stack**: `dev`

### Production
- **Purpose**: Live production environment
- **URL**: https://tylernorlund.com
- **AWS Account**: Production
- **Pulumi Stack**: `prod`

## Deployment Process

### 1. Infrastructure Deployment

```bash
cd infra

# Select the appropriate stack
pulumi stack select prod

# Preview changes
pulumi preview

# Apply changes
pulumi up

# Verify outputs
pulumi stack output
```

### 2. Frontend Deployment

```bash
cd portfolio

# Install dependencies
npm install

# Build production bundle
npm run build

# Deploy to S3
aws s3 sync ./out s3://portfolio-website-prod --delete

# Invalidate CloudFront cache
aws cloudfront create-invalidation \
  --distribution-id EXXXXXXXXX \
  --paths "/*"
```

### 3. Backend Deployment

```bash
# Package Lambda functions
cd infra
pulumi up -y

# Functions are automatically deployed via Pulumi
# Check Lambda console for verification
```

## Automated Deployment

### GitHub Actions Deployment

Deployments can be triggered via GitHub Actions:

```bash
# Tag a release
git tag -a v1.0.0 -m "Release version 1.0.0"
git push origin v1.0.0

# This triggers the deployment workflow
```

### Manual Workflow Trigger

1. Go to GitHub Actions tab
2. Select "Deploy to Production" workflow
3. Click "Run workflow"
4. Select branch and environment
5. Confirm deployment

## Post-Deployment Verification

### Health Checks

```bash
# Check API health
curl https://api.tylernorlund.com/health

# Verify frontend
curl -I https://tylernorlund.com

# Test receipt upload endpoint
curl -X POST https://api.tylernorlund.com/receipts/upload \
  -H "Content-Type: application/json" \
  -d '{"test": true}'
```

### Monitoring

1. **CloudWatch Dashboards**: Check metrics and logs
2. **Lambda Metrics**: Verify function invocations
3. **DynamoDB Metrics**: Monitor read/write capacity
4. **Error Rates**: Check for elevated error rates

## Rollback Procedures

### Infrastructure Rollback

```bash
# View deployment history
pulumi history

# Rollback to previous version
pulumi up --target-dependents --target-replace urn:pulumi:prod::portfolio::*

# Or restore specific version
pulumi stack export > backup.json
pulumi stack import --file previous-state.json
```

### Frontend Rollback

```bash
# Restore previous S3 version
aws s3 sync s3://portfolio-backup/v1.0.0 s3://portfolio-website-prod --delete

# Clear CloudFront cache
aws cloudfront create-invalidation --distribution-id EXXXXXXXXX --paths "/*"
```

### Lambda Rollback

```bash
# Using AWS CLI
aws lambda update-function-code \
  --function-name receipt-processor \
  --s3-bucket lambda-deployments \
  --s3-key previous-version.zip

# Or using Pulumi
pulumi up --target urn:pulumi:prod::portfolio::aws:lambda/function:Function::receipt-processor
```

## Environment Variables

### Required Secrets

Set these in GitHub Secrets or local environment:

```bash
export AWS_ACCESS_KEY_ID="..."
export AWS_SECRET_ACCESS_KEY="..."
export PULUMI_ACCESS_TOKEN="..."
export OPENAI_API_KEY="..."
export GOOGLE_PLACES_API_KEY="..."
```

### Configuration

```bash
# Set Pulumi config
pulumi config set aws:region us-east-1
pulumi config set portfolio:environment production
pulumi config set --secret portfolio:apiKey "..."
```

## Troubleshooting

### Common Issues

**CloudFront not updating**
- Wait 5-10 minutes for propagation
- Create cache invalidation
- Check origin settings

**Lambda function errors**
- Check CloudWatch logs
- Verify IAM permissions
- Check environment variables

**DynamoDB throttling**
- Switch to on-demand billing
- Increase provisioned capacity
- Implement exponential backoff

**S3 sync failures**
- Verify bucket permissions
- Check AWS credentials
- Ensure bucket exists

## Security Considerations

1. **Never commit secrets** to repository
2. **Use IAM roles** for service permissions
3. **Enable MFA** for production deployments
4. **Audit logs** regularly
5. **Rotate credentials** quarterly

## Cost Monitoring

After deployment:
1. Check AWS Cost Explorer
2. Set up billing alerts
3. Review resource utilization
4. Optimize unused resources