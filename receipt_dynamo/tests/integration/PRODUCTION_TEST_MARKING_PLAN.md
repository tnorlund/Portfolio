# Production Test Marking Plan

This plan identifies which integration tests should be marked as `unused_in_production` based on analysis of actual DynamoClient method usage in the `infra/` directory.

## Implementation Steps

1. First, update `pyproject.toml` to add the new marker
2. Then update each test file listed below with the appropriate marking

## pyproject.toml Update

Add to the markers section:
```toml
"unused_in_production: marks tests for entities not used in production infrastructure",
```

## Test Files to Mark as `unused_in_production`

### Definitely Not Used in Production

These entities have NO methods called in the infra/ directory:

| File | Entity | Reason |
|------|--------|---------|
| `test__instance.py` | Instance | No instance-related methods found in infra/ |
| `test__job.py` | Job | Generic job entity not used (OCR Job is used instead) |
| `test__job_checkpoint.py` | JobCheckpoint | No job checkpoint methods found |
| `test__job_dependency.py` | JobDependency | No job dependency methods found |
| `test__job_log.py` | JobLog | No job log methods found |
| `test__job_metric.py` | JobMetric | No job metric methods found |
| `test__job_resource.py` | JobResource | No job resource methods found |
| `test__queue.py` | Queue | Queue operations handled differently in infra |
| `test__receipt_chatgpt_validation.py` | ReceiptChatGPTValidation | No ChatGPT validation methods found |
| `test__receipt_field.py` | ReceiptField | No receipt field methods found |
| `test__receipt_label_analysis.py` | ReceiptLabelAnalysis | No label analysis methods found |
| `test__receipt_line_item_analysis.py` | ReceiptLineItemAnalysis | No line item analysis methods found |
| `test__receipt_section.py` | ReceiptSection | No receipt section methods found |
| `test__receipt_structure_analysis.py` | ReceiptStructureAnalysis | No structure analysis methods found |
| `test__receipt_validation_category.py` | ReceiptValidationCategory | No validation category methods found |
| `test__receipt_validation_result.py` | ReceiptValidationResult | No validation result methods found |
| `test__receipt_validation_summary.py` | ReceiptValidationSummary | No validation summary methods found |

### Used in Production (DO NOT MARK)

These entities ARE used in the infra/ directory:

| File | Entity | Used Methods |
|------|--------|--------------|
| `test__image.py` | Image | `list_images()`, `list_images_by_type()`, `get_image_details()` |
| `test__receipt.py` | Receipt | `list_receipts()`, `get_receipt_details()` |
| `test__ocr_job.py` | OCRJob | `add_ocr_job()`, `addOCRJob()` |
| `test__receipt_metadata.py` | ReceiptMetadata | `getReceiptMetadata()`, `list_receipt_metadatas()` |
| `test__receipt_word_label.py` | ReceiptWordLabel | `get_receipt_word_labels_by_label()` |
| `test__label_count_cache.py` | LabelCountCache | `list_label_count_caches()`, `add/update_label_count_cache()` |
| `test__places_cache.py` | PlacesCache | Places API caching (keep for future use) |
| `test__batch_summary.py` | BatchSummary | Batch processing operations |
| `test__line.py` | Line | `list_lines()`, `add_lines()`, `delete_lines()` |
| `test__word.py` | Word | `list_words()`, `add_words()`, `delete_words()` |
| `test__letter.py` | Letter | `list_letters()`, `add_letters()`, `delete_letters()` |
| `test__receipt_line.py` | ReceiptLine | `list_receipt_lines()`, `add_receipt_lines()`, `delete_receipt_lines()` |
| `test__receipt_word.py` | ReceiptWord | `list_receipt_words()`, `add_receipt_words()`, `delete_receipt_words()` |
| `test__receipt_letter.py` | ReceiptLetter | `list_receipt_letters()`, `add_receipt_letters()`, `delete_receipt_letters()` |

### Special Cases (DO NOT MARK)

| File | Reason |
|------|--------|
| `test__pulumi.py` | Infrastructure state management |
| `test__export_and_import.py` | Data migration utilities |
| `test_dynamo_client.py` | Core client functionality |
| `test__cluster.py.skip` | Already skipped |

## Marking Template

For each file that needs marking, add this after the imports:

```python
import pytest
# ... other imports ...

# This entity is not used in production infrastructure
pytestmark = [
    pytest.mark.integration,
    pytest.mark.unused_in_production
]

# ... rest of the test file ...
```

## Execution Plan

1. **Update pyproject.toml** with the new marker
2. **Mark 16 test files** as unused_in_production (see list above)
3. **Verify marking** by running: `pytest -m "integration and not unused_in_production" --co -q`
4. **Update CI/CD** to use the new marker for production test runs

## Running Tests After Marking

```bash
# Run only production-relevant integration tests
pytest tests/integration -m "integration and not unused_in_production"

# Run ALL integration tests (including unused)
pytest tests/integration -m "integration"

# Count production vs non-production tests
pytest tests/integration -m "integration and not unused_in_production" --co -q | wc -l
pytest tests/integration -m "integration and unused_in_production" --co -q | wc -l
```

## Expected Outcome

- Faster CI/CD runs by skipping irrelevant tests
- Clear documentation of which entities are actually used in production
- Easy ability to run full test suite when needed
- Maintains test coverage for potential future use