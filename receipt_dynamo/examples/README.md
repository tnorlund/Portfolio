# OCR Refinement Module Examples

This directory contains examples showing how to use the OCR refinement functionality.

## Overview

The OCR refinement module in `receipt_dynamo/data/refine_ocr.py` provides functionality to:

1. Process new OCR results for receipts
2. Map tags from old OCR results to new OCR results
3. Preserve human-validated tags
4. Use batch operations for optimal DynamoDB performance

## Example Usage

Run the example:

```bash
python examples/refine_receipt_ocr_example.py
```

Options:
- `-i IMAGE_ID -r RECEIPT_ID`: Process a specific receipt
- `-m`: Process multiple receipts (up to 5)
- `--commit`: Commit changes to the database
- `--env [prod|dev|test]`: Specify the environment to use

## Using the Module in Your Code

```python
from receipt_dynamo.data._pulumi import load_env
from receipt_dynamo.data.refine_ocr import refine_receipt_ocr, process_all_receipts

# Load environment configuration
env = load_env("dev")  # or "prod", "test", etc.

# Process a single receipt
result = refine_receipt_ocr(
    image_id="your-image-id",
    receipt_id=123,
    env=env,
    debug=True,   # Set to False in production
    commit_changes=True  # Set to False for a dry run
)

# Process multiple receipts
results = process_all_receipts(
    env=env,
    limit=10,
    debug=True,
    commit_changes=True
)
```

## Performance Note

The module automatically uses batch operations for DynamoDB transactions which significantly improves performance:

- Reduces the number of DynamoDB operations from hundreds to just 8 batch operations
- Speeds up database operations by approximately 15x
- Preserves all functionality including human-validated tag prioritization 