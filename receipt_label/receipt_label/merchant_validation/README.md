# Merchant Validation

Semantic understanding of receipts is necessary for accurate word labeling. Here, we define some functions to help better develop some metadata for each receipt using a combination of ChatGPT and Google Places.

---

## 📦 Functions

### `list_receipts_for_merchant_validation()`

Lists all receipts that do not have receipt metadata. This provides the `image_id` and `receipt_id` per validation process.

## `get_receipt_details()`

Gets the receipt details given the `image_id` and `receipt_id`. This provides the receipt, lines, words, letters, tags, and labels for the validation.

### `extract_candidate_merchant_fields(words)`

Extracts possible `address`, `url`, and `phone` values from `ReceiptWord` entities.

### `query_google_places(extracted_dict, google_places_api_key)`

Queries the Google Places API using a dict of `ReceiptWords` grouped by type and returns the top place match, if any.

### `infer_merchant_with_gpt(receipt_word_lines)`

When no result is found in Google Places, this function sends the text of the receipt lines to GPT using function-calling with OpenAI and asks it to infer likely merchant metadata. Returns a structured result containing a guessed merchant name, address, and phone number.

### `write_receipt_metadata_to_dynamo(metadata)`

Stores a `ReceiptMetadata` entity to DynamoDB based on either a successful or failed match.

### `build_receipt_metadata_from_result(receipt_id, image_id, gpt_result, google_result, raw_receipt_fields)`

Builds a `ReceiptMetadata` object from a successful merchant match.

### `retry_google_search_with_inferred_data(gpt_merchant_data)`

Uses the data inferred by GPT (e.g., merchant name + address) to retry a Google Places API search. Returns a new Google match or `None`.

### `build_receipt_metadata_from_result_no_match(receipt_id, image_id, raw_fields, reasoning)`

Builds a `ReceiptMetadata` object for the no‑match scenario.

### `validate_match_with_gpt(receipt_fields, google_place)`

Compares extracted merchant fields against a Google Places result using GPT function-calling. Returns a structured decision including match validity, matched fields, and confidence.

Stores a fallback `ReceiptMetadata` record when no match is found — even after retrying with GPT. Includes the attempted inputs, GPT inferences (if any), and a status of `"NO_MATCH"`.

---

## 🧠 Usage

This module is designed to be run inside a Step Function dedicated to receipt-level merchant validation. It operates independently from the embedding flow and focuses on identifying and validating the business that issued the receipt.

### Step-by-step Usage:

1. **Extract merchant fields** using `extract_candidate_merchant_fields(...)`, pulling from ReceiptWord or ReceiptWordLabel entries.
2. **Query Google Places API** using the extracted fields via `query_google_places(...)`.
3. If Google returns a result:
   - Check if the result is valid using `is_valid_google_match(...)`
   - If invalid, call `validate_match_with_gpt(...)` to determine if GPT accepts the match
   - If either is valid, proceed with `build_receipt_metadata_from_result(...)` and `write_receipt_metadata_to_dynamo(...)`
4. If no Google match is found:
   - **Infer merchant metadata with GPT** via `infer_merchant_with_gpt(...)`
   - **Retry Google Places query** with `retry_google_search_with_inferred_data(...)`
   - If still no match, call `write_no_match_receipt_metadata(...)`
5. **The output of this module is a ReceiptMetadata entity**, saved in DynamoDB, which supports future word label validation and Pinecone scoping.

## 📊 Step Function Architecture

```mermaid
flowchart TD
    Start([Start]) --> list_receipts["List receipts needing merchant validation"]
    list_receipts --> ValidateMerchant["Validate Merchant"]

    subgraph "Validate Merchant"
        direction TB
        get_receipt_details["Get Receipt Details"] --> extract_candidate_merchant_fields["Extract candidate merchant fields"]
        extract_candidate_merchant_fields --> query_google_places["Query Google Places API"]
        query_google_places --> IsMatchFound{"Is match found?"}

        IsMatchFound -- Yes --> is_valid_google_match["Is valid Google match?"]
        is_valid_google_match -- Yes --> build_receipt_metadata_from_result["Build validated ReceiptMetadata"]
        build_receipt_metadata_from_result --> write_receipt_metadata_to_dynamo["Write results to DynamoDB"]
        is_valid_google_match -- No --> validate_match_with_gpt["Validate match with GPT"]
        validate_match_with_gpt -- Yes --> build_receipt_metadata_from_result
        validate_match_with_gpt -- No --> InferWithGPT["Infer merchant info with GPT"]

        IsMatchFound -- No --> InferWithGPT
        InferWithGPT --> retry_google_search_with_inferred_data["Retry Google Places with inferred data"]
        retry_google_search_with_inferred_data --> IsRetryMatchFound{"Match found on retry?"}
        IsRetryMatchFound -- Yes --> build_receipt_metadata_from_result
        IsRetryMatchFound -- No --> build_receipt_metadata_from_result_no_match["Build no-match ReceiptMetadata"]
        build_receipt_metadata_from_result_no_match --> write_receipt_metadata_to_dynamo
    end

    write_receipt_metadata_to_dynamo --> End([End])
    write_receipt_metadata_to_dynamo --> End([End])
```

## 🛠️ Remaining Work

- [ ] Create confidence thresholds or fallback logic when GPT match is “UNSURE”.
- [x] Implement `retry_google_search_with_inferred_data(gpt_merchant_data)`
- [ ] Add tests for `is_valid_google_match(...)` with diverse `place` input cases.
- [ ] Implement `build_receipt_metadata_from_result_no_match(...)` to store fallback metadata when no Google match is accepted, even after GPT validation.
- [ ] Add retry validation step using `validate_match_with_gpt` on the Google match retrieved via retry
- [ ] Add a confidence threshold (e.g. GPT confidence >= 0.7) before accepting "YES" decisions
- [ ] Unit tests
- [ ] End to End tests???
