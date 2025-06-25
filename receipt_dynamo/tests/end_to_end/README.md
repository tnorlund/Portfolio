# End-to-End Tests

⚠️ **IMPORTANT**: These are NOT unit tests! These tests require real AWS services and internet connectivity.

## Overview

This directory contains end-to-end (e2e) tests that validate the integration between `receipt_dynamo` and actual AWS services. Unlike unit tests, these tests:

- **Connect to real AWS services** (DynamoDB, S3)
- **Require internet connectivity**
- **Use actual AWS credentials**
- **May incur AWS costs** (though minimal)
- **Depend on existing data** in the production environment

## Prerequisites

### 1. AWS Credentials
You must have valid AWS credentials configured. The tests use boto3's standard credential resolution:
- AWS profile (`~/.aws/credentials`)
- Environment variables (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`)
- IAM role (if running on EC2/Lambda)

### 2. Pulumi Configuration
The tests use Pulumi to retrieve AWS resource names. You need:
- Pulumi CLI installed
- Access to the `tnorlund/portfolio/{env}` stack
- Run `pulumi login` if not already authenticated

### 3. Environment Setup
The tests load configuration from Pulumi. To set up manually:

```bash
# Get the DynamoDB table name from Pulumi
pulumi stack select tnorlund/portfolio/dev
export DYNAMODB_TABLE_NAME=$(pulumi stack output dynamodb_table_name)

# Or use the prod stack (which the tests default to)
pulumi stack select tnorlund/portfolio/prod
```

### 4. Platform Requirements
- **test__ocr.py**: Requires macOS (uses Apple Vision framework)
- **test_dynamo_client.py**: Works on any platform with AWS access

## Test Files

### test_dynamo_client.py
Tests the DynamoDB client functionality:
- Listing receipts, images, lines, words, and letters
- Pagination functionality
- Data retrieval from production DynamoDB table

### test__ocr.py
Tests OCR functionality (macOS only):
- Downloads a receipt image from S3
- Performs OCR using Apple Vision
- Validates OCR results

## Running the Tests

```bash
# Run all end-to-end tests
pytest -m end_to_end

# Run with verbose output to see what's happening
pytest -v -m end_to_end

# Run a specific test file
pytest tests/end_to_end/test_dynamo_client.py

# Skip end-to-end tests when running all tests
pytest -m "not end_to_end"
```

## Important Notes for Automation

### For Claude/AI Assistants
- **DO NOT** run these tests automatically during code review or CI
- **DO NOT** run these tests without explicit user permission
- These tests require manual setup and AWS credentials
- They connect to production AWS resources

### For CI/CD
These tests should be:
- Run in a separate pipeline with proper AWS credentials
- Excluded from regular unit test runs
- Run only when changes affect AWS integration code

## Troubleshooting

### "Unable to locate credentials"
- Ensure AWS credentials are configured
- Check `aws configure list` to verify setup

### "Pulumi stack not found"
- Run `pulumi login`
- Ensure you have access to the `tnorlund/portfolio` project

### "No module named 'pyobjc'" (macOS)
- Install with: `pip install pyobjc-framework-Vision`
- Only needed for test__ocr.py

### Tests fail with "No receipts found"
- The production DynamoDB table must contain data
- Verify table name with: `pulumi stack output dynamodb_table_name`

## AWS Permissions Required

The AWS credentials must have permissions for:
- `dynamodb:Query` on the table and all GSIs
- `dynamodb:Scan` on the table
- `s3:GetObject` on the S3 bucket containing receipt images

## Cost Considerations

These tests make real AWS API calls which may incur minimal costs:
- DynamoDB read operations
- S3 data transfer (downloading images)
- Costs are typically < $0.01 per test run