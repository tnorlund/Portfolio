# Validation by Merchant

This directory defines a Step Function that validates word labels **grouped by
merchant**. It first lists all unique merchants with their associated receipts
and then runs validation for each batch.

## Workflow

1. **ListUniqueMerchants** – a Lambda collects receipt IDs grouped by canonical
   merchant name.
2. **ValidateLabel** – a `Map` state iterating over each batch and validating the
   selected labels for every receipt.

Use this when validating labels across receipts owned by the same merchant.
For receipt-level merchant metadata validation, see
`infra/validate_merchant_step_functions`.
