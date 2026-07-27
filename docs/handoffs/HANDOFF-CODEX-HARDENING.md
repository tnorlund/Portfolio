# Handoff: implement the render-synthesis hardening plan

Repo: `~/Portfolio` on the Mac mini. Read
**`docs/plans/RENDER_SYNTHESIS_HARDENING_2026-07-24.md` in full before touching
anything** — it has the diagnosis, the file:line citations, and the reasoning
behind every task below. This document is the execution order.

Context: 12 of the 17 PRs from your render-fidelity sprint are merged to `main`.
`#1254` added real CI for `receipt_agent` and the repo-root tests, so unlike last
sprint **a green check now means something for renderer work.** Five PRs are
stranded and the system has structural problems the plan describes.

## Guardrails — violating any of these is worse than not doing the task

1. **NEVER write to DynamoDB.** Read-only boto3 queries are fine. In particular:
   **do NOT seal or activate Costco truth v2.** Its gate record says
   `overall=FAIL` (arithmetic, columns, separators, tokens). The gate is correct
   to refuse it. Do not run `mint_merchant_truth_v2.py --live`,
   `activate_merchant_truth.py`, or `promote_merchant_truth.py`. If you believe a
   Dynamo write is required, STOP and write down why instead.
2. **Do NOT merge any PR.** Mark ready-for-review; the owner merges.
3. **Base every branch on `main`. Never branch from another PR's branch.**
   Stacked PRs skip the whole CI matrix (`main.yml:6-7` fires only on
   `pull_request: branches: [main]`) and squash-merging a parent kills its
   children — that is what closed #1240 last time.
4. **Renderer changes must be byte-identical unless the PR explicitly intends a
   pixel change.** Prove it with
   `python3.12 synthesis_loop/render_regression_guard.py check /tmp/rrg` — the
   **production** renderer, SHA-256 pinned. It needs AWS + the dev table + S3,
   which you have on the mini. A step that cannot be made byte-identical is too
   big — split it.

   **Do NOT use `corpus_regression_gate.py` as your byte-identical proof.** It
   cannot detect a rendering regression: all three of its fixtures have
   `manifest_words == syn_words` (verified byte-equal), and it renders both sides
   with a toy 1px-comb stand-in, not the production renderer
   (`corpus_regression_gate.py:140-171` says so itself). **It compares an image to
   itself.** It is a legitimate *eval-logic determinism* pin — keep running it, it
   catches threshold and variant-selector drift — but it proves nothing about
   pixels. `costco_wholesale_v1` is invented content (`BANANAS / MILK / EGGS`),
   not a real Costco receipt; the name is misleading.
5. **Never edit `synthesis_loop/corpus_baseline.json` or
   `render_regression_baseline.json` to make a failing metric pass.** Recapture is
   only for reviewed, intended changes.
6. Lint: `black --line-length 79`, `isort --profile=black --line-length 79`.
   Run tests locally in a Python 3.12 venv with the editable local stack before
   pushing (see `main.yml:133-152` for the exact install recipe).
7. Keep the capture-red → fix → prove-pass commit discipline. It is the best part
   of your work and it caught a real regression last sprint.

---

## Task 1 — Rescue the 5 stranded PRs (do this first; it unblocks the sprint)

All five branches are intact on the remote. Each needs a rebase onto current
`main` and a force-push. The owner could not do this from their laptop.

| PR | branch | state |
|---|---|---|
| #1240 | `codex/costco-separator-placement` | CLOSED — reopen after rebase |
| #1241 | `codex/costco-measured-columns` | conflicted; depends on #1240 |
| #1243 | `codex/sprouts-inferred-separator` | one add/add hunk in `receipt_renderer.py` |
| #1248 | `codex/grocery-structural-gap-separators` | conflicts at the same anchor |
| #1249 | `codex/scaled-row-amount-lanes` | conflicts in `receipt_agent/tests/test_section_style.py` |

#1239 and its stack parents were **squash-merged**, so the children still carry
the parents' pre-squash commits. Do NOT plain-rebase those onto main — you will
replay commits already in `main`. Use:

```
git rebase --onto origin/main <parent_pr_head_oid> <branch>
```

For #1240 the parent head oid is `37011010a5a45ff0395d3daf7e12555fa26a3b94`
(#1239's head). That rebase is verified clean and yields exactly 3 commits.
#1241 then rebases onto the new #1240. #1243/#1248/#1249 rebase onto `main`
directly.

Conflict resolution notes:
- #1243, #1248 and #1240 all insert new module-level helpers at the **same
  anchor**, the gap after `_draw_dash_row` (`receipt_renderer.py:488`). These are
  add/add conflicts — keep **both** sides, do not drop either merchant's helper.
- #1249 and #1241 both rewrite the lane decision at `receipt_renderer.py:1057`
  with **mutually exclusive semantics** (pixel projection vs
  `layout_columns_by_section` lookup). This is a real semantic conflict, not
  textual. Do not guess. Resolve so both paths coexist behind explicit
  conditions, and if you cannot, STOP and write down the tradeoff for the owner.

For each: rebase → run `receipt_agent` tests + repo-root tests → force-push →
reopen (#1240) → retarget base to `main` → mark ready-for-review. Report the
local pass count per PR. **Do not merge.**

## Task 2 — Make CI a gate (small, highest ROI in the plan)

Two commits on one branch `chore/ci-gating`:

1. `.coderabbit.yaml` — add the missing block. `drafts` defaults to `false`,
   which is why all 16 sprint PRs got zero external review:
   ```yaml
   reviews:
     auto_review:
       drafts: true
   ```
2. New `.github/CODEOWNERS`:
   ```
   /receipt_agent/receipt_agent/agents/label_evaluator/rendering/  @tnorlund
   /synthesis_loop/corpus_baseline.json                            @tnorlund
   /synthesis_loop/render_regression_baseline.json                 @tnorlund
   ```

**The branch ruleset is owner-only — do not attempt it.** Instead, put the exact
`gh api` command the owner should run in the PR body, requiring status checks
`Python (receipt_agent)`, `Python (repository tests)`, `Lambda Syntax`,
`TypeScript` (these are job `name:` values from `main.yml:53` and `:196`, not job
ids), plus one approving review and block-force-push, targeting `main` only.

## Task 3 — Verify evidence against ACTIVE truth (R1 + R2)

This is the check that would have caught the sprint's misleading Costco evidence.

`full_fidelity_eval.build_stamp` (`synthesis_loop/full_fidelity_eval.py:467-489`)
already emits `{"git_sha", "dirty", "merchant_truth": {slug, version,
bundle_hash, mode, expected_version, expected_bundle_hash}}`. Nothing verifies it.

Write `scripts/verify_evidence_stamps.py`. For every evidence file carrying a
`stamp`, fail when:
- `mode != "online-active"` (a `fixture` stamp means the bundle was fabricated —
  `mint_merchant_truth_v2.py:705-722` hardcodes `status=SEALED`/`PASS` into a temp
  fixture, which is exactly how Costco's v2 evidence came to exist)
- `get_active_merchant_truth(slug) != (stamp.version, stamp.bundle_hash)`
- `dirty` is true
- `git_sha` is not an ancestor of the PR head

Error text must name both bundles, e.g. *"evidence/X measured costco_wholesale v2
(6b709eb0…) but ACTIVE is v1 (c5cd3120…); this evidence describes a bundle nobody
uses."*

Wire it into `main.yml` alongside a `corpus_regression_gate.py check --json` step.
The gate needs no AWS; the stamp verifier needs read-only Dynamo — if CI creds are
a problem, have the job read a committed `evidence/ACTIVE_FLEET.json` snapshot and
add a separate refresh path. Say which you chose and why.

Add tests. Include a fixture reproducing the Costco v2 case and assert it fails.

## Task 3b — Fix the inverted gates (do this BEFORE Tasks 4-5; they depend on it)

Right now **the only gate that runs in CI cannot detect a rendering regression,
and the only gate that can does not run.** Tasks 4 and 5 are refactors whose
entire safety argument is "prove byte-identical" — that argument is unsupported
until this is fixed.

1. **Extend `render_regression_guard.py` coverage.** Its `PINNED` set (`:49-74`)
   is 4 receipts across 3 merchants (Costco ×2, Vons, Sprouts). **Gelson's,
   Trader Joe's, Wild Fork, The Stand and Dollar Tree are uncovered — including 3
   of the 5 merchants the sprint claims to have improved.** Add one pinned
   receipt for each, recapture `render_regression_baseline.json`, and label that
   recapture clearly in the PR (it is a baseline change, so it needs the owner's
   approving review per guardrail 5).
2. **Wire it to a nightly/dispatch workflow** on a self-hosted macOS runner with
   dev-read creds: `python3.12 synthesis_loop/render_regression_guard.py check
   /tmp/rrg`. It cannot go in PR CI (needs AWS), but "re-derived nightly" beats
   today's "re-derived by nothing" — its only test
   (`tests/test_render_regression_guard.py:24`) monkeypatches `_render_all` away
   and never renders a pixel.
3. Keep `corpus_regression_gate.py` in PR CI as the eval-logic pin it actually
   is, and **fix its misleading naming** — rename the `costco_wholesale_v1` entry
   to something like `synthetic_selftest_v1`, or add a header comment stating
   plainly that its inputs are invented and both sides are identical. The current
   name invites the reading that a real Costco receipt is under standing
   protection. It is not.

## Task 4 — Renderer reads `layout_template.separators` (the root-cause fix)

Read the plan's diagnosis section first. Summary: measured separators/columns are
captured, hash-verified, stored, loaded into the profile at
`scripts/render_synthetic_receipts.py:1235` — and **read by no renderer code
path**. `tools/glyph-studio/py/glyphstudio/layout_template.py:262-266` says so
outright: *"NOT yet consumed by the renderer (P3)."* That gap is why every
merchant campaign becomes a hand-written heuristic in one shared 545-line
function, which is what generates the merge conflicts.

Add a `separators` field to `RenderConfig` (`receipt_renderer.py:109-228`),
plumbed through `merchant_typography` (`render_synthetic_receipts.py:1619`), which
already passes unknown profile keys through verbatim at `:1655`. Semantics — both
Costco branches converged on these independently, so follow them:

- `None` → legacy heuristics, **byte-identical to today**
- `[]` → authoritative "draw only literal OCR rules, invent nothing"
- non-empty → draw at the measured `pos_frac_med`

Ship this **alone**, with `None` proving byte-identical output across the corpus
gate. Do not flip any merchant onto measured separators in the same PR — that flip
becomes a Dynamo write later, owner-gated, not a code change.

## Task 5 — Extract `rendering/separators.py` (pure motion, no behavior change)

Move `_separator_anchor_rows` (`receipt_renderer.py:383`) and `_separator_layout`
(`:429`) into a new `receipt_agent/.../rendering/separators.py`. Separator sources
become a list of functions that `_render_grid` iterates, so a future separator fix
is a **new file plus one list entry** instead of an insertion into a shared
anchor. Two of the four collision points from Task 1 disappear.

Zero behavior change; prove byte-identical. Note for context: both Costco branches
independently shipped *byte-identical* re-extractions of these same two functions
because no shared base landed first — this task is that missing shared base.

**Stop after Task 5.** Splitting `_render_grid` into plan/paint (plan Phase 3.3)
is the next step but needs a design pass against the actual control flow first;
propose a design, do not implement it.

---

## Context you must know, but must NOT act on unilaterally

An audit found that **the synthesis loop has no validated link to its downstream
objective.** `receipt_layoutlm/.../data_loader.py:524-556` trains only on real,
human-`VALID`-labelled words — there is no synthetic ingestion branch, no source
flag, no dataset-mixing parameter. `scripts/render_synthetic_receipts.py:1-16`
describes itself as a QA artifact that "touches no gate," and the bridging script
it names, `verify_synthetic_replay.py`, **does not exist on `main`** — it lives
only in unmerged worktrees under `.claude/worktrees/`. The retrospective never
mentions LayoutLM, training, or F1 at all.

So the chain *better fidelity → better synthetic training data → higher F1* is
unmeasured at every link, and the seven metrics are self-referential: thresholds
were calibrated on the same six receipts they validate
(`full_fidelity_eval.py:184-189` sets `TOKEN_INK_RECALL_MIN = 0.97` from two
readings of one Gelson's receipt).

Related: the eval corpus is **one receipt per merchant, 6 of 314 available
(1.9%)** — Sprouts is evaluated on 1 of 199. The §7.2 variant machinery exists
*because* merchants print multiple layouts, so the variant selector is currently
validated at n=1 per merchant.

**Do not try to fix this yourself in this pass.** It needs an owner decision
about priorities. What you SHOULD do, cheaply, at the end:
- Read one worktree copy of `verify_synthetic_replay.py` and report whether it
  would actually close the training loop or merely audit a Step Function. That
  determines whether the bridge is mostly-built or unbuilt.
- Report what it would take to raise the eval corpus from 1 to 3 receipts per
  merchant (18 total), variant-stratified. Do not build it; scope it.

---

## Preservation warning

`_render_grid` is dense with *earned* behavior: the thermal pitch-floor
derivation (`:736-743`), the bitmap-advance rule (`:1039-1041`), the two-dot
reinforcement (`:1076-1083`), the dash baseline offset (`:480-484`). Every one is
a fidelity bug someone paid for. A refactor that silently drops one surfaces as a
gate FAIL three merchants later and nobody will suspect the refactor. Gate every
step on byte-identical output.

## Report back

Per task: what you changed, local test pass counts, CI status, and ready/held with
a one-line reason. List anything you could not resolve and why — especially the
#1249/#1241 lane conflict if you could not make both paths coexist. Do not merge;
hand the merge decision to the owner.
