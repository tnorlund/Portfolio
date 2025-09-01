# Sprouts Dry Run Batch Analysis Guide

## Overview

The `sprouts_dry_run_batch.py` script runs dry run analysis on all 100 Sprouts receipts using the N-parallel analyzer with Phase 2 label updating. Results are saved locally for analysis.

## Prerequisites

```bash
# Required environment variables
export OLLAMA_API_KEY='your-ollama-api-key'
export LANGCHAIN_API_KEY='your-langsmith-api-key'
```

## Quick Start

```bash
# Run dry run on all Sprouts receipts using N-parallel analyzer
python sprouts_dry_run_batch.py
```

## What It Does

### **Analysis Process:**
1. **N-Parallel Two-Phase Analysis** on each receipt:
   - **Phase 1**: 120B model finds currency labels (GRAND_TOTAL, LINE_TOTAL, etc.)
   - **Phase 2**: N parallel 20B models find line-item components (PRODUCT_NAME, QUANTITY, etc.)

2. **Dry Run Label Updates**:
   - Shows what labels would be added/updated in DynamoDB
   - **No actual database changes** made (`dry_run=True`)
   - Uses same logic as production label updating

3. **Local Storage**:
   - Individual JSON file per receipt
   - Batch summary with aggregated stats
   - Serialized label data and analysis results

### **Output Structure:**

```
sprouts_dry_run_results/
├── batch_summary_n_parallel.json           # Overall stats and summary
├── 87bbf863-e4e2-4079-ac28-82e7abee02fb_1_n_parallel.json  # Individual results
├── b6e7af49-3802-46f2-822e-bbd49cd55ada_1_n_parallel.json
└── ... (100 total receipt files)
```

### **Individual Receipt File Format:**
```json
{
  "image_id": "87bbf863-e4e2-4079-ac28-82e7abee02fb",
  "receipt_id": 1,
  "analyzer_type": "n_parallel",
  "status": "success",
  "processing_time": 35.73,
  "confidence_score": 0.972,
  "discovered_labels": [
    {
      "word_text": "CHIPS",
      "label_type": "PRODUCT_NAME",
      "confidence": 0.95,
      "reasoning": "Product name from line_total_0: ...",
      "value": 0.0,
      "line_ids": [15, 18],
      "context": "CHIPS 6.29"
    },
    {
      "word_text": "16.77",
      "label_type": "GRAND_TOTAL", 
      "confidence": 0.99,
      "value": 16.77,
      "line_ids": [26]
    }
  ],
  "validation_results": {...},
  "analysis_timestamp": "2023-01-01T12:00:00"
}
```

### **Batch Summary File Format:**
```json
{
  "batch_info": {
    "analyzer_type": "n_parallel",
    "total_receipts": 100,
    "successful": 95,
    "failed": 5,
    "total_time": 3500.2,
    "avg_time_per_receipt": 35.0
  },
  "successful_analyses": [...],  // All successful results
  "failed_analyses": [...]       // All failed results with errors
}
```

## Configuration Options

### **Analyzer Types:**
```python
# N-Parallel (recommended)
analyzer_type="n_parallel"      # N parallel 20B calls per LINE_TOTAL

# Single-Parallel  
analyzer_type="single_parallel" # 1 combined 20B call for all line items

# Sequential (baseline)
analyzer_type="sequential"       # Sequential 120B calls per line item
```

### **Concurrency Control:**
```python
max_concurrent=3    # Conservative for API rate limits
max_concurrent=5    # More aggressive if API allows
max_concurrent=1    # Sequential processing
```

### **Output Directory:**
```python
output_dir="./sprouts_dry_run_results"        # Default
output_dir="/path/to/custom/output"           # Custom location
```

## Expected Results

### **Performance Metrics:**
- **Total receipts**: 100 Sprouts receipts
- **Expected time**: ~50-60 minutes (35s avg per receipt)
- **Success rate**: 90-95% (some receipts may fail due to formatting issues)
- **Labels per receipt**: 5-15 labels average
- **Label types discovered**:
  - Currency: GRAND_TOTAL, LINE_TOTAL, SUBTOTAL, TAX
  - Line items: PRODUCT_NAME, QUANTITY, UNIT_PRICE

### **What You'll Learn:**
1. **N-Parallel Performance**: Real-world timing vs sequential approaches
2. **Label Coverage**: How many labels are discovered vs missed
3. **Confidence Scores**: Quality of label classifications
4. **Failure Modes**: Which receipt formats cause issues
5. **Line-Item Quality**: How well Phase 2 extracts product names/quantities

## Analysis Commands

After running the batch, analyze results:

```bash
# Count successful vs failed
ls sprouts_dry_run_results/*_n_parallel.json | wc -l

# View batch summary
cat sprouts_dry_run_results/batch_summary_n_parallel.json | jq .batch_info

# Find highest confidence receipts
jq -r '.confidence_score' sprouts_dry_run_results/*_n_parallel.json | sort -nr | head -5

# Count total labels discovered
jq -r '.discovered_labels | length' sprouts_dry_run_results/*_n_parallel.json | awk '{sum+=$1} END {print sum}'

# Find most common label types
jq -r '.discovered_labels[].label_type' sprouts_dry_run_results/*_n_parallel.json | sort | uniq -c | sort -nr
```

## Troubleshooting

### **Common Issues:**

**API Rate Limits:**
- Reduce `max_concurrent` from 3 to 1 
- Add delays between batches

**Memory Usage:**
- Process receipts in smaller batches
- Clear results between batches

**Failed Receipts:**
- Check `failed_analyses` in batch summary
- Common causes: OCR quality, receipt format variations

**Missing Labels:**
- Some receipts may not have clear LINE_TOTAL amounts
- Phase 2 depends on Phase 1 finding LINE_TOTAL labels

## Next Steps

After dry run completion:

1. **Analyze Results**: Review label discovery patterns and accuracy
2. **Production Run**: Set `dry_run=False` to actually update labels in DynamoDB
3. **Performance Comparison**: Compare with sequential and single-parallel approaches
4. **Error Analysis**: Investigate failed receipts and improve robustness

## LangSmith Integration

All API calls are tracked in LangSmith project `sprouts-dry-run-n_parallel`. View traces at:
- https://smith.langchain.com/
- Look for parallel execution patterns in Phase 2
- Analyze prompt effectiveness and response quality