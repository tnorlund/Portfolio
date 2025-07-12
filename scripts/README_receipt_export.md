# Receipt Data Export and Testing Tools

This document describes the enhanced receipt data export and testing tools for local development and testing of the receipt label decision engine.

## Overview

The tools enable you to:
1. Export receipt data from DynamoDB for local testing
2. Download associated images from S3 (optional)
3. Test the pattern detection and decision engine locally
4. Analyze performance and accuracy metrics
5. Develop and iterate quickly without AWS costs

## Scripts

### 1. `export_receipt_data.py` - Enhanced Export Tool

Export receipt data from DynamoDB with various filtering options.

**Status**: ✅ **Working** - Successfully tested with real DynamoDB data

### 2. `test_decision_engine.py` - Decision Engine Test Harness

Test the receipt labeling decision engine with exported data and real pattern detection.

**Status**: ✅ **Working** - Pattern detection integration fixed and tested

**Key Features**:
- Real Pinecone vector database integration
- Local pattern detection (currency, datetime, contact, quantity)
- Decision engine testing with essential label requirements
- Performance metrics and cost savings analysis
- MockWord object compatibility with ReceiptWord interface

**Recent Fixes**:
- Fixed MockWord.calculate_centroid() to return tuple instead of dictionary
- Verified compatibility with pattern detector interfaces
- Tested with real Pinecone integration

**Usage**:
```bash
# Test all receipts with real Pinecone
export PINECONE_API_KEY="your-key"
export PINECONE_INDEX_NAME="receipt-validation-dev"
export PINECONE_HOST="https://..."
export DYNAMODB_TABLE_NAME="ReceiptsTable-dc5be22"
python scripts/test_decision_engine.py ./receipt_data

# Test specific receipt
python scripts/test_decision_engine.py ./receipt_data --receipt image_ae0d9a91-ee91-4b88-aa68-881799eb9ab2_receipt_00001

# Pattern detection only (no Pinecone)
python scripts/test_decision_engine.py ./receipt_data --no-pinecone
```

#### Usage Examples

**Export a single receipt:**
```bash
# Export specific receipt (requires both image_id and receipt_id)
python scripts/export_receipt_data.py single \
    --image-id 550e8400-e29b-41d4-a716-446655440000 \
    --receipt-id 1

# With image download
python scripts/export_receipt_data.py single \
    --image-id 550e8400-e29b-41d4-a716-446655440000 \
    --receipt-id 1 \
    --download-images
```

**Export all receipts from an image:**
```bash
# Export all receipts from a specific image
python scripts/export_receipt_data.py image \
    --image-id 550e8400-e29b-41d4-a716-446655440000

# Limit number of receipts
python scripts/export_receipt_data.py image \
    --image-id 550e8400-e29b-41d4-a716-446655440000 \
    --limit 5 \
    --download-images
```

**Export by merchant:**
```bash
# Export receipts for a specific merchant
python scripts/export_receipt_data.py merchant \
    --name "Walmart" \
    --limit 10

# Popular merchants to test
python scripts/export_receipt_data.py merchant --name "McDonald's" --limit 5
python scripts/export_receipt_data.py merchant --name "Target" --limit 5
python scripts/export_receipt_data.py merchant --name "CVS" --limit 5
```

**Export labeled receipts:**
```bash
# Export receipts that have labels (for validation testing)
python scripts/export_receipt_data.py labeled \
    --limit 20 \
    --min-labels 10
```

**Create sample dataset:**
```bash
# Create a diverse sample for testing
python scripts/export_receipt_data.py sample \
    --size 50 \
    --download-images

# Smaller sample for quick testing
python scripts/export_receipt_data.py sample --size 10
```

#### Output Structure

```
receipt_data/
├── image_550e8400_receipt_00001/
│   ├── receipt.json          # Receipt entity data
│   ├── words.json           # ReceiptWord entities
│   ├── lines.json           # ReceiptLine entities
│   ├── metadata.json        # ReceiptMetadata (merchant info)
│   ├── labels.json          # ReceiptWordLabel entities (if any)
│   ├── image.jpg            # Downloaded image (optional)
│   └── export_summary.json  # Export metadata
├── image_550e8400_receipt_00002/
│   └── ...
├── index.json               # Master index of all exports
└── sample_index.json        # Sample dataset metadata (if created)
```

### 2. `test_decision_engine.py` - Local Testing Tool

Test the decision engine logic with exported data.

#### Usage Examples

**Test all exported receipts:**
```bash
# Basic test run
python scripts/test_decision_engine.py ./receipt_data

# With detailed report
python scripts/test_decision_engine.py ./receipt_data \
    --report detailed \
    --output test_results.txt
```

**Test specific receipt:**
```bash
# Test single receipt
python scripts/test_decision_engine.py ./receipt_data \
    --receipt image_550e8400_receipt_00001
```

**Use custom merchant patterns:**
```bash
# Create merchant patterns file
cat > merchant_patterns.json << EOF
{
  "WALMART": {
    "patterns": [
      {"text": "ROLLBACK", "label": "DISCOUNT", "confidence": 0.95},
      {"text": "TC#", "label": "TRANSACTION_ID", "confidence": 0.9},
      {"text": "ST#", "label": "STORE_NUMBER", "confidence": 0.9}
    ]
  },
  "MCDONALDS": {
    "patterns": [
      {"text": "Big Mac", "label": "PRODUCT_NAME", "confidence": 0.95},
      {"text": "McFlurry", "label": "PRODUCT_NAME", "confidence": 0.95},
      {"text": "Happy Meal", "label": "PRODUCT_NAME", "confidence": 0.95}
    ]
  }
}
EOF

# Test with custom patterns
python scripts/test_decision_engine.py ./receipt_data \
    --patterns merchant_patterns.json
```

#### Sample Output

```
============================================================
DECISION ENGINE TEST REPORT
============================================================

Total Receipts Tested: 25
GPT Required: 4 (16.0%)
Patterns Sufficient: 21 (84.0%)

Decision Reasons:
  - Missing essential labels: MERCHANT_NAME, DATE: 3
  - Found 8 meaningful unlabeled words (threshold: 5): 1

Missing Essential Labels:
  - MERCHANT_NAME: 2 receipts
  - DATE: 1 receipts

Pattern Detection Coverage:
  - currency_detector: 145 labels
  - datetime_detector: 23 labels
  - merchant_pattern: 67 labels
  - metadata_match: 20 labels
  - quantity_detector: 12 labels

Average Pattern Detection Time: 0.042s
Total Processing Time: 1.050s

Expected Cost Savings:
  - Skip Rate: 84.0%
  - Estimated Savings: ~59% reduction in GPT costs
============================================================
```

## Complete Workflow Example

Here's a complete workflow for testing the decision engine locally:

```bash
# 1. Set up environment
export DYNAMODB_TABLE_NAME=your-table-name
export AWS_PROFILE=your-profile  # If using named profiles

# 2. Export sample data
echo "Creating sample dataset..."
python scripts/export_receipt_data.py sample \
    --size 30 \
    --output-dir ./test_data

# 3. Export specific merchants for testing
echo "Exporting Walmart receipts..."
python scripts/export_receipt_data.py merchant \
    --name "Walmart" \
    --limit 5 \
    --output-dir ./test_data

# 4. Export some labeled receipts for validation
echo "Exporting labeled receipts..."
python scripts/export_receipt_data.py labeled \
    --limit 10 \
    --min-labels 5 \
    --output-dir ./test_data

# 5. Run decision engine tests
echo "Testing decision engine..."
python scripts/test_decision_engine.py ./test_data \
    --report detailed \
    --output results_$(date +%Y%m%d_%H%M%S).txt

# 6. Analyze results
cat results_*.txt | grep "Skip Rate"
```

## Performance Testing

To test performance at scale:

```bash
# Export larger dataset
python scripts/export_receipt_data.py sample \
    --size 100 \
    --output-dir ./perf_test_data

# Run performance test
time python scripts/test_decision_engine.py ./perf_test_data \
    --output performance_report.txt

# Check processing times
grep "Average Pattern Detection Time" performance_report.txt
```

## Development Tips

1. **Start Small**: Begin with a few receipts to verify your setup
2. **Use Merchant Filter**: Focus on specific merchants when debugging patterns
3. **Compare with Production**: Export receipts with existing labels to validate accuracy
4. **Iterate Quickly**: Modify pattern detectors and test immediately with local data
5. **Monitor Costs**: The export script shows AWS API call counts

## Troubleshooting

**No receipts found:**
- Check your AWS credentials and DynamoDB table name
- Verify the table has receipt data
- Try different filter criteria

**Pattern detection not working:**
- Ensure receipt_label package is installed: `pip install -e receipt_label`
- Check that pattern detectors are properly initialized
- Review word text formatting in exported JSON

**Memory issues with large exports:**
- Use the `--limit` parameter to restrict data size
- Export in batches by merchant or date range
- Process receipts in smaller groups

## Advanced Usage

### Custom Export Filters

Extend the export script to add custom filters:

```python
# In export_receipt_data.py, add new method:
def export_by_date_range(self, start_date: str, end_date: str, limit: int = 100):
    """Export receipts within a date range."""
    # Implementation here
    pass
```

### Parallel Testing

Test multiple receipts in parallel for faster results:

```bash
# Use GNU parallel
find ./receipt_data -name "image_*" -type d | \
    parallel -j 4 python scripts/test_decision_engine.py ./receipt_data --receipt {}
```

### Integration with CI/CD

```yaml
# .github/workflows/test-decision-engine.yml
name: Test Decision Engine
on: [pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - name: Export test data
        run: python scripts/export_receipt_data.py sample --size 20
      - name: Run tests
        run: python scripts/test_decision_engine.py ./receipt_data
```

## Next Steps

1. **Enhance Pattern Detectors**: Use test results to improve pattern accuracy
2. **Add More Mock Services**: Implement mock S3, mock merchant API, etc.
3. **Create Benchmarks**: Establish baseline metrics for pattern detection
4. **Build Dashboard**: Visualize test results and trends over time