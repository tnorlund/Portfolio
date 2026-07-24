# Agentic Sprints Retrospective — 2026-07-20 → 07-23

Three back-to-back agent-driven sprints (~35 merged PRs in ~4 days) that
took this repo from "render fidelity whack-a-mole" to a governed,
measured, self-improving system. This document is the single wrap-up of
what was built, what state everything is in, and what remains.

## The arc

1. **Whack-a-mole plan** (07-20/21): diagnosed why fidelity work kept
   regressing — knowledge in flat files and chat logs, evidence going
   stale, no gates. Built the **MerchantTruth system**: versioned immutable
   per-merchant bundles in DynamoDB (content-hashed, provenance-carrying,
   owner-gated ACTIVE pointer, append-only audits), cut the renderer AND
   eval over to read only through a fail-closed hash-verifying loader
   (16/16 byte-identical proof), and stood up the nightly loop harness v0.
2. **Costco v2 pilot** (07-22): made Costco the first COMPLETE merchant in
   Dynamo. Contract v3.1 (schema evolution, variants, gate bridge, gate
   records), mined catalog (75 items, attribution-quarantined), 22 curator
   notes preserved as proposals, variant clustering (verdict: **REFUTED** —
   self-checkout is the dominant format, not a variant), v2 minted through
   a real eval gate.
3. **Hardening sprint** (07-22/23, in progress): external citation-backed
   research validated the architecture (ahead of the field on
   mutation-tested guards, belief-revision truth store, cross-vendor
   review, metric-first discipline) and identified the gap: autonomous-loop
   instrumentation. Phase 1 is now MERGED: full stream-json **trajectory
   logging** + authoritative token metrics, a **2M-token circuit breaker**
   (exit 123, fails open on parse errors), a **stall watchdog** (exit 122,
   preserves partial reports), all sharing one monitor with documented
   precedence (token 123 > stall 122 > wall 124), plus the standing
   **corpus regression gate** (3 entries incl. one freezing the §7.2
   variant selector and its tie-break; zero-AWS, byte-deterministic).

## Current live state (dev, ReceiptsTable-dc5be22)

- **Fleet**: 13 merchants ACTIVE at truth v1; 3 blocked on font publication
  (amazon_fresh, dollar_tree, smith_s — assets vaulted, publish staged).
- **Costco**: v1 ACTIVE (PASS bootstrap gate); **v2 OPEN** (bundle
  6b709eb0…), seal blocked by the honest fidelity gate — overall FAIL,
  4 red metrics: tokens (payment-section ink missing, recall 0.72),
  arithmetic, columns, separators. Logo and graphics now PASS. Gate
  records queryable (`list_gate_records`). Catalog: 75 provenance-backed
  items. Proposals: 4 (self-checkout REFUTED at v2 with 24 receipt refs;
  3 preserved curator notes).
- **Both stacks deployed** at main; renderer/eval read only from truth.

## The development machine (how this was built)

- Parallel implementer agents building PR-sized work items from an
  approved plan, each with failing-test-first verification.
- ONE persistent independent reviewer agent on every PR: worktree
  execution, **mutation testing of guards** (remove a guard → a test must
  go red), adversarial repro construction, live read-only evidence checks.
  ~25 reviews this arc; every CHANGES-REQUESTED cycle caught a real defect
  (33× token undercount, gap-list cross-PR mismatch, suppressed curator
  note, orphaning watchdog, catalog INVALID-label contamination…).
- Cross-vendor audits: OpenAI codex sessions did wide-angle reviews whose
  findings became fix PRs (and codex authored two fix PRs itself, held to
  the same review bar).
- Serialized merges, one per main CI run; owner gates on: plan approval,
  policy ratification, batch writes, ACTIVE flips, anything prod.

## Remaining work (in priority order)

**Owner ceremonies (nothing blocks on code):**
1. Nightly loop go-live: `launchctl bootstrap gui/$UID
   ~/Portfolio/infra/ops/com.tnorlund.receipt-nightly.plist`, receipt-tools
   OAuth (Cognito gateway), then ONE supervised run of
   `scripts/nightly/run_nightly.sh` on the new instrumented harness →
   unlocks unsupervised nights.
2. W7 font publish (3 commands staged in PR #1205) → mint the trio
   (`--merchants amazon_fresh,dollar_tree,smith_s --expected-missing-slugs
   "" --live --verify`) → diff → flip → fleet 16/16.
3. Costco v2 seal: fix the 4 red metrics (fidelity campaigns; #1216-class
   render work — the payment-tail is the big one), re-run the mint (it
   resumes), seal, flip.

**Hardening sprint remainder** (plan:
`~/.claude/plans/humble-skipping-quilt.md`):
- H7 CI corpus-gate wiring (its builder agent was lost to a network drop
  mid-implementation — restart it; design + acceptance rehearsal are fully
  specified in the plan).
- H4 resume (optional pre-unsupervised), H5 BRIEF v1 (read-only proposal
  targeting + catalog drift watch), H8 allowlist replacing
  bypassPermissions (derive empirically from trajectories), H9 scoped IAM
  (pulumi), H10 BRIEF v2 (gate-record writes), H11/H12 codex per-PR
  blocking review on high-risk paths.

**Tracked follow-ups**: #1214 stylescan savings-rule bug (may unlock a
real INSTANT-SAVINGS variant), #1208 bad ReceiptPlace row (CVS/In-N-Out),
#1217 section-scale geometry, plus the small carry-forwards logged on
#1190.

## Where things live

- Contract: `docs/architecture/MERCHANT_TRUTH_DYNAMO.md` (v3.1)
- Truth tooling: `scripts/{activate,cleanup,mint_v2,diff}_merchant_truth*`,
  `synthesis_loop/fleet_status.py`, `scripts/ingest_merchant_catalog.py`,
  `scripts/import_crosswalk_notes.py`
- Nightly: `scripts/nightly/` (wrapper, watchdog, trajectory_tools,
  contract checker), `docs/nightly/`, `infra/ops/*.plist`
- Gates: `synthesis_loop/corpus_regression_gate.py`,
  `synthesis_loop/render_regression_guard.py`, gate records in Dynamo
- Ceremony evidence: `~/COSTCO_V2_CEREMONY_REPORT.md`,
  `~/section_label_kickoff/` (mini)
