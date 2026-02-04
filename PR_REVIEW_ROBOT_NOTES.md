# PR 696 Robot Review Notes

PR: `feat(qa-agent): add question-answering agent with ReAct RAG workflow`

Branch: `feat/qa-agent`

Current commit: `85e94ef5c7f0b6eca0b764da7d02abe8be520fc1`

Last updated: 2026-02-04

## Serious Issues To Fix

| ID | File | Issue | Status |
| --- | --- | --- | --- |
| 1 | `receipt_langsmith/receipt_langsmith/spark/qa_viz_cache_job.py` | Loader functions swallow exceptions and can return empty results; catch specific errors, log malformed NDJSON lines, and raise to avoid silent success. | DONE |
| 2 | `infra/qa_agent_step_functions/infrastructure.py` | Guarded import sets `CodeBuildDockerImage` / `dynamo_layer` to `None` but uses them later; remove guard or add explicit checks with clear errors. | DONE |
| 3 | `receipt_langsmith/receipt_langsmith/spark/qa_viz_cache_job.py` | `_write_cache_from_ndjson` ignores `max_questions`; apply the limit and pass through from callers. | DONE |
| 4 | `receipt_langsmith/receipt_langsmith/spark/qa_viz_cache_job.py` | `_classify_run` checks `"tool"` before `"agent"`; reorder to avoid misclassification. | DONE |
| 5 | `receipt_langsmith/receipt_langsmith/spark/qa_viz_cache_job.py` | Metadata `total_questions` should reflect cached items (len(cache_files)) or include a new `cached_questions` field; keep average cost consistent. | DONE |
| 6 | `portfolio/hooks/useQAQueue.ts` | Stale fetches can overwrite newer selections; track latest requested index and ignore stale responses. | DONE |

## Notes

Source: CodeRabbit review comments on PR 696.
Attempted GH API refresh on 2026-02-04; `gh pr view` intermittently failed with API connection errors.

## Robot Actionable Comments (All)

| ID | File | Issue | Status |
| --- | --- | --- | --- |
| A1 | `receipt_langsmith/receipt_langsmith/spark/qa_viz_cache_job.py` | Loader functions swallow exceptions; catch specific errors, log malformed NDJSON lines, raise on failures. | DONE |
| A2 | `receipt_langsmith/receipt_langsmith/spark/qa_viz_cache_job.py` | Metadata `total_questions` should reflect cached items or add `cached_questions`; keep avg cost consistent. | DONE |
| A3 | `receipt_langsmith/receipt_langsmith/spark/qa_viz_cache_job.py` | `_classify_run` should check `agent` before `tool` to avoid misclassification. | DONE |
| A4 | `receipt_langsmith/receipt_langsmith/spark/qa_viz_cache_job.py` | `_write_cache_from_ndjson` should respect `max_questions`; pass through from callers. | DONE |
| A5 | `infra/qa_agent_step_functions/infrastructure.py` | Guarded import sets `CodeBuildDockerImage` / `dynamo_layer` to `None` but they’re used later; remove guard or add explicit checks. | DONE |
| A6 | `portfolio/hooks/useQAQueue.ts` | Stale fetch responses can overwrite newer selections; track latest requested index and ignore stale responses. | DONE |
| A7 | `portfolio/components/ui/Figures/QAAgentFlow.tsx` | Replace hard-coded “+2 more” with computed remaining count from `structuredData`. | N/A (not present in current code) |
| A8 | `portfolio/components/ui/Figures/QAAgentFlow.tsx` | Reset effect should respect `autoPlay` prop and include it in dependencies. | DONE |
| A9 | `portfolio/components/ui/Figures/QAAgentFlow.tsx` | Progress bar widths should use `DEFAULT_STEP_MS` for missing durationMs. | DONE |

## Robot Nitpicks / Suggestions

| ID | File | Issue | Status |
| --- | --- | --- | --- |
| N1 | `receipt_langsmith/receipt_langsmith/entities/qa_trace.py` | Use `Literal` for `QATraceStep.type` to constrain allowed step types. | DONE |
| N2 | `receipt_langsmith/receipt_langsmith/entities/qa_trace.py` | Add trailing newline at end of file. | DONE |
| N3 | `receipt_langsmith/receipt_langsmith/entities/__init__.py` | Fix alphabetical import ordering for `qa_trace` vs `place_id_finder`. | DONE |
| N4 | `infra/qa_agent_step_functions/handlers/query_receipt_metadata.py` | Validate `DYNAMODB_TABLE_NAME` early (fail fast). | DONE |
| N5 | `infra/routes/qa_viz_cache/lambdas/index.py` | Fail fast if `S3_CACHE_BUCKET` is missing. | DONE |
| N6 | `infra/qa_agent_step_functions/infrastructure.py` | Scope EMR Serverless permissions to specific application ARN. | DONE |
| N7 | `infra/qa_agent_step_functions/infrastructure.py` | Scope ECR image pull permissions to specific repositories. | DONE |
| N8 | `receipt_langsmith/receipt_langsmith/spark/merged_job.py` | Require `--execution-id` for `qa-cache` job. | DONE |
| N9 | `receipt_langsmith/receipt_langsmith/spark/qa_viz_cache_job.py` | Deduplicate `parse_s3_path` / `to_s3a` with `merged_job.py`. | DONE |
| N10 | `receipt_langsmith/receipt_langsmith/spark/qa_viz_cache_job.py` | Ensure `_classify_run` ordering is explicit (comment if intentional). | DONE |
| N11 | `receipt_langsmith/receipt_langsmith/spark/qa_viz_cache_job.py` | Add explicit exit code handling in `main()`. | DONE |
| N12 | `receipt_langsmith/receipt_langsmith/spark/qa_viz_cache_job.py` | Specify explicit Spark types when adding nullable columns. | DONE |
| N13 | `portfolio/components/ui/Figures/QAAgentFlow.tsx` | Share QA trace types with `portfolio/hooks/useQACache.ts` to avoid drift. | DONE |
