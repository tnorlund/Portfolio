# Inventory: Chroma/embedding write path

Compiled 2026-08-31 by mapping agent (chroma-write-path) against origin/main @ 1b6540c81.

## Headline: three write paths, and Chroma is load-bearing inside ingest decisions

Two ingest *algorithms* — **merchant resolution** and **Tier-1 label validation** — query
Chroma inline during `process_ocr` and decide what gets written to DynamoDB. Removing
Chroma means replacing those algorithms' retrieval, not just deleting a store.

The three write paths:

1. **Delta → compaction** (the documented one): payload → local delta tarball →
   `s3://chromadb-{env}-shared-buckets-vectors-*/{lines,words}/delta/{run_id}` →
   `CompactionRun` row → DynamoDB stream → SQS → compaction Lambda merges into the S3
   snapshot and dual-writes Cloud.
2. **Direct Cloud upsert at ingest** — `receipt_chroma/receipt_chroma/embedding/cloud_upsert.py`
   (808 lines, PR #1273). Writes vectors straight to Chroma Cloud right after the delta is
   durable (queryable in seconds). Called from
   `receipt_upload/receipt_upload/merchant_resolution/embedding_processor.py:353`
   (`_upsert_to_cloud_nonfatal`). **The delta path is now the backstop, not the primary
   route to Cloud.**
3. **Ephemeral local Chroma clients** — `create_embeddings_and_compaction_run` returns live
   `lines_client`/`words_client` over snapshot+delta merged directories so the *same Lambda
   invocation* can query them. This is what merchant resolution and label validation consume.
   ⚠️ Key constraint for the DynamoDB design: vector indexing is async, so same-invocation
   read-after-write via SearchVectors is NOT guaranteed — the replacement for these inline
   queries must either query pre-existing vectors only (fine: they query the *corpus*, not
   the just-written receipt) or use in-memory similarity over fetched vectors.

## 1. Producers

**Real orchestrator lives in receipt_upload, not receipt_chroma:**
`MerchantResolvingEmbeddingProcessor` at
`receipt_upload/receipt_upload/merchant_resolution/embedding_processor.py:1425`, called from
`infra/upload_images/container_ocr/handler/handler.py:444`. Two parallel worker pipelines
(`_run_lines_pipeline_worker:645`, `_run_words_pipeline_worker:1005`) that embed, resolve
merchant by vector similarity, validate labels by neighbor consensus, upload deltas, create
the CompactionRun, and upsert to Cloud.

Library primitive: `receipt_chroma/receipt_chroma/embedding/orchestration.py:962`
`create_embeddings_and_compaction_run` (steps at :1-13; `_upload_delta:887`;
`create_compaction_run:752`).

Other producers (same library):
- `infra/upload_images/container_ocr/handler/ocr_processor.py:1382-1400` — re-embed after smart re-OCR
- `infra/merge_receipt_lambda/lambdas/merge_receipt.py:103-106` (hard-fails if CHROMADB_BUCKET unset, :131-140)
- `infra/resegment_receipt_lambda/lambdas/resegment_receipt.py:1246`
- `infra/combine_receipts_step_functions/lambdas/embedding_utils.py:77-83`
- `receipt_upload/receipt_upload/font_letter_analysis.py:446` — `upsert_letter_samples_to_chroma`,
  a **separate letter-style-vector collection** unrelated to lines/words. Easy to miss.

**Collections & schema.** Two collections, `lines` and `words`
(`receipt_dynamo/receipt_dynamo/constants.py:171-175`). IDs:
`IMAGE#{uuid}#RECEIPT#{id:05d}#LINE#{id:05d}[#WORD#{id:05d}]`
(`receipt_chroma/embedding/records.py:40-107`). Metadata schemas:
`embedding/metadata/word_metadata.py:74-88`, `line_metadata.py:76-89`, `create_row_metadata:138`
(row_line_ids as JSON string). Label enrichment adds `label_status`, `valid_labels_array`,
`invalid_labels_array`, `label_confidence`, `label_proposed_by`, `label_validated_at`
(`word_metadata.py:94-180`) — the array-overlap structure the agent's label queries filter on.
⚠️ Array-valued metadata won't map to DynamoDB INLINE_FILTER equality — needs flattening or
a different query strategy.

## 2. Compaction pipeline

| Piece | Location |
|---|---|
| Public API | `receipt_chroma/compaction/__init__.py:26-42` |
| Coordinator | `receipt_chroma/compaction/processor.py:17` `process_collection_updates` |
| Dual-write | `compaction/dual_write.py:190` `apply_collection_updates`; `CloudConfig.from_env:60`; bulk sync `:473` |
| Delta merge | `compaction/deltas.py` `merge_compaction_deltas` |
| Per-entity appliers | `compaction/labels.py`, `metadata.py` (place), `sections.py`, `deletions.py` |
| Message ordering | `compaction/message_ordering.py` `sort_and_deduplicate_messages` |
| Snapshot I/O | `s3/snapshot.py:34` upload, `:192` download, `:342` init-empty |
| Lock | `lock_manager.py:22` — DynamoDB lock + heartbeat thread |

Handler: `infra/chromadb_compaction/lambdas/enhanced_compaction_handler.py` —
`process_collection:305` → CHROMADB_BUCKET :328 → LockManager :344 →
`download_snapshot_atomic` :378 → `apply_collection_updates` :410 →
`upload_snapshot_atomic` :447.

Message production: `receipt_dynamo_stream/message_builder.py:90` (one message per
collection on COMPACTION_RUN INSERT), fan-out `sqs_publisher.py:43-73`.
Apply order fixed at `processor.py:28-33`: delta merges → place → label → section updates.

**Three infra traps** (corroborates infra agent):
1. `ChromaDBQueues` also owns summary-queue and line-item-queue + updaters
   (`components/lambda_functions.py:428-488`, `:600-734`) — pure line-item logic, no
   vectors; `__main__.py:352-353` wires the summary queue into UploadImages. Extract, don't delete.
2. `infra/security.py:9` ChromaSecurity and `infra/chroma/nat_egress.py` are Chroma-named
   but supply SGs and NAT for EVERY VPC Lambda. Rename at most.
3. `infra/chromadb_buckets.py:19-28` creates the bucket as an **import side effect**;
   imported by `__main__.py:132` AND `routes/address_similarity_cache_generator/infra.py:11`.

**Second, parallel compaction implementation**:
`infra/embedding_step_functions/unified_embedding/handlers/compaction.py` (3000+ lines),
driven by the `embedding-compact` Lambda, writing the same bucket (dormant batch pipeline).

## 3. DynamoDB entities existing purely for this pipeline

- **CompactionRun** — `entities/compaction_run.py:29`, accessor `data/_compaction_run.py`.
  PK=IMAGE#{id}, SK=RECEIPT#{id:05d}#COMPACTION_RUN#{run_id}, GSI1PK=RUNS. Its INSERT is
  the stream trigger for the whole pipeline.
- **CompactionLock** — `entities/compaction_lock.py:18`, accessor `data/_compaction_lock.py`.
  PK=LOCK#{collection}#{lock_id}, SK=LOCK.
- **CompactionState**, **ChromaDBCollection** enums — `constants.py:178-185`, `:171-175`.
- **EmbeddingStatus** enum — `constants.py:73-81`; field on shared base
  `entities/receipt_text_geometry_entity.py:51`.

⚠️ **`embedding_status` is the trap: it is the entire GSI1PK on both ReceiptWord
(`entities/receipt_word.py:113`) and ReceiptLine (`entities/receipt_line.py:83`)** — the two
highest-cardinality entity types in the table. Only functional consumers are the dormant
batch handlers (`find_unembedded_words.py:95`, `find_unembedded.py:86`, pollers flipping
PENDING→SUCCESS). The live ingest path never sets it. Dead weight but structurally
load-bearing for GSI1 — removal = full-table migration, deserves its own phase (or an
explicit keep-and-freeze decision).

## 4. OpenAI embedding call sites and non-Chroma uses

One model everywhere: `text-embedding-3-small` (constant `embedding/orchestration.py:60`,
overridable via OPENAI_EMBEDDING_MODEL :1007).
- Live path: `receipt_chroma/embedding/openai/realtime.py:35` `embed_texts`.
- Batch path (dormant): `receipt_chroma/embedding/openai/{submit,poll,batch_status}.py` — OpenAI Batch API.

Non-receipt_chroma call sites — all feed a Chroma write or query, **none independent**:
- `receipt_agent/clients/factory.py:402` `create_embed_fn` — embeds query text for Chroma search
- `receipt_agent/agents/place_validator.py:169` — same
- `receipt_upload/merchant_resolution/resolver.py:820,1871` `_generate_embedding` → queries `lines`

Cost accounting: `receipt_langsmith/receipt_langsmith/spark/label_validation_processor.py:40`.

⚠️ **Dormancy: the entire `infra/embedding_step_functions/` tree is deployed but
untriggered.** `embed_all` is exported (`infra/__main__.py:225`) but never invoked; no
EventBridge rule triggers any embedding SF. Last functional change PR #1268. 6 zip Lambdas,
5 container Lambdas at 8GB, 3 state machines, an ECR repo, a dashboard, and the second
compaction implementation — all idle. **Cheapest first deletion, and it takes
embedding_status's only consumer with it.**

## 5. Dependency declarations

| File:line | Declaration | Kind |
|---|---|---|
| receipt_chroma/pyproject.toml:26 | chromadb>=1.5.0,<1.6.0 | main dep of package being removed |
| receipt_agent/pyproject.toml:35 | receipt-chroma | **main dep** |
| receipt_upload/pyproject.toml:32 | receipt-chroma | **main dep** |
| infra/chromadb_compaction/lambdas/pyproject.toml:19 | receipt-chroma | extra (`full`); :22 lists receipt-label[full] — stale, package gone |
| tools/glyph-studio/py/pyproject.toml:20 | receipt-chroma | extra (`sections`), deliberately optional |

No requirements.txt declares it. Lambdas get it via `pip install /tmp/receipt_chroma`
across **14 Dockerfiles** in infra/ (compaction, unified_embedding, combine_receipts,
container_ocr, merge_receipt, resegment, fix_place, label_refresh, mcp_server, qa_agent,
two label_evaluator variants, both similarity cache generators). CI installs in 5 places in
main.yml (:79, :107-111, :136-140, :151-155, :230-234).

## Inline ingest-time query shapes (the substantive replacement constraint)

**Merchant resolution** — `receipt_upload/merchant_resolution/resolver.py:1344`.
Collection `lines`; n_results=20; **no where filter — unscoped nearest-neighbor over the
whole corpus** (current receipt discarded in post-processing). Includes
metadatas/distances/documents. Feeds three tiers: chroma_phone :1062, chroma_address :1094,
chroma_text :1125 (weakest, corroboration-gated :1127-1150).

**Tier-1 label validation** — `receipt_upload/label_validation/validator.py`.
Collection `words`; n_results=10 per label (:277; two queries :230-266 → 20 max); filtered
`where={"$and": [{"label_status": "validated"}, {label_field: ...}]}` (:181-186, :311-317);
includes metadatas/distances; similarity-threshold cut + confidence decision (:324, :466).
Queries the **corpus** (previously-validated words from other receipts as consensus pool);
reads the current receipt's own embeddings only from an in-memory cache (:128-133) to avoid
a redundant fetch.

**Both need a whole-corpus nearest-neighbor index at ingest time** — a per-receipt or
recent-window structure serves neither. Both query pre-existing corpus vectors, so
DynamoDB's async indexing delay is acceptable (a just-ingested receipt not yet being
searchable doesn't affect its own resolution). Mapping to SearchVectors:
- merchant resolution: unscoped query → vector index with NO partition key (or fan-out); top-20 ≤ 100 cap ✓
- label validation: equality filters → label_status works as INLINE_FILTER; the per-label
  field filter hits the array-metadata problem (needs flattening or post-filter in code)

**Schema discrepancy to settle in the spec**: the validator filters on flat per-label
metadata keys — `label_{LABEL}: True` at `validator.py:305-312`, and a
`label_field: label_value` form at `:181-186` — while the writer emits array-valued
`valid_labels_array`/`invalid_labels_array` at
`receipt_chroma/embedding/metadata/word_metadata.py:158-163`. Both forms are live in code;
the replacement design must determine which the corpus actually carries.
(Spec-relevant: the flat `label_{LABEL}: True` form maps DIRECTLY onto DynamoDB
INLINE_FILTER equality — standardizing on it solves the array-filter problem.)
