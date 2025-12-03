# Receipt Lifecycle Walkthrough: Adding and Deleting Receipts

This document explains the complete process of adding and deleting receipts, covering both DynamoDB and ChromaDB operations.

---

## 📝 Adding a Receipt

### Overview
When you add a receipt, you're creating entities in both DynamoDB (structured data) and ChromaDB (embeddings for semantic search). The process involves multiple steps that must happen in the correct order.

### Step-by-Step Process

#### 1. **DynamoDB: Save Receipt Entities** (Source of Truth)

**Order of Creation:**
```
Receipt (parent entity)
  ├── ReceiptLine (child of Receipt)
  ├── ReceiptWord (child of ReceiptLine)
  ├── ReceiptLetter (child of ReceiptWord, optional)
  ├── ReceiptWordLabel (references ReceiptWord, optional)
  └── ReceiptMetadata (references Receipt, optional)
```

**What Happens:**
- `client.add_receipt(receipt)` - Creates the main Receipt entity
- `client.add_receipt_lines(receipt_lines)` - Creates ReceiptLine entities
- `client.add_receipt_words(receipt_words)` - Creates ReceiptWord entities
- `client.add_receipt_letters(receipt_letters)` - Creates ReceiptLetter entities (if any)
- `client.add_receipt_metadata(receipt_metadata)` - Creates ReceiptMetadata entity (if any)
- `client.add_receipt_word_label(label)` - Creates ReceiptWordLabel entities (if any)

**DynamoDB Structure:**
- **Primary Key (PK)**: `IMAGE#{image_id}`
- **Sort Key (SK)**:
  - Receipt: `RECEIPT#{receipt_id:05d}`
  - ReceiptLine: `RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}`
  - ReceiptWord: `RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#WORD#{word_id:05d}`
  - ReceiptLetter: `RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#WORD#{word_id:05d}#LETTER#{letter_id:05d}`
  - ReceiptWordLabel: `RECEIPT#{receipt_id:05d}#WORD#{word_id:05d}#LABEL#{label}`
  - ReceiptMetadata: `RECEIPT#{receipt_id:05d}#METADATA`

**Why This Order:**
- Foreign key relationships: Words reference Lines, Letters reference Words, Labels reference Words
- Must create parent entities before children

---

#### 2. **ChromaDB: Create Embeddings** (Semantic Search - Realtime)

**What Happens:**
1. **Create Local Delta Collections** (temporary):
   - Create two local ChromaDB collections in temp directories:
     - `lines_{run_id}/` - For line embeddings
     - `words_{run_id}/` - For word embeddings
   - Mode: `delta` (incremental changes, not full snapshots)
   - Mode: `metadata_only=True` (we'll add embeddings next)

2. **Generate Embeddings Using OpenAI API (Realtime)**:
   - Call `upsert_embeddings()` which internally calls:
     - `embed_lines_realtime()` - Makes **direct OpenAI API calls** via `openai_client.embeddings.create()`
       - Model: `text-embedding-3-small`
       - Input: List of line texts
       - Returns: Embeddings immediately (synchronous, not batch)
     - `embed_words_realtime()` - Makes **direct OpenAI API calls** via `openai_client.embeddings.create()`
       - Model: `text-embedding-3-small`
       - Input: List of word texts with context
       - Returns: Embeddings immediately (synchronous, not batch)
   - **Realtime vs Batch**:
     - **Realtime**: Direct API calls, immediate results, synchronous
     - **Batch**: Upload NDJSON to OpenAI, submit batch job, poll for completion (asynchronous)
   - This stores embeddings in the local delta collections

3. **Upload Deltas to S3** (Source of Truth):
   - `line_client.persist_and_upload_delta()` - Uploads delta to S3
     - S3 Key: `lines/delta/{run_id}/`
     - S3 Bucket: `chromadb_bucket` (e.g., "chromadb-bucket")
   - `word_client.persist_and_upload_delta()` - Uploads delta to S3
     - S3 Key: `words/delta/{run_id}/`
     - S3 Bucket: `chromadb_bucket`
   - These are **delta files** (incremental changes), not full snapshots
   - **S3 is the source of truth** - these delta files will be merged into snapshots

4. **Create CompactionRun Entity in DynamoDB**:
   - Creates a `CompactionRun` entity in DynamoDB:
     ```python
     CompactionRun(
         run_id=run_id,  # UUID
         image_id=image_id,
         receipt_id=receipt_id,
         lines_delta_prefix=lines_delta_key,  # S3 key: "lines/delta/{run_id}/"
         words_delta_prefix=words_delta_key,  # S3 key: "words/delta/{run_id}/"
     )
     ```
   - This entity triggers the compaction process via DynamoDB streams

**ChromaDB ID Format:**
- **Lines**: `IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}`
- **Words**: `IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#WORD#{word_id:05d}`

**Why Deltas First:**
- Deltas are small, incremental changes
- Full snapshots are large and expensive to update
- Compactor merges deltas into snapshots later

---

#### 3. **Compaction: Merge Deltas into Snapshots** (Background Process)

**What Happens:**
1. **DynamoDB Stream Event**: When `CompactionRun` is created, DynamoDB streams fire an event
2. **Stream Processor**: Processes the stream event and detects `COMPACTION_RUN` entity type
3. **Compactor Lambda**:
   - Downloads the **latest snapshot** from S3 (source of truth)
     - S3 Key: `lines/snapshot/latest/` and `words/snapshot/latest/`
   - Downloads the **delta files** from S3 (from step 2)
     - S3 Keys: `lines/delta/{run_id}/` and `words/delta/{run_id}/`
   - Merges deltas into the snapshot (adds new embeddings to existing snapshot)
   - Uploads the **updated snapshot** back to S3 (overwrites `latest/`)
   - Updates `CompactionRun` status in DynamoDB: `PENDING` → `COMPLETED` (or `FAILED`)

**S3 Structure:**
- **Snapshots** (source of truth): `{collection}/snapshot/latest/`
  - Full ChromaDB collection state
  - Only modified by compactor
  - Used for querying/searching
- **Deltas** (temporary): `{collection}/delta/{run_id}/`
  - Incremental changes (new embeddings)
  - Created by realtime embedding process
  - Merged into snapshots by compactor

**Why Compaction:**
- Snapshots are the source of truth for ChromaDB
- Deltas are merged into snapshots periodically
- This keeps snapshots up-to-date without constant full rewrites
- Efficient: Only merge new embeddings, don't recreate entire collection

---

#### 4. **NDJSON Export** (Optional, for Audit Trail)

**What Happens:**
- Exports receipt lines and words to NDJSON files in S3
- S3 Keys:
  - `receipts/{image_id}/receipt-{receipt_id:05d}/lines.ndjson`
  - `receipts/{image_id}/receipt-{receipt_id:05d}/words.ndjson`
- Used for audit trail and reprocessing if needed

---

### Complete Flow Diagram

```
┌─────────────────────────────────────────────────────────────┐
│ 1. DynamoDB: Save Receipt Entities                           │
│    - Receipt                                                  │
│    - ReceiptLine, ReceiptWord, ReceiptLetter                  │
│    - ReceiptWordLabel, ReceiptMetadata                        │
└─────────────────────────────────────────────────────────────┘
                        ↓
┌─────────────────────────────────────────────────────────────┐
│ 2. ChromaDB: Create Embeddings                               │
│    - Create local delta collections                          │
│    - Generate embeddings (OpenAI API)                        │
│    - Upload deltas to S3                                     │
│    - Create CompactionRun in DynamoDB                        │
└─────────────────────────────────────────────────────────────┘
                        ↓
┌─────────────────────────────────────────────────────────────┐
│ 3. DynamoDB Streams: Trigger Compaction                     │
│    - CompactionRun created → Stream event                    │
└─────────────────────────────────────────────────────────────┘
                        ↓
┌─────────────────────────────────────────────────────────────┐
│ 4. Compactor Lambda: Merge Deltas into Snapshots            │
│    - Download snapshot from S3 (source of truth)             │
│    - Download deltas from S3                                 │
│    - Merge deltas into snapshot                              │
│    - Upload updated snapshot to S3                          │
│    - Update CompactionRun status                             │
└─────────────────────────────────────────────────────────────┘
                        ↓
┌─────────────────────────────────────────────────────────────┐
│ 5. NDJSON Export (Optional)                                  │
│    - Export lines/words to S3 for audit trail               │
└─────────────────────────────────────────────────────────────┘
```

---

## 🗑️ Deleting a Receipt

### Overview
When you delete a receipt, you must delete from DynamoDB in reverse order of creation (to respect foreign key constraints). ChromaDB embeddings are automatically deleted by the enhanced compactor when the Receipt entity is deleted.

### Step-by-Step Process

#### 1. **DynamoDB: Delete Receipt Entities** (Reverse Order)

**Order of Deletion:**
```
ReceiptWordLabel (references Words)  ← Delete first
  ↓
ReceiptWord (references Lines)
  ↓
ReceiptLine (references Receipt)
  ↓
ReceiptLetter (references Words)
  ↓
ReceiptMetadata (references Receipt)
  ↓
CompactionRun (references Receipt)
  ↓
Receipt (parent entity)              ← Delete last
```

**What Happens:**
- `client.delete_receipt_word_label(...)` - Delete all labels for the receipt
- `client.delete_receipt_words(receipt_words)` - Delete all words
- `client.delete_receipt_lines(receipt_lines)` - Delete all lines
- `client.delete_receipt_letters(receipt_letters)` - Delete all letters (if any)
- `client.delete_receipt_metadata(image_id, receipt_id)` - Delete metadata (if any)
- `client.delete_compaction_run(...)` - Delete all compaction runs for the receipt
- `client.delete_receipt(image_id, receipt_id)` - Delete the Receipt entity **LAST**

**Why Reverse Order:**
- Foreign key constraints: Can't delete parent before children
- Must delete children first, then parents

---

#### 2. **DynamoDB Streams: Receipt Deletion Event**

**What Happens:**
- When `client.delete_receipt()` is called, DynamoDB deletes the Receipt entity
- DynamoDB streams immediately fire a `REMOVE` event for the Receipt entity
- The stream processor detects this as a `RECEIPT` entity deletion

**Stream Event Structure:**
```json
{
  "eventName": "REMOVE",
  "dynamodb": {
    "Keys": {
      "PK": {"S": "IMAGE#{image_id}"},
      "SK": {"S": "RECEIPT#{receipt_id:05d}"}
    },
    "OldImage": {
      // Full Receipt entity data
      "image_id": "...",
      "receipt_id": 1,
      ...
    }
  }
}
```

---

#### 3. **Enhanced Compactor: Delete Embeddings from ChromaDB**

**What Happens:**
1. **Stream Processor**:
   - Parses the stream event
   - Detects `RECEIPT` entity type from SK pattern: `RECEIPT#{receipt_id:05d}`
   - Creates a `StreamMessage` for receipt deletion
   - Targets both collections: `LINES` and `WORDS`

2. **Compactor Lambda**:
   - Downloads the **latest snapshot** from S3 (source of truth)
     - `lines/snapshot/latest/` - For lines collection
     - `words/snapshot/latest/` - For words collection
   - Opens ChromaDB collections from the snapshots

3. **Query DynamoDB for Lines/Words**:
   - **Important**: The compactor queries DynamoDB for ReceiptLine and ReceiptWord entities
   - This is why you should delete the Receipt **before** deleting lines/words (if possible)
   - If lines/words are already deleted, the compactor can't construct ChromaDB IDs
   - Constructs ChromaDB IDs:
     - Lines: `IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}`
     - Words: `IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#WORD#{word_id:05d}`

4. **Delete Embeddings from Collection**:
   - Calls `collection.delete(ids=chromadb_ids)` for both collections
   - This removes embeddings from the in-memory collection

5. **Upload Updated Snapshot to S3**:
   - Uploads the updated snapshot back to S3
   - This is the **source of truth** - the snapshot in S3 is now updated
   - The compactor is the only thing that modifies snapshots

**Why Query DynamoDB:**
- The compactor needs to know which embeddings to delete
- It constructs ChromaDB IDs from DynamoDB entities
- If entities are already deleted, it can't construct IDs (embeddings may be orphaned)

**Edge Case:**
- If you delete lines/words before the Receipt, the compactor won't find them in DynamoDB
- It will log a warning and return 0 deletions
- Embeddings may remain orphaned (expected behavior when deleting in reverse order)

---

### Complete Flow Diagram

```
┌─────────────────────────────────────────────────────────────┐
│ 1. DynamoDB: Delete Receipt Entities (Reverse Order)        │
│    - ReceiptWordLabel (first)                                │
│    - ReceiptWord                                             │
│    - ReceiptLine                                             │
│    - ReceiptLetter, ReceiptMetadata                          │
│    - CompactionRun                                           │
│    - Receipt (last)                                          │
└─────────────────────────────────────────────────────────────┘
                        ↓
┌─────────────────────────────────────────────────────────────┐
│ 2. DynamoDB Streams: Receipt Deletion Event                  │
│    - Receipt deleted → REMOVE event                          │
│    - Stream processor detects RECEIPT entity type            │
└─────────────────────────────────────────────────────────────┘
                        ↓
┌─────────────────────────────────────────────────────────────┐
│ 3. Enhanced Compactor: Delete Embeddings                    │
│    - Download snapshot from S3 (source of truth)            │
│    - Query DynamoDB for ReceiptLine/ReceiptWord entities     │
│    - Construct ChromaDB IDs from entities                    │
│    - Delete embeddings from collection                       │
│    - Upload updated snapshot to S3                           │
└─────────────────────────────────────────────────────────────┘
```

---

## 🔑 Key Concepts

### Realtime vs Batch Embedding

**Realtime Embedding** (Used in this lifecycle module):
- **Method**: Direct OpenAI API calls via `openai_client.embeddings.create()`
- **Timing**: Synchronous, immediate results
- **Process**:
  1. Call OpenAI Embeddings API directly
  2. Get embeddings back immediately
  3. Store in local delta collections
  4. Upload deltas to S3
  5. Create CompactionRun to trigger compaction
- **Use Case**: When you need embeddings immediately (e.g., split/combine receipts)
- **Code**: `embed_lines_realtime()`, `embed_words_realtime()`

**Batch Embedding** (Used in background processing):
- **Method**: OpenAI Batch API (asynchronous)
- **Timing**: Asynchronous, requires polling
- **Process**:
  1. Upload NDJSON file to OpenAI
  2. Submit batch job to OpenAI
  3. Poll for completion (can take minutes/hours)
  4. Download results when complete
  5. Process results and store to ChromaDB
- **Use Case**: Bulk processing of many receipts (cost-effective, but slower)
- **Code**: `submit_embedding_batch()`, `poll_embedding_batch()`

**This lifecycle module uses realtime embedding** for immediate results when creating receipts.

### Source of Truth

1. **DynamoDB**: Source of truth for structured data (Receipt, ReceiptLine, ReceiptWord, etc.)
2. **S3 Snapshots**: Source of truth for ChromaDB embeddings
   - Location: `{collection}/snapshot/latest/`
   - Only the enhanced compactor modifies these
   - Never manually modify snapshots

### Delta vs Snapshot

- **Deltas**: Incremental changes (small, fast to create)
  - Location: `{collection}/delta/{run_id}/`
  - Created when embeddings are generated
  - Temporary - merged into snapshots by compactor

- **Snapshots**: Full collection state (large, source of truth)
  - Location: `{collection}/snapshot/latest/`
  - Updated by compactor when merging deltas
  - Only source of truth for ChromaDB

### Compaction Process

1. **Trigger**: CompactionRun created in DynamoDB → Stream event
2. **Process**: Compactor downloads snapshot + deltas, merges them, uploads updated snapshot
3. **Result**: Snapshots are up-to-date with all embeddings

### Deletion Process

1. **DynamoDB**: Delete entities in reverse order (respects foreign keys)
2. **Streams**: Receipt deletion triggers compactor
3. **Compactor**: Queries DynamoDB for lines/words, constructs IDs, deletes from snapshots
4. **Important**: Compactor is the only thing that modifies ChromaDB snapshots

---

## ⚠️ Important Notes

1. **Never Manually Delete Embeddings**: The enhanced compactor is the source of truth. Don't download snapshots locally, modify them, and upload them back.

2. **Deletion Order Matters**:
   - For DynamoDB: Delete children before parents (reverse of creation)
   - For ChromaDB: Delete Receipt before lines/words (if possible) so compactor can query them

3. **Orphaned Embeddings**: If you delete lines/words before the Receipt, the compactor can't find them in DynamoDB and embeddings may remain orphaned. This is expected behavior.

4. **Compaction is Asynchronous**: Embeddings are created immediately, but compaction (merging into snapshots) happens asynchronously via DynamoDB streams.

5. **S3/EFS are Source of Truth**: The snapshots in S3/EFS are the authoritative source for ChromaDB. Local copies are temporary and should not be modified.

