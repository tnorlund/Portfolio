# Poll Embedding Batch

This Step Function periodically polls OpenAI batch embedding jobs and handles successful ingestion into Pinecone. The full flow includes error handling, retries, and updates to the DynamoDB label and batch state.

```mermaid
flowchart TD
    Start([Start]) --> ListPendingEmbeddingBatches["List Pending Batches"]
    ListPendingEmbeddingBatches --> RetrieveBatchStatus["Check Batch Status with Open AI"]
    ListPendingEmbeddingBatches --> RetrieveBatchStatus1["Check Batch Status with Open AI"]
    ListPendingEmbeddingBatches --> RetrieveBatchEllipsis["..."]

    subgraph "Check Batch Status with Open AI"
        direction TB
        RetrieveBatchStatus["Query Open AI"] --> IsBatchComplete["Check if Batch Complete"]
        IsBatchComplete -->|complete| DownloadResults["Download Results"]
        IsBatchComplete -->|not complete| End([End])
        DownloadResults --> UpsertPinecone["Add to Pinecone"]
        UpsertPinecone --> AddEmbeddingBatchResult["Add Embedding Batch Results to DynamoDB"]
        AddEmbeddingBatchResult --> UpdateBatchSummary["Update Batch Summary in DynamoDB"]
    end

    UpdateBatchSummary --> End([End])
```
