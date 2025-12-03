# Exported Image Data for Testing and Rollback

This directory contains complete exports of all DynamoDB records for two problematic images that will be used to test the receipt splitting functionality.

## Exported Images

1. **13da1048-3888-429f-b2aa-b3e15341da5e**
   - 3 receipts
   - 156 image-level lines
   - 417 image-level words
   - 2,133 image-level letters
   - 2 OCR jobs
   - 2 OCR routing decisions

2. **752cf8e2-cb69-4643-8f28-0ac9e3d2df25**
   - 1 receipt
   - 88 image-level lines
   - 201 image-level words
   - 1,039 image-level letters

## Directory Structure

```
exported_image_data/
├── {image_id}/
│   ├── image.json                    # Image entity
│   ├── lines.json                    # Image-level Line entities
│   ├── words.json                    # Image-level Word entities
│   ├── letters.json                  # Image-level Letter entities
│   ├── ocr_jobs.json                 # OCR job entities
│   ├── ocr_routing_decisions.json    # OCR routing decision entities
│   ├── summary.json                  # Summary of all data
│   └── receipt_{receipt_id}/
│       ├── receipt.json              # Receipt entity
│       ├── receipt_lines.json        # ReceiptLine entities
│       ├── receipt_words.json        # ReceiptWord entities
│       ├── receipt_letters.json      # ReceiptLetter entities
│       ├── receipt_word_labels.json  # ReceiptWordLabel entities
│       ├── receipt_metadata.json     # ReceiptMetadata entity
│       └── compaction_runs.json      # CompactionRun entities (if any)
```

## Data Format

All JSON files use `dict(entity)` for serialization, which means they can be loaded back using:
```python
from receipt_dynamo.entities import Receipt, ReceiptLine, ReceiptWord, ...

# Load from JSON
with open("receipt.json", "r") as f:
    data = json.load(f)
    receipt = Receipt(**data)
```

## Usage for Rollback

If the split receipt operation needs to be rolled back, you can use these JSON files to recreate all the original entities:

1. **Load entities from JSON files**
2. **Recreate them in DynamoDB** using the appropriate client methods
3. **Restore ChromaDB embeddings** if needed (from backup or re-embedding)

## Export Script

The export was created using:
```bash
python scripts/export_image_data_for_rollback.py \
    --image-ids 13da1048-3888-429f-b2aa-b3e15341da5e 752cf8e2-cb69-4643-8f28-0ac9e3d2df25 \
    --output-dir ./exported_image_data
```

## Total Size

- **33 JSON files**
- **6.6 MB** total

## Notes

- All entities are exported using `dict(entity)` for serialization
- Image-level OCR data (lines, words, letters) is included
- Receipt-level OCR data (receipt_lines, receipt_words, receipt_letters) is included
- Labels, metadata, and compaction runs are included
- OCR jobs and routing decisions are included for completeness

