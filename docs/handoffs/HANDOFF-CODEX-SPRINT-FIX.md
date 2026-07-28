# Handoff: Remediate the receipt-render-fidelity sprint (PRs #1238–#1253)

Repo: `~/Portfolio`. You (codex) opened 16 draft PRs in a render-fidelity sprint. An
independent multi-agent review found the *code* is good (test-first, real evidence, no slop)
but the *integration* is broken: everything is stranded in draft, CI validates none of it,
and there are hidden cross-concern stacks and one hard conflict. Your job is to make this
batch trustworthy and mergeable **without throwing away the work**. Do NOT re-slice the PRs
into new ones — fix them in place.

## Ground truth you must internalize before touching anything
1. **CI does not test your work.** `.github/workflows/main.yml` Python matrix =
   `[receipt_dynamo, receipt_dynamo_stream, receipt_chroma, receipt_places, receipt_langsmith,
   receipt_upload]`. `receipt_agent` is only `pip install --no-deps` as a dependency; its own
   `tests/` never run. Root `tests/` and `scripts/test_*.py` have no job either. **A green
   check means nothing for renderer PRs.** You must run pytest locally to trust anything.
2. **All 16 PRs are DRAFT** → CodeRabbit auto-skips and nothing can merge.
3. **The two "red" PRs (#1249, #1250) are false reds** — flaky `Browser Tests` only
   (localhost:3001 port collision / webkit connect on the mac-mini runners). They touch no
   TS/frontend code. Re-run, don't "fix."
4. **Hidden cross-concern stacks** (bases are `codex/*` branches, not `main`), which also means
   they skipped the whole test matrix:
   - `#1239 ← #1240 ← #1241 ← #1251` — #1251 is a **Gelson's** fix stacked on three **Costco** PRs.
   - `#1250 ← #1253` — both Wild Fork (same topic, acceptable).
5. **#1245 ⇄ #1246 hard-conflict**: both add a function `_inbody_barcode_payload` with
   *incompatible* signatures and both add a test class `TestInbodyBarcodePayload` to
   `tests/test_render_systemic_fixes.py`.
6. **#1240/#1241 "Costco-only" proof is an illusion.** The "Vons/Sprouts byte-identical" guard
   only holds because those merchants have no `layout_template`. Measured-layout code fires for
   **all 4** merchants that have one: `Costco Wholesale`, `Gelson's Westlake Village`,
   `The Stand`, `Dollar Tree`. Gelson's silently gains 2 drawn separator rules — with **no
   before/after evidence**.

## Guardrails (do not violate)
- **Do NOT merge any PR.** Merges are owner-gated. Your endpoint is "ready-for-review, full CI
  green, evidence complete." The owner pulls the trigger.
- **Run tests locally** in a Python 3.12 venv with the editable local stack
  (`pip install -e receipt_dynamo receipt_agent ...`). Report pass counts per PR.
- **Do NOT force-resolve the barcode conflict by deleting one merchant's path.** Unify both.
- Lint: black line-length **79** (python), prettier (TS). One logical fix per commit; keep the
  capture-red → fix → prove-pass + `before/after.json` evidence convention — it's the best part
  of your work.
- Keep changes minimal and legacy-preserving (each layer must stay a no-op when its data is absent).

## Tasks, in order

### 1. Land the CI fix first (separate PR off `main`)
Branch `fix/ci-cover-receipt-agent` off `main`. Add `receipt_agent` (and a root-`tests/` +
`scripts` pytest invocation) to the matrix in `.github/workflows/main.yml` so renderer/agent
tests actually run. Expect to wire up deps and a sane `--timeout`. Get it green, mark ready.
This unblocks trustworthy CI for everything below. If full coverage is too big in one step,
at minimum add a `receipt_agent` job that runs `cd receipt_agent && pytest tests`.

### 2. Resolve the #1245 ⇄ #1246 barcode conflict
Unify into **one** `_inbody_barcode_payload` supporting both options — Sprouts
(`payload_from_hri`, with the leading-`99` Code128 encoder fix) and Vons
(`payload_shaping="raw"`, Code C wide modules). Merge the two `graphics_profile_for_merchant`
merchant blocks and collapse the duplicate `TestInbodyBarcodePayload` into one class covering
both merchants. Put the unified change on #1245; rebase #1246 on top so its diff is minimal.

### 3. Un-stack the cross-concern dependency (#1251)
Determine whether #1251 (Gelson's flag lanes) actually depends on #1241's new code in
`receipt_grid.py`/`test_measured_layout.py`:
- If it compiles + tests pass against **`main`**, rebase #1251 onto `main` so it's independent.
- If it genuinely needs #1241's measured-column infra, **fold #1251 into #1241** (they're the
  same subsystem) rather than leaving a Gelson's PR stacked on Costco.
Leave the same-topic stacks (#1239→#1240→#1241, #1250→#1253) as stacks, but ensure each PR
**retargets its base to `main`** as its parent lands so the full matrix runs on every one.

### 4. Close the #1240/#1241 evidence gap (correctness — highest priority)
Render `Gelson's Westlake Village`, `The Stand`, and `Dollar Tree` before/after for both PRs.
For each: either commit `before/after.json` proving no unintended change, or fix the regression
(Gelson's 2 drawn rules must be *correct*, not incidental). The "Costco-only" framing in the PR
descriptions is wrong — update it to name the real blast radius (all 4 `layout_template` merchants).

### 5. Make CI actually run, then clear reds
After #1 lands and the stacked PRs retarget to `main`, confirm the full matrix runs on **every**
PR (especially #1241, #1251, #1253, which previously ran only CodeRabbit + GitGuardian). Re-run
the flaky Browser Tests on #1249/#1250 until green (infra flake, no code change).

### 6. Mark ready-for-review — but do not merge
Once a PR is (a) full-CI green, (b) locally test-verified, (c) evidence-complete, lift its draft
flag so CodeRabbit runs. Recommended readiness order (dependencies respected):
`#1238, #1247, #1249, #1252, #1242→#1244→#1243→#1245→#1246` (independents/Sprouts), then
`#1239→#1240→#1241(+#1251)` (Costco, after evidence), then `#1250→#1253` (Wild Fork).
Leave #1240/#1241 draft until their evidence gap (task 4) is closed.

## Report back
For each PR: local pytest pass count, CI status after retarget, evidence added, ready/held +
one-line reason. List anything you could not resolve and why. Do not merge; hand the merge
decision to the owner.
