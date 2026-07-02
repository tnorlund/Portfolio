# Codex task: fix the Sprouts data-built glyph font (letterforms + spacing)

Paste everything below into codex on the mac mini. It is self-contained. A
separate Claude (headless) will review each rendered glyph and the on-receipt
result and tell you which letters still need work — iterate until it signs off.

---

## Repo / branch

- Repo: **Portfolio_grid_discipline** (find it under the mac mini home; it is a
  git worktree, NOT `~/Portfolio`). Branch: **`feat/grid-discipline`**.
- HEAD you are building on: `439416cef Sharpen diagonal glyphs: tight-cluster
  vote + per-glyph crispest pick`.
- Disk note: the mac mini disk runs near full. Before large runs, check
  `df -h` and clean `/tmp/gridfix` / stale `/tmp/*.samples.npz` if needed. Write
  all outputs under `/tmp/gridfix/sprouts_font*/` and the atlas to
  `$BITMATRIX_DIR` (default `/tmp/bitmatrix`).

## What this is

We render synthetic receipts in each merchant's REAL font. Costco renders in an
extracted bitmap atlas (`bitMatrix-C2.glyphs.npz`) built from a paid-font CHART
via `synthesis_loop/extract_bitmatrix.py` — that atlas is the QUALITY BAR: crisp,
complete, correctly-spaced. There is no chart for Sprouts, so we build its atlas
DATA-DRIVEN from Sprouts' own ~227 receipts (each has per-character
`ReceiptLetter` boxes + an S3 image). The builder is
**`synthesis_loop/build_merchant_glyphs.py`**. It median-votes each character's
actual printed letterform and writes the SAME atlas format Costco uses, which
drops into the merchant profile as `bitmap_font`.

Your job: make the Sprouts glyphs and their spacing genuinely good — correct,
crisp, legible letterforms with the right monospace advance — reviewing EACH
letter individually. This is a glyph-quality + spacing task, not a renderer
change.

## The atlas format (contract — do not change)

`<name>.glyphs.npz` holds, per glyph:
- `c<codepoint>` = a 2-D `uint8` binary array (1 = ink), trimmed to the ink bbox.
- `o<codepoint>` = `int16` baseline offset = the glyph's ink BOTTOM row minus the
  line's cap baseline (caps/digits ≈ 0, descenders positive/below, hyphen/star
  negative/above).

Consumed by `receipt_agent/receipt_agent/agents/label_evaluator/rendering/bitmap_font.py`:
- `cap_h` = median height of the `_CAP_REF = "ABDEFGHKLMNPRSTUVXZ"` glyphs.
- monospace advance = `cap_px * advance_ratio`, where
  `advance_ratio = (median width of "MWHNUABDOR" glyphs + 2) / cap_h`.
- Missing glyphs render via a TTF fallback (already wired in
  `receipt_grid.draw_token_chars`), so it's fine to DROP a hopeless glyph rather
  than ship a broken one — the body TTF covers it.

## Current algorithm (in build_merchant_glyphs.py) and its weaknesses

Pipeline: `get_receipt_places_by_merchant("Sprouts Farmers Market")` → per
receipt load `receipt_letters` + `load_raw_image_from_s3` → per printed LINE,
crop each letter, `auto_polarity` + `sauvola_mask` (from
`synthesis_loop/glyph_segment.py`), compute the line's baseline + cap-height from
`_CAP_REF` letters + digits, scale each glyph to a common `REF_CAP` and place it
on a canvas at its baseline offset. Samples are cached to
`<out>/sprouts.samples.npz` so vote tuning needs no re-fetch (set
`REBUILD_SAMPLES=1` to re-collect from S3). Then per char `_vote()`:
- registers samples by ink LEFT-edge and by CENTER,
- builds full-inlier mean and tight-cluster mean (top-half by IoU-to-consensus),
- keeps the CRISPEST of the four (max squared deviation from 0.5 over ink),
- IoU-outlier rejection, `_median3` cleanup; small glyphs (dots/commas) bypass
  alignment; too-few-sample glyphs are dropped to TTF.

**Known-good now:** digits `0-9`, `A-G I L O-U Z`, most lowercase, common
punctuation `( ) [ ] : . - /`, and `E F L T` (fixed by left-edge registration).

**Still wrong / your targets (review each):**
- Uppercase diagonals: `H K M N V W X Y` — improved but rough; stroke jitter and
  slant variance don't average cleanly.
- Lowercase diagonals: `v w x y`; and shape-complex lowercase `g h i p q`.
- Rare/low-sample punctuation: `% & @ " # $ !` — noisy.
- Missing entirely (fine to leave to TTF): `j z ' + < = > _ | ~`.
- Spacing: verify the monospace advance matches real Sprouts. Real Sprouts cell
  aspect (char width / glyph height) measured ≈ **0.559**; the profile currently
  uses `condense: 0.92` against this atlas. Check `advance_ratio` and condense so
  words are neither too airy nor overlapping.

## Ideas worth trying (you decide — measure, don't guess)

- Rotation/shear-tolerant registration for diagonals (small-angle search per
  sample, pick the angle maximizing IoU to consensus) before voting.
- Medoid / best-exemplar: for glyphs that won't average, use the single most
  representative REAL sample (a crisp printed instance) instead of a blurred mean.
- Stroke-width-aware cleanup (thin-then-reconstruct) so median filtering doesn't
  sever thin diagonals.
- Per-glyph sample-count reporting + minimum-quality gate that DROPS to TTF only
  when genuinely hopeless (a previous naive gate was mis-calibrated and dropped
  everything — gate on connected-component count / legibility, not raw fuzz).
- Reuse `synthesis_loop/logo_master.py`'s `_phase_shift` / `_iou` (FFT
  registration + IoU) — proven for de-noising the logos.
- The Costco path `synthesis_loop/extract_bitmatrix.py` shows what a crisp,
  complete atlas looks like — match that quality where the data allows.

## Environment / how to run

```bash
cd <Portfolio_grid_discipline>
# find the working venv (has boto3, PIL, numpy, receipt_dynamo, receipt_upload):
PY=<repo>/.venv/bin/python   # or ~/Portfolio/.venv/bin/python — pick whichever imports receipt_dynamo
export PYTHONPATH="$PWD/receipt_agent:$PWD/receipt_dynamo:$PWD/receipt_upload"
export DYNAMODB_TABLE_NAME=ReceiptsTable-dc5be22 AWS_REGION=us-east-1 PORTFOLIO_ENV=dev
export BITMATRIX_DIR=/tmp/bitmatrix FONT_LIB=/tmp/fonts_lib
mkdir -p /tmp/gridfix/sprouts_font2

# Build (first run collects from S3 ~3-5 min and caches; re-runs are instant):
"$PY" synthesis_loop/build_merchant_glyphs.py "Sprouts Farmers Market" /tmp/gridfix/sprouts_font2 sprouts 200
# force re-collect from S3 after changing the COLLECTION (not the vote):
REBUILD_SAMPLES=1 "$PY" synthesis_loop/build_merchant_glyphs.py "Sprouts Farmers Market" /tmp/gridfix/sprouts_font2 sprouts 200
```

Outputs: `/tmp/gridfix/sprouts_font2/sprouts.glyphs.npz` (+ `.verify.png`,
`.samples.npz`). To use it in a render, copy it to
`/tmp/bitmatrix/sprouts.glyphs.npz` (the Sprouts profile in
`scripts/merchant_profiles.json` points at `sprouts.glyphs.npz`).

### Per-letter review sheet (build this every iteration for Claude to review)

```python
import numpy as np
from PIL import Image, ImageDraw, ImageFont
a=np.load("/tmp/gridfix/sprouts_font2/sprouts.glyphs.npz")
chars=sorted(chr(int(k[1:])) for k in a.files if k.startswith("c"))
capref="ABDEFGHKLMNPRSTUVXZ0123456789"
caph=np.median([a[f"c{ord(c)}"].shape[0] for c in capref if f"c{ord(c)}" in a.files])
CAP=54; TW=96; TH=110; COLS=10; rows=(len(chars)+COLS-1)//COLS
sheet=Image.new("RGB",(COLS*TW,rows*TH),(255,255,255)); dd=ImageDraw.Draw(sheet)
lab=ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf",16)
for i,ch in enumerate(chars):
    g=a[f"c{ord(ch)}"]; off=int(a[f"o{ord(ch)}"]); sc=CAP/caph
    nh,nw=max(1,int(g.shape[0]*sc)),max(1,int(g.shape[1]*sc))
    im=Image.fromarray(((1-g)*255).astype(np.uint8),"L").convert("RGB").resize((nw,nh),Image.NEAREST)
    cx=(i%COLS)*TW; cy=(i//COLS)*TH; base=cy+20+CAP
    dd.line([(cx,base),(cx+TW,base)],fill=(210,210,255))
    sheet.paste(im,(cx+(TW-nw)//2, base+int(off*sc)-nh))
    dd.rectangle([cx,cy,cx+TW-1,cy+18],fill=(240,240,240)); dd.text((cx+3,cy+1),repr(ch),fill=(200,0,0),font=lab)
    dd.rectangle([cx,cy,cx+TW-1,cy+TH-1],outline=(220,220,220))
sheet.save("/tmp/gridfix/sprouts_font2/glyph_review.png")
```

### On-receipt spacing check (the real test)

Render a real Sprouts receipt in the atlas and compare to its photo. Use
`scripts/render_synthetic_receipts.py`: build atlas+profile via
`cached_glyph_atlas` / `cached_font_profile` for `"Sprouts Farmers Market"`, take
a receipt's OCR words (`bbox = [tl.x*1000, tl.y*1000, br.x*1000, br.y*1000]`),
render with `_render_cached_hybrid(rec, atlas, profile, width=760,
height=round(760*rec.height/rec.width), section_scale=..., **merchant_typography("Sprouts Farmers Market"))`.
Good same-content receipts to eyeball: `04ebdb8a-…` r1, `00ded398-…` r2.

`synthesis_loop/glyph_review.py receipt ...` now writes deterministic
`*.scorecard.json` and `*.scorecard.md` files next to the comparison PNG. Use
those as the quantitative gate for font size, word density, price right-edge
alignment, colon spacing, mid-line anchor drift, and barcode caption sizing.

## Methodology (do this, per letter)

1. Rebuild the atlas + the review sheet + one on-receipt render.
2. Go through the glyph sheet ONE CHARACTER AT A TIME. For each, decide: GOOD /
   MARGINAL / BROKEN, and the specific defect (severed stroke, doubled edge,
   wrong shape, mis-baseline, too fuzzy, wrong width). Keep a table.
3. Fix the algorithm for the failing CLASS (diagonals, thin strokes, low-sample),
   not one glyph at a time by hand. Re-vote from cache (fast) to test.
4. Verify spacing on the on-receipt render — advance/condense so words match the
   real photo's density (target cell aspect ≈ 0.559).
5. Repeat until every printed character on a real Sprouts receipt is legible and
   correctly shaped, or is deliberately dropped to the clean TTF fallback.

## Constraints (hard)

- NEVER buy or download fonts. Data-driven only, from Sprouts' own receipts.
- Do NOT touch Costco: its profile, its `bitMatrix-C2` atlas, or the shared
  renderer in a way that changes Costco output. Costco must stay pixel-identical.
  Only edit `synthesis_loop/build_merchant_glyphs.py` and, if spacing needs it,
  the Sprouts row of `scripts/merchant_profiles.json` (`condense` / a pinned
  `advance_ratio`). Run `pytest receipt_agent/tests/test_receipt_renderer.py`
  after any renderer-adjacent change.
- The `.glyphs.npz` / `.samples.npz` stay LOCAL (out of git); the builder script
  is the source of truth and must reproduce the atlas.

## Deliverables each round

1. `sprouts.glyphs.npz` regenerated.
2. `glyph_review.png` (labeled per-glyph sheet).
3. An on-receipt REAL-vs-SYNTH comparison PNG.
4. The receipt `*.scorecard.md/json` outputs and a short note on every BLOCKER.
5. A short per-letter verdict table (GOOD/MARGINAL/BROKEN + defect) and what you
   changed in the algorithm.

Success = the per-letter reviewer signs off that every character on a real
Sprouts receipt is correctly shaped and spaced (or cleanly TTF-fallen-back), at
receipt scale.
