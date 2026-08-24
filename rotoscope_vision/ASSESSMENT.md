# Assessment: is measurement-first the right direction?

Built on `claude/rotoscope-vision-metrics` from PLAN.md, then evaluated on
`IMG_0974.mov` (720×1280, 198 frames, handheld gym clip, Zercher squat with
barbell and band). Both the shipped pipeline (`--evidence legacy`) and the
new one (`--evidence soft`) ran through identical registration and
identical metrics; the only difference is how prop evidence becomes a mask.

## What was built

| Plan item | Status |
|---|---|
| `Params` JSON (every tunable, documented ranges), `--params` / `--dump-params` | done |
| Per-frame `metrics.jsonl`, aggregated `summary.json`, `contact.png` | done |
| Baseline comparison with red lines (`--baseline`, `bench/objective.json`) | done |
| Optical flow (`VNGenerateOpticalFlowRequest`, sign auto-calibrated on the person mask) | done |
| Flow-warped prior, flow-propagated watershed markers (`propagateMarkers`) | done |
| Soft evidence: logistic difference, chromaticity shadow probability, pose bar-line prior, one decision | done |
| Body pose (`VNDetectHumanBodyPoseRequest`) → bar line (elbows, wrist fallback), floor line (ankles) | done |
| Plate discs: fit at bar ends, tracked with exponential smoothing, asserted as evidence | done |
| Coordinate-descent sweep (`scripts/sweep.py`) with leaderboard and red-line gate | done |
| Min-cut decision | **not done** — replaced by box-smoothed posterior + threshold + connectivity |
| Hand-labeled keyframes (supervised IoU) | **not done** — unsupervised metrics only |
| RVM CoreML backbone comparison | not started |

## Numbers (full clip, 198 frames)

| metric (mean) | legacy | soft | better |
|---|---|---|---|
| objective (bench/objective.json) | 0.804 | **2.707** | ↑ |
| propFlicker (component appear/vanish/±30 % events per frame) | 8.48 | **2.34** | ↓ 72 % |
| shadowLikeInProps | 0.133 | **0.037** | ↓ 72 % |
| floorContactLeak | 0.080 | **0.023** | ↓ 71 % |
| discCount (tracked plates per frame) | 0 | 0.87 | — |
| discRadiusDelta (px, frame to frame) | — | 1.20 | stable |
| paintBoundaryRecall | 0.833 | 0.869 | ↑ |
| paintPSNR (dB, inside mask) | 22.6 | 23.0 | ↑ |
| maskTemporalIoU (flow-warped) | 0.929 | 0.925 | ≈ |
| bgFalseRate (background pixels over threshold) | 0.0356 | 0.0365 | ≈ (same registration) |
| regAccepted | 0.74 | 0.76 | ≈ |
| maskComponents | 2.04 | 2.29 | slightly worse |
| paintTemporalDelta | 88 | 96 | slightly worse (see caveat) |
| msTotal per frame | 401 | 320 | ↓ |
| red lines crossed | — | none | |

The contact sheets (7 keyframes × source / focus / evidence / paint) agree
with the numbers: legacy shows bar stubs plus bench debris in both standing
phases, half-disc plates, and a shadow blob under the deep squat; soft shows
the full bar through the white bench in both standing phases, full tracked
discs, and no shadow except the deep squat.

## Why this is the right direction

1. **The loop closes.** Twenty tuning rounds on the old pipeline were judged
   from seven stills and regressed as often as they improved. One run now
   produces a scalar objective, fifteen named diagnostics, a contact sheet,
   and a red-line check against a committed baseline. The sweep script can
   iterate without a human looking at frames.
2. **The metrics predicted the fix.** `propFlicker` and `shadowLikeInProps`
   were the two numbers the plan named as the symptoms; the soft model
   moved exactly those (−72 % each) while leaving registration metrics
   untouched, which is the signature of a modeling improvement rather than
   a threshold shuffle.
3. **Soft evidence is cheaper, not just better.** Removing the carry /
   expiry / opening chain and replacing it with fused probabilities cut
   per-frame time 401 → 320 ms even with pose and flow added.
4. **Structure beats thresholds for the hard cases.** The bar through the
   bench and the black plate over the black rack were unsolvable by any
   difference threshold; the pose line and tracked discs solve both without
   touching the difference stage.

## What is not yet right, and what the numbers say to do next

- **`maskComponents` 2.3 and the left plate.** The left plate sits at the
  frame edge behind the bench; the disc fitter needs a blob on the *far*
  side of the elbow line and rarely gets one there. Next: seed the second
  disc from the first (same radius, mirrored along the bar at the same
  distance) and let the difference evidence confirm or reject it.
- **Deep-squat shadow (row 4).** `shadowLikeInProps` 3.7 % is mostly that
  frame. The chromaticity test cannot separate a shadow on a dark floor from
  a dark plate over the same floor; the plan's floor-plane prior (ankles →
  floor line, `floorContactLeak` already measures it) should gate shadow
  probability by height rather than chroma alone.
- **`paintTemporalDelta` ≈ 90 is a metric problem before it is a paint
  problem.** It warps whole-frame paint with nearest-neighbour flow across
  basin boundaries, so any basin re-coloring counts at full strength. It
  should be measured per basin (mean color of the same tracked basin) and
  is not yet trustworthy as an objective term.
- **Optical flow calibration IoU 0.82–0.84** is lower than expected for a
  one-frame gap (identity is ~0.80). Either Vision's medium-accuracy flow
  is coarse on this footage or the field is being read at the wrong
  resolution; try `flowAccuracy: high` and check `maskTemporalIoU` moves.
- **No ground truth.** Every number above is unsupervised. The plan's eight
  labeled keyframes are the missing calibration; until they exist the
  objective weights are judgment calls.
- **Min-cut** was deferred; the smoothed-threshold decision is adequate
  here but will not give the boundary-length regularization the plan
  wanted for the soft alpha edge.

## Verdict

Measurement-first is the right direction: it turned an argument about
seven stills into a comparison of two committed summaries, and the first
modeling change it guided removed roughly three quarters of the flicker and
shadow leakage without any regression on the red lines. The remaining
defects are now specific, named, and each has a metric that will move when
it is fixed.

## Reproduce

```bash
cd rotoscope_vision && swift build -c release
B=.build/release/rotoscope-vision
$B ~/IMG_0974.mov --subject held --evidence legacy --metrics runs/legacy --out-dir runs/legacy --no-mov
$B ~/IMG_0974.mov --subject held --evidence soft   --metrics runs/soft   --out-dir runs/soft   --no-mov \
   --baseline runs/legacy/summary.json --objective bench/objective.json
python3 scripts/sweep.py ~/IMG_0974.mov --frames 60 --out runs/sweep --keys diffCenter,priorWeight,structWeight,smoothRadius
```
