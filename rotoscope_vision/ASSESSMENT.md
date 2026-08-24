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

## The optimizer loop, exercised once

`scripts/sweep.py` on a 60-frame excerpt, four keys, 12 trials, ~6 min:

| trial | change | objective | result |
|---|---|---|---|
| 0 | defaults | 3.901 | baseline |
| 1–2 | diffCenter 28 / 34 | 3.14 / 3.62 | **blocked**: propFlicker 1.73 → 3.63 / 3.07 (red line) |
| 3 | diffCenter 48 | 3.989 | accepted |
| 4 | diffCenter 56 | 3.772 | rejected |
| 5–6 | priorWeight 0.3 / 0.8 | 3.96 / 3.91 | rejected |
| 7 | structWeight 0.4 | **4.052** | accepted |
| 8–9 | structWeight 0.75 / 1.0 | 3.99 / 3.99 | rejected |
| 10 | smoothRadius 0 | 3.27 | **blocked**: propFlicker 1.64 → 2.90 |
| 11–12 | smoothRadius 1 / 3 | 3.85 / 4.00 | rejected |

Validated on the full clip against `bench/baseline-soft.json`
(`bench/params-sweep1.json`): objective 2.707 → **2.844**, propFlicker
2.34 → 2.21, shadowLikeInProps 0.037 → 0.033, floorContactLeak 0.023 →
0.015, maskTemporalIoU 0.925 → 0.931, 320 → 290 ms/frame, no red lines
crossed. Two honest caveats the loop itself surfaced: propArea fell 20.1k →
17.4k and discRadiusDelta rose 1.2 → 2.1 px, so the "improvement" trades a
little plate coverage for less flicker — the objective cannot see lost true
prop area because there is no ground truth yet. That is the strongest
argument for labeling the eight keyframes next: without them the optimizer
will happily shrink the props.

## Verdict

Measurement-first is the right direction: it turned an argument about
seven stills into a comparison of two committed summaries, and the first
modeling change it guided removed roughly three quarters of the flicker and
shadow leakage without any regression on the red lines. The remaining
defects are now specific, named, and each has a metric that will move when
it is fixed.

## Round two: identity over time (`--evidence tracks`)

The soft pipeline still re-decided the props from pixels every frame, so
anything decided per frame could flicker, and its bar-line and disc-fitter
priors made it a barbell tool. Round two replaces the per-frame decision
with **feature tracks that carry identity**: a Lucas–Kanade tracker over
Shi–Tomasi corners (≈1000 live tracks, forward–backward error p95 0.016 px),
a classifier that labels each track background / subject / moving / attached
/ shadow-like from motion against a background-consensus similarity, strict
plate agreement and NCC-vs-plate texture, and a clusterer that groups
co-moving tracks into objects (rigid when a similarity fit explains them,
deformable otherwise), attaches objects by contact plus co-motion with the
subject, and expels tracks that keep disagreeing with an object's transform.
Each attached object renders its own pixels: deformable ones are grown
per frame by a crop watershed seeded from their tracks; rigid ones capture a
template at their best-supported frame and render it through the tracked
transform, falling back to growth when the template's photometric residual
exceeds `photoTolerance`. Nothing in the path knows what a bar, disc or
band is.

### Numbers (full clip, 198 frames, `bench/baseline-soft.json` vs `bench/baseline-tracks.json`)

| metric (mean) | soft | tracks | read |
|---|---|---|---|
| propFlicker (components appearing/vanishing per frame) | 2.34 | **1.81** | less phasing |
| floorContactLeak | 0.023 | **0.008** | shadow under the feet mostly gone |
| maskTemporalIoU | 0.925 | **0.931** | slightly steadier |
| paintBoundaryRecall | 0.869 | 0.875 | unchanged |
| bgFalseRate | 0.037 | 0.042 | within noise, no red line |
| maskComponents | 2.29 | 7.23 | worse: hollow band + separate plates + fragments |
| shadowLikeInProps | 0.037 | 0.078 | worse: grown regions catch some shadow at the plate rim |
| propArea | 20.1k | 12.2k | smaller props: no hole fill, left plate often missing |
| msTotal | 320 | 365 | tracker 34 ms + objects 26 ms |

(`paintTemporalDelta` 95.7 in the soft baseline is the pre-fix value from a
mis-warped RGBA compare; the corrected metric is ≈10.8 for both.)

What the keyframes show (`contact.png`, tracks column = track overlay):
the band is lifted with its interior **empty** on all seven keyframes and
never disappears; the bar and right plate persist on five of seven; no
bystander, rack or floor leaks after the occlusion gate; the left plate
appears only where its rim yields enough corners (frames 90–120).

### Why this is the right direction

- **Identity is now measured, not hoped for.** `objectIdChurn`,
  `objPersistence`, `objGeomResidual`, `objPhotoResidual`, `objColorDrift`
  and `objAreaDelta` are per-object numbers that say whether the same thing
  is being followed and whether its model still explains the pixels. The
  soft pipeline had no equivalent — a plate that phased out simply produced
  a lower propArea.
- **Flicker fell without a prior.** propFlicker went 8.5 (legacy) → 2.3
  (soft, with bar/disc priors) → 1.8 (tracks, no priors). The band's
  interior is empty because nothing fills holes; that was a specific ask.
- **The failures are all one class**: places with too few trackable
  features (the dark left plate against the dark rack, the plate interior).
  That is a feature-supply problem, addressable generically (multi-scale
  detection, lower-contrast corners inside an object's hull, template
  support from the object's own colour model) rather than with a disc
  fitter.

### What is not right yet

1. **Left plate.** Two tracks in its region at frame 0; when it does form an
   object it is a rigid one and renders as a clean disc (frame 90), so the
   fix is feature supply, not modelling.
2. **Right plate detaches during the descent** (frame 150: `comotion` −0.3
   while `contactFrac` 0.7). Co-motion compares the object with the ten
   nearest subject tracks, which at the plate's centroid are shoulder/head
   tracks that move differently from the hands. Compare against the subject
   tracks nearest the *contact point* instead.
3. **Fragments.** maskComponents 7.2: small grown regions from 3–5-track
   objects. A minimum rendered support (tracks × area) would remove most.
4. **Rigidity chaining.** Anything that pauses beside the subject can
   rigid-link for a window; expulsion now splits it after `outlierExpel`
   frames, but the attach decision should also require the object's own
   motion history to be non-trivial before it can ever render.

### Verdict

Tracks beat soft on the two metrics the user complained about (flicker,
shadow under the feet) and on temporal stability, with zero
category-specific code, and they expose the remaining defects as named
per-object numbers. They lose on component count and prop area, which are
the hole-fill and disc priors the soft path had. The direction holds;
the next work is feature supply for low-contrast objects and the
contact-point co-motion fix, both generic.

## Reproduce

```bash
cd rotoscope_vision && swift build -c release
B=.build/release/rotoscope-vision
$B ~/IMG_0974.mov --subject held --evidence legacy --metrics runs/legacy --out-dir runs/legacy --no-mov
$B ~/IMG_0974.mov --subject held --evidence soft   --metrics runs/soft   --out-dir runs/soft   --no-mov \
   --baseline runs/legacy/summary.json --objective bench/objective.json
python3 scripts/sweep.py ~/IMG_0974.mov --frames 60 --out runs/sweep --keys diffCenter,priorWeight,structWeight,smoothRadius
$B ~/IMG_0974.mov --subject held --evidence tracks --metrics runs/tracks --out-dir runs/tracks --no-mov \
   --baseline bench/baseline-tracks.json --stills runs/tracks --stills-every 30   # NNNN-tracks.csv/.png, NNNN-objects.json
```
