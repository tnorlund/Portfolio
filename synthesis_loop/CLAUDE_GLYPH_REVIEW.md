# Goal: per-letter review of the Sprouts data-built glyph font

You are the REVIEWER in a loop: codex edits `synthesis_loop/build_merchant_glyphs.py`
to improve the Sprouts glyph atlas; you inspect the result letter-by-letter and
tell it exactly what is still wrong, until every character is correct. You do NOT
edit the builder — you review and direct. Context on what's being built is in
`synthesis_loop/CODEX_GLYPH_TASK.md`; read it first.

Repo: **Portfolio_grid_discipline**, branch **`feat/grid-discipline`**.

## Each review round

Regenerate the artifacts yourself (don't trust stale ones):

```bash
PY=<venv python that imports receipt_dynamo>
export PYTHONPATH="$PWD/receipt_agent:$PWD/receipt_dynamo:$PWD/receipt_upload"
export DYNAMODB_TABLE_NAME=ReceiptsTable-dc5be22 AWS_REGION=us-east-1 PORTFOLIO_ENV=dev
export BITMATRIX_DIR=/tmp/bitmatrix FONT_LIB=/tmp/fonts_lib

# per-glyph contact sheet from the current atlas:
"$PY" synthesis_loop/glyph_review.py sheet /tmp/gridfix/sprouts_font2/sprouts.glyphs.npz /tmp/gridfix/review_sheet.png
# on-receipt spacing/legibility (copy the atlas into place first):
cp /tmp/gridfix/sprouts_font2/sprouts.glyphs.npz /tmp/bitmatrix/sprouts.glyphs.npz
"$PY" synthesis_loop/glyph_review.py receipt "Sprouts Farmers Market" 04ebdb8a-b560-4c00-aa09-f6afa3bda458 1 /tmp/gridfix/review_recA.png
"$PY" synthesis_loop/glyph_review.py receipt "Sprouts Farmers Market" 00ded398-af6f-4a49-86f7-c79ccb554e48 2 /tmp/gridfix/review_recB.png
```

Then OPEN and actually look at `review_sheet.png` and the two receipt PNGs.

## What to judge — every character, one at a time

Go through the sheet glyph by glyph. For each, a verdict + the specific defect:

- **GOOD** — correct letterform, crisp, right proportions, sits on the baseline.
- **MARGINAL** — recognizable but noisy/rough/slightly malformed.
- **BROKEN** — wrong shape, severed/shredded stroke, illegible, or mis-baselined.

Name the defect precisely so codex can fix the CLASS, not one glyph:
severed diagonal, doubled edge, missing stem, filled counter (O/D/Q/a/e/o closed
loops turning solid), descender lost (g/p/q/y), ascender lost (b/d/h/k/l),
wrong baseline offset, too fuzzy (didn't converge), wrong width/advance.

Pay special attention to the historically weak classes: uppercase diagonals
`H K M N V W X Y`, lowercase diagonals `v w x y`, complex lowercase `g h i p q`,
and low-sample punctuation `% & @ " # $ !`.

## Spacing (the on-receipt PNGs)

- Compare SYNTH vs REAL word density. Letters should not overlap, and words
  should not be airier than the real photo. Real Sprouts cell aspect (char width
  / glyph height) ≈ **0.559**. If off, tell codex to adjust the atlas
  `advance_ratio` or the Sprouts `condense` in `scripts/merchant_profiles.json`
  (currently 0.92) — and by roughly how much / which direction.
- Check baselines: a row of text must sit on one line; call out any glyph that
  floats high/low (a bad `o<codepoint>` offset).

## Output each round

1. A table: `char | GOOD/MARGINAL/BROKEN | defect`.
2. The dominant failure pattern this round and the ONE algorithmic change most
   likely to fix the most glyphs (tell codex what to try).
3. A spacing verdict (OK / too tight / too airy / baseline drift) with direction.
4. A clear GO / NO-GO: are we done?

## Sign-off criteria (say DONE only when all hold)

- On a real Sprouts receipt render, EVERY printed character is legible and
  correctly shaped, OR is a glyph deliberately absent from the atlas and rendered
  by the clean TTF fallback (acceptable for genuinely rare chars).
- No glyph is clearly the wrong letter or has a severed/filled stroke at receipt
  scale.
- Word spacing matches the real photo (no overlap, not too airy); text sits on
  consistent baselines.
- `pytest receipt_agent/tests/test_receipt_renderer.py` passes and Costco output
  is unchanged (codex must not have touched Costco's profile/atlas or the shared
  renderer in a breaking way).

Keep the loop tight: verdict table → one concrete instruction → codex iterates →
re-review. Prefer directing codex to fix a whole failing class over hand-tweaks.
