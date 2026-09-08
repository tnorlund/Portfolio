# Inventory: tests, CI, scripts, docs touching Chroma

Compiled 2026-08-31 by mapping agent (chroma-tests-ci) against origin/main @ 1b6540c81.

## 1. Tests

### Deletes wholesale
- `receipt_chroma/tests/` — 41 files, ~553 tests (26 unit, 14 integration, conftest, factories).
- `infra/chromadb_compaction/` — 16 test files, ~106 tests. **Never runs in CI** (infra/pyproject.toml:131 sets testpaths but no workflow invokes pytest in infra/) — already dead weight.
- Flaky-skip cluster disappears with the package (7 skips, not the remembered 3):
  - `receipt_chroma/tests/integration/test_compaction_e2e.py:183,:354` (moto-S3 checksum)
  - `receipt_chroma/tests/unit/test_dual_write.py:341,392,469,547,608` (chromadb caches cloud creds at import time)
  - conditional: `test_compaction_sections.py:413` importorskip("chromadb")

### Edits (files also cover non-Chroma behavior)
- `receipt_agent/tests/`: test_chroma_helpers.py (16, deletable), test_client_factory_exceptions.py (2), test_tools.py (13), test_state_models.py (12), test_place_finder_agent_cap.py (7); conftest.py:63 `mock_chroma_client` fixture.
- `receipt_upload/tests/`: **test_merchant_resolver.py (49 tests, 43 Chroma — heaviest coupling outside receipt_chroma)**, test_merchant_embedding_processor.py (14, deletable), test_section_pipeline_contract.py (9), test_ingest_cloud_upsert.py (26), test_font_letter_analysis.py, test_semantic_proposer.py, test_section_verifier.py, test_llm_runner.py.
- `receipt_dynamo_stream/tests/`: all 8 chroma-touching files (~92 tests). Package exists largely to detect "ChromaDB-relevant field changes" → SQS deltas; removal likely retires most of the package.
- `receipt_dynamo/tests/`: unit/test_compaction_lock.py (34), integration/test__compaction_lock.py (29), unit/test_compaction_run.py (9), integration/test__compaction_run.py (1), unit/test_receipt_row.py. CompactionLock/CompactionRun are Chroma-only entities.
- Root `tests/`: test_receipt_mcp_lazy_chroma.py (9, deletable), test_resegment_receipt_lambda.py (21), test_local_analytics_cache.py (11), test_evaluate_section_geometry.py (4).
- `scripts/`: test_qa_agent.py, test_qa_enhanced.py, test_qa_marquee_questions.py, test_label_validation.py, test_pattern_discovery.py.
- `infra/components/test_docker_package_contexts.py`: CHROMA_IMAGE_CONTEXTS tuple + 3 tests lose their subject; test_codebuild_content_hash.py:104 uses a receipt_chroma edit as fixture.
- `infra/tests/test_compaction_lock_config.py` (6) — the only infra Chroma test CI runs (in lambda-syntax job).
- `infra/embedding_step_functions/`: tests/test_embedding_state_handlers.py (7), unified_embedding/handlers/tests/test_close_chromadb_client.py (10, entirely Chroma), test_group_chunks_merge.py (2), standalone_test_close_client.py (not collected), handlers/tests/conftest.py, simple_lambdas/normalize_poll_batches_data/test_handler.py.
- `tools/glyph-studio/py/tests/test_packaging.py` (2 tests asserting receipt-chroma is not a default dep but is in `sections` extra — become meaningless).
- `infra/routes/word_similarity_cache_generator/tests/test_focused_receipt_fetch.py`.

## 2. CI (.github/workflows)

All Chroma CI lives in `main.yml` (459 lines; jobs: lambda-syntax, python-tests, repository-tests, typescript-tests, browser-tests, deploy, smoke-tests). stale.yml, swift-ci.yml, vision-portrait-worker-ci.yml: zero refs.

- python-tests matrix: drop `receipt_chroma` leg (line 79 of 7 legs) + its case branch :107-113.
- `pip install chromadb` in FOUR install branches: receipt_chroma :111, receipt_upload :140, receipt_agent :155, repository-tests :234; each also `pip install --no-deps -e receipt_chroma` (:110,:136,:151,:230). Dropping chromadb speeds the three surviving jobs.
- lambda-syntax runs `pytest -q infra/tests/test_compaction_lock_config.py` (:68) — step goes.
- :191 comment justifying `--reruns 1` cites "moto-S3 timestamp races, ChromaDB lock contention under pytest-xdist" — rationale needs rewriting (moto part may still apply).
- **No workflow builds/deploys a Chroma image** — image builds happen in CodeBuild inside Pulumi (`infra/chromadb_compaction/components/docker_image.py`, `infra/chromadb_compaction/lambdas/Dockerfile`); cleanup is an infra edit, not a workflow edit.
- `.github/pull_request_template.md:21` has a receipt_chroma checkbox.

## 3. Scripts, cross-package refs, env vars

### scripts/ (28 files)
- Delete outright: sync_to_chroma_cloud.py, delete_chroma_cloud_collection.py, verify_chromadb_snapshot.py, reset_embedding_status.py.
- Import receipt_chroma, need surgery: receipt_mcp_server.py, backfill_receipt_rows.py, build_section_order_priors.py, local_analytics_cache.py, evaluate_section_geometry.py, evaluate_single_receipt.py, evaluate_qa_agent.py, ocr_outlier_prototype.py, test_{qa_agent,qa_enhanced,qa_marquee_questions,label_validation,pattern_discovery}.py.
- Mention-only cleanup: analyze_all_merchants.py, dev_label_evaluator.py, evaluate_currency_labels.py, local_qa_run.py, ocr_outlier_batch.py, reconcile_dev_to_prod.py, reprocess_photo_receipts.py, run_place_finder.py, txinfo_shadow_candidate.py, validate_step_function_jsonpath.py, README.md.

### MCP surface (sharpest user-facing edge)
`scripts/receipt_mcp_server.py:75` — `CHROMA_TOOLS = {search_receipts, list_all_receipts, search_product_lines, validate_word_similarity}`: four MCP tools stop working. Plus ChromaNotConfiguredError, CHROMA_NOT_CONFIGURED_MESSAGE, sys.path.insert :46, optional-credentials config path. Mirrored in `infra/mcp_server_lambda/lambdas/receipt_mcp_server_server.py`.

### receipt_chroma imported from 30+ files outside its package
Heaviest: `receipt_upload/receipt_upload/merchant_resolution/{resolver,embedding_processor}.py` (5 each), `receipt_agent/receipt_agent/clients/factory.py` (4), `infra/embedding_step_functions/unified_embedding/handlers/compaction.py` (4).

Single-import consumers:
- infra: routes/word_similarity_cache_generator/lambdas/index.py, routes/address_similarity_cache_generator/lambdas/index.py, chromadb_compaction/lambdas/enhanced_compaction_handler.py, resegment_receipt_lambda/lambdas/resegment_receipt.py, merge_receipt_lambda/lambdas/merge_receipt.py, label_refresh_lambda/lambdas/label_refresh.py, fix_place_lambda/lambdas/fix_place.py, label_evaluator_step_functions/lambdas/unified_receipt_evaluator.py + utils/s3_helpers.py, upload_images/container_ocr/handler/ocr_processor.py, combine_receipts_step_functions/lambdas/embedding_utils.py, embedding_step_functions/unified_embedding/utils/dual_chroma_client.py + handlers/{line_polling,word_polling,submit_openai,submit_words_openai}.py, mcp_server_lambda/lambdas/receipt_mcp_server_server.py.
- receipt_upload: section_verifier.py, section_assignment.py, label_validation/validator.py, font_letter_analysis.py, receipt_processing/rows.py.
- receipt_agent: utils/chroma_helpers.py, lifecycle/embedding_manager.py, agents/place_validator.py, agents/question_answering/tools/search.py, clients/factory.py; examples/{batch_validate,validate_single_receipt}.py.
- tools/glyph-studio/py/glyphstudio/section_propagate.py.
- receipt_ocr_swift/Scripts/{generate_section_parity,generate_receipt_structure_parity}.py.

### Env vars (28 names, by occurrence)
CHROMADB_BUCKET 122, CHROMA_CLOUD_API_KEY 63, CHROMA_CLOUD_TENANT 54, CHROMA_CLOUD_DATABASE 52, CHROMA_CLOUD_ENABLED 51, CHROMADB_RELEVANT_FIELDS 19, CHROMA_TOOLS 14, CHROMA_WORDS_DIRECTORY 13, CHROMA_LINES_DIRECTORY 13, CHROMA_PERSIST_DIRECTORY 10, CHROMADB_BUCKET_NAME 7, CHROMA_COLLECTIONS 7, CHROMADB_PATH 4, CHROMA_NOT_CONFIGURED_MESSAGE 4, CHROMA_MAX_N_RESULTS 4, CHROMA_IMAGE_CONTEXTS 4, CHROMA_ENV_VARS 4, CHROMA_SIMILARITY 3, CHROMADB_WORDS_QUEUE_URL 2, CHROMADB_LINES_QUEUE_URL 2, CHROMADB_STORAGE_MODE 2, CHROMA_API_KEY 2, CHROMA_ALLOW_CHECKSUM_BYPASS 2, CHROMA_ROOT 1, CHROMA_OPENAI_API_KEY 1, CHROMA_COLUMN 1. Creds also in Pulumi.dev.yaml / Pulumi.prod.yaml.

### Frontend / other languages
- Swift: comment only (receipt_ocr_swift/Sources/ReceiptOCRCore/Sections/SectionAssignment.swift:5).
- TypeScript: portfolio/types/api.ts:330-337,735-797; components/ui/Figures/WordSimilarity.tsx:708-714; LabelValidationVisualization/index.tsx:23,29-31,382-394; QAAgentFlow.tsx:644; Logos/ChromaLogo.tsx + Logos/index.tsx:15.
- glyph-studio: tools/glyph-studio/server/env.mjs:61; py/pyproject.toml:16,20.

### pyproject dependency declarations
receipt_chroma/pyproject.toml:26,180; receipt_agent/pyproject.toml:8,20,33,35,135; receipt_upload/pyproject.toml:32,58; receipt_dynamo_stream/pyproject.toml:28; infra/pyproject.toml:60,64,69,125,131; infra/chromadb_compaction/lambdas/pyproject.toml:6,8,19; tools/glyph-studio/py/pyproject.toml:16,20.

## 4. Docs (~90 .md files reference Chroma)

Live/load-bearing (update): CLAUDE.md:422,633; AGENTS.md:10-11,18; README.md:95,109,194,207; scripts/README.md:22; docs/README.md; docs/development/setup.md; receipt_chroma/README.md (18 hits); receipt_agent/README.md (16); receipt_dynamo_stream/README.md:10; .github/pull_request_template.md:21.

Delete/archive — docs/ (hit counts): CHROMADB_CLIENT_CLOSING_WORKAROUND.md 53, development/TESTING_STRATEGY.md 23 (already stale), RECEIPT_LABEL_MIGRATION.md 17, UNIFIED_LABELING_PIPELINE.md 12, local-analytics-cache.md 11, chromadb-compaction-strategy.md 11, DELTA_VALIDATION_AND_RETRY_IMPLEMENTATION.md 9, SECTION_GEOMETRY_EMBEDDING_EXPERIMENT.md 8, architecture/mac-ocr-aws-handoff.md 18, ocr-migration-runbook.md 5, legacy_receipt_label_flows.md 5, RECEIPT_MERGE_AND_CLUSTERING_GAMEPLAN.md 3, DATA_MIGRATION_DEV_TO_PROD.md 3, docs/README.md 2, QA_AGENT_MODEL_COMPARISON.md 2, UPLOAD_DETERMINISM_D2_EVALUATION.md 2, Upload_Process_refactor.md 1, txinfo_fresh_shadow_protocol.md 1, development/setup.md 4, architecture/CANONICAL_FIELDS_DEPRECATION.md 1, line-items/STATE_OF_THE_SYSTEM.md 1, line-items/PLAN.md 1, line-items/retro/retro_historian.md 1, handoffs/HANDOFF-CODEX-SPRINT-FIX.md 1.

infra/ docs: fix_chromadb_buckets.md 31, VALIDATION_PIPELINE_CHROMADB_MIGRATION.md 28, README_CHROMADB_METRICS.md 19, LAMBDA_LAYER_SIZE_ANALYSIS.md 18, PR_IMPROVEMENTS_SUMMARY.md 5, chromadb_compaction/README.md 19 + tests/README_NEW.md 18 + tests/README.md 15 + README_stream_processor.md 14 + tests/MIGRATION.md 11 + QUEUE_STRATEGY.md 9 + tests/TESTING_GUIDE.md 7, embedding_step_functions/WORD_INGEST_MIGRATION_GUIDE.md 52 + MEMORY_OPTIMIZATION.md 30 + MIGRATION_COMPLETE_SUMMARY.md 24 + WORKFLOW_STEPS_REFERENCE.md 14 + README.md 5 + tests/README.md 1, combine_receipts_step_functions/README.md 5.

Package docs: receipt_agent/receipt_agent/lifecycle/RECEIPT_LIFECYCLE_WALKTHROUGH.md 24, receipt_agent/README.md 16, lifecycle/README.md 15, docs/ACCESS_PATTERNS.md 13, CHANGELOG.md 3, REFACTOR_SUMMARY.md 2, MIGRATION_STATUS.md 1, LINE_LENGTH_BATCH_PLAN.md 1, tools/README.md 1; receipt_chroma/README.md 18; receipt_langsmith/docs/chromadb_evidence_gaps.md 14; receipt_dynamo_stream/README.md 1; tools/glyph-studio/FONT_INTELLIGENCE_EPIC.md 9 + ROW_SCHEMA.md 8 + GAMEPLAN.md 1; portfolio/components/ui/Figures/LabelEvaluatorVisualization/SCANNER_FLOW.md 2; .github/pr-reviews/reports/REVIEW_SYSTEM_SUMMARY.md 2.

~30 more under docs/archive/ — historical, leave alone.

## 5. Embedding-status fields outside chroma packages

Definition: `receipt_dynamo/receipt_dynamo/constants.py:73` — `EmbeddingStatus` enum (NONE/PENDING/SUCCESS/FAILED/NOISE).

Entities: receipt_dynamo entities receipt_word.py, receipt_line.py, receipt_text_geometry_entity.py, embedding_batch_result.py, entity_factory.py.

Data layer: receipt_dynamo/data/_receipt_word.py, _receipt_line.py, _embedding_batch_result.py.

Tests: receipt_dynamo unit test_receipt_word.py, test_receipt_line.py, test_embedding_batch_result.py, test_batch_result_data_ops.py, test_entity_foundation_contracts.py, test_misc_record_contracts.py, test_util_serialization.py; integration test__receipt_word.py, test__receipt_line.py.

Non-test consumers:
- receipt_agent/receipt_agent/graph/nodes.py
- infra/label_evaluator_step_functions/evaluator_types.py, lambdas/utils/serialization (utils module)
- infra/embedding_step_functions/simple_lambdas/{backfill_control,find_unembedded,mark_batches_complete}/handler.py
- infra/embedding_step_functions/unified_embedding/embedding_ingest.py, handlers/{find_unembedded,find_unembedded_words,line_polling,submit_openai,submit_words_openai}.py
- synthesis_loop/backfill_reverse_ocr.py
- scripts/{copy_dynamodb_dev_to_prod,ocr_migration_apply,ocr_migration_rehearsal,sync_receipt_ocr_dev_to_prod,reconcile_dev_to_prod,reset_embedding_status,test_realtime_embedding}.py
