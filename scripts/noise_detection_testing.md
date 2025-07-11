# Noise Detection Testing Scripts

This directory contains laptop scripts for testing and validating the noise word detection feature (Epic #188).

## Scripts

### 1. `test_noise_detection.py`
Full analysis script that exports real receipt data and generates comprehensive reports.

**Features:**
- Exports receipts from DynamoDB using existing tools
- Analyzes noise patterns across different receipt types
- Calculates potential cost savings from filtering embeddings
- Generates detailed markdown reports

**Usage:**
```bash
# Export from DynamoDB and analyze
python scripts/test_noise_detection.py --table-name dev-receipts --limit 20 --output-dir ./noise-analysis

# Analyze already exported data
python scripts/test_noise_detection.py --local-data ./exported-receipts --output-dir ./noise-analysis
```

**Output:**
- `noise-analysis/noise_detection_report.md` - Human-readable report
- `noise-analysis/statistics.json` - Raw statistics
- `noise-analysis/detailed_analysis.json` - Per-receipt analysis
- `noise-analysis/exports/` - Exported receipt data (if using --table-name)

### 2. `validate_noise_patterns.py`
Interactive tool for testing specific words and patterns.

**Features:**
- Test individual words to see if they're classified as noise
- Test sample receipts (grocery, restaurant, gas station)
- Validate edge cases
- Interactive mode for exploration

**Usage:**
```bash
# Interactive mode
python scripts/validate_noise_patterns.py

# Test specific word
python scripts/validate_noise_patterns.py --word "****"

# Test receipt type
python scripts/validate_noise_patterns.py --receipt grocery

# Test from file
python scripts/validate_noise_patterns.py --file my_receipt.txt
```

## Example Workflow

1. **Export and analyze real data:**
   ```bash
   # Export 50 receipts and analyze
   python scripts/test_noise_detection.py \
     --table-name portfolio-dev \
     --limit 50 \
     --output-dir ./noise-test-2024-01
   ```

2. **Review the report:**
   ```bash
   cat ./noise-test-2024-01/noise_detection_report.md
   ```

3. **Test specific patterns found:**
   ```bash
   # Use interactive mode to test edge cases
   python scripts/validate_noise_patterns.py
   > test ****1234
   > test $15.99
   > receipt grocery
   ```

## Key Metrics to Track

From the analysis, you should see:
- **Noise percentage**: Typically 25-45% of words are noise
- **Common patterns**: Punctuation (. , :), separators (- | =), artifacts (**** ....)
- **Cost savings**: Based on OpenAI embedding pricing ($0.00002/token)
- **Merchant differences**: Gas stations may have more noise (pump displays)

## Integration with Epic #192

These scripts help validate that Epic #188 (Noise Detection) is working correctly before it becomes a dependency for Epic #192 (Agent Integration). The noise filtering ensures:

1. Reduced embedding costs (25-45% fewer tokens)
2. Cleaner data for the agent labeling system
3. Better performance in downstream processing

## Troubleshooting

If you get import errors:
```bash
# Make sure you're in the project root
cd /path/to/Portfolio-phase2-batch1

# Install required packages
pip install -e receipt_dynamo
pip install -e receipt_label
```

If DynamoDB export fails:
- Check AWS credentials: `aws configure list`
- Verify table name: `aws dynamodb list-tables`
- Use smaller --limit value for testing
