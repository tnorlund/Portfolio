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

[table pending from agent]

## Frontend: seven precomputed figures

[pending from agent]

