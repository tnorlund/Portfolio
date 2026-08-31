# Spec: Remove ChromaDB — collapse vector search into DynamoDB

Status: DRAFT for review · Branch: `claude/chroma-removal-spec` · 2026-08-31
Companion docs (full evidence, file:line):
[research-dynamodb-vector-search.md](research-dynamodb-vector-search.md) ·
[inventory-write-path.md](inventory-write-path.md) ·
[inventory-read-path.md](inventory-read-path.md) ·
[inventory-infra.md](inventory-infra.md) ·
[inventory-tests-ci-scripts.md](inventory-tests-ci-scripts.md)

## 1. Summary

Replace Chroma (Cloud + S3 snapshots + compaction pipeline) with **DynamoDB native
vector search** (GA 2026-08-05): store embeddings on items in the existing
`ReceiptsTable`, create vector indexes on them, and query via `SearchVectors`.
The entire synchronization machinery — delta tarballs, S3 vector buckets,
COMPACTION_RUN stream messages, SQS queues, the 10GB compaction Lambda, dual-write,
snapshots, distributed locks — exists solely to keep a second datastore consistent
with DynamoDB. With vectors *in* DynamoDB, that machinery has nothing to synchronize.

Why this is smaller than it looks (verified empirically by the mapping agents):

- Of the entire Chroma read surface, only **~7 consumers genuinely need vector
  similarity**. ~11 are exact lookups in disguise (DynamoDB serves them today),
  and **6 are already dead in production** — they filter the words collection on
  `label_{X}` keys the writer never emits (`avg_chroma_rate = 0.0` on dev;
  1,074 VALID GRAND_TOTAL words in DynamoDB vs 0 label-search matches).
- The `infra/embedding_step_functions/` batch tree (11 Lambdas, 3 state machines)
  is deployed but **untriggered** — pure teardown.
- The frontend has zero live Chroma dependency: nothing 404s; seven precomputed
  figures freeze (two start lying and need copy edits).
- PR #1400 (compaction Lambda right-sizing) was never merged — the 10GB Lambda
  and both paid VPC interface endpoints are still live. This removal supersedes it.

## 2. Goals / non-goals

**Goals**
1. Zero Chroma anywhere: no `chromadb` dependency, no Chroma Cloud account, no
   vector S3 buckets, no compaction pipeline.
2. Preserve (or knowingly restore) the ~7 genuine vector features on
   `SearchVectors`, most importantly merchant resolution (protects Google Places
   spend) and QA-agent discovery (hard-fails without a port).
3. Cut AWS spend: the compaction Lambdas (~96% of Lambda spend, ~$240/mo pace),
   two paid VPC interface endpoints, Chroma Cloud subscription, ~60GB of
   chroma-justified ephemeral storage across seven other Lambdas.
4. Delete the dead label-validation illusion rather than port it; if word-label
   consensus is wanted later, build it correctly on the new index (feature
   restoration, not parity).

**Non-goals**
- Changing the embedding model (`text-embedding-3-small`, 1536-dim, stays).
- Reworking merchant resolution / section verification logic beyond swapping
  their retrieval layer.
- The letter-style vector collection (`font_letter_analysis.py:446`): out of
  scope for the main migration; its write path is deleted with `receipt_chroma`
  and the feature is parked (revive on a third vector index if ever needed).

## 3. Target architecture

### 3.1 Vector storage: dedicated embedding items, not inline attributes

Ground truth that shapes this (verified against `infra/dynamo_db.py:60-98` and
the entities): **all six GSIs project ALL**, `delete_receipt` sweeps the base
table by `begins_with(SK, "RECEIPT#{r:05d}")` (`_receipt.py:346`), and hot
receipt-detail reads go through GSI4, not base-table prefix scans. RCU is
charged on items *read*, not returned, so SK placement decides who pays for
vectors.

Store each embedding on a **dedicated item** in the parent's item collection:

```
PK   = IMAGE#{image_id}
SK   = RECEIPT#{r:05d}#LINE#{l:05d}#EMBEDDING     (visual row, keyed by primary line;
                                                   row_line_ids attr lists the rest)
SK   = RECEIPT#{r:05d}#LINE#{l:05d}#WORD#{w:05d}#EMBEDDING          (words)
TYPE = RECEIPT_LINE_EMBEDDING / RECEIPT_WORD_EMBEDDING
vector = List<Number>, 1536 floats (~15–25KB; DynamoDB decimals ≈10–18B/float)
+ flattened filter/display attributes (§3.3)
**NO GSI1–4 keys.**
```

Why each choice:

- **Dedicated items, not attributes on words/lines**: inlining would replicate
  every vector into all five ALL-projection GSIs the parents occupy (~6×
  storage) and bloat every read of the hot items.
- **No GSI1–4 keys on embedding items**: keeps them out of every GSI-based
  access path (GSI4 receipt-details, GSI2/GSI3 enumerations) with zero code
  change, and avoids ALL-projection copies. The vector index is their only
  reader.
- **Under the `RECEIPT#{r}` SK prefix**: `delete_receipt` / merge sweeps remove
  them automatically — no new orphan class (see the orphan-summary history).
  Every alternative (a `VEC#` SK namespace, a separate PK, a separate table)
  requires all deletion/merge/copy paths to remember a second location forever;
  a separate table would reintroduce exactly the two-datastore consistency
  problem this migration eliminates. Considered and rejected.
- **Keep `TYPE`** (→ one extra full copy in ALL-projection GSITYPE): ~$5/mo at
  ~500k vectors, buys enumerate-by-type for backfill audits. Revisit only if
  storage cost surprises.

Priced consequences (accepted):
- Base-table `begins_with(RECEIPT#{r})` scans now traverse vectors: the delete
  sweep *wants* them; `get_receipt_item_type_counts` (rare admin call) gets
  slower — comment at the call site.
- Writes: ~40 WCU per embedding (item + GSITYPE copy) → ~$0.01 per 200-word
  receipt; full-corpus backfill ~$25–50 of WCU + ~$1–2 OpenAI.
- Streams: every embedding write emits a ~40KB NEW_AND_OLD_IMAGES record; the
  stream processor must skip `*_EMBEDDING` TYPEs first thing
  (one guard clause — add in Phase 2 before the writer ships).
- Dev↔prod copy scripts deliberately **skip** embedding/evidence items
  (vectors are re-derivable; run backfill on the destination instead) —
  document in the scripts, don't "fix" them to copy vectors.

### 3.2 Indexes (2 of the 5 allowed)

| Index | Vector attr on | Distance | Partition key | Inline filters | Projection |
|---|---|---|---|---|---|
| `lines-vectors` | line embedding items | COSINE | **none** (corpus-wide queries dominate: merchant resolution, semantic search) | `section_type` (equality) | INCLUDE: text, merchant_name, place_id, image_id/receipt_id/line_id, row_line_ids, section_type |
| `words-vectors` | word embedding items | COSINE | **none** | `label_status` (equality) | INCLUDE: text, merchant_name, image/receipt/line/word ids, label_status |

**Embedding content formats (unchanged from today; relocated in §6 F):**
- Line vectors are **visual-row** embeddings: `{row_above}\n{target_row}\n{row_below}`
  with `<EDGE>` at receipt top/bottom (`formatting/line_format.py:140-172`), one vector
  per visual row keyed by its primary line, `row_line_ids` listing the rest.
- Word vectors are flat **±2-word context** embeddings, `<EDGE>`-padded at row
  boundaries: `"<EDGE> Subtotal Total Tax Discount"`
  (`formatting/word_format.py:format_word_context_embedding_input`).

Constraints that shaped this (see research doc): distance function and INCLUDE
projections are **immutable** after creation — get them right first; filters are
equality-only (no ranges/IN); max 100 results per query; indexing is
**asynchronous** after write.

No partition key: merchant resolution queries the whole corpus with no filter
(top-20), and the QA agent's discovery is corpus-wide. Corpus is small
(≈10⁵–10⁶ vectors) — well inside unpartitioned comfort. If scale ever demands
it, a partitioned index can be added alongside (5-index budget).

**Async indexing is acceptable**: both inline ingest queries (merchant
resolution, section verification) search *pre-existing corpus* vectors, never
the receipt being written. No consumer requires read-after-write searchability.

### 3.3 Metadata: flatten, and settle the words/lines schema split

The Chroma words schema (array-valued `valid_labels_array`/`invalid_labels_array`)
is the root cause of the dead query surface and cannot express equality filters.
The new word embedding items carry:

- `label_status` ∈ {validated, pending, none} — inline filter
- `primary_label` (single highest-confidence valid label) + `valid_labels` as a
  projected (non-filter) attribute for **post-filtering in code**: query
  top-100 with `label_status = validated`, filter by label client-side, take
  top-k. This avoids one-attribute-per-label sprawl while keeping the consensus
  queries correct — and is strictly better than today's silently-empty results.
- `merchant_name`, `place_id`, `section_type`, geometry summary as display fields.

The 32-key Chroma metadata cap and its array workarounds disappear.

### 3.4 Write path: embed-and-put, nothing else

`create_embeddings_and_compaction_run(...)` (and the whole delta/compaction/
cloud-upsert stack behind it) is replaced by a thin module — working name
`receipt_dynamo.embeddings` (or a new small `receipt_vectors` package):

1. `embed_texts()` via OpenAI realtime (moved out of `receipt_chroma`, see §6 F).
2. `BatchWriteItem` the embedding items alongside the words/lines already written.
3. Done. No CompactionRun, no S3, no SQS, no lock, no dual-write, no snapshot.

Producers to convert (all currently call the orchestration primitive):
`process_ocr`/container OCR handler, merge_receipt, resegment_receipt,
combine_receipts, smart re-OCR re-embed. Label/place/section *updates* that
today flow through compaction appliers become plain `UpdateItem` on the
embedding items (or are dropped where the consuming query is being deleted —
which is most of them; only fields in §3.3 need maintaining).

`EmbeddingStatus` keeps its enum but simplifies in meaning: set SUCCESS when
the embedding item is written, FAILED on OpenAI errors (retry via the existing
poison-message-tolerant SQS path). The GSI1 question is deferred (§7).

### 3.5 Read path: consumer-by-consumer disposition

Full tables with file:line in [inventory-read-path.md](inventory-read-path.md).
Summary of dispositions:

| Disposition | Consumers |
|---|---|
| **Port to SearchVectors** (7) | merchant resolution (`resolver.py:1346`, top-20 lines), section verifier (top-k lines), semantic PRODUCT_NAME proposer (words, label_status filter), MCP `search_receipts`/`search_product_lines` semantic modes, QA-agent search (`search.py` — mandatory: no fallback exists), `chroma_resolve_words` consensus (words, top-30), fix_place Tier-3 + address-similarity figure generator (port or park — lowest stakes) |
| **Re-implement on DynamoDB Query/GSI** (~11) | `search_by_merchant_name`, `search_by_place_id`, the dummy-embedding count (`agentic.py:1007` — template: `get_merchant_consensus` at `:872`), `list_all_receipts`, text/substring modes of `search_receipts`/`search_product_lines`/QA `label_lines` (substring scan over DynamoDB text — same semantics as today's `where_document $contains`, honestly labeled), word-similarity figure generator, collection count health check |
| **Delete as dead** (6) | Tier-1 label validation (`validator.py`), `validate_word_similarity` (both MCP variants), `search_receipts` label mode, label_refresh Lambda (dry-run on both stacks), `financial_subagent` chroma leg, `tools_simplified.py` |
| **Top-100 cap check** | QA search uses n_results ≤ 300 today — page or trim to 100; everything else already ≤ 60 |

MCP note: fix `scripts/receipt_mcp_server.py` and the **vendored fork**
`infra/mcp_server_lambda/lambdas/receipt_mcp_server_server.py` (92 diverged
lines) in the same PR. `validate_word_similarity` is retired and **replaced by
a working consensus tool on the words index** (see §3.7) — this is a planned
deliverable, not an option: the human labeling loop (LayoutLM proposes →
user + Claude-with-MCP confirms) is its direct consumer, and today's tool
silently returns nothing.

### 3.5a Merchant resolution: no math changes

Same embedding model, and DynamoDB COSINE returns the same quantity Chroma
returns today — cosine distance = 1 − cosine similarity, range 0–2, lower is
closer. All thresholds, the phone/address/text tier logic, and corroboration
gating carry over verbatim; only the retrieval call changes. Residual risk is
ANN-vs-ANN: both engines are approximate, so neighbors at a threshold boundary
can differ — this is exactly what the §8 golden-set gate screens.

### 3.5b Capability parity: Chroma ops → DynamoDB

| Chroma capability in use | DynamoDB equivalent | Notes |
|---|---|---|
| `.query()` top-k, ranked by distance | `SearchVectors`, ranked by score | same cosine-distance semantics |
| `where` equality / `$and` of equalities | inline filter attributes | filter set is **declared at index creation** — choose deliberately; changing it means a new index |
| `where` ranges / `$in` / negation | not supported (equality only) | post-filter client-side within top-100; current usage is negligible |
| `where_document $contains` substring | not part of vector search | moves to DynamoDB text query — these were substring searches, not similarity searches (see read-path verdicts) |
| `.get(ids=…, include=embeddings)` | GetItem/BatchGetItem on embedding items | improvement: no second datastore round-trip |
| `.get(where=…)` metadata scans | Query/GSI | the DYNAMO-verdict consumers |
| `n_results` up to 300 (Cloud cap) | **100 max per call** | QA search trims/pages; all other callers ≤ 60 |
| `.count()` | Query count | trivial |

Net: every search/filter/sort actually used survives; the losses are the
substring operator (relocated, not lost), rich filter operators (post-filter),
and result depth (300→100).

### 3.7 Word-label consensus rebuild (resolved — this is in scope)

Context: word labeling is LayoutLM first-guess + human/Claude confirmation via
MCP tools. LayoutLM does not cover line items, so the items region has no model
assist — and the Chroma tool that should have provided similarity evidence has
been silently dead. The core question the tool must answer, both polarities:
*"what similar words have this label, or have been proven NOT to have it — and
why?"*

**Design: search-then-join.** No new item type needed — `ReceiptWordLabel`
rows already hold label, validation_status, and `reasoning`, keyed adjacent to
the word (`…#WORD#{w}#LABEL#{name}`). The tool composes what exists:

1. `GetItem` the target word's stored vector (word embedding item).
2. `SearchVectors` on `words-vectors`, `label_status = validated`, top-k.
3. Similarity-threshold cut (same thresholds as the old validator intended).
4. `BatchGetItem` each surviving neighbor's `LABEL#` rows → per-neighbor
   labels, verdicts, and the original `reasoning`.
5. Aggregate: scored evidence for/against the candidate label, each neighbor
   carrying its rationale.

Evidence *against* falls out identically — a neighbor whose `LABEL#{X}` row is
INVALID is a counter-example with its reasoning. Cost is one `BatchGetItem`
round-trip (≤100 keys, milliseconds). What this avoids: a third item type,
duplicated vectors, a write hook on every validation event, and an evidence
backfill — labeling stays pure `ReceiptWordLabel`, fully decoupled from the
vector layer.

**Accepted trade-off (documented, not solved):** Chroma's intended semantics
pre-filtered inside the ANN search ("nearest among words labeled X"); the join
post-filters, so for a *rare* label the top-k validated neighbors may carry
little signal about X even when labeled examples exist further out. Since the
validator threshold-cut its results anyway — distant neighbors are weak
evidence however found — the practical loss is minimal. If a rare-label case
ever demonstrates otherwise, a dedicated (word, label, verdict) evidence index
with `label`/`verdict` equality filters is a known, additive follow-up (index
budget allows it); it is deliberately not in the launch design.

Deliverables:
- New MCP tool (working name `similar_labeled_words`): the search-then-join
  above, both polarities, evidence with reasoning and provenance — what
  `validate_word_similarity` always claimed to do. Serves the confirm-labels
  workflow directly.
- The ingest-side semantic PRODUCT_NAME proposer (already a live VECTOR
  consumer) uses the same pattern and closes part of the PRODUCT_NAME-at-ingest
  gap.
- Tier-1 auto-validation at ingest is rebuilt only after the MCP tool proves
  hit-rate in the human loop — measured adoption first, automation second.

### 3.6 Backfill

One-shot script (pattern exists in the repo's backfill recipes): scan words/
lines (or re-embed from text via OpenAI batch — ~$1–2 per full corpus at
current sizes), write embedding items, wait for index `ACTIVE`/backfill done.
Optionally salvage existing vectors from the latest S3 snapshot's
`chroma.sqlite3` to avoid re-embedding spend; re-embedding is simpler and cheap
enough that it is the default. Run on dev, validate (§8), then prod.

## 4. What gets deleted (net)

- Packages: `receipt_chroma` (minus two relocated subpackages, §6 F);
  most of `receipt_dynamo_stream`'s chroma routing (pruned, not deleted — §6 D).
- Infra components: `chromadb_compaction` (after extracting the two non-chroma
  updaters + queues), `chromadb_buckets.py`, both similarity cache generators
  (word variant re-pointed at DynamoDB, address variant ported or parked),
  `label_refresh_lambda`, embedding-compact + normalize-batches, the dormant
  `embedding_step_functions` batch tree, 2 paid VPC endpoints.
- DynamoDB: `CompactionRun`, `CompactionLock` entities + accessors + ~73 tests;
  `ChromaDBCollection`/`CompactionState` enums.
- Config: 4 Chroma Cloud keys × 2 stacks; 28 `CHROMA*` env var names.
- Tests: ~660 wholesale (receipt_chroma 553 + never-run infra 106) — including
  all 7 chronic flaky skips; edits across ~20 more files.
- CI: `receipt_chroma` matrix leg; `chromadb` pip installs in 4 branches;
  the lambda-syntax compaction-lock pytest step.
- Docs: ~25 delete/archive, ~10 live docs updated (list in tests-ci inventory).
- Frontend: `ChromaLogo`, chroma-labeled chart bars/captions/tier panel,
  resume.tsx claim, chroma-typed API fields.
- External: Chroma Cloud account itself (export nothing — DynamoDB is already
  authoritative for all data; vectors are re-derivable).

Cost effect: ~$240/mo compaction Lambdas → ~$0 (SearchVectors pay-per-request at
this volume is dollars), Chroma Cloud subscription → $0, 2 paid VPC endpoints →
$0, S3 vector bucket storage/requests → $0, OpenAI spend unchanged.
The NAT instance **stays** (upload_images uses the same subnets).

## 5. Phasing (PR sequence)

Each phase is independently shippable and leaves both stacks deployable.
Ordering rules from the infra inventory are load-bearing; violating them fails
`pulumi preview` or breaks non-vector production paths.

**Phase 0 — prerequisites (small PRs, no behavior change)**
- boto3 ≥ 1.43.64 across packages and images (local dev machines too: current
  1.43.53 / awscli 2.31.29 lack `search-vectors`).
- Verify Pulumi AWS provider supports `VectorIndexes`/`VectorIndexUpdates`;
  if not, create indexes via a bootstrap script (boto3 `UpdateTable`) and track
  as a documented manual resource until provider support lands.
- Spike: create both indexes on **dev**, backfill a few hundred receipts,
  benchmark merchant-resolution recall vs Chroma answers (golden set, §8).

**Phase 1 — dead-code deletions (zero behavior change, immediate wins)**
1. Delete the 6 dead words-`label_X` query paths + `label_refresh_lambda`
   component (dry-run on both stacks) + `tools_simplified.py`.
2. Delete the dormant `embedding_step_functions` batch tree (11 Lambdas, 3 SFs,
   ECR, dashboard, second compaction impl). ⚠️ Drop each container Lambda's
   legacy-URN `aliases` in the same change. This removes `embedding_status`'s
   only functional consumer.
3. Port the ~11 disguised exact-lookups to DynamoDB (template:
   `get_merchant_consensus`). Kills the dummy-embedding OpenAI call.
4. Dead-code list from the infra inventory §G (processor/ dir, dual_chroma_client,
   unwired simple_lambdas, stale monitoring builders, conftest.py.bak).

**Phase 2 — build the new vector path (additive; Chroma still running)**
1. Relocate `receipt_chroma.embedding.{openai,formatting}` to a non-chroma home.
   ⚠️ `formatting` is the Swift-parity surface: regenerate parity fixtures and
   port the Swift side in the same PR or CI goes red.
2. New embed-and-put writer + embedding-item entities in `receipt_dynamo`.
3. Create indexes (dev then prod), run backfill, verify recall gates.
4. Dual-run window: ingest writes embedding items *in addition to* the existing
   Chroma path (cheap — it's one BatchWrite); genuine-vector consumers gain a
   `VECTOR_BACKEND=dynamodb|chroma` switch, default chroma.

**Phase 3 — cutover**
1. Flip `VECTOR_BACKEND=dynamodb` on dev; soak against golden receipts + live
   ingest; compare merchant-resolution hit rate and Places-call volume.
2. Flip prod. Deploy dev stack explicitly (CI deploys prod only — standing gotcha).
3. Retire `validate_word_similarity` + Chroma-only MCP behavior; fix script and
   vendored Lambda fork in one PR.

**Phase 4 — teardown (strict order)**
1. Relocate summary-updater, line-item-updater, summary/line-item queues out of
   `chromadb_compaction` into their own component; re-point `upload_images`
   consumption (`__main__.py:352-353`). Prune-don't-delete
   `CHROMADB_RELEVANT_FIELDS` (keep RECEIPT_SUMMARY/RECEIPT_SECTION routing);
   rename package/env vars.
2. Remove chroma writers from the five producer Lambdas (merge: delete the
   error-dict guard at `merge_receipt.py:139-140`, not just the env var;
   resegment: `_embed_outputs`; upload_images bracket-access env reads).
3. Strip the four `require_secret("CHROMA_CLOUD_API_KEY")` calls, THEN remove
   the config keys from both stack YAMLs.
4. Delete `chromadb_compaction` component, `chromadb_buckets.py` (fix its two
   other importers first), compaction queues/Lambda/alarms/ECR, stack exports
   (`chromadb_*`, `enhanced_compaction_function_arn`, …) after grepping
   scripts/Swift config loaders for `pulumi stack output` readers.
5. Remove the two paid VPC interface endpoints; keep NAT + gateways
   (edit their `route_table_ids` if subnets change). Rename `ChromaSecurity`.
6. Empty + delete the vector S3 buckets (dev, then prod after a 2-week soak;
   final snapshot copied to glacier-tier archive first as a courtesy backstop).
7. Close the Chroma Cloud account.

**Phase 5 — cleanup + /receipt page rewrite**
- pyproject/CI/test edits (drop matrix leg, 4 chromadb installs, lint leg;
  rewrite the `--reruns` justification comment), pull-request template,
  ~35 doc updates/deletions, `EmbeddingStatus` GSI1 decision (§7).
- **/receipt page rewrite** (concurrent with Phase 2–3, not after): see §5a.

### 5a. /receipt page: present "similarity search", not a technology

The page currently narrates the Chroma pipeline by name (tier panels, timing
bars, logos). The rewrite reframes every figure around the *capability* —
embeddings and similarity search — so the story survives any future backend
change. Editorial rules:

1. Name the concepts (embedding space, nearest neighbors, similarity score,
   consensus voting), not the vendor. Backend named at most once, as an
   implementation footnote.
2. No per-technology performance theater: WordSimilarity's "Open Chroma"/
   "Chroma Fetch" timing bars become capability-level stages (e.g. "load
   vectors" / "search") or are dropped; regenerate the cached JSON so frozen
   Chroma numbers never render.
3. Tier panel "ChromaDB" → "Similarity search" (`TIER_COLORS.chroma` renamed);
   QAAgentFlow caption → "Searching receipts by meaning and metadata";
   `ChromaLogo` leaves the tech strip (DynamoDB already present);
   resume.tsx drops "(FAISS + ChromaDB)" for "vector similarity search".
4. Figures whose *data* is Chroma-shaped get their generators ported in Phase 2
   (word-similarity → DynamoDB text query; address-similarity → SearchVectors)
   so the page shows live data at cutover, not frozen caches.
5. Types follow the same rule: `validation_source: "chroma"` → `"similarity"`,
   `chromadb_init_ms` → stage-named fields, `avg_chroma_rate` →
   `avg_similarity_rate` — coordinated generator+frontend change per figure.

## 6. Landmines (each verified, with evidence in the inventories)

A. **Import-time Pulumi breaks**: `chromadb_buckets.py` import side effect
   (3 importers + export-at-import); address-similarity self-instantiation
   chain into `GET /address_similarity`; eager `handlers/__init__` chromadb
   import; module-scope `os.environ["CHROMADB_BUCKET"]` in two route Lambdas.
B. **Required-kwarg TypeErrors** in exactly four component constructors
   (merge, resegment, both similarity generators).
C. **Vendored MCP fork** — 92 diverged lines; one PR for both copies.
D. **Stream processor is prune-not-delete** — summary/line-item routing and
   the change-detection allowlist drive line-item recompute; the
   `chromadb:enable-line-item-refine` flag is chroma-named but not chroma.
E. **Runtime signatures**: `create_qa_graph`/place-finder graph/
   `MerchantResolvingEmbeddingProcessor` require chroma args positionally.
F. **`receipt_chroma` split**: `.embedding.openai` + `.embedding.formatting`
   have no chromadb imports but are imported by surviving code; `formatting`
   is mirrored by the Swift port → parity-fixture regeneration in same PR.
G. **Legacy-URN aliases** on embedding container Lambdas — drop alias and
   resource together.
H. **moto cannot mock SearchVectors** (feature is 3 weeks old): test the new
   query layer via a thin injectable interface with a fake, plus a small
   dev-stack integration suite (pattern: existing `end_to_end` marker).

## 7. Open questions (decide during review)

1. **`embedding_status` as GSI1PK** on ReceiptWord/ReceiptLine: keep-and-freeze
   (write SUCCESS, never query) vs full-table migration to remove. Default:
   **keep-and-freeze**; revisit only if GSI1 storage cost matters.
2. ~~Word-label consensus restoration~~ **Resolved — in scope** (§3.7): the
   words index backs a working `similar_labeled_words` MCP tool for the
   LayoutLM-propose / human-confirm loop, with line items (no LayoutLM
   coverage) as the primary beneficiary; ingest auto-validation returns only
   after measured hit-rate.
3. ~~Address-similarity figure~~ **Resolved — port** (§5a): the /receipt page
   is rewritten around "similarity search" as a capability, concurrent with
   Phases 2–3, and figure generators are ported so the page shows live data.
4. **QA search n_results 300 → 100**: trim or paginate (3 calls)? Default: trim
   to 100, measure answer quality on the marquee questions.
5. Third index for letter-style vectors: park (default) or migrate.

## 8. Validation gates

- Golden-set recall: merchant resolution on the golden receipts must match
  Chroma's resolved merchant ≥ 98%, and Google Places call volume in the soak
  window must not rise materially.
- Section verifier agree/disagree EMF rates comparable pre/post cutover.
- QA agent marquee questions: scorecard no worse than current baseline
  (`local_qa_run.py`, 30s/question loop).
- `SearchVectors` p95 latency < 100ms from Lambda (expect single-digit ms).
- After Phase 4: `grep -ri chroma` in live code paths returns only history/
  archive docs; both stacks `pulumi preview` clean; full CI green including
  the three previously-flaky-skip suites now gone.

## 9. Rough sizing

Phases 0–1: a few days of PRs, immediately merged, no risk. Phase 2: the real
work (~writer + 7 consumer ports + backfill + parity fixtures). Phase 3: soak
time dominates. Phases 4–5: mechanical but ordering-sensitive teardown. The
whole effort is a stack of ~15–25 small PRs, of which only the Phase-2/3 core
touches live ingest behavior.
