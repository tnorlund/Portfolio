# Add a merchant to receipt synthesis (the mint loop)

This runbook mints a per-merchant thermal font + profile from the merchant's
own receipts and calibrates it until a synthetic render passes a side-by-side
review against a real receipt. It was executed end-to-end for Vons, Trader
Joe's, and CVS; Sprouts and Costco predate it. Budget roughly one focused
session per merchant. Every command below is copy-paste runnable.

**Environment** (all steps): `python` = `~/Portfolio/.venv/bin/python`.
`WT` = this worktree root. AWS creds with access to `ReceiptsTable-dc5be22`
and the `raw-image-bucket-c779c32` vault bucket. macOS required for OCR steps
(Apple Vision). scipy/skimage are deliberately NOT in the venv — everything
is numpy+PIL.

```bash
export WT=/path/to/worktree
export PYTHONPATH=$WT/receipt_agent:$WT/receipt_dynamo:$WT/receipt_upload:$WT/scripts:$WT/synthesis_loop
export DYNAMODB_TABLE_NAME=ReceiptsTable-dc5be22 AWS_REGION=us-east-1
export BITMATRIX_DIR=/tmp/bitmatrix FONT_LIB=/tmp/fonts_lib PORTFOLIO_ENV=dev
```

## 0. Choose the merchant + census the corpus

- List merchants and counts (receipt-tools MCP `list_merchants`, or Dynamo).
  **≥20 receipts** is comfortable; Vons worked with 31, TJ/CVS with ~20.
- Collect ALL name variants ("CVS", "CVS pharmacy", "CVS pharmacy®"). The
  receipts query is case-insensitive — differently-CASED variants return the
  SAME receipts (don't sum them), but genuinely different strings are
  separate pools.
- Eyeball 2–3 receipts (CDN images). Watch for **mislabeled receipts** in the
  pool (a Home Depot tagged "Trader Joe's" polluted the TJ corpus) — flag
  them, they degrade the consensus.

## 1. Build the letterform corpus

```bash
python $WT/synthesis_loop/build_merchant_glyphs.py \
  "Name;NAME VARIANT;Store #12 Variant" /tmp/gridfix/<slug>_studio <slug>
```

Pools `;`-separated variants. Ignore the built npz — the samples cache
(`<slug>.samples.npz`) is the deliverable. "forced fallback" / "dropped"
lists are normal for noisy corpora; the studio pipeline replaces them.

## 2. REFINE the corpus (do not skip)

```bash
cd $WT/tools/glyph-studio/py
python -m glyphstudio.refine /tmp/gridfix/<slug>_studio/<slug>.samples.npz \
                             /tmp/gridfix/<slug>_studio/<slug>.refined.npz
```

Pooled/photo corpora stack neighbor letters and second font faces into the
consensus; tracing that mush produces broken glyphs that per-glyph QA CANNOT
detect (the "truth" is equally polluted). Refining fixed 6 cap-height
deviations on TJ in one re-trace. Use the **refined** npz for every
downstream step, and register it in `server/env.mjs` `SAMPLES` so the MCP
tools see it.

## 3. Trace + simplify + compile

```bash
python -m glyphstudio.trace /tmp/gridfix/<slug>_studio/<slug>.refined.npz ../fonts/<slug>
GLYPHSTUDIO_SAMPLES=/tmp/gridfix/<slug>_studio/<slug>.refined.npz \
  python -m glyphstudio.simplify ../fonts/<slug> --apply --json > /tmp/simplify.json
PYTHONPATH=.:$WT/receipt_agent python -m glyphstudio.compile ../fonts/<slug> /tmp/gridfix/<slug>_studio/<slug>.glyphs.npz
```

`GLYPHSTUDIO_SAMPLES` must point at THIS merchant's corpus — the fidelity
gate judges against it. The compile self-check must end with **no cap-height
deviations**; deviations at this stage mean the corpus needs more refining or
the glyph needs hand-authoring (step 5).

## 4. Per-glyph QA fleet

Render compare strips (`python -m glyphstudio.compare <refined.npz>
../fonts/<slug> out.png "--chars=ABC..." --scale 2`; note the `--chars=` form
— some chars start with `-`) in ~6-char batches and review each row:
[soft consensus | compiled | overlay]. Triage OK/FIX, repair FIX glyphs by
editing the skeleton JSON (`glyphs/u<hex4>.json`, set `provenance: "edited"`),
re-compare until the overlay locks. `python -m glyphstudio.measure` gives the
authoring numbers (ink bbox, stems, bars, holes, stroke width).

Hard-won caveats:
- **Triage passes polluted glyphs when the consensus is equally polluted.**
  A `U` traced from a corpus that lost its right stem "matches truth"
  perfectly. Sanity-check suspicious letterforms against FACE CONSISTENCY
  (sibling widths/shapes) and against step 7's receipt render — TJ shipped
  `C U V` broken past a 136-agent fleet and they only surfaced as
  `[HI[KEN` / `BLLE` at receipt size.
- A `w` narrower than `v`, an `_` taller than wide, a `.` with a vertical
  tail = junk corpus for that char. Author from face metrics instead.
- If agents do repairs, remember reverts only work on TRACKED files — commit
  the traced font before fleet repairs so `git checkout --` can undo.

## 5. Hand-author the gaps

`python -m glyphstudio.handcraft <refined.npz> ../fonts/<slug> --chars '...'`
covers `KMNVWXYvwxyzk%#"^`;?OQURS&aehiou{}qj!+` — parametric skeletons sized
from the consensus envelope (or face-consistent defaults when the char has no
samples). The publish gate requires **94/94 coverage**; author the leftovers
(`[]\|~<>@`…) as simple line/circle skeletons (see `u005b`/`u0040` in the cvs
font for patterns). Conventions: cap units y-up, baseline 0, cap ink 1000,
x-height ~700, centerlines inset dot/2 (~50u), kappa 0.5523 bowls.

## 6. Measure pitch + styles, write the profile

```bash
# pitch/cap fleet median: per uppercase/digit word, (width_px/chars)/height_px
# (see scratch measure_pitch.py pattern in the session log; ~30 lines)
# stylescan per receipt, then aggregate:
python -m glyphstudio.stylescan <image_id> <receipt_id> out.json --merchant <slug>
python -m glyphstudio.styleagg /tmp/gridfix/<slug>_studio/scans stylemap-agg.json
```

- Add a `_<SLUG>_RULES` section-classifier list in `stylescan.py` (copy the
  vons/cvs pattern; classify store_header/items/payment/footer/etc from a
  line-text dump of 2 receipts).
- Write `fonts/<slug>/stylemap.json` from the aggregate (sizeScale / weight /
  underline per section, with `notes`). Bold = `stroke_rel med ≳ 1.12`.
- Set `metrics.pitchRatioTarget` (the fleet median) and `preview.condense =
  pitchRatioTarget / advance_ratio` in `font.json`.
- Add the merchant to `scripts/merchant_profiles.json` **under `profiles`**
  (top-level entries are silently inert!). Copy the "Trader Joe's" record:
  `aliases` (ALL name variants), `logo`, `typography` (bitmap_font names,
  condense, pitch_ratio, stylemap, bitmap_cap_ratio 0.66 /
  ocr_cap_height_ratio 0.649 as starting points), `logo_anchor`
  (`phrases` = every OCR reading of the wordmark INCLUDING double-reads like
  "• CVS", `extend_left: false`, `center: true`), and `graphics`
  (`footer_codes: false` if the merchant prints no footer QR/barcode —
  check a real receipt).

## 7. Calibrate against a real receipt (the actual quality gate)

Pick review receipts by OCR coverage (words per y-band, small max void,
SCAN type), then loop:

```bash
python $WT/synthesis_loop/glyph_review.py receipt "<Merchant>" <image_id> <rid> /tmp/review.png
```

with `RECEIPT_PAPER_STRENGTH=0.3` (texture at 1.0 inflates density and hides
thin ink). Read the metrics line and the PNG side-by-side. Targets:
**h_ratio 0.95–1.05, wpc_ratio 0.95–1.05, density_ratio ≥ 0.85**.

Knobs, in order of impact:
- `font.json params.weight` — thermal prints are BOLD; TJ needed 1.4, CVS
  1.35, Vons 1.2. After changing weight, recompute `condense` (the pitch
  guard compares `advance × condense` and weight fattens the advance).
- `ocr_cap_height_ratio` (profile) — scales glyph height from OCR boxes; on
  merchants where the pitch-derived floor dominates it's nearly inert, so
  always re-measure after changing it.
- Glyph surgery for anything that misreads at receipt size (step 4 caveats).

Iterate render → inspect → adjust. Stage compiled npz to `$BITMATRIX_DIR`
(delete any symlink first — macOS `cp` writes THROUGH symlinks) and clear
`/tmp/render_cache/*<slug>*` between iterations.

Scorecard notes: `price_right_delta` gradients that grow down the page are
REAL-SCAN ROTATION, not render bugs. OCR-garble words rendered faithfully
("Extradare") are correct behavior. Genuine source-OCR gaps (TJ lost half its
price column) can be backfilled with arithmetic gates — see
`backfill_reverse_ocr.py` for the pattern (only with explicit approval for
Dynamo writes).

## 8. Logo

Brand PNG/SVG → black-on-white L-mode mask, ~690px wide, trimmed. **Snap
near-white (≥250) to 255** — LANCZOS leaves 254-value background and the
renderer's contrast boost turns alpha-1 into a visible gray box behind the
logo. If no brand asset exists, `logo_master.py` votes one from the
receipts.

## 9. Publish to the vault

```bash
python $WT/synthesis_loop/publish_merchant_font.py "<Merchant>" \
  $WT/tools/glyph-studio/fonts/<slug> --logo /path/to/<slug>_logo.png
```

Compiles + self-checks (94/94, pitch guard on the regular face only), builds
the heavy face at `regular weight × 1.33`, uploads content-addressed npz/logo/
stylemap to S3, writes MerchantFont pointers to Dynamo, refreshes the local
cache, and clears render caches. Any machine now renders this merchant with
zero manual setup (the renderer self-heals from the pointers).

## 10. Verify + commit

Re-run step 7 once from a cold cache (proves vault resolution), then commit
the font sources (`fonts/<slug>/` — skeleton JSONs + font.json + stylemap
are the source of truth; compiled npz and samples npz stay OUT of git),
the stylescan rules, and the profile.

Optional: showcase finale assets (`final.webp`/`real.webp`/labels) — see the
existing `portfolio/public/synthetic-receipts/pipeline/<slug>/` sets for the
format (760px width, render-true labels via `RenderConfig.box_sink`,
margin 10).
