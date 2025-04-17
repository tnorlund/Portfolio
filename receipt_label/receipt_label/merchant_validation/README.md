# Merchant Validation

Semantic understanding of receipts is necessary for accurate word labeling. Here, we define some functions to help better develop some metadata for each receipt using a combination of ChatGPT and Google Places.

```mermaid
flowchart TD
    Start([Start]) --> ExtractReceiptFields["Extract candidate merchant fields"]
    ExtractReceiptFields --> SearchGooglePlaces["Query Google Places API"]
    SearchGooglePlaces --> IsMatchFound{"Is match found?"}

    IsMatchFound -- Yes --> ValidateWithGPT["Validate match with GPT"]
    IsMatchFound -- No --> InferWithGPT["Write 'no match' ReceiptMetadata"]
    InferWithGPT --> RetryGoogleSearch["Retry Google Places with inferred data"]
    RetryGoogleSearch --> IsRetryMatchFound{"Match found on retry?"}
    IsRetryMatchFound -- Yes --> ValidateWithGPT
    ValidateWithGPT --> WriteMetadata["Write validated ReceiptMetadata to DynamoDB"]
    WriteNoResultMetadata --> End([End])
    WriteMetadata --> End([End])
```
