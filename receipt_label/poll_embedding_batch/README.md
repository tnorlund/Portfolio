<pre><code>```mermaid
stateDiagram-v2
    [*] --> ListPendingEmbeddingBatches
    ListPendingEmbeddingBatches --> RetrieveBatchStatus
    RetrieveBatchStatus --> IsBatchComplete
    IsBatchComplete --> DownloadResults : if complete
    IsBatchComplete --> [*] : if still pending
    DownloadResults --> ParseResults
    ParseResults --> UpsertToPinecone
    UpsertToPinecone --> WriteEmbeddingResults
    WriteEmbeddingResults --> UpdateLabelsToPending
    UpdateLabelsToPending --> MarkBatchComplete
    MarkBatchComplete --> [*]
```</code></pre>
