# Scripts Directory

This directory contains essential utility scripts for the receipt label project.

## Available Scripts

### Data Management
- `export_receipt_with_labels.py` - Export receipt data from DynamoDB for local testing and validation

## Demo Scripts

For comprehensive testing and demonstration, see the `../demos/` directory which contains:

- **Decision Engine Testing**: Complete Smart Decision Engine validation
- **Pattern Detection Testing**: Advanced pattern detection with optimization levels  
- **Integration Testing**: End-to-end system integration tests
- **Results Analysis**: Performance analysis and cost reduction validation

## Usage Examples

### Data Export
```bash
# Export production data for local testing
python scripts/export_receipt_with_labels.py --limit 200

# Export specific merchant data  
python scripts/export_receipt_with_labels.py --merchant "Walmart" --limit 50

# Export to specific directory
python scripts/export_receipt_with_labels.py --output-dir ./custom_data --limit 100
```

### Demo Scripts
```bash
# See comprehensive testing capabilities
cd demos/
python test_decision_engine_local.py --config aggressive --max-receipts 50
python test_pattern_detection_local.py --optimization-level advanced
```

## Configuration

The export script supports:
- `--limit N` - Number of receipts to export
- `--merchant NAME` - Filter by specific merchant
- `--output-dir DIR` - Output directory (default: ./receipt_data_with_labels)
- `--verbose` - Enable detailed logging

## Environment Variables

- `DYNAMODB_TABLE_NAME` - DynamoDB table for data export (required)
- `AWS_PROFILE` - AWS profile for DynamoDB access
- `AWS_REGION` - AWS region (default: us-east-1)

## Integration with Testing

The export script integrates with the testing infrastructure:
- Uses `DynamoClient` from receipt_dynamo package
- Exports data in format compatible with `LocalDataLoader`
- Generates metadata files for merchant and validation information
- Supports both production and development DynamoDB tables

For comprehensive testing examples and advanced usage, see the `demos/` directory.