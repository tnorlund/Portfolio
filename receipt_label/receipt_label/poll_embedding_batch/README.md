# Poll Embedding Batch

This Step Function periodically polls OpenAI batch embedding jobs and handles successful ingestion into Pinecone. The full flow includes error handling, retries, and updates to the DynamoDB label and batch state.

```mermaid
stateDiagram-v2
    [*] --> ListPendingEmbeddingBatches
    ListPendingEmbeddingBatches --> RetrieveBatchStatus
    RetrieveBatchStatus --> IsBatchComplete
    IsBatchComplete --> DownloadResults : if complete
    IsBatchComplete --> WaitAndRetry : if still pending
    WaitAndRetry --> RetrieveBatchStatus

    DownloadResults --> ParseResults
    ParseResults --> UpsertToPinecone
    UpsertToPinecone --> WriteEmbeddingResults
    WriteEmbeddingResults --> UpdateLabelsToPending
    UpdateLabelsToPending --> MarkBatchComplete
    MarkBatchComplete --> [*]

    DownloadResults --> Fail : if download error
    UpsertToPinecone --> Fail : if Pinecone error
    RetrieveBatchStatus --> Fail : if API error
    ListPendingEmbeddingBatches --> Fail : if no batches found
```
