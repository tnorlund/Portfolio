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
        Start([Start]) --> list_pending_completion_batches["List Pending Batches"]
        list_pending_completion_batches --> map_batches("Map over Pending Batches")

        subgraph MapChunks Branch
            direction TB
            get_openai_batch_status["Poll OpenAI Status"]
            get_openai_batch_status --> End([End])
            get_openai_batch_status -- |complete| --> download_openai_batch_result["Download Results"]
            Download --> parse_results["Parse NDJSON into CompletionBatchResult entries"]
            parse_results --> map_results{"Check if Valid"}

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
```
