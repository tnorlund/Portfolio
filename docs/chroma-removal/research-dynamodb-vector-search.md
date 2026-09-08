# Research: DynamoDB Native Vector Search (target architecture)

Verified 2026-08-31 against AWS docs. Feature went **GA 2026-08-05**, all commercial regions.

## What it is

A new index type (`VectorIndexes` on `CreateTable`, `VectorIndexUpdates` on
`UpdateTable`) over an item attribute holding a `List` of `Number` floats.
Queried via the `SearchVectors` API (ANN, similarity-ranked). No separate
datastore, no replication pipeline — this is the feature that makes Chroma
(and its entire sync machinery) unnecessary.

## Facts that constrain the spec

| Fact | Implication for Portfolio |
| --- | --- |
| Max **5 vector indexes** per table | words + lines collections = 2 indexes; fine |
| **On-demand capacity mode only** | ✅ verified 2026-08-31: dev `ReceiptsTable-dc5be22` and prod `ReceiptsTable-d7ff76a` are both PAY_PER_REQUEST |
| Up to **4,096 dims** | OpenAI 1536-dim embeddings fit |
| Distance function **immutable** after creation | choose COSINE (OpenAI text embeddings) and get it right first |
| SearchSchema: at most **1 partition key** (HASH), low/medium cardinality recommended | merchant name is the natural scope for word-similarity queries |
| `INLINE_FILTER` attrs: **equality only** (no ranges/IN yet) | Chroma metadata (valid/invalid/pending label arrays, 32-key cap pain) must flatten to exact-match attrs |
| Partition key value **required** in `SearchConditionExpression` if defined | corpus-wide searches need either no PK on the index, or a fan-out |
| Top **100** results per SearchVectors call | current consumers paginate at ≤250 on Chroma anyway; check each consumer |
| Indexing is **asynchronous** after write (even single-region) | no read-after-write; spec must state eventual searchability. Still far faster than delta→SQS→compaction→Cloud today |
| Projections: KEYS_ONLY / INCLUDE / ALL; INCLUDE set **immutable** | decide projected attrs up front; SearchVectors returns only projected attrs |
| Streams unaffected; TTL removes index entries; PITR rebuilds index (backfill wait) | existing stream processor keeps working; restore runbook needs an index-ACTIVE wait |
| DAX does not support SearchVectors | N/A (no DAX in Portfolio) |
| Pay-per-request billing | replaces Chroma Cloud subscription + ~$240/mo compaction Lambda spend |
| boto3 **≥ 1.43.64** | version-bump needed: local dev machine has boto3 1.43.53 and aws-cli 2.31.29, **neither supports search-vectors yet** (verified 2026-08-31); moto almost certainly can't mock SearchVectors → test strategy needed |
| IaC support | verify Pulumi aws provider supports `VectorIndexes`; escape hatch = aws CLI/boto3 `UpdateTable` until it does |

## Open questions to resolve during spec synthesis

1. **Where vectors live**: inline on RECEIPT_WORD/RECEIPT_LINE items (inflates
   every read of hot items by ~6–12KB of float list) vs. dedicated
   `EMBEDDING#` items keyed to word/line. Weigh RCU cost, item-size, and the
   projection that SearchVectors needs.
2. **Partition key choice** per index (merchant? label? none?) — driven by the
   read-path inventory of actual query shapes.
3. **Which consumers need similarity at all** vs. exact lookups a GSI already
   serves (read-path agent reporting).
4. Embedding generation stays OpenAI at ingest, or move to Bedrock? (Cost/api
   key surface question; default: keep OpenAI, unchanged.)

## Sources

- https://aws.amazon.com/about-aws/whats-new/2026/08/amazon-dynamodb-vector-search/
- https://aws.amazon.com/blogs/aws/amazon-dynamodb-now-supports-real-time-vector-search-at-any-scale/
- https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/VectorSearch.html
