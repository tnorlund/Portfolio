# Plan: harden the receipt-synthesis system

Date: 2026-07-24. Written after merging 12 of the 17 render-fidelity sprint PRs
(#1237–#1254) and running a four-agent review of the system.

All findings below were independently verified against `origin/main`, the live
GitHub API, and read-only DynamoDB queries. Citations are `file:line`.

---

## The one-paragraph diagnosis

The renderer and its grader read **different sources of truth**. Measured
per-merchant layout (`columns`, `sections`, `separators`) is captured from real
receipts, stored hash-verified in the merchant-truth bundle, and loaded into the
profile at `scripts/render_synthetic_receipts.py:1235` — then **read by nobody in
the renderer**. Only `full_fidelity_eval.py`, `merchant_truth_diff.py`,
`build_variant_layout.py` and migrations consume it. The codebase says so itself
at `tools/glyph-studio/py/glyphstudio/layout_template.py:262-266`: *"NOT yet
consumed by the renderer (P3)."* P3 was never done.

Consequence: every merchant campaign is "invent a Python heuristic until the
measured grader goes green," and every heuristic must land in one shared
545-line function, `_render_grid` (`receipt_renderer.py:618-1162`). That is the
engine that generated 5 merge conflicts across a 16-PR sprint — not bad luck in
PR slicing. Both Costco branches independently invented a `measured_separators`
config field to bridge this exact gap; the code was pointing at the seam.

Two amplifiers made it worse: CI measured nothing relevant (fixed by #1254), and
nothing in the repo *gates* on CI at all (still true).

---

## Phase 0 — Finish the sprint (blocked on decisions)

### 0.1 Land the 5 stranded PRs
`#1241, #1243, #1248, #1249` are CONFLICTING; `#1240` was closed when I
squash-merged its stack parent. All six branches are intact on the remote:

| PR | branch | state |
|---|---|---|
| #1240 | `codex/costco-separator-placement` | closed; rebases clean onto main |
| #1241 | `codex/costco-measured-columns` | conflicted; depends on #1240 |
| #1243 | `codex/sprouts-inferred-separator` | one add/add hunk in `receipt_renderer.py` |
| #1248 | `codex/grocery-structural-gap-separators` | conflicts at the same anchor |
| #1249 | `codex/scaled-row-amount-lanes` | conflicts in `tests/test_section_style.py` |

Each needs `git rebase origin/main` + **force-push**, which the permission
classifier currently blocks. Two ways forward — owner picks:
- grant force-push for `codex/*`, or
- push the rebased work as fresh branches and open replacement PRs (no force
  required; costs the existing review threads).

Merge protocol going forward: **squash for everything, no stacks.** If a stack is
unavoidable, merge the parent with `--merge`, never `--squash` — squashing a
parent rewrites its children's history and kills them (this is what closed #1240).

### 0.2 Costco truth v2 — DO NOT seal or activate it. The gate correctly failed it.
Dev holds a Costco **v2 manifest** (`bundle_hash 6b709eb0…`) at `status=OPEN`,
`gate_status=PENDING`, no `sealed_at`; `TRUTH#ACTIVE` still points at v1
(`c5cd3120…`). The sprint's Costco evidence was measured against v2 —
`costco_v2_*.json` carry `"truth_version": 2` and the v2 hash, and the ACTIVE v1
hash appears in **no** evidence file. So an `online-active` Costco render still
resolves v1 and **the merged Costco gains do not take effect.**

**But the fix is not to seal it.** The v2 gate record
(`GATE#2026-07-22T19:21:41…#v0000000002`, `eval_git_sha dd37262e` = HEAD) reads
`overall=FAIL`:

| metric | verdict |
|---|---|
| arithmetic | FAIL — `sum_lines_eq_subtotal` lhs 2112.32 vs rhs 879.97; `total_eq_tender` 957.97 vs 181.60 |
| columns | FAIL — amount lane wobble_iqr 10.71px vs limit 3.86; outlier_frac 0.50 vs 0.40; shear 10.33 px/100px (real wobble is 0.68px) |
| separators | FAIL — real has one `double` rule at y=0.858, synth emits one `dash` at y=0.984 |
| tokens | FAIL |
| style | PASS_WITH_GAPS |
| graphics, logo | PASS (both improved from v1 — the sprint's real gain) |

`overall=FAIL` → `GateBlockedError` → seal never attempted → manifest stays OPEN.
`activate_merchant_truth.py:153-157` would refuse it regardless. The pipeline is
working as designed. **The correct action is to fix the failing metrics, not to
force the bundle through.**

Two caveats that make the v1-vs-v2 comparison weaker than it looks:
- The runs are **not comparable**. v1 was evaluated on receipt `01c0bc98…#1`,
  v2 on `0324604e…#1`. Nothing pins a golden receipt per merchant, so "v2 beats
  v1" is not established by these records (see R5 below).
- The v2 evidence is **unrecoverable** — `evidence_refs` point into a
  `TemporaryDirectory` created at `mint_merchant_truth_v2.py:745`, deleted when
  the mint exited (see R4 below).
- `arithmetic` FAIL is a **receipt-labelling** defect, not a bundle defect. The
  gate blocks a bundle seal on corpus quality it does not control (see R8).

### 0.3 The bigger integrity problem: no ACTIVE bundle has ever passed a real gate
All 13 ACTIVE bundles carry `gate_status=PASS` derived from
`bootstrap_gate_results()`
(`receipt_dynamo/migrations/merchant_truth_v1_live.py:139-171`), which hardcodes
`{"status":"PASS","passed":True}` with its own note that *"no fidelity eval has
run against truth-loaded renders yet."* **12 of the 13 have zero gate records.**
The one that does — Costco v1 — has two, and both say FAIL.

Every consumer treats that field as a verified fidelity signal:
`merchant_truth_loader.py:416-419`, the renderer, `activate_merchant_truth.py`.
It is not one, and nothing distinguishes a bootstrap PASS from an earned PASS.
`gate_results.kind` already carries `"migration-bootstrap-seal"` — surface it in
`fleet_status` and in the activation summary before anyone builds on it.

Also note: **`FLIP_ACTIVE` and `PROMOTE` have never run for any merchant.** No
merchant has ever advanced past its first version. The v2 path is untested end
to end.

### 0.4 Prod needs nothing — confirmed, close this question
`grep -rl "merchant_truth\|MERCHANT_TRUTH\|MerchantTruth" infra/` → **zero
files**. Same across `portfolio/` TS/TSX. No Lambda, step function, Pulumi
resource, or web code reads merchant truth; no deployed code can even import the
renderer. Merchant truth is purely a local synthetic-rendering / fidelity-eval
concern, so the **empty prod table is a non-issue** and
`promote_merchant_truth.py` has correctly never been run.

Do not "fix" prod. One caveat for later: the promoter is a **dead end by
construction** — it explicitly refuses to import `TRUTH#ACTIVE`
(`receipt_dynamo/promotions/merchant_truth.py:195-199`) and nothing else sets it,
so a promoted prod bundle would be invisible to the loader. If prod truth ever
becomes real, a prod-activation ceremony has to be designed; it does not exist.
Keep the promoter, but label it not-yet-wired rather than leaving it looking like
a working step 5.

---

## Phase 1 — Make "green" mean something (hours; highest ROI)

Verified current state:
```
branches/main/protection → 404 Branch not protected
rulesets                 → []
CODEOWNERS               → does not exist
.coderabbit.yaml         → no reviews.auto_review block
```

1. **`.coderabbit.yaml`** — add:
   ```yaml
   reviews:
     auto_review:
       drafts: true
   ```
   `drafts` defaults to `false`; this single omission is why all 16 sprint PRs
   got zero external review. CodeRabbit's own skip comment on #1243 names this file.

2. **Branch ruleset on `main`** requiring status checks
   `Python (receipt_agent)`, `Python (repository tests)`, `Lambda Syntax`,
   `TypeScript`; plus 1 approving review and block force-push **to main**.
   Names must match the job `name:` values (`main.yml:53`, `:196`), not job ids.
   Targets `main` only — does not affect the Phase 0.1 rebases on `codex/*`.

3. **`CODEOWNERS`**:
   ```
   /receipt_agent/receipt_agent/agents/label_evaluator/rendering/  @tnorlund
   /synthesis_loop/corpus_baseline.json                            @tnorlund
   /synthesis_loop/render_regression_baseline.json                 @tnorlund
   ```
   The two baseline files are the only way to launder a regression into a PASS.
   That is precisely where a self-approving agent must be stopped.

#1254 fixed *what* CI measures. Without this phase, a PR with a red
`Python (receipt_agent)` can still merge.

---

## Phase 2 — Make evidence machine-verified (days)

`grep -rl "evidence/" tests/ scripts/` returns **zero hits**. The 20 hand-written
`synthesis_loop/evidence/*.json` files are claims no machine re-derives, in five
mutually incompatible schemas across two directory conventions. Every hand-shaped
file drops `inputs_hash`/`atlas_hash` — the exact fields
`full_fidelity_eval.build_stamp` (`:467-492`) computes that would let a verifier
confirm the eval ran on the inputs it claims.

Meanwhile the real harness already exists, is offline/zero-AWS, and is already
wired into CI by #1254: `tests/test_corpus_regression_gate.py` (verified locally,
**12 passed**). But `synthesis_loop/corpus_manifest.json` has **3 entries**
(`costco_wholesale_v1`, `fixture_mart_v1`, `variant_selftest_v1`) — none for
Sprouts, Vons, Gelson's, Wild Fork or The Stand, the merchants the sprint was about.

**An evidence claim should be a corpus entry, not a file.** Per fidelity PR:
1. `tests/fixtures/merchant_truth/<slug>.json`
2. `tests/fixtures/corpus_gate/<slug>_<metric>.inputs.json`
   (model on `tests/fixtures/corpus_gate/build_corpus_inputs.py`)
3. an entry in `synthesis_loop/corpus_manifest.json`
4. a recaptured `synthesis_loop/corpus_baseline.json`

"Before" = the entry failing on the parent commit. "After" = the recaptured
baseline. Re-derived on every push, forever.

Then: backfill entries for the sprint's six merchants, delete
`synthesis_loop/evidence/` and root `evidence/`, and add a `pr-hygiene` job that
fails when base != main, or when a rendering change adds evidence without
touching the corpus, or when a baseline file changes without a
`baseline-recapture` label.

Separately: `render_regression_guard.py` needs AWS so it stays out of PR CI, but
its committed pixel hashes are currently re-derived by **nothing** — its only
tests monkeypatch `_render_all` away. Give it a nightly dispatch job with
dev-read creds.

---

## Phase 3 — Dissolve the conflict engine (weeks)

Ordered by leverage. Every step gated on byte-identical output across the
13-merchant ACTIVE fleet via the corpus gate + render guard. **A step that cannot
be made byte-identical is too big — split it.**

1. **Renderer reads `layout_template.separators`** (~2 days, low risk).
   One `RenderConfig` field, plumbed through `merchant_typography`
   (`render_synthetic_receipts.py:1619`), which already passes unknown keys
   through verbatim (`:1655`). Semantics both Costco branches converged on
   independently: `None` → legacy heuristics byte-identical; `[]` → draw only
   literal OCR rules; non-empty → draw at measured `pos_frac_med`.
   **After this, flipping a merchant to measured separators is a Dynamo write,
   not a code change — zero conflict surface.** This removes the *reason* the
   next three separator PRs would ever be written. Do it first, alone.

2. **Extract `rendering/separators.py`** (~2 days, low risk) as pure motion —
   move `_separator_anchor_rows` (`:383`) and `_separator_layout` (`:429`), no
   behavior change, merged **before** any merchant branch is cut. Separator
   sources become a list of functions; `_render_grid` iterates them. Two of the
   four collision points disappear: Sprouts' `_inferred_policy_separator_baselines`
   and grocery's `_structural_gap_separator_centers` would have merged clean.
   This is exactly the shared base whose absence caused 2 of the 5 conflicts —
   both Costco branches shipped *byte-identical* re-extractions of these two
   functions in parallel.

3. **Split `_render_grid` at the plan/paint seam** (~1 week, medium risk), as two
   byte-identical PRs: (a) introduce a frozen `GridContext`, pass it down;
   (b) extract `plan_rows` → `list[RowPlan]`, then `paint_rows`. The lane
   conflict at `:1057` — where `codex/scaled-row-amount-lanes` and
   `codex/costco-measured-columns` both rewrite the same line with mutually
   exclusive semantics — becomes a field on `RowPlan` set by one resolver.

4. **Split `RenderConfig`** (~3 days) — 41 fields over 119 lines
   (`receipt_renderer.py:109-228`) is the single append point for every new
   merchant behavior. Keep engine knobs; move ~25 merchant-truth fields to a
   `MerchantRenderSpec` populated from the bundle. Do **after** step 3 or you
   churn every call site twice.

5. **Lower priority**: stylemap rules to data (`receipt_stylemap.py:167`);
   delete `graphics_profile_for_merchant`'s name matching
   (`receipt_graphics.py:162`) by moving the sprouts/vons blocks into their
   bundles' `flags.graphics` — the overlay already exists at
   `render_synthetic_receipts.py:2659-2666`, so those literals are already
   shadowable dead data. Also de-duplicate the POS vocabulary that exists twice,
   as a tuple at `receipt_renderer.py:841-861` and a regex at
   `receipt_stylemap.py:116-122`.

**Do not** build a merchant plugin registry or a rendering DSL, and do not move
the geometry into data. `_separator_layout`'s whitespace math,
`assign_row_baselines`, `draw_token_chars` — nobody has ever conflicted on these
and they would be worse as configuration. The line is: **measurements are data,
mechanics are code.** The renderer is on the wrong side of it for separators and
columns, and nowhere else.

**Preservation warning.** `_render_grid` is unusually dense in *earned* behavior:
the pitch-floor derivation (`:736-743`, thermal receipts pack at ~1.08× glyph
height), the bitmap-advance rule (`:1039-1041`), the two-dot reinforcement
(`:1076-1083`), the dash baseline offset (`:480-484`). Each is a fidelity bug
someone paid for. A refactor that silently drops one surfaces as a gate FAIL
three merchants later and nobody will suspect the refactor.

---

## Phase 4 — Measurement quality (the most important section in this document)

### 4.1 There is no validated link to the downstream objective
`receipt_layoutlm/receipt_layoutlm/data_loader.py:524-556` trains only on real,
human-`VALID`-labelled words. No synthetic ingestion branch, no source flag, no
dataset-mixing parameter — `grep -i synth data_loader.py` returns nothing.
`scripts/render_synthetic_receipts.py:1-16` calls itself a QA artifact that
"consumes data only and touches no gate." The bridging script it names,
`verify_synthetic_replay.py`, **is not on `main`** — verified via `git ls-tree`;
it exists only in unmerged worktrees under `.claude/worktrees/`. And the sprint
retrospective mentions LayoutLM, training, downstream and F1 **zero times.**

So *better fidelity → better synthetic training data → higher F1* is unmeasured
at every link, and the seven metrics are self-referential — thresholds calibrated
on the same six receipts they validate (`full_fidelity_eval.py:184-189` derives
`TOKEN_INK_RECALL_MIN = 0.97` from two readings of a single Gelson's receipt).

**Decisive experiment, cheap:** generate synthetics for one merchant with enough
real data (Sprouts, 199 receipts); train two LayoutLM runs on identical real data,
one augmented with synthetics; compare held-out F1 on real receipts using existing
`Job.results.best_f1` infra. Then repeat with *deliberately degraded* renders
(disable one metric's fix) to test whether the fidelity metrics predict the F1
delta at all. That second half is what converts the suite from plausible to
validated.

### 4.2 The gates are inverted
- `corpus_regression_gate.py` **runs in CI** (via `tests/test_corpus_regression_gate.py:255`)
  but **cannot detect a rendering regression.** All three fixtures have
  `manifest_words == syn_words` (verified byte-equal), and both sides are drawn by
  a toy 1px-comb renderer, not the production one (`:140-171` states this).
  **It compares an image to itself.** Legitimate as an eval-logic determinism pin;
  worthless as pixel proof. `costco_wholesale_v1` is invented content
  (`BANANAS / MILK / EGGS`) — the name is misleading and should change.
- `render_regression_guard.py` uses the production renderer with SHA-256 pins, but
  **does not run anywhere** — its only test monkeypatches `_render_all` away and
  never renders a pixel. It covers 4 receipts / 3 merchants (Costco ×2, Vons,
  Sprouts), missing Gelson's, Trader Joe's, Wild Fork, The Stand and Dollar Tree —
  3 of the 5 merchants this sprint claims to have improved.

**Any refactor in Phase 3 must be gated on `render_regression_guard`, not the
corpus gate.** Fix coverage and wire it nightly before starting Phase 3.

### 4.3 Corpus: 6 receipts, 1.9% of what's available
One receipt per merchant. Sprouts is evaluated on 1 of 199 available; Costco 1 of
39; Vons 1 of 29; Trader Joe's 1 of 22; Wild Fork 1 of 18; Gelson's 1 of 7. The
§7.2 variant machinery exists *because* merchants print multiple layouts — so the
variant selector is validated at **n=1 per merchant**, and calibration set ==
validation set.

By the rule of three, n=1 supports no claim at all; n=10 supports "not obviously
broken"; n=30 supports "≤10% of this merchant's receipts fail." Recommended:
start at **3/merchant (18 total), run once** — if PASS rates hold, a full corpus
is worth funding; if they collapse, you learned it at 3× cost instead of 22×.
Then floor of 10/merchant, variant-stratified, all-available for the thin ones
(~130 total, requiring zero new data collection).

### 4.4 Highest-value missing metric: OCR round-trip
Nothing in the suite verifies the rendered glyphs spell the right characters.
`text_recall`/`text_precision` (`full_fidelity_eval.py:1205-1212`) compare the
real manifest against **the renderer's own word list** — metadata vs metadata,
never a pixel. `ink_recall` (`:1218-1235`) reads `syn_gray` against the synth's
own manifest — a self-consistency check. So a glyph atlas rendering `BANANAS` as
`BXNXNXS`, or with wrong kerning or x-height, scores 1.0/1.0/1.0.

This matters because the downstream consumer does not consume pixels — it consumes
**OCR output over those pixels**. Add `metric_ocr_roundtrip`: run the same Swift
Vision OCR that metric 5 already shells out to over the synth PNG, IoU-match to
the manifest, report char-error-rate, exact-token fraction, unread and phantom
tokens, and a confusion table. It has a natural null (real-vs-real CER is the
noise floor), reuses existing infra, and its units are the ones the LayoutLM
pipeline reports — the closest available proxy for the real objective while 4.1
is unbuilt.

Runner-up, deliberately ranked below: whitespace / inter-row rhythm (real thermal
feeds jitter; mechanically even spacing passes everything today). Considered and
rejected: image-level SSIM (too sensitive to legitimate paper-texture variation
to gate on).

---

## Sprint operating procedure (for the next agent)

```
SETUP
 0. Land the shared-base PRs (Phase 3 steps 1-2) BEFORE cutting merchant
    branches. Prove zero behavior change with the corpus gate + render guard.

PER WORK ITEM  (exactly one merchant x one metric)
 1. Branch from main. Never from another PR's branch.
 2. Capture RED as a corpus entry, not a JSON file. The gate MUST report a
    finding naming your receipt + metric; if it doesn't, the fixture does not
    reproduce the defect — fix the fixture before writing any code.
 3. Fix inside the ONE function that owns the metric. New behavior goes behind
    a RenderConfig field defaulting to OLD behavior.
 4. Recapture; gate must return ok:true. Commit "prove pass" with the baseline.
 5. Open READY FOR REVIEW. Never draft.

HARD LIMITS
 - Max 3 open PRs touching rendering/ at once.
 - Never open a PR based on another PR's branch.
 - Never edit corpus_baseline.json or render_regression_baseline.json to make a
   failing metric pass. Recapture requires the owner's approving review.
 - A claim in a PR body that no committed artifact can re-derive is not
   evidence. Delete it or make it a corpus entry.
```

Keep what worked: capture-red → fix → prove-pass with committed evidence. That
discipline caught a real self-inflicted regression during the sprint (Dollar Tree
phantom separators 0→4, fixed back to 0). The problem was never rigor — it was
that the rigor produced artifacts no machine checks, in a file everyone edits.

---

## Phase 2b — Merchant-truth lifecycle safeguards

The stamp needed for verification **already exists and is already committed**.
`full_fidelity_eval.build_stamp` (`:467-489`) emits
`{"git_sha", "dirty", "merchant_truth": {slug, version, bundle_hash, mode,
expected_version, expected_bundle_hash}}`, and
`evidence/wild_fork_separator_logo_filter/{before,after}.json` carry it honestly
(`mode: online-active`, v1, the real ACTIVE hash). Costco's v2 evidence carries
`mode: fixture` against a **fabricated `status=SEALED` payload**
(`mint_merchant_truth_v2.py:705-722` hardcodes SEALED/PASS into a temp fixture)
for a bundle that is OPEN in the table. That distinction is machine-checkable
today and checked by nobody.

**R1 (highest value, ~40 lines) — assert committed evidence was measured against
ACTIVE truth.** New `scripts/verify_evidence_stamps.py` as a CI job. For every
evidence file carrying a `stamp`: `mode` must be `online-active` (reject
`fixture`/`pinned`); `get_active_merchant_truth(slug)` must equal
`(version, bundle_hash)`; `dirty` must be false; `git_sha` must be an ancestor of
the PR head. Failure message: *"evidence/X measured costco_wholesale v2
(6b709eb0…) but ACTIVE is v1 (c5cd3120…); this evidence describes a bundle nobody
uses."* This is exactly the check that would have caught Costco.

**R2 — run `corpus_regression_gate.py` in CI; it already does 80% of this.** It
already captures `{"truth": {slug, version, bundle_hash}}` per entry (`:283-288`)
and diffs those three fields against the committed baseline (`:344-357`),
emitting a `metric:"truth"` finding on drift. It pins *fixture* drift, so it
complements R1 rather than replacing it.

**R3 — make stranded manifests loud.** Extend `synthesis_loop/fleet_status.py:315`
to exit nonzero on: any OPEN manifest older than N days (Costco v2 since
07-22); any merchant where `max(SEALED version) > ACTIVE.version`; any merchant
with PROPOSAL rows and zero manifests (`amazon_fresh`, `dollar_tree`, `smith_s` —
these were never minted at all, not "proposals awaiting promotion"); any ACTIVE
with `gate_status=PASS` whose newest gate record is FAIL.

**R4 — preserve gate evidence.** `mint_merchant_truth_v2.py:744-748` passes a
`TemporaryDirectory` as `--out-root`, so every path in `evidence_refs` is dead
when the mint exits. Use `args.eval_out_root` (default `.out`); keep only the
fixture JSON in temp. Better: upload the five artifacts to S3 and store `s3://`
URIs so a gate record is self-describing forever.

**R7 — make mint retries reuse the OPEN version (needed BEFORE the next Costco
attempt).** `mint_merchant_truth_v2.py:941` unconditionally calls
`next_mint_version()`, which counts OPEN versions — so re-running the mint after
a code fix allocates **v3** and leaves v2 as permanent debris. The
`if existing.status == "SEALED": SKIP` idempotency branch (`:1017-1020`) is
unreachable on retry. Add `--retry-open`, or print the exact
`cleanup_merchant_truth_open_version.py --slug costco_wholesale --version 2
--delete` command instead of silently burning a version.

**R5, R6, R8 — worth a short RFC, not immediate.** R5: pin a golden receipt per
merchant (`--eval-image-id`/`--eval-receipt-id` are free-form, which is why v1 and
v2 were measured on different receipts). R6: `add_gate_record`
(`_merchant_truth_gate.py:52-68`) is a bare create-only put that never verifies
the `(version, bundle_hash)` it describes exists — wrap in `TransactWriteItems`
with a ConditionCheck, and stamp `truth_mode`/`is_active` onto the record. R8:
split the gate into bundle-attributable (columns, separators, logo, style,
tokens, graphics) vs corpus-attributable (arithmetic) so a good bundle isn't
blocked forever by receipt-labelling defects it cannot control — a policy change,
so it needs an explicit reviewed decision.

Order: R1+R2 together (one CI job) → R3 → R4 → R7 → then the RFC set.

---

## If you do only three things

1. **Run the downstream experiment (4.1).** Everything else in this document
   optimizes a proxy that has never been shown to matter. One merchant, two
   training runs, plus the degraded-render ablation. If fidelity doesn't move F1,
   the correct response is to stop investing in fidelity — and you cannot know
   that today.
2. **Un-invert the gates (4.2)** — extend `render_regression_guard` to the 5
   uncovered merchants and run it nightly; stop treating the corpus gate as pixel
   proof. Phase 3's entire safety argument depends on this.
3. `.coderabbit.yaml` + branch ruleset + CODEOWNERS (Phase 1, ~10 minutes) — the
   cheapest real risk reduction on the list.

Then: R1+R2 (evidence-stamp verification), then Phase 3.1
(`layout_template.separators`), which is what makes future merchant work stop
colliding.

Explicitly **not** on this list: sealing Costco v2 (the gate is right to refuse
it) and populating prod (nothing reads it).
