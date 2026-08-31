# Inventory: Chroma read/query consumers

Compiled 2026-08-31 by mapping agent (chroma-read-path) against origin/main @ 1b6540c81,
with empirical verification against live Chroma Cloud (dev).

## Headline

Chroma's read surface is much smaller than it looks, and a significant fraction **is
already dead in production** — filtering on metadata keys the writer never writes.
The frontend has **zero live Chroma dependency**; removal freezes seven precomputed
figures but breaks nothing public. Genuine blast radius: receipt ingest (receipt_upload)
and two agent surfaces.

## Load-bearing discovery: words/lines schema split several consumers get wrong

Documented in `receipt_agent/utils/chroma_helpers.py:842-861`:
- **words** collection stores labels as arrays: `valid_labels_array` / `invalid_labels_array`
- **lines** collection stores them as booleans: `label_GRAND_TOTAL: True`

Only writer of `label_{NAME}` booleans: `receipt_chroma/compaction/labels.py:393`
(`_update_row_labels`, reached solely from `_apply_line_label_updates` :165 — the **lines**
path). The words writers (`data/operations.py:908-927`, `embedding/metadata/word_metadata.py:73-89`)
emit only label_status/label_confidence/label_proposed_by/valid_labels_array/
invalid_labels_array/label_validated_at. **No scalar `label` key on words at all.**

Every consumer querying **words** with `where={"label_X": True}` or `where={"label": ...}`
matches zero records. Verified three ways against live dev:
1. `search_receipts(query="GRAND_TOTAL", search_type="label")` → total_matches: 0, while
   DynamoDB holds 1,074 VALID GRAND_TOTAL words.
2. `validate_word_similarity` on a real validated word ("3.94") → confidence 0.0, empty
   evidence, "No similar validated words found" — while the exact-id `.get()` succeeded,
   so the embedding exists; only the where filters fail.
3. Live dev label-validation viz: **aggregate_stats.avg_chroma_rate = 0.0** — the Chroma
   tier resolves 0% of words; everything falls through to the LLM.

Dead call sites (all filter words on non-existent keys):

| File:line | Consumer | Status |
|---|---|---|
| scripts/receipt_mcp_server.py:3486-3504 | validate_word_similarity_impl, both polarities | dead |
| scripts/receipt_mcp_server.py:2601 | search_receipts_impl label mode, where={"label": …} | dead |
| infra/mcp_server_lambda/lambdas/receipt_mcp_server_server.py:3505/3518/3651 | same, deployed fork | dead |
| receipt_upload/label_validation/validator.py:184, :315 | **Tier-1 label validation, live ingest path** | dead |
| infra/label_refresh_lambda/lambdas/label_refresh.py:151/157 | stream-driven re-validation | dead (and DRY_RUN on both stacks) |
| receipt_agent/.../label_evaluator/financial_subagent.py:291 | financial column detection | dead; always falls back to local_col_x |

Consequences:
1. **Corrects a stored assumption**: validate_word_similarity misses are schema mismatch,
   not Chroma-sync lag; waiting never fixes them.
2. Shrinks removal cost: the Chroma-avoids-an-LLM-call savings claimed by
   `docs/UNIFIED_LABELING_PIPELINE.md:66-114` and `unified_receipt_evaluator.py:1047` are
   **not currently realized** on the words path — removing Chroma there costs nothing not
   already lost. (Corollary for the new design: implementing word-label consensus on
   DynamoDB vector search is a *feature restoration*, not parity.)

Caveat (agent's own): `operations.py:772-778` strips `label_`-prefixed keys from word
records, implying an older writer once set them; legacy word records may still carry them —
possibly decay rather than day-one zero. Either way, measured avg_chroma_rate=0.0 on dev
says the end state has already arrived — **treat these paths as dead for planning**.

## Consumer inventory (per-consumer verdicts)

Verdicts: **DYNAMO** = DynamoDB can serve it today; **VECTOR** = genuinely needs
nearest-neighbor; **DEAD** = matches zero rows now.

### MCP servers (4 Chroma-gated tools of ~60)

Boundary declared at `scripts/receipt_mcp_server.py:75-82` (CHROMA_TOOLS); dispatch :2217
passes chroma_client=None to everything else. `list_words_by_label` is DynamoDB (:2273), as
are get_receipt, list_merchants, get_receipts_by_merchant, get_receipt_summaries,
list_categories, label_validation_summary.

| Consumer | Op / collection | Breaks | Verdict |
|---|---|---|---|
| search_receipts label (:2600) | .get(where={"label"}) / words | nothing — 0 today | **DEAD** |
| search_receipts text (:2673, default) | .get(where_document $contains) / lines, unbounded | substring receipt lookup | **DYNAMO** |
| search_receipts semantic (:2631) | .query(n_results=limit*2) / lines | meaning-based discovery | **VECTOR** |
| list_all_receipts (:2853) | .get(include=metadatas) / lines, no filter/limit | receipt enumeration | **DYNAMO** (Dynamo authoritative; Cloud quota hazard) |
| search_product_lines text (:3068, default limit=100) | .get(where_document + non_item_section_filter) / lines | product-line spend queries | **DYNAMO** |
| search_product_lines semantic (:2988) | .query(n_results=limit*3) / lines | same, fuzzy | **VECTOR** |
| validate_word_similarity (:3456 get, :3486/:3502 query) | .get(ids=) + 2× .query(where=label_X) / words | nothing — 0.0 confidence today | **DEAD** |

Already degrades cleanly: ChromaNotConfiguredError (:92) fails the four individually with
error_type "chroma_not_configured" (:2578); CHROMA_CLOUD_ENABLED=false runs Dynamo-only.

⚠️ **Fork risk**: `infra/mcp_server_lambda/lambdas/receipt_mcp_server_server.py` is a
vendored copy, **92 changed lines** vs the script — not an import. Same CHROMA_TOOLS
:75-82. Deployed as container Lambda + Function URL (IAM) + public Cognito-JWT route
`/receipt/mcp` (infra/mcp_auth_gateway.py:294), hit by the daily receipt_label_fixer cron.
**Fix both files in one PR.**

### receipt_upload — live ingest path, the real blast radius

Reached from SQS via `infra/upload_images/container_ocr/handler/handler.py:444`. No cache
to coast on.

| Consumer | Op / collection | Breaks | Verdict |
|---|---|---|---|
| merchant_resolution/resolver.py:1346 | .query(n_results=20), no filter / lines | merchant resolution by header similarity; **a hit skips a Google Places call** | **VECTOR** (removal raises Places spend) |
| section_verifier.py:124 | .query(n_results=KNN_NEIGHBORS) / lines | cross-receipt vote confirming section proposals; feeds UploadLambdaSectionAgreed/Disagreed/Abstained EMF metrics (handler.py:215) — alarms die too | **VECTOR** |
| line_items/semantic_proposer.py:171 | .query(where={"label_status":"validated"}) / words | kNN majority-vote PRODUCT_NAME detection | **VECTOR** (filter key exists — this one is LIVE) |
| label_validation/validator.py:184,:315 | .query(where=$and[label_status, label_X]) / words | nothing — Tier-1 resolves 0% today | **DEAD** |

### receipt_agent — 23 read consumers, ~6 truly vector

**Live and valuable (VECTOR):**

| Consumer | Op / collection | Breaks |
|---|---|---|
| utils/chroma_helpers.py:1074,:1187 via chroma_resolve_words (:191) | unfiltered .query(n_results=30) / words | consensus voting that **replaces an LLM call** when confident (currency_subagent.py:527). Trades cost, not correctness. |
| utils/chroma_helpers.py:881 _query_label_evidence_for_collection | 2× .query(n_results=15), correct per-collection clause / both | label evidence feeding LLM review |
| graph/nodes.py:340 search_similar_receipts | .query(n_results=10) / lines | merchant candidates; high-confidence hit **skips Google Places** (agents/validation/graph.py:55) |
| question_answering/tools/search.py:480,:640,:977 | .query(n_results≤300) / lines | QA agent semantic discovery |
| chroma_helpers.py:722,:762; graph/nodes.py:113,:169; agentic.py:482,:595 | .get(ids=, include=["embeddings"]) | vector-payload fetches feeding the above — die with their consumers |
| agentic.py:503,:619,:716 | .query() / lines+words | agentic "find similar to my line/word", free-text line search |

**Disguised exact-lookups — delete rather than port (DYNAMO):**

| Consumer | Why |
|---|---|
| agents/agentic/tools/agentic.py:1007 | **Smoking gun**: .query(query_embeddings=[dummy_embedding], n_results=100, where={"place_id"}); comment :1002 admits the embedding only satisfies the signature. Burns an OpenAI call per invocation for a discarded value, to compute a count. agentic.py:872 get_merchant_consensus is already a working Dynamo implementation of the same idea. |
| tools/chroma.py:317 search_by_merchant_name | where={"merchant_name"} does all the work |
| tools/chroma.py:396 search_by_place_id | .get(where={"place_id"}, limit=100) — pure keyed fetch |
| question_answering/tools/search.py:445 label_lines | .get(where={label_X: True}) / lines — correct schema but **no limit**, full metadata scan |
| search.py:520,:1085 (both **text defaults**) | .get(where_document $contains), unbounded — substring index, not vectors |
| label_evaluator/pattern_discovery.py:253,:333 | probes with literal label name / fixed boilerplate as query text; where does the selection. No infra caller. |
| agentic.py:977 | get_collection("lines").count() — health check |

**Dead (DEAD):**

| Consumer | Why |
|---|---|
| label_evaluator/financial_subagent.py:291 | where={label_X} / words → pos_count never reaches min_chroma_evidence=3 (:400); always falls back to local_col_x |
| question_answering/tools/search.py:415 | .get(where={"label"}) / words |
| question_answering/tools_simplified.py (whole module) | mirrors search.py; no callers |
| chroma_helpers.py:1664,:2028,:1302,:984 | exported but no production callers |

Notes:
- No Chroma read at import time; client construction lazy everywhere. Most paths handle
  chroma_client=None (label_evaluator/graph.py:132, llm_review.py:552,
  unified_receipt_evaluator.py:1023, tools/registry.py:123).
- ⚠️ **Exception: the QA agent has no non-Chroma discovery tool** — without search it
  cannot obtain the receipt IDs get_receipt needs.
- Two hard imports break naive removal: utils/chroma_helpers.py:18 and
  question_answering/tools/search.py:26 (`non_item_section_filter`, a stdlib-only leaf
  worth vendoring).
- Config gating scattered: only chroma_persist_directory in config/settings.py:70; the rest
  raw os.environ in clients/factory.py:78-276.

### infra — 5 deployed read surfaces

| Consumer | Op / collection | Trigger | Verdict |
|---|---|---|---|
| routes/word_similarity_cache_generator/lambdas/index.py:662,:715 | .get(where_document $contains "MILK") / lines, metadata_only — **zero vectors** | EventBridge rate(1 day) (infra.py:268); VPC-attached, wired __main__.py:296 | **DYNAMO** |
| routes/address_similarity_cache_generator/lambdas/index.py:389,:471 | .get(ids=, include=embeddings) then .query(n_results=8) / lines | EventBridge rate(1 day) (infra.py:241); not in VPC, S3-snapshot only (no Cloud env vars) | **VECTOR** — lowest-stakes, presentational demo |
| label_evaluator_step_functions → unified_receipt_evaluator.py:1043,:1125 | delegates into receipt_agent chroma_helpers + financial_subagent | SF Map state; **no schedule, manual start** | **VECTOR** + one DEAD leg |
| qa_agent_step_functions/lambdas/run_question.py:326 | delegates into search.py; Chroma **Cloud** | SF RunAllQuestions; **manual start** | mixed — 2 of 4 modes pure metadata |
| fix_place_lambda/lambdas/fix_place.py:155 | .query() / lines via tools/chroma.py:147 | Tier-3 fallback only; invoked by embedding line_polling.py:145 — NOT by MCP, which reimplements locally at receipt_mcp_server_server.py:4415 | **VECTOR**, low frequency |
| label_refresh_lambda/lambdas/label_refresh.py:151,:157 | .query(where=$and[label_status,label_X]) / words | DynamoDB stream, LATEST | **DEAD** — and dry_run defaults True on both stacks (__main__.py:1312; flag unset in both Pulumi YAMLs). **Cheapest delete.** |

Confirmed NOT read consumers (write/compaction only): combine_receipts_step_functions,
merge_receipt_lambda, resegment_receipt_lambda, label_validation_viz_cache,
embedding_step_functions.

Infra notes: CHROMA_CLOUD_ENABLED hardcoded "false" at
label_evaluator_step_functions/infrastructure.py:459 → Cloud branch at
unified_receipt_evaluator.py:922 unreachable (S3-snapshot only). Read surface is
**identical on dev and prod**; only stack-conditional component in __main__.py is WebAnalytics.

## Frontend: stale, not broken — seven precomputed figures

`portfolio/next.config.js:9` is `output: "export"`; **no Next.js API routes**. Browser
calls API Gateway directly; every handler is s3_client.get_object with no Chroma import
(routes/word_similarity/handler/index.py:49, routes/address_similarity/handler/index.py:49,
label_validation_viz_cache/lambdas/index.py:36, label_evaluator_viz_cache/lambdas/index.py:47,
qa_viz_cache/lambdas/index.py:35). Chroma sits one layer upstream in the daily generators.

**Kill Chroma and nothing 404s, nothing throws** — daily regeneration fails, S3 caches
freeze, seven figures on the public /receipt page go permanently stale:

| Figure | Component | Endpoint |
|---|---|---|
| Word Similarity (milk prices) | Figures/WordSimilarity.tsx:139 | /word_similarity |
| Address Similarity side-by-side | Figures/AddressSimilaritySideBySide.tsx:165 | /address_similarity |
| Address Similarity standalone | Figures/AddressSimilarity.tsx:420 | /address_similarity (superseded sibling, unreferenced from receipt.tsx) |
| Label Validation Visualization | Figures/LabelValidationVisualization/index.tsx:851 | /label_validation/visualization |
| Label Evaluator Visualization | Figures/LabelEvaluatorVisualization/index.tsx:1386 | /label_evaluator/visualization |
| Between-Receipt Visualization | Figures/BetweenReceiptVisualization/index.tsx:160 | /label_evaluator/visualization |
| QA Agent Flow | Figures/QAAgentFlow.tsx:644 | /qa/visualization |

Two become actively misleading rather than merely outdated:
- **WordSimilarity.tsx:708-714** — timing bar chart with bars labeled "Open Chroma"/"Chroma
  Fetch" (timing.chromadb_init_ms / chromadb_fetch_all_ms), baked into cached JSON; after
  removal the site displays frozen performance numbers for a database that no longer exists.
- **QAAgentFlow.tsx:644** — hardcoded caption "Searching DynamoDB and ChromaDB for relevant
  receipts"; static string, false the moment Chroma is gone.

Cosmetic, same sweep: LabelValidationVisualization/index.tsx:584 tier panel named "ChromaDB"
(TIER_COLORS.chroma :31), animating a tier resolving 0% of words; ChromaLogo.tsx in the
tech-stack strip (receipt.tsx:371); pages/resume.tsx:32 "distributed vector search
(FAISS + ChromaDB)".

Types/fixtures: portfolio/types/api.ts:255-345 (similarity_distance, chromadb_init_ms,
use_chroma_cloud), :735-800 (validation_source "chroma"|"llm", avg_chroma_rate), :631-636
(ReviewEvidence.similarity_score); portfolio/e2e/fixtures/word-similarity.ts:4.

Cleared as not Chroma-backed: LabelValidationCount.tsx, LabelWordCloud.tsx,
LabelValidationTimeline.tsx (DynamoDB counts), EmbeddingExample.tsx (client-side mock).

## Closing summary (agent's)

Smaller job than ~200 matching files suggest: **~11 consumers are disguised exact-lookups**
DynamoDB serves today, **6 already dead**, **~7 genuinely need vector similarity** — of
which 3 are demos/low-frequency. The two with real money attached:
`merchant_resolution/resolver.py:1346` (a hit skips Google Places spend) and
`chroma_resolve_words` (a confident consensus skips an LLM call) — cost trade-offs, not
correctness cliffs.

Frontend is a stale-data problem, not an outage problem. The real work is receipt_upload's
live-ingest query sites — no cache to coast on.

Suggested order: (1) delete six dead words-label_X paths (zero behavior change); (2) port
disguised lookups (template: agentic.py:872 get_merchant_consensus); (3) swap
where_document substring searches for a text index; (4) decide explicitly on the seven
real vector consumers; (5) frontend copy edits (independent, worth doing even if removal
stalls).

Most likely to bite: the **vendored MCP fork** (92 diverged lines — fix script + Lambda
copy in one PR) and the **QA agent's lack of any non-Chroma discovery tool** (the one
surface that hard-fails rather than degrades).

