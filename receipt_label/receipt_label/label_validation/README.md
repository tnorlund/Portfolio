# Label Validation Module

This directory hosts the field specific validators used by the
`receipt_label` package. Each function checks a `ReceiptWord` and its
`ReceiptWordLabel` to determine whether the label matches the word text and
context. Every validator returns a `LabelValidationResult` describing the
outcome.

## Available Validators

### `validate_currency`
Fetches the word's vector from Pinecone, queries similar vectors tagged as
currency and confirms the text looks like a monetary amount.

### `validate_date`
Uses regex based parsing and Pinecone similarity to validate dates in
multiple formats including ISO-8601.

### `validate_time`
Checks for both 12 and 24 hour time formats (optionally with timezone
information) and combines that with Pinecone similarity.

### `validate_phone_number`
Validates phone number labels using digit patterns. Neighbor words are
merged so that numbers split across tokens are recognized. Pinecone is used
for semantic confirmation.

### `validate_address`
Compares the text and its neighbors to the canonical address stored in the
receipt metadata, applying fuzzy matching to handle abbreviations.

### `validate_merchant_name_pinecone`
Queries Pinecone for embeddings associated with the merchant name and
verifies that the word resembles part of a business name.

### `validate_merchant_name_google`
Fuzzy matches the token against the canonical merchant name resolved via
Google Places without using embeddings.

## Helper Utilities

- `get_unique_merchants_and_data` aggregates receipt metadata by merchant
  name.
- `update_labels` applies validation results to DynamoDB and Pinecone,
  marking labels as valid or invalid.
