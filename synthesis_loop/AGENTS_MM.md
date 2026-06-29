# Multi-merchant synthesis hill-climb — agent contract (codex brain)

> Read automatically by Codex when it runs under `synthesis_loop/` for the multi-merchant loop (`run_loop_mm.sh`).

## Mission
Make synthetic receipts for **all 8 merchants** (Amazon Fresh, Costco, Gelson's, Smith's, Sprouts, Target,
The Home Depot, Vons) more realistic and better LayoutLM training data. The score is the **objective verifier**
(`verify_candidates.py`) averaged across **every merchant** — it cannot be gamed like the old opus-judge loop.

## The non-negotiable rule (this is why we built it this way)
**Every fix MUST be generalizable and apply to ALL merchants.** Earlier, fixes were added only to
`sprouts_parameterization.py`, so only Sprouts improved. Now the reconciliation passes live in ONE shared module
that every merchant uses:
- **Edit `receipt_agent/receipt_agent/agents/label_evaluator/synthesis_reconcile.py`** (the shared passes:
  header dedup, payment dedup, de-overlap + re-space, label sanitization) — a fix here reaches every merchant.
- Or the **generic** `merchant_synthesis.py` path (the `_flatten_lines` chain, used by all non-Sprouts merchants).
- **NEVER** edit a single-merchant file (`sprouts_parameterization.py`) to "fix" a problem — if Sprouts has it,
  the generic path probably has it too; fix it once, in the shared module.
- If a defect is genuinely merchant-format-specific, generalize it (drive it off the candidate's `merchant_name`
  / data), don't hardcode one merchant.

## Each round you get `state/feedback.json`
- `per_merchant`: each merchant's mean score + which verifier checks still fail (e.g. `costco: {arithmetic: 12/16}`).
- `line_review`: opus's **line-level** visual critique of one merchant's render — `worst_lines` and `bad_lines`
  (`{n, text, issue}`) — catching visual tells the structural checks miss. Use these to find the real problem.

Pick the **single highest-impact** fix: prefer one that lifts a check failing across MANY merchants (lowest-scoring
merchant first), or a line-review issue seen repeatedly. The loop reverts your commit automatically if it doesn't
raise the cross-merchant `overall_mean`, so aim for real, generalizable gains.

## Verifier checks (what "better" means — keep all of them passing)
`bbox_valid` (no inverted/degenerate boxes), `no_overlap`, `word_spacing` (real gaps between words),
`single_header`, `single_payment_block`, `no_garble`, `arithmetic` (line totals + tax = grand total),
`labels_valid` (entity content matches its label). Do not regress a passing check to fix another.

## Hard guardrails (no approval is asked)
- Edit ONLY the shared/generic synthesis code + render layer. Never merchant-specific hacks.
- Do NOT render / push / call AWS / spawn claude / merge / deploy — the loop's shell does regen + scoring + the
  opus line review as sibling processes.
- Never touch `main`/`integration/*`; never force-push.
- Commit one change per round, named by the check + merchants targeted.
