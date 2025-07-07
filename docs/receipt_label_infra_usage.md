# receipt_label Package Usage in Infrastructure

This document summarizes how the `receipt_label` package is utilized within the `infra/` directory and outlines opportunities for improvement.

## Initial Label Generation

The `ReceiptLabeler` class updates word labels when processing receipts. During processing, existing labels are overwritten with the new label and reasoning:

```python
existing_label.label = new_label
existing_label.reasoning = f"{label_info['reasoning']} (Previously labeled as '{old_label}': {old_reasoning})"
```
【F:receipt_label/receipt_label/core/labeler.py†L366-L368】

A Lambda handler invokes this logic via `process_receipt_by_id`:

```python
labeler = ReceiptLabeler(
    places_api_key=GOOGLE_PLACES_API_KEY,
    dynamodb_table_name=DYNAMODB_TABLE_NAME,
    gpt_api_key=OPENAI_API_KEY,
    validation_level="basic",
)
analysis_result = labeler.process_receipt_by_id(
    receipt_id=int(receipt_id),
    image_id=image_id,
    save_results=True,
)
```
【F:infra/lambda_functions/receipt_processor/handler/process_receipt.py†L121-L135】

For manual testing there is a CLI entry point:

```python
if __name__ == "__main__":
    import sys
    if len(sys.argv) != 3:
        print(f"Usage: python {__file__} <image_id> <receipt_id>")
        sys.exit(1)
    test_event = {"image_id": sys.argv[1], "receipt_id": int(sys.argv[2])}
    result = validate_handler(test_event, None)
    print(f"Result: {result}")
```
【F:infra/validate_merchant_step_functions/handlers/validate_single_receipt_v2.py†L84-L95】

## Validation Helpers

After labels are generated, helper functions update their validation status:

```python
def update_label_validation_status(
    labels: list[ReceiptWordLabel], client_manager: ClientManager = None
) -> None:
    if client_manager is None:
        client_manager = get_client_manager()
    for label in labels:
        label.validation_status = ValidationStatus.PENDING.value
    client_manager.dynamo.updateReceiptWordLabels(labels)
```
【F:receipt_label/receipt_label/completion/submit.py†L362-L370】

`update_valid_labels` marks valid labels and updates metadata:

```python
def update_valid_labels(
    valid_labels_results: list[LabelResult],
    client_manager: ClientManager = None,
) -> None:
    if client_manager is None:
        client_manager = get_client_manager()
    valid_by_vector: dict[str, list[str]] = {}
    for res in valid_labels_results:
        res.label_from_dynamo.validation_status = ValidationStatus.VALID.value
        res.label_from_dynamo.label_proposed_by = "COMPLETION_BATCH"
        vid = _build_vector_id(res.label_from_dynamo)
        valid_by_vector.setdefault(vid, []).append(res.label_from_dynamo.label)
    ...
```
【F:receipt_label/receipt_label/completion/poll.py†L280-L298】

Invalid labels go through `update_invalid_labels`, which sets `INVALID` or `NEEDS_REVIEW` and may create correction labels:

```python
def update_invalid_labels(
    invalid_labels_results: list[LabelResult],
    client_manager: ClientManager = None,
) -> None:
    if client_manager is None:
        client_manager = get_client_manager()
    labels_to_update: list[ReceiptWordLabel] = []
    labels_to_add: list[ReceiptWordLabel] = []
    ...
    for res in invalid_labels_results:
        if any(
            other.validation_status == ValidationStatus.INVALID.value
            for other in res.other_labels
        ):
            res.label_from_dynamo.validation_status = (
                ValidationStatus.NEEDS_REVIEW.value
            )
        else:
            res.label_from_dynamo.validation_status = (
                ValidationStatus.INVALID.value
            )
        res.label_from_dynamo.label_proposed_by = "COMPLETION_BATCH"
        labels_to_update.append(res.label_from_dynamo)
```
【F:receipt_label/receipt_label/completion/poll.py†L339-L377】

## Usage Across `infra/`

The infrastructure code leverages these helpers in several Lambda handlers and Pulumi components:

- Merchant validation functions call utilities from `receipt_label.merchant_validation` and `label_validation`.
- Embedding and completion Lambdas use modules from `receipt_label.embedding` and `receipt_label.completion` for batch processing.
- The receipt processing Lambda initializes `ReceiptLabeler` for primary analysis of receipts.

These integrations allow the infrastructure layer to invoke labeling, embedding, and validation logic without duplicating code.

## Opportunities for Improvement

The current setup works but there are areas to refine for cleanliness and idempotency:

1. **Switch handlers to `get_client_manager()`**  
   Several handlers still call the deprecated `get_clients()` helper. Updating them to use `get_client_manager()` would provide consistent dependency injection.
2. **Standardize environment variables**  
   Components export both `DYNAMO_TABLE_NAME` and `DYNAMODB_TABLE_NAME`. Using a single name avoids misconfiguration.
3. **Idempotency checks**  
   Polling handlers could confirm whether results were already written before updating DynamoDB or Pinecone to prevent duplicate writes.

### Suggested Task Stubs

#### Switch infra handlers to use `get_client_manager()`
1. Update imports and replace `get_clients()` calls.
2. Apply changes in:
   - `infra/validate_merchant_step_functions/handlers/consolidate_new_metadata.py`
   - `infra/validation_pipeline/lambda.py`
   - `infra/validation_by_merchant/lambda.py`
3. Run `isort`, `black`, `pylint`, and `mypy` on modified files.

#### Standardize environment variable name
1. Replace `DYNAMO_TABLE_NAME` with `DYNAMODB_TABLE_NAME` across Pulumi components.
2. Verify `ClientConfig.from_env()` continues to read `DYNAMODB_TABLE_NAME`.
3. Update any related documentation snippets.

These improvements will help keep the infrastructure codebase consistent, idempotent, and easier to maintain.
