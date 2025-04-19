# Submit Completion Batch

This module defines the core logic for preparing and submitting completion batches to OpenAI's asynchronous Completion API. It is responsible for retrieving receipt word embeddings and their associated label metadata, constructing validation and refinement prompts for each item, formatting them into OpenAI-compliant payloads, and logging batch submissions along with their responses.

This is typically the second step in a two-phase Step Function pipeline, following the embedding batch submission.

---

## 📦 Functions

### `generate_completion_batch_id()`

Generates a unique UUID for each completion batch.

### `list_validation_targets()`

Fetches all `ReceiptWordLabel` items with `validation_status = "PENDING"` (or the flag indicating they need validation).

### `chunk_validation_targets(targets, batch_size)`

Splits the list of pending `ReceiptWordLabel` records into chunks of size `batch_size` for parallel Map processing.

### `fetch_receipt_word_embeddings(labels)`

Retrieves receipt word embeddings and associated metadata (including OCR spatial data) for the given labels from DynamoDB or Pinecone.

### `join_labels_with_embeddings(labels, embeddings)`

Joins each label record with its corresponding embedding and OCR data.

### `chunk_joined_records(joined, batch_size)`

Splits the joined list into appropriately sized chunks for submission.

### `build_completion_prompts(joined_batch)`

Constructs validation/refinement prompts for each item in the batch, including context lines and examples.

### `format_openai_input(joined_prompts)`

Formats the batch of prompts into OpenAI-compliant NDJSON input.

### `write_ndjson(batch_id, input_data)`

Writes the formatted completion payload to a newline-delimited JSON file.

### `upload_ndjson_file(filepath)`

Uploads the NDJSON file to OpenAI's file endpoint for asynchronous completion.

### `submit_openai_completion_batch(file_id)`

Submits the completion batch job to OpenAI using the uploaded file ID, returning a job identifier.

### `create_completion_batch_summary(batch_id, joined)`

Creates a `CompletionBatchSummary` record in DynamoDB to track the job submission and metadata.

### `poll_completion_batch_results(batch_id)`

Periodically checks the status of the asynchronous completion job and retrieves results when complete.

### `process_completion_results(batch_id, results)`

Processes retrieved completion results and updates the corresponding `ReceiptWordLabel` items according to the model output:

- **If `is_valid` is `True:**
  - Set the record's `validation_status` to `VALID`.
  - Keep the original proposed label unchanged.
  - Clear or leave blank any existing rationale.
- **If `is_valid` is `False:**
  - Set the record's `validation_status` to `INVALID`.
  - Update the record's label to the suggested `correct_label`.
  - Save the provided `rationale` explanation.

## 📊 Step Function Architecture

```mermaid
flowchart TB
    Start([Start]) --> ListValidationTargets["List Validation Targets"]
    ListValidationTargets --> ChunkTargets["Chunk Validation Targets"]
    ChunkTargets --> MapChunks{"Map over chunks"}

    subgraph MapChunks Branch
        direction TB
        FetchEmbeddings["Fetch Receipt Word Embeddings"] --> JoinEmbeddingsAndLabels["Merge Embeddings and Labels"]
        JoinEmbeddingsAndLabels --> BuildPrompts["Build Completion Prompts"]
        BuildPrompts --> FormatChunk["Format Chunk into NDJSON"]
        FormatChunk --> UploadChunk["Upload NDJSON to S3"]
        UploadChunk --> SubmitCompletionJob["Submit Completion job to OpenAI"]
        SubmitCompletionJob --> CreateCompletionBatchSummary["Create CompletionBatchSummary in DynamoDB"]
        CreateCompletionBatchSummary --> PollResults["Poll Completion Results"]
        PollResults --> ProcessResults["Process Completion Results"]
        ProcessResults -->|is_valid == True| SetValid["Set VALID, keep label, clear rationale"]
        ProcessResults -->|is_valid == False| SetInvalid["Set INVALID, update label, save rationale"]
        SetValid --> End([End])
        SetInvalid --> End
    end
```
