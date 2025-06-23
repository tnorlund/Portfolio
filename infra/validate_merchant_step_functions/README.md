# Validate Merchant Step Functions

This module defines a Step Function that validates merchants **per receipt**. The
workflow is designed to run after receipt OCR ingestion to consolidate merchant
metadata.

## Workflow

1. **ListReceipts** – gathers receipt identifiers that require merchant
   validation.
2. **ValidateReceipt** – a `Map` state invoking a Lambda to validate each
   receipt individually.
3. **ConsolidateMetadata** – merges the validated merchant information for
   reporting and further processing.

The focus here is on validating the merchant for each receipt. If you need to
validate existing word labels grouped by merchant, see the companion module in
`infra/validation_by_merchant`.
