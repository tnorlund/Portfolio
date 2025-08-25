# Lambda Layer Version Update Tool

This tool allows you to manually update Lambda Layer version numbers in your Pulumi state files and synchronize them with AWS Lambda functions.

## Overview

When working with Lambda layers in Pulumi, you may need to:
- Update all Lambda functions to use a specific layer version
- Synchronize Pulumi state with actual AWS resources
- Roll back to previous layer versions
- Batch update multiple functions efficiently

This tool provides a safe, automated way to perform these operations.

## How It Works

### Pulumi State Management
Pulumi stores infrastructure state in JSON format. For Lambda functions using layers, the state contains:
```json
{
  "type": "aws:lambda/function:Function",
  "outputs": {
    "layers": [
      "arn:aws:lambda:us-east-1:123456789012:layer:receipt-dynamo-dev:1",
      "arn:aws:lambda:us-east-1:123456789012:layer:receipt-label-dev:2"
    ]
  }
}
```

The tool:
1. **Exports** current state to JSON
2. **Parses** layer ARNs and identifies version numbers
3. **Updates** version numbers based on your configuration
4. **Imports** the modified state back to Pulumi
5. **Syncs** with AWS Lambda functions (optional)

## Prerequisites

1. **Pulumi CLI** installed and authenticated
2. **AWS CLI** configured with appropriate permissions
3. **Python 3.8+** with required packages:
   ```bash
   pip install boto3 pyyaml
   ```
4. **Permissions** needed:
   - Pulumi: Stack read/write access
   - AWS: Lambda read/write, layer read access

## Quick Start

1. **Configure layer versions** in `layer_config.yaml`:
   ```yaml
   layers:
     receipt-dynamo:
       version: 5  # Update to version 5
     receipt-label:
       version: 3  # Update to version 3
   ```

2. **Run in dry-run mode** to see what would change:
   ```bash
   python update_layer_versions.py --config layer_config.yaml --stack dev --dry-run
   ```

3. **Apply changes** to Pulumi state:
   ```bash
   python update_layer_versions.py --config layer_config.yaml --stack dev
   ```

4. **Sync with AWS** Lambda functions:
   ```bash
   python update_layer_versions.py --config layer_config.yaml --stack dev --sync-aws
   ```

## Configuration File Format

### Basic Structure
```yaml
layers:
  layer-name:
    version: target_version_number
    description: "Optional description"

settings:
  aws_region: "us-east-1"
  validate_layer_exists: true
  backup_retention_days: 30
```

### Stack-Specific Overrides
```yaml
stack_overrides:
  dev:
    receipt-dynamo:
      version: 6  # Dev uses newer version
  prod:
    receipt-dynamo:
      version: 5  # Prod uses stable version
```

## Command-Line Options

```bash
python update_layer_versions.py [options]

Required:
  --config FILE     Path to layer configuration YAML file

Optional:
  --stack NAME      Pulumi stack name (default: dev)
  --dry-run         Show changes without applying them
  --sync-aws        Also update AWS Lambda functions
  --verbose         Enable detailed logging
  --help            Show help message
```

## Safety Features

### Automatic Backups
- Original state files are backed up before modification
- Backups are timestamped and stored in `backups/` directory
- Backup retention is configurable

### Validation
- Layer ARN format validation
- Version number validation
- AWS layer existence checking (optional)
- Dry-run mode for testing

### Error Handling
- Graceful failure with detailed error messages
- Rollback capability using backup files
- AWS permission validation

## Usage Examples

### 1. Update Development Stack
```bash
# First, see what would change
python update_layer_versions.py \
  --config layer_config.yaml \
  --stack dev \
  --dry-run

# Apply the changes
python update_layer_versions.py \
  --config layer_config.yaml \
  --stack dev
```

### 2. Update Production with AWS Sync
```bash
# Update prod stack and sync with AWS
python update_layer_versions.py \
  --config layer_config.yaml \
  --stack prod \
  --sync-aws \
  --verbose
```

## Troubleshooting

### Common Issues

1. **"State file not found"**
   - Ensure you're in the correct directory (should contain Pulumi.yaml)
   - Verify stack name is correct

2. **"Invalid layer ARN format"**
   - Check layer names in configuration file
   - Verify AWS account ID and region are correct

3. **"AWS credentials not configured"**
   - Run `aws configure` or set environment variables
   - Ensure IAM permissions for Lambda access

### Recovery Procedures

#### Rollback Pulumi State
If something goes wrong, you can restore from backup:
```bash
# List available backups
ls -la backups/

# Restore from backup
pulumi stack import --stack dev --file backups/dev_state_20240824_143022.json
```

## Best Practices

1. **Always test with dry-run first**
2. **Update one stack at a time**
3. **Keep layer versions in version control**
4. **Document version changes in git commits**
5. **Verify functions work after updates**
6. **Keep backup files for at least 30 days**