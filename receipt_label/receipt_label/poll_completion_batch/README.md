# Poll Completion Batch

This module manages the asynchronous polling of OpenAI completion jobs submitted by the submission pipeline. Given a `batch_id`, it periodically checks the job status via OpenAI’s API until the job reaches a terminal state. Upon successful completion, it downloads the NDJSON result file, parses each entry into a `CompletionBatchResult`, and emits these results to the downstream processing Step Function for validation handling. This module does not modify any `ReceiptWordLabel` records directly—it only retrieves and models the raw completion outputs.

---

## 📦 Functions

---

## 🧠 Usage

---

## 📊 Step Function Architecture

```mermaid
    flowchart TB
        Start([Start]) --> ListPending["List Pending CompletionBatchSummaries"]
        ListPending --> ChunkPending["Chunk Pending CompletionBatchSummaries"]
        ChunkPending --> MapChunks{"Map over chunks"}

        subgraph MapChunks Branch
            direction TB
            CheckStatus{"Check Job Status via OpenAI"}
            CheckStatus -- No --> End([End])
            CheckStatus -- Yes --> Download["Download NDJSON result file"]
            Download --> ParseResults["Parse NDJSON into CompletionBatchResult entries"]
            ParseResults --> ForEachResult{"Is `is_valid` true?"}

            subgraph DynamoSync
                direction TB
                ForEachResult -- Yes --> UpdateValid["Update ReceiptWordLabel: VALID"]
                UpdateValid --> UpsertPineconeValid["Upsert Pinecone metadata: VALID"]
                ForEachResult -- No --> UpdateInvalid["Update ReceiptWordLabel: INVALID"]
                UpdateInvalid --> CreateProposed["Create new ReceiptWordLabel: status=NONE"]
                CreateProposed --> UpsertPineconeInvalid["Upsert Pinecone metadata: proposed label"]
            end

            UpsertPineconeValid --> End
            UpsertPineconeInvalid --> End
        end

        MapBatches --> ForEachBatch
```
