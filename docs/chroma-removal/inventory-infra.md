# Inventory: Chroma infrastructure (Pulumi)

Compiled 2026-08-31 by mapping agent (chroma-infra) against origin/main @ 1b6540c81.

## Headline

Three groups: a **pure-chroma core** (compaction stack, vector buckets, NAT/VPC that
exists to feed it), a **wide consumer fringe** (14 Dockerfiles, ~13 Pulumi components),
and — the trap — **two non-chroma Lambdas and two non-chroma SQS queues living inside
the chroma component that would die with a naive teardown.**

Corrections to prior assumptions:
- **PR #1400 is NOT merged.** Commit 8984c383f exists only on `chore/aws-cost-reduction`.
  On main the compaction Lambda is still 10240 MB / 10240 MB ephemeral
  (`infra/chromadb_compaction/components/lambda_functions.py:143-145`) and the paid VPC
  interface endpoints still exist. (Spec implication: removal supersedes #1400 —
  don't bother landing the right-size first unless removal is slow.)
- **No ECS Chroma service exists.** Removed when Chroma Cloud became the query target;
  `infra/security.py:11-15` documents it. Only two security groups survive.

## 1. Pulumi resources

### Compaction core — `infra/chromadb_compaction/`

Entry: `infra/__main__.py:204-212` → `ChromaDBCompactionInfrastructure`
(`infrastructure.py:20`). Instance name `chromadb-{stack}` → resources `chromadb-dev-*` /
`chromadb-prod-*`.

**The expensive one**: `chromadb-{stack}-docker-{stack}` (`components/docker_image.py:105`),
enhanced compaction handler:
- memory 10240, ephemeral 10240, timeout 900, reserved_concurrency 4 (`lambda_functions.py:138-160`)
- two ESMs, batch_size 1000, maximum_concurrency 2 (`lambda_functions.py:960-987`)
- container image, arm64, VPC-attached (`lambda_functions.py:216-219`) — only VPC-attached
  Lambda in the compaction stack; ~96% of Lambda spend.

**SQS** (`components/sqs_queues.py`), all `chromadb-{stack}-queues-*`:

| Queue | Chroma? | Line |
|---|---|---|
| -lines-queue + -lines-dlq | yes | :148, :119 |
| -words-queue + -words-dlq | yes | :181, :133 |
| -summary-queue + -summary-dlq | **NO — must survive** | :222, :206 |
| -line-item-queue + -line-item-dlq | **NO — must survive** | :259, :245 |

**S3**: `ChromaDBBuckets` (`components/s3_buckets.py:18`), instantiated at
`infra/chromadb_buckets.py:19-21` as `chromadb-{stack}-shared-buckets` → bucket
`{name}-vectors`, force_destroy=True, versioning, AES256, 7 lifecycle rules
(delta/ 7d, words/delta/ 7d, lines/delta/ 7d, intermediate/ 1d, temp/ 1d,
llm-validation/ 14d, noncurrent 1d).

**Other Lambdas in the same component:**
- `chromadb-{stack}-stream-processor` — zip, py3.13, arm64, 256MB, 120s, no VPC
  (`lambda_functions.py:250-379`). Cheap. **Not purely chroma — see §2.**
- `chromadb-{stack}-summary-updater` — zip, 256MB, 60s (`lambda_functions.py:440-477`). Zero chroma code.
- `chromadb-{stack}-line-item-updater` — zip, 512MB, 120s (`lambda_functions.py:612-670`). Zero chroma code.

**Alarms** (`components/alarms.py`): CompactionLockAcquisitionFailed :66,
CompactionSnapshotUploadError :88, CompactionDeltaMergeError :110 — EMF namespace
`EmbeddingWorkflow`, wired to notification_system.critical_error_topic_arn.

**ECR/CodeBuild** (`components/docker_image.py:98-104`): dockerfile
`infra/chromadb_compaction/lambdas/Dockerfile`, context repo root,
source_paths=[receipt_dynamo, receipt_dynamo_stream, receipt_chroma]; lifecycle policy :169,
repo policy :178.

### Networking — second real cost driver

`infra/chroma/nat_egress.py` (`NatEgress`, imported `__main__.py:87`, instantiated ~:195):
- :100-137 t4g.nano NAT instance (AL2 ARM64, iptables MASQUERADE user_data)
- :139-144 Elastic IP
- :28-41 two private subnets (10.0.101.0/24, 10.0.102.0/24); :44-61 route table + assoc; :147-153 default route → NAT ENI
- :64-85 NAT security group

VPC endpoints in `__main__.py`:

| Endpoint | Line | Billed |
|---|---|---|
| s3-gateway-{stack} | :229-236 | free |
| dynamodb-gateway-{stack} | :239-246 | free |
| logs-interface-{stack} | :259-267 | **hourly + per-GB** |
| sqs-interface-{stack} | :279-287 | **hourly + per-GB** |

Both gateways list `nat.private_rt.id` in route_table_ids — deleting NAT forces editing them.

⚠️ **NAT does not die with chroma.** `nat.private_subnet_ids` consumers: compaction
(`__main__.py:202,209`), embedding SF (:220), word_similarity_cache_generator (:298),
**upload_images (:340)** — the last is unrelated to chroma and alone keeps the NAT alive.

`ChromaSecurity` (`infra/security.py:9`, instantiated `__main__.py:101`) — misnamed,
generic: sg_lambda :26 + sg_vpce :47; sg_lambda_id consumed at `__main__.py:210,221,299,348`;
sg_vpce_id at :266,286. **Rename, don't delete.** Docstring :11-15 confirms ECS Chroma gone.

## 2. Stream processor: non-chroma work that must survive

Live logic is **`receipt_dynamo_stream/`**, not `infra/chromadb_compaction/lambdas/processor/`
(that dir is dead; only importer is uncollected `lambdas/tests/test_lambda_imports.py:68`).
Imported at `lambdas/stream_processor.py:42-49`.

Routing — `receipt_dynamo_stream/receipt_dynamo_stream/message_builder.py`:

| Entity | Targets | Line |
|---|---|---|
| COMPACTION_RUN INSERT | lines + words (1 msg each) | :86-149 |
| RECEIPT_PLACE | lines, words, **RECEIPT_SUMMARY** | :230-245 |
| RECEIPT_WORD_LABEL | words, lines, **RECEIPT_SUMMARY** | :248-269 |
| RECEIPT_SECTION | lines, **+ LINE_ITEMS if section_type==ITEMS** | :290-315 |
| RECEIPT_SUMMARY | **LINE_ITEMS only** | :272-287 |
| RECEIPT / RECEIPT_WORD / RECEIPT_LINE | lines/words only | :318-348 |

Queue fan-out by env var: `sqs_publisher.py:59-93` → LINES_QUEUE_URL, WORDS_QUEUE_URL,
RECEIPT_SUMMARY_QUEUE_URL, LINE_ITEM_QUEUE_URL. Enum TargetQueue `models.py:37-43`.

**Must survive:** RECEIPT_SUMMARY → line-items, and PLACE/WORD_LABEL → summary. These drive
ReceiptSummary recompute + RECEIPT_LINE_ITEM rewrites incl. LINE_ITEM_REFINE Tier-3
(`lambda_functions.py:632-647`, flag `chromadb:enable-line-item-refine` = "true" both stacks —
**flag is chroma-named but not chroma**).

⚠️ **Trap:** all non-COMPACTION_RUN messages pass `get_chromadb_relevant_changes()`
(`message_builder.py:173`), dropped when empty and not REMOVE (:183-184). The allowlist
`CHROMADB_RELEVANT_FIELDS` (`change_detection/detector.py:9-36`) contains
`RECEIPT_SUMMARY: ["timestamp_computed"]` and `RECEIPT_SECTION: [...]` — deleting it
silently kills line-item recompute. **Rename + prune, never delete.**
Also `_INSERT_SYNCED_ENTITY_TYPES = {"RECEIPT_SECTION","RECEIPT_SUMMARY"}`
(`message_builder.py:51`) — INSERT path is non-chroma-load-bearing.

## 3. Chroma Cloud config/secrets

Namespace `portfolio`, per stack:

| Key | dev | prod |
|---|---|---|
| CHROMA_CLOUD_API_KEY (encrypted) | Pulumi.dev.yaml:57-58 | Pulumi.prod.yaml:46-47 |
| CHROMA_CLOUD_TENANT | :59 | :48 |
| CHROMA_CLOUD_DATABASE (receipt_dev/receipt_prod) | :60 | :49 |
| CHROMA_CLOUD_ENABLED ("true") | :61 | :50 |

`chromadb:enable-line-item-refine` (dev:64/prod:57) is **not chroma** despite the namespace.
**No GitHub secrets** — only AWS_* + PULUMI_ACCESS_TOKEN in .github/.

Client construction: `receipt_chroma/data/chroma_client.py:474-529`
(Cloud → Persistent → Ephemeral fallback). Quotas: `embedding/cloud_upsert.py:60`
UPSERT_BATCH_SIZE=250, [tail pending from agent].

## 4. Consumer fringe: 14 Dockerfiles, ~13 Pulumi components

[pending from agent]

## 5. Deletion cross-references / exports

[pending from agent]
