# Work Order 2 — Rework D0 (#1148) and D2 (#1145)

These are the two PRs that **empirically fail against real data**. They share a
root cause: **the model guessed instead of measured.** D0 fit its thresholds so
the training merchants pass by construction; D2 has no signal to find the item
body, so it collapses. Both need rework, not fix-forward.

---

## D2 — section assignment (#1145)

**The evidence:** [evidence/d2_recent_uploads_eval.md](../evidence/d2_recent_uploads_eval.md).
Ran your `assign_row_sections` offline against the 22 newest real dev uploads,
scored per-row against QA'd ground truth:

- Overall agreement **58.7%** (252/429 rows).
- Anchors fine: PAYMENT 0.82, ADDRESS 0.79, FOOTER 0.75, STOREFRONT 0.71 recall.
- **Content sections collapse: ITEMS 0.09, SUMMARY 0.04, TOTAL_LINE 0.00, SURVEY
  0.00.** ITEMS proposed on only 2 of 18 receipts that have one.
- On unknown merchants the monotone Viterbi degenerates into 2–3 mega-sections
  (giant top ADDRESS + bottom FOOTER swallowing the item body). Chick-fil-A:
  0.16 agreement, entire item block labeled ADDRESS.

**Root cause:** the emission model is 6 numeric Gaussians (position, y_center,
x_span, alpha/digit ratio, has_amount) that give anchors strong signal and the
content sections none, so the optimal monotone path absorbs the middle of the
receipt into a neighboring anchor. Contributing: merchant priors are fit
`include_tokens=False`, so the 93 repeat merchants get *no* keyword evidence to
separate ITEMS from PAYMENT (P3 at `section_assignment.py:260`).

**What to change:**

1. **Give content sections real signal.** Add emission features that distinguish
   the item body: price-column geometry (a right-aligned amount column),
   per-row amount alignment, quantity tokens, `@`/`each`/unit-price patterns.
   Consider a minimum-support penalty so the path cannot collapse a 20-row middle
   into an anchor.
2. **Add token evidence to merchant priors** (or fall back to the global model's
   tokens when the merchant model lacks them). Fix the build script's
   `include_tokens=False` for merchant priors.
3. **P2 — golden fixtures must assert expected sections, not just determinism**
   (`test_section_assignment.py:139`). Add per-row expected `section_type`
   assertions. Use these exact receipts as fixtures: **Chick-fil-A `cb581977`**
   (unknown-merchant collapse case) and **Smith's `ff574b98`** (good case). This
   is the regression the current tests can't see.
4. **P2 — verifier VALID→PENDING demotion guard** (`section_verifier.py:206`):
   only demote when the section's *current* status is PENDING, so a re-embed can't
   silently regress a human-QA'd VALID section. The dev corpus is 99.70% VALID
   ground truth — protect it.

**Acceptance:** re-run the offline eval harness against the same 22 uploads (the
harness lives at `/private/tmp/d2_eval_wt/d2_eval_driver.py` per the evidence
doc's Reproduction section — or rebuild it; it just drives `assign_row_sections`
with real rows and scores vs QA'd sections). Target: **ITEMS recall ≥ 0.70 and
overall agreement ≥ 0.80**, with the golden fixtures pinning both the collapse
case and the good case. Until then, D2 is a skeleton/anchor detector, not a
section labeler — do not let its PENDING proposals feed the payload builder as
ground truth.

---

## D0 — typeface fingerprint (#1148)

Three P1s. See [REVIEW_FEEDBACK.md](../REVIEW_FEEDBACK.md) for all of them
verbatim; the essence:

1. **Reuse canonical machinery, don't re-vendor it**
   (`glyph_matching.py:102`). `normalize_glyph`, `clean_letter_mask`,
   `_components`, `_shift`, `shifted_iou` are verbatim copies of glyphstudio's
   `typography.py`/`family_cluster.py`/`glyph_score.py`. Import glyphstudio
   (`tools/glyph-studio/py/pyproject.toml` — it's a real package). #1111 was
   de-vendored precisely so these primitives have one source of truth and inherit
   the #1142/#1147 tolerance fixes. If the Lambda genuinely can't depend on it,
   add exhaustive parity tests over **all four** primitives on a real crop corpus
   — not just `shifted_iou` on two synthetic bars.

2. **Derive the accept threshold from a real separation, not min-of-winners**
   (`build_typeface_registry.py:187`). `minimum_winner_iou = min(winners)` over 6
   merchants is circular — every calibration example passes by construction, and
   nothing bounds the false-positive rate. Off-target atlases reach 0.60–0.68 on
   matching input, above the 0.4263 floor. **Score each merchant's crops against
   every OTHER atlas to build an impostor distribution, and set the cut where the
   genuine-match and impostor distributions separate.** Add a negative-control
   test asserting a foreign receipt's crops do NOT clear the floor against the
   wrong atlas (`test_typeface_fingerprint.py:92`). Use the #1110 per-merchant
   crop manifests.

3. **Gate on distinct-letterform coverage, not raw crop count**
   (`merchant_fingerprint.py:109`). Today 600 identical 'A' crops →
   `confidence=0.929` (confident attribution from ONE glyph), and any receipt with
   <569 crops abstains entirely (excludes most non-jumbo receipts + the whole
   long-tail POS class D0 targets). Gate on distinct-character coverage and/or
   per-char-median deviation — glyphstudio provides `line_attribution` /
   `per_char_medians` / `calibrated_deviation`.

Plus the P2s: merchant atlases judged against ROM-only calibration
(`:173`), the mislabeled per-receipt-min derivation (`:182`), and the
median ambiguity band hiding 0.0001 ties (`:191`).

**Acceptance:** D0 imports glyphstudio (or has full parity tests); the accept
floor is derived from a documented genuine-vs-impostor separation with a passing
negative-control test; the evidence gate is on distinct letterforms; confidence
is documented as source-relative. **No threshold may be fit to the acceptance
metric.**
