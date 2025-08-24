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
    compatible_runtimes:
      - "python3.12"

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

### 3. Test Configuration
```bash
# Check what would happen without making changes
python update_layer_versions.py \
  --config layer_config.yaml \
  --stack dev \
  --dry-run \
  --sync-aws \
  --verbose
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

## Understanding the Output

### Dry-Run Output
```
INFO - Found 3 layer version updates:
  - Function my-function-1: Layer receipt-dynamo-dev version 1 -> 5
  - Function my-function-2: Layer receipt-dynamo-dev version 2 -> 5
  - Function my-function-3: Layer receipt-label-dev version 1 -> 3
```

### Successful Update
```
INFO - Exporting stack state for dev
INFO - State exported to: backups/dev_state_20240824_143022.json
INFO - Function my-function-1: Layer receipt-dynamo-dev version 1 -> 5
INFO - State saved to: backups/dev_updated_20240824_143023.json
INFO - Importing state for stack dev
INFO - Pulumi state updated successfully
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

4. **"Layer version does not exist"**
   - Verify the target version exists in AWS
   - Check if layer was deleted or not yet published

### Recovery Procedures

#### Rollback Pulumi State
If something goes wrong, you can restore from backup:
```bash
# List available backups
ls -la backups/

# Restore from backup
pulumi stack import --stack dev --file backups/dev_state_20240824_143022.json
```

#### Fix Layer Versions Manually
If you need to fix specific functions:
```bash
# Update single function via AWS CLI
aws lambda update-function-configuration \
  --function-name my-function \
  --layers "arn:aws:lambda:us-east-1:123456789012:layer:receipt-dynamo-dev:5"
```

## Advanced Usage

### Custom Layer ARN Patterns
If your layers don't follow the standard naming convention:
```yaml
layers:
  custom-layer-name:
    version: 1
    # Tool will match against actual AWS layer names
```

### Batch Operations
Process multiple stacks:
```bash
for stack in dev staging prod; do
  python update_layer_versions.py \
    --config layer_config.yaml \
    --stack $stack \
    --dry-run
done
```

### Integration with CI/CD
```yaml
# GitHub Actions example
- name: Update layer versions
  run: |
    python infra/update_layer_versions.py \
      --config infra/layer_config.yaml \
      --stack prod \
      --sync-aws
```

## Best Practices

1. **Always test with dry-run first**
2. **Update one stack at a time**
3. **Keep layer versions in version control**
4. **Document version changes in git commits**
5. **Verify functions work after updates**
6. **Keep backup files for at least 30 days**

## Files Created by This Tool

- `backups/` - Directory for state backups
- `backups/{stack}_state_{timestamp}.json` - Original state backup
- `backups/{stack}_updated_{timestamp}.json` - Updated state file
- Logs are written to stdout/stderr

## API Reference

### LayerVersionUpdater Class

#### Methods
- `export_stack_state()` - Export current Pulumi state
- `update_layer_versions_in_state()` - Update layer versions in state
- `import_stack_state()` - Import modified state back to Pulumi  
- `sync_with_aws()` - Synchronize with AWS Lambda functions
- `run()` - Main execution method

#### Configuration
- `config_path` - Path to YAML configuration file
- `stack_name` - Target Pulumi stack
- `layer_mappings` - Layer version mappings from config

## Contributing

To extend this tool:
1. Add new layer matching patterns in `_build_layer_arn()`
2. Extend configuration format in `_load_config()`
3. Add new AWS resource types in `find_lambda_resources()`
4. Improve error handling and logging

## Support

If you encounter issues:
1. Check the troubleshooting section above
2. Run with `--verbose` for detailed output
3. Verify your configuration file syntax
4. Test with `--dry-run` first