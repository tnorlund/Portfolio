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
(Cloud → Persistent → Ephemeral fallback).

Quota/retry constants:
- `embedding/cloud_upsert.py:60` UPSERT_BATCH_SIZE=250 (:62 documents Cloud quotas); :92
  _THROTTLE_ERRORS=(RateLimitError, QuotaError); :536-570 per-record retry on batch
  rejection, :570 rate_limited_abort drop counter; :676 batch clamp max(1,min(batch,250))
- `data/chroma_client.py:169-207` _retry_with_backoff, MAX_RETRIES=4, MAX_DELAY=8.0s
  (applied :657-690, 739-744, 796, 832)
- `compaction/dual_write.py:60-108` CloudConfig.from_env() — None when
  CHROMA_CLOUD_ENABLED != "true"; ValueError if enabled without api_key/tenant/database
- Compaction tuning defaults HEARTBEAT_INTERVAL=60s / LOCK_DURATION=16min
  (`embedding_step_functions/.../compaction.py:50-53`); Pulumi overrides to 30s/3min on the
  compaction Lambda (`chromadb_compaction/components/lambda_functions.py:178-180`)

## 4. Consumer fringe: 14 Dockerfiles / Pulumi components

14 Dockerfiles `COPY receipt_chroma/`, each paired with a source_paths entry; pairing
asserted by `infra/components/test_docker_package_contexts.py:11-80` (CHROMA_IMAGE_CONTEXTS,
14 entries) — never run in CI, won't block, but goes stale.

| Component | source_paths | Verdict |
|---|---|---|
| chromadb_compaction | components/docker_image.py:103 | delete |
| embedding_step_functions | components/docker_image.py:104 | delete compact + normalize-batches lambdas |
| combine_receipts | infrastructure.py:391 | edit, ~30 lines |
| routes/word_similarity_cache_generator | infra.py:217 | **delete component** |
| routes/address_similarity_cache_generator | infra.py:205 | **delete component** |
| label_refresh_lambda | infrastructure.py:204 | **delete component** (aligns with pipeline-consolidation plan) |
| merge_receipt_lambda | infrastructure.py:228 | edit args+IAM, drop Step 11 (lambdas/merge_receipt.py:414-447) |
| resegment_receipt_lambda | infrastructure.py:202 | edit args+IAM, drop _embed_outputs (:1239-1281, callers :1650,:1874) |
| upload_images | infra.py:636 | edit; loses ingest embedding (container_ocr/handler/handler.py:444-473, ocr_processor.py:1387-1417) — **replaced by DDB vector write in new design** |
| mcp_server_lambda | infrastructure.py:272 | cheapest — 4/60 tools gated at :75-82, already degrades (:6743-6749) |
| qa_agent_step_functions | infrastructure.py:319 | **product decision** — guts semantic search (new design: port to SearchVectors) |
| fix_place_lambda | infrastructure.py:217 | edit; Tier-3 fallback only (lambdas/fix_place.py:150-169), dev already defaults `tiered` |
| label_evaluator — unified | infrastructure.py:477 | already Optional=None (:77-78), already degrades (:1024-1027) |
| label_evaluator — pattern_builder | infrastructure.py:522 | **dead weight: no chroma env vars, no chroma calls, 10240MB Lambda (:498)** |

False positive: `routes/label_validation_viz_cache/` — "chroma" is a LangSmith trace-name
label only (lambdas/cache_generator.py:158, :341-342, :461-467). No Pulumi change; its
Tier-1 dashboard panel will silently render zeros post-removal.

Reclaimable ephemeral_storage=10240 (all chroma-justified): word_similarity infra.py:224,
address_similarity infra.py:212, merge_receipt infrastructure.py:211, resegment
infrastructure.py:211, label_evaluator infrastructure.py:441, qa_agent infrastructure.py:295;
upload_images at 4096 (infra.py:571).

### CI touchpoints — .github/workflows/main.yml

No job builds a chroma image; CodeBuild is triggered by `pulumi up` in deploy (:392-399,
the only Pulumi deploy, prod only).
- :42-49 py_compile over all infra/**/*.py
- :62-68 pytest infra/tests/test_compaction_lock_config.py — only CI step exercising infra tests
- :79 python-tests matrix entry receipt_chroma
- :107-113 that leg's install — note bare `chromadb` install **ignores the <1.6.0 pin** in receipt_chroma/pyproject.toml:26
- :136,:140 receipt_upload leg installs receipt_chroma editable + chromadb
- :151,:155 receipt_agent leg, same
- :184-186 lint: pip install -e receipt_chroma[lint], black/isort --check over receipt_chroma/
- :191 --reruns 1 justified in-comment by "ChromaDB lock contention under pytest-xdist"
- :194-198 cd receipt_chroma && pytest tests -n auto
- :230,:234 repository-tests installs --no-deps -e receipt_chroma + chromadb
- :263-265 pytest tests scripts/test_*.py — collects tests/test_receipt_mcp_lazy_chroma.py
  (stubs the module :59-78) and scripts/test_qa_{agent,enhanced,marquee_questions}.py
  (real `from receipt_chroma import ChromaClient`)
- :377-433 deploy job: the only Pulumi deploy (pulumi up --yes on tnorlund/portfolio/prod) —
  what triggers every chroma CodeBuild image build
- **Not covered by any job**: infra/chromadb_compaction/tests/, lambdas/tests/,
  infra/components/test_docker_package_contexts.py (despite infra/pyproject.toml:131)

Delete-with-no-other-edits → **red**: lambda-syntax, python-tests
(receipt_chroma|receipt_upload|receipt_agent), repository-tests, deploy.
**Green**: python-tests (receipt_dynamo|receipt_dynamo_stream|receipt_places|receipt_langsmith),
typescript-tests, browser-tests, swift-ci.yml. **Skip**: smoke-tests (needs deploy).

No chroma GitHub secrets/variables — only AWS_* (:371-372,:457-458) and
PULUMI_ACCESS_TOKEN (:384,:399,:433,:459).

## 5. Cross-references / exports that break on deletion

### A. Import-time — fails `pulumi preview` before any resource diffing

1. `infra/chromadb_buckets.py` — module-scope import by `__main__.py:132`,
   `routes/word_similarity_cache_generator/infra.py:10`,
   `routes/address_similarity_cache_generator/infra.py:11`; calls pulumi.export at import (:27-28).
2. `routes/address_similarity_cache_generator/infra.py:288-290` self-instantiates at import,
   exports cache_bucket_name (:296) → consumed by `routes/address_similarity/infra.py:12`,
   reached via `api_gateway.py:7`. Deleting the file breaks GET /address_similarity at
   preview time, not deploy time.
3. `embedding_step_functions/infrastructure.py:16-18` imports ChromaDBBuckets from
   chromadb_compaction at module scope — fallback branch :71-78 unreachable since
   `__main__.py:208` always passes buckets. Remove import in the same change.
4. Four `require_secret("CHROMA_CLOUD_API_KEY")` calls: fix_place_lambda/infrastructure.py:47,
   label_refresh_lambda/infrastructure.py:40, mcp_server_lambda/infrastructure.py:42,
   qa_agent_step_functions/infrastructure.py:42. **Strip before removing the config key**,
   or both stacks fail preview.
5. `embedding_step_functions/unified_embedding/handlers/__init__.py:8-15` eagerly imports
   compaction → `from chromadb.errors import NotFoundError` (compaction.py:18) + module-level
   DynamoClient (:47) — forces chromadb into every container Lambda's cold start.
6. Module-scope `os.environ["CHROMADB_BUCKET"]` (KeyError at import):
   routes/word_similarity_cache_generator/lambdas/index.py:34,
   routes/address_similarity_cache_generator/lambdas/index.py:21.

### B. Required-kwarg TypeErrors — exactly four

| Component | Params | Caller |
|---|---|---|
| merge_receipt_lambda/infrastructure.py:69-70,:260-261 | bucket name + arn | __main__.py:1264-1265 |
| resegment_receipt_lambda/infrastructure.py:42-43,:244-245 | bucket name + arn | __main__.py:1279-1280 |
| routes/word_similarity_cache_generator/infra.py:36,:303 | bucket name | __main__.py:296-297 |
| routes/address_similarity_cache_generator/infra.py:38,:276 | bucket name | self (infra.py:288) |

Safe to just drop the kwarg (already Optional=None):
label_evaluator_step_functions/infrastructure.py:77-78 (caller __main__.py:1393-1394),
upload_images/infra.py:64 (caller __main__.py:346).

### C. Stack exports — __main__.py:1086-1110

`chromadb_bucket_name` (exported TWICE — also chromadb_buckets.py:27),
`chromadb_lines_queue_url`, `chromadb_words_queue_url`, `stream_processor_function_arn`,
`enhanced_compaction_function_arn`, `embedding_chromadb_bucket_name`,
`embedding_chromadb_bucket_arn`. Verify no `pulumi stack output` consumer (Swift worker
config loader, scripts) reads these before dropping.

### D. Must relocate, not delete — inside chromadb_compaction, tagged Project=ChromaDB, zero chroma code

- `chromadb-{stack}-summary-updater` (components/lambda_functions.py:440-477) + ESM :480-488
- `chromadb-{stack}-line-item-updater` (:612-670) + ESM :723-734 + two inline RolePolicies
  (trigger-reocr invoke, OCR-queue send, :675-721)
- summary_queue/summary_dlq (sqs_queues.py:222,:206); line_item_queue/line_item_dlq (:259,:245)
- upload_images consumes summary_queue_url/arn — __main__.py:352-353 →
  upload_images/infra.py:399,480-481,587. Its post-re-OCR summary recompute breaks despite
  having nothing to do with vectors.

### E. Runtime signature breaks (Python, not Pulumi)

- `create_qa_graph(chroma_client=...)` required — receipt_agent/agents/question_answering/graph.py:854-857
- `create_receipt_place_finder_graph(chroma_client=...)` required, docstring claims
  None-tolerant — agents/place_finder/graph.py:404-417 (verify)
- `MerchantResolvingEmbeddingProcessor(chromadb_bucket=...)` required —
  receipt_upload/merchant_resolution/embedding_processor.py:1439
- merge_receipt.py:139-140 returns an error **dict** (not exception) when CHROMADB_BUCKET
  unset — delete the guard, not just the env var, or every merge refuses
- resegment_receipt.py:1806 — os.environ["CHROMADB_BUCKET"] inside handler, KeyError at invoke
- upload_images/container_ocr/handler/handler.py:446 — bracket access, KeyError; :380 uses .get(...,"")

### F. The receipt_chroma split problem

`receipt_chroma.embedding.openai` and `.embedding.formatting` contain **no chromadb imports**
(only embedding/delta/producer.py:18,97,108 does). Four surviving embedding container
Lambdas + submit_openai.py:23-30 + submit_words_openai.py:19-22 import OpenAI-batch and
text-layout helpers from there — those images keep chromadb purely to reach non-vector code
until the two subpackages are relocated. `embedding/formatting/__init__.py:32-55` is the
surface the Swift port mirrors (SectionAssignment.swift:5), so **Swift parity fixtures are
downstream of that move** (anti-drift CI gate applies).

### G. Dead code, free to delete

- infra/chromadb_compaction/lambdas/processor/ — only importer is uncollected test_lambda_imports.py:68
- embedding_step_functions/unified_embedding/utils/dual_chroma_client.py — zero importers
  (live DualChromaClient is unrelated: receipt_agent/clients/factory.py:125)
- embedding_step_functions/simple_lambdas/{prepare_chunk_groups,prepare_merge_pairs,split_into_chunks,create_chunk_groups}/ —
  not wired to any Pulumi resource (absent from zip_configs); all four read CHROMADB_BUCKET
- infra/components/test_docker_package_contexts.py — never run in CI; goes stale the moment
  any Dockerfile drops COPY receipt_chroma/
- embedding_step_functions/.../tests/test_close_chromadb_client.py + standalone_test_close_client.py
- embedding_step_functions/components/monitoring.py:269-345,:464-540 — superseded _create_*
  widget builders, never called
- infra/chromadb_compaction/tests/conftest.py.bak — stray backup
- Orphaned metric/trace emitters once compaction goes: unified_embedding/utils/metrics.py:362-365
  (track_chromadb_operation), utils/tracing.py:309-312,372-382, utils/circuit_breaker.py:381-416
  (chromadb_circuit_breaker, protect_chromadb_call); dashboard widget monitoring.py:653-658
- Docs: infra/fix_chromadb_buckets.md, infra/README_CHROMADB_METRICS.md,
  infra/VALIDATION_PIPELINE_CHROMADB_MIGRATION.md, infra/chromadb_compaction/{README.md,
  README_stream_processor.md,QUEUE_STRATEGY.md,get_chromadb_metrics.sh},
  infra/embedding_step_functions/{MEMORY_OPTIMIZATION.md,MIGRATION_COMPLETE_SUMMARY.md,
  WORD_INGEST_MIGRATION_GUIDE.md,WORKFLOW_STEPS_REFERENCE.md}, receipt_chroma/README.md
  (linked from README.md:194); prose refs CLAUDE.md:422,633, AGENTS.md:11,
  pull_request_template.md:21

⚠️ **Sequencing note that outranks the list**: `aliases=[self._legacy_container_lambda_urn(name)]`
at `embedding_step_functions/components/lambda_functions.py:459,465-476` — deleting a
container Lambda must drop its legacy-URN alias in the same change, or Pulumi may attempt
adopt-then-delete on a resource that no longer exists.

## Agent's closing summary (teardown rules)

- **Pure delete**: infra/chromadb_compaction/ (minus two updaters + two queues),
  infra/chromadb_buckets.py, routes/word_similarity_cache_generator/,
  routes/address_similarity_cache_generator/, label_refresh_lambda/,
  embedding-compact + embedding-normalize-batches Lambdas.
- **Relocate first** or non-vector production breaks: summary-updater, line-item-updater,
  summary_queue, line_item_queue; prune-not-delete CHROMADB_RELEVANT_FIELDS
  (receipt_dynamo_stream/change_detection/detector.py:9-36).
- **Keep the NAT** until upload_images is un-VPC'd (__main__.py:340).
- **Order-of-operations**: strip the four require_secret calls BEFORE removing the Pulumi
  config key; relocate receipt_chroma.embedding.{openai,formatting} BEFORE dropping
  receipt_chroma from the four surviving embedding images.
- **Corrections**: PR #1400 unmerged (compaction Lambda still 10240MB/10240MB, paid
  interface endpoints live); no ECS Chroma service exists.
