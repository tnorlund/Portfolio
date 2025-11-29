# LangSmith Edge Case Analysis

## Overview

This guide explains how to use LangSmith to identify edge cases across all CORE_LABEL types in the label harmonizer.

## Quick Start

### 1. Run Harmonizer with Clean Project

To use a clean project for analysis, set the `LANGCHAIN_PROJECT` environment variable in your Step Function execution or Lambda:

```bash
# In Pulumi config (recommended for clean runs)
cd infra
pulumi config set langchain_project "label-harmonizer-v2" --stack dev

# Or set in Lambda environment (temporary)
export LANGCHAIN_PROJECT="label-harmonizer-v2"
```

### 2. Trigger Step Function with All Label Types

```bash
# Dry run with all CORE_LABELS (default)
aws stepfunctions start-execution \
  --state-machine-arn <arn> \
  --input '{"dry_run": true, "max_merchants": 10}'

# Or specify specific label types
aws stepfunctions start-execution \
  --state-machine-arn <arn> \
  --input '{"dry_run": true, "label_types": ["MERCHANT_NAME", "PRODUCT_NAME"], "max_merchants": 10}'
```

### 3. Analyze Edge Cases

```bash
# Analyze all label types from last 24 hours
python scripts/analyze_langsmith_edge_cases.py --project label-harmonizer-v2

# Analyze specific label type
python scripts/analyze_langsmith_edge_cases.py \
  --project label-harmonizer-v2 \
  --label-type MERCHANT_NAME

# Export to CSV for deeper analysis
python scripts/analyze_langsmith_edge_cases.py \
  --project label-harmonizer-v2 \
  --export-csv edge_cases.csv

# Custom time range
python scripts/analyze_langsmith_edge_cases.py \
  --project label-harmonizer-v2 \
  --hours 48 \
  --limit 5000
```

## What Gets Captured

Every LLM call in the harmonizer includes:

### Metadata (for filtering):
- `label_type`: The CORE_LABEL being validated (e.g., "MERCHANT_NAME")
- `merchant`: Merchant name (e.g., "Sprouts Farmers Market")
- `word`: Word being evaluated (e.g., "SPROUTS")
- `validation_status`: Current status (e.g., "INVALID", "PENDING")
- `image_id`, `receipt_id`, `line_id`, `word_id`: Full context
- `prompt_version`: Prompt version (e.g., "v2-markdown-full-context")
- `has_full_receipt_context`: Whether full receipt was provided
- `has_similar_words`: Whether similar words were found

### Tags (for grouping):
- `label-harmonizer`: All harmonizer runs
- `label-type:MERCHANT_NAME`: Filter by label type
- `merchant:Sprouts Farmers Market`: Filter by merchant
- `outlier-detection`: All outlier detection calls

### Output:
- `is_outlier`: Boolean decision
- `reasoning`: LLM's reasoning (if provided)

## Edge Case Types Identified

The script identifies:

1. **Outliers Detected**: Words flagged as outliers (potential false positives)
2. **Status INVALID**: Words with INVALID validation status
3. **Status NEEDS_REVIEW**: Words requiring manual review

## Example Output

```
================================================================================
SUMMARY BY LABEL TYPE
================================================================================

MERCHANT_NAME:
  Total traces: 45
  Outliers detected: 12
  Non-outliers: 33
  Edge cases: 15
  Merchants: 3 (Costco Wholesale, In-N-Out Burger, Sprouts Farmers Market)

PRODUCT_NAME:
  Total traces: 120
  Outliers detected: 8
  Non-outliers: 112
  Edge cases: 10
  Merchants: 5 (Target, Walmart, ...)

================================================================================
EDGE CASES BY LABEL TYPE
================================================================================

MERCHANT_NAME: 15 edge cases
  outlier_detected: 12
    - Word: 'SPROUTS' | Merchant: Sprouts Farmers Market | Status: INVALID
      Reasoning: Word appears in product description context...
      URL: https://smith.langchain.com/o/default/projects/p/label-harmonizer-v2/r/...
    - Word: 'OUT' | Merchant: In-N-Out Burger | Status: NEEDS_REVIEW
      Reasoning: Component of merchant name but context unclear...
      URL: https://smith.langchain.com/o/default/projects/p/label-harmonizer-v2/r/...
```

## Using LangSmith UI

1. **Filter by Project**: Select your project (e.g., "label-harmonizer-v2")
2. **Filter by Metadata**: Use filters like:
   - `metadata.label_type == "MERCHANT_NAME"`
   - `metadata.merchant == "Sprouts Farmers Market"`
   - `metadata.word == "SPROUTS"`
3. **Filter by Tags**: Use tags like:
   - `label-type:MERCHANT_NAME`
   - `merchant:Sprouts Farmers Market`
4. **Compare Prompts**: Use the prompt version metadata to compare different prompt versions

## Best Practices

1. **Use Clean Projects**: Create a new project for each major run to avoid confusion
   - `label-harmonizer-v2` for current run
   - `label-harmonizer-v3` for next iteration

2. **Export to CSV**: Export edge cases to CSV for deeper analysis in Excel/Python
   ```bash
   python scripts/analyze_langsmith_edge_cases.py \
     --project label-harmonizer-v2 \
     --export-csv edge_cases_$(date +%Y%m%d).csv
   ```

3. **Time-Based Analysis**: Use `--hours` to analyze specific time windows
   ```bash
   # Analyze only the last run
   python scripts/analyze_langsmith_edge_cases.py \
     --project label-harmonizer-v2 \
     --hours 2
   ```

4. **Label Type Focus**: Analyze one label type at a time for detailed review
   ```bash
   python scripts/analyze_langsmith_edge_cases.py \
     --project label-harmonizer-v2 \
     --label-type MERCHANT_NAME \
     --export-csv merchant_name_edge_cases.csv
   ```

## Troubleshooting

### No Traces Found

If the script finds 0 traces:

1. **Check Project Name**: Verify the project exists in LangSmith
   ```bash
   # List projects (requires LangSmith CLI or UI)
   ```

2. **Check Time Range**: Increase `--hours` to look further back
   ```bash
   python scripts/analyze_langsmith_edge_cases.py --hours 168  # Last week
   ```

3. **Check Filters**: Remove `--label-type` filter to see all traces
   ```bash
   python scripts/analyze_langsmith_edge_cases.py  # No filters
   ```

4. **Verify API Key**: Ensure `LANGCHAIN_API_KEY` is set correctly
   ```bash
   export LANGCHAIN_API_KEY=$(cd infra && pulumi config get LANGCHAIN_API_KEY --stack dev)
   ```

### Metadata Not Found

If metadata fields are missing:

1. **Check Lambda Environment**: Ensure `LANGCHAIN_PROJECT` is set in Lambda
2. **Check Prompt Version**: Verify `prompt_version` in metadata matches expectations
3. **Check LangSmith Version**: Ensure LangSmith client is up to date

## Next Steps

After identifying edge cases:

1. **Review in LangSmith**: Click URLs to see full prompts and responses
2. **Identify Patterns**: Look for common patterns across edge cases
3. **Refine Prompts**: Update prompts based on edge case analysis
4. **Re-run**: Run harmonizer again with refined prompts
5. **Compare**: Use different project names to compare results

