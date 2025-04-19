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
    ListPending --> MapBatches{"For each pending batch"}

    subgraph ForEachBatch
        direction TB
        CheckStatus{"Check Job Status via OpenAI"}
        CheckStatus -- No --> End([End])
        CheckStatus -- Yes --> Download["Download NDJSON result file"]
        Download --> ParseResults["Parse NDJSON into CompletionBatchResult entries"]
        ParseResults --> ForEachResult{"Is Valid?"}

        subgraph DynamoAndPineconeSync
            direction TB
            ForEachResult -- Yes --> AddValid["Add to validItems list"]
            ForEachResult -- No -->  AddInvalid["Add to invalidItems list"]

            AddValid     --> WriteDynamoValid["batchWrite RECEIPT_WORD_LABEL → VALID updates"]
            AddInvalid   --> WriteDynamoInvalid["batchWrite RECEIPT_WORD_LABEL → INVALID updates"]

            WriteDynamoValid   --> UpsertPineconeValid["batchUpsert Pinecone metadata VALID"]
            WriteDynamoInvalid --> UpsertPineconeInvalid["batchUpsert Pinecone metadata INVALID"]
        end

    UpsertPineconeValid   --> End([End])
    UpsertPineconeInvalid --> End

    end
```
