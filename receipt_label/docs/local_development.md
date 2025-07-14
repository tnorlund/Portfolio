# Local Development Guide

This guide explains how to set up and use the local development environment for the receipt labeling system, allowing you to develop and test without making external API calls or incurring costs.

## Overview

The local development setup provides:
- 🗄️ Local data loading from exported DynamoDB snapshots
- 🔌 API stubbing for OpenAI, Pinecone, and other services
- 💰 Cost-free testing and development
- 🚀 Fast iteration cycles
- 🧪 Reproducible test environments

## Quick Start

### 1. Export Sample Data

First, export a sample dataset from DynamoDB:

```bash
# Set your DynamoDB table name
export DYNAMODB_TABLE_NAME="your-table-name"

# Export sample data (20 receipts by default)
make export-sample-data
```

This creates a `./receipt_data` directory with structured JSON files for each receipt.

### 2. Run Local Tests

Test the pipeline with stubbed APIs:

```bash
# Run integration tests with local data
make test-local

# Validate the entire pipeline
make validate-pipeline
```

### 3. Use in Development

```python
from receipt_label.data.local_data_loader import LocalDataLoader

# Load local data
loader = LocalDataLoader("./receipt_data")
receipts = loader.list_available_receipts()

# Load a specific receipt
image_id, receipt_id = receipts[0]
receipt, words, lines = loader.load_receipt_by_id(image_id, receipt_id)
```

## Configuration

### Environment Variables

Control the local development environment with these variables:

```bash
# Enable all API stubs
export USE_STUB_APIS=true

# Or stub individual services
export STUB_OPENAI=true
export STUB_PINECONE=true
export STUB_PLACES_API=true
export STUB_DYNAMODB=true

# Local data directory
export RECEIPT_LOCAL_DATA_DIR=./receipt_data

# Enable local caching
export ENABLE_LOCAL_CACHE=true
export CACHE_TTL_SECONDS=3600

# Cost tracking
export TRACK_API_COSTS=true
export COST_LOG_FILE=./logs/api_costs.log

# Verbose logging for debugging
export VERBOSE_LOGGING=true
export LOG_API_REQUESTS=true
```

### Python Configuration

```python
from receipt_label.config.development import get_development_config

config = get_development_config()

# Check configuration
print(f"Using stub APIs: {config.use_stub_apis}")
print(f"Local data dir: {config.local_data_dir}")
print(f"Cache enabled: {config.enable_local_cache}")
```

## Working with Local Data

### Loading Receipt Data

```python
from receipt_label.data.local_data_loader import LocalDataLoader

loader = LocalDataLoader("./receipt_data")

# List all available receipts
receipts = loader.list_available_receipts()
print(f"Found {len(receipts)} receipts")

# Load receipt with all entities
image_id, receipt_id = receipts[0]
receipt, words, lines, labels = loader.load_receipt_with_labels(image_id, receipt_id)

# Load metadata if available
metadata = loader.load_receipt_metadata(image_id, receipt_id)
if metadata:
    print(f"Merchant: {metadata.merchant_name}")
```

### Using API Stubs in Tests

```python
import pytest
from receipt_label.labeler import ReceiptLabeler

def test_labeling_with_stubs(stub_all_apis):
    """Test with all APIs stubbed."""
    labeler = ReceiptLabeler()
    
    # This will use stubbed responses instead of real API calls
    result = labeler.label_receipt(request)
    
    # Verify stubbed response
    assert result.metadata.get("stub_response") is True
```

## Exporting Different Data Types

### Export Specific Receipts

```bash
# Single receipt
python scripts/export_receipt_data.py single \
    --image-id 550e8400-e29b-41d4-a716-446655440000 \
    --receipt-id 1

# All receipts from an image
python scripts/export_receipt_data.py image \
    --image-id 550e8400-e29b-41d4-a716-446655440000
```

### Export by Merchant

```bash
# Export receipts for a specific merchant
python scripts/export_receipt_data.py merchant \
    --name "Walmart" \
    --limit 10
```

### Export Labeled Receipts

```bash
# Export receipts that have labels (for validation)
python scripts/export_receipt_data.py labeled \
    --limit 20 \
    --min-labels 5
```

## Testing Strategies

### 1. Unit Tests with Mocked Data

```python
from receipt_label.data.local_data_loader import create_mock_receipt_from_export

def test_process_receipt():
    # Create mock data
    export_data = {
        "receipt": {"image_id": "123", "receipt_id": 1},
        "words": [{"word_id": 1, "text": "WALMART", "x": 10, "y": 10}],
        "lines": [{"line_id": 1, "receipt_id": 1}]
    }
    
    receipt, words, lines = create_mock_receipt_from_export(export_data)
    # Test your processing logic
```

### 2. Integration Tests with Local Data

```python
@pytest.mark.integration
def test_full_pipeline(local_data_dir, enable_cost_free_testing):
    """Test the complete pipeline without external calls."""
    loader = LocalDataLoader(local_data_dir)
    
    # Process multiple receipts
    for image_id, receipt_id in loader.list_available_receipts()[:5]:
        receipt, words, lines = loader.load_receipt_by_id(image_id, receipt_id)
        # Run your pipeline
```

### 3. Performance Testing

```bash
# Test pattern detection performance
python scripts/test_decision_engine.py ./receipt_data \
    --report detailed \
    --output performance_report.txt

# Check processing times
grep "Average Pattern Detection Time" performance_report.txt
```

## Cost Tracking

When `TRACK_API_COSTS=true`, the system logs estimated costs:

```python
# View cost logs
cat ./logs/api_costs.log

# Example output:
# 2024-01-15 10:30:00 - OpenAI GPT-4 - 1500 tokens - $0.045
# 2024-01-15 10:31:00 - Pinecone Query - 100 vectors - $0.001
```

## Debugging Tips

### 1. Enable Verbose Logging

```bash
export VERBOSE_LOGGING=true
export LOG_API_REQUESTS=true
```

### 2. Check Stub Responses

Stubbed responses include metadata to identify them:

```python
result = labeler.label_receipt(request)
if result.metadata.get("stub_response"):
    print("This was a stubbed response")
```

### 3. Validate Data Loading

```bash
# Check exported data structure
ls -la ./receipt_data/
find ./receipt_data -name "*.json" | head -10
```

## Common Issues

### No Data Found

```bash
# Error: No local data found
# Solution: Export data first
make export-sample-data
```

### AWS Credentials Missing

```bash
# Error: Unable to locate credentials
# Solution: Configure AWS credentials
aws configure
# Or use environment variables
export AWS_ACCESS_KEY_ID=your-key
export AWS_SECRET_ACCESS_KEY=your-secret
```

### Import Errors

```bash
# Error: ModuleNotFoundError
# Solution: Install packages in development mode
pip install -e receipt_label
pip install -e receipt_dynamo
```

## Best Practices

1. **Always test locally first** - Use `make test-local` before pushing
2. **Keep test data small** - 20-50 receipts is usually sufficient
3. **Use meaningful test data** - Export receipts from different merchants
4. **Version your test data** - Commit sample data for reproducible tests
5. **Monitor stub usage** - Ensure tests aren't accidentally calling real APIs

## Advanced Usage

### Custom Export Filters

Create specialized datasets:

```python
# Export receipts with specific characteristics
exporter = ReceiptExporter(dynamo_client, "./custom_data")
exporter.export_by_merchant("Walmart", limit=10)
exporter.export_labeled_receipts(min_labels=10)
```

### Parallel Testing

```bash
# Test multiple receipts in parallel
find ./receipt_data -name "image_*" -type d | \
    parallel -j 4 "USE_STUB_APIS=true pytest -k test_receipt_{}"
```

### CI/CD Integration

```yaml
# .github/workflows/local-tests.yml
- name: Export test data
  run: make export-sample-data
  
- name: Run local tests
  run: make test-local
  
- name: Validate pipeline
  run: make validate-pipeline
```

## Next Steps

- Review [Receipt Export Documentation](../../scripts/README_receipt_export.md)
- Check [API Stubbing Guide](../tests/fixtures/api_stubs.py)
- See [Development Configuration](../receipt_label/config/development.py)