# Submit Embedding Batch

This module defines the core logic for preparing and submitting embedding batches to OpenAI's asynchronous Batch API. It is responsible for retrieving receipt word labels that have not yet been embedded, joining them with spatial OCR data, formatting the payload, and logging the batch submission.

This is typically the first step in a two-phase Step Function pipeline, followed later by a polling + processing step.

---

## 📦 Functions

### `list_receipt_words_with_no_embeddings()`

Fetches all ReceiptWords items with `embedding_status = "NONE"`.

### `chunk_into_embedding_batches(receipt_words)`

Splits the list of ReceiptWords into chunks based on the combination of Receipt ID and Image ID.

### `generate_batch_id()`

Generates a unique UUID for each embedding batch.

### `format_word_context_embedding()`

Prepares OpenAI-compliant embedding payload that contains the words to the left and right.

### `format_spatial_embedding()`

Prepares OpenAI-compliant embedding payload that contains the word and its semantically descriptive description of the word's position on the page.

### `combine_embeddings()`

Combine the different embeddings to a single list.

### `generate_ndjson()`

Generate a local file for the embeddings provided.

### `write_ndjson(batch_id, input_data)`

Writes OpenAI batch payload to a newline-delimited JSON file.

### `upload_ndjson_to_s3`

Uploads the NDJSON file to S3.

### `download_ndjson_from_s3`

Download the NDJSON from S3.

### `upload_ndjson_to_openai(filepath)`

Uploads the NDJSON file to OpenAI's file endpoint for batch use.

### `submit_batch_to_openai(file_id)`

Submits the embedding job to OpenAI using the uploaded file ID.

### `create_batch_summary(batch_id, joined)`

Builds a BatchSummary entity with "PENDING" status.

### `add_batch_summary(batch_summary)`

Adds the batch summary to DynamoDB.

---

## 🧠 Usage

This module is split across two phases in a Step Function workflow:

### Phase 1: Prepare Batches

1. List all receipt words with `embedding_status = "NONE"`
2. Chunk the data into batches (by receipt)
   1. Generate Batch ID
   2. Formate the word context embedding
   3. Generate the NDJSON file for all embeddings
   4. Upload each NDJSON file to S3
3. Return metadata for each batch (including `batch_id`, `s3_bucket`, and `s3_key`)

### Phase 2: Submit to OpenAI

1. Download the NDJSON from S3
2. Upload the NDJSON to OpenAI
3. Submit the batch to OpenAI
4. Create the Batch Summary
5. Add the Batch Summary to DynamoDB

---

## 📊 Step Function Architecture

```mermaid
flowchart TD
    start([Start]) --> list_receipt_words_with_no_embeddings["List Receipt Words With No Embeddings"]
    list_receipt_words_with_no_embeddings --> chunk_into_embedding_batches["Chunk Into Embedding Batches"]
    chunk_into_embedding_batches --> format_chunk["Format Chunk"]
    format_chunk --> map_chunks["Map Chunks"]

    subgraph map_chunks
        direction TD

        generate_batch_id["Generate Batch ID"] --> format_word_context_embedding["Format Word Context Embedding"]
        format_word_context_embedding --> generate_ndjson["Generate NDJSON"]
        generate_ndjson --> write_ndjson["Write NDJSON"]
        write_ndjson --> upload_ndjson_to_s3["Upload to S3"]
        upload_ndjson_to_s3 --> submit_embedding["Submit Embedding"]
    end

    subgraph submit_embedding
        direction TD
        download_from_s3["Download From S3"] --> upload_ndjson_to_openai["Upload NDJSON to OpenAI"]
        upload_ndjson_to_openai --> submit_batch_to_openai["Submit Batch to OpenAI"]
        submit_batch_to_openai --> create_batch_summary["Create Batch Summary"]
        create_batch_summary --> add_batch_summary["Add Batch Summary"]
    end

    add_batch_summary --> end([End])
```
