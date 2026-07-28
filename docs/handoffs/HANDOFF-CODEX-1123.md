# Handoff: Issue #1123 — Restore receipt-stack rendering in the QA visualization

Repo: `~/Portfolio` (work on a branch off `origin/main`; main is current as of 2026-07-14).
Issue: https://github.com/tnorlund/Portfolio/issues/1123

## The problem
The QA agent figure on `/receipt` (dev.tylernorlund.com/receipt) used to render a stack of
receipt thumbnails as evidence under each answer. Commit `f4e60c226` deleted that rendering
(~505 lines) from `portfolio/components/ui/Figures/QAAgentFlow.tsx`. The DATA still flows
end-to-end and was verified live on 2026-07-14 — the component just never reads it.

## The intact data contract (verified)
`GET https://dev-api.tylernorlund.com/qa/visualization?index=N` returns per-question:
- `trace: TraceStep[]` where `TraceStep.receipts?: ReceiptEvidence[]`
- `ReceiptEvidence = { imageId, merchant, item, amount, thumbnailKey, width, height }`
  (see `portfolio/hooks/qaTypes.ts:3-11`; producer: `receipt_langsmith/receipt_langsmith/spark/qa_viz_cache_helpers.py:472-512`)
- Example: the dairy question (index 2) carries 217 evidence entries. `thumbnailKey` is a
  CDN path — prepend `getCdnBaseUrl()` from `portfolio/utils/cdnBase.ts` (NEVER hardcode a
  domain; dev builds bake NEXT_PUBLIC_CDN_URL).
- The `synthesize` trace step carries the answer markdown; evidence rides on that step today.

## Task
1. Restore a receipt-thumbnail stack in `QAAgentFlow.tsx` fed from `TraceStep.receipts`
   (dedupe by imageId; a small overlapping-stack or grid — look at `git show f4e60c226^:portfolio/components/ui/Figures/QAAgentFlow.tsx`
   for the deleted implementation as reference, but a simpler clean version is fine).
2. Optionally render `structuredData` where present.
3. Keep it responsive + theme-aware like sibling figures (see ReceiptStack.tsx for image
   loading with format fallback via `utils/imageFormat.ts`).

## Bonus quick wins (separate commits, same branch ok — all audited findings)
- `receipt_agent/receipt_agent/agents/question_answering/graph.py:550`: `state_holder["aggregated_amount"]`
  is read but NEVER written → `totalAmount` is always null. Write it when aggregation tools run.
- `receipt_agent/.../tools/search.py:310-336`: `search_type="label"` queries a scalar `label`
  metadata key that no longer exists (schema moved to `valid_labels_array`) — fix the where-filter
  or remove the branch + update SYSTEM_PROMPT (which currently recommends the broken call).
- Inject today's date into PLAN_SYSTEM_PROMPT/SYSTEM_PROMPT so relative-date questions resolve.
- The 32-question list is duplicated in `infra/qa_agent_step_functions/lambdas/run_question.py:30-63`
  and `portfolio/components/ui/Figures/QuestionMarquee.tsx:4-37` — single-source it.

## Verification
- `cd portfolio && npm ci && npm test` (component tests) and the e2e mock spec `e2e/qa-agent-flow.spec.ts`.
- Local render: `npm run dev` → http://localhost:3000/receipt (dev proxy hits dev-api).
- Do NOT deploy; open a PR. CI must be green (black line-length 79 for python, prettier for TS).
