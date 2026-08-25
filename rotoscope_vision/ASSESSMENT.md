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

## Round three (Mini): contact-point co-motion, feature supply, minimum support

Continued on the Mac Mini (macOS 26.x). First lesson of the machine: Apple
Vision's output is deterministic back-to-back (two runs of one binary are
byte-identical) but **drifts across builds and time** — the same baseline code
gave propFlicker 2.27 when `bench/baseline-tracks-mini.json` was captured and
1.72 an hour later. So a delta read by comparing an old `runs/` dir to a new
one is contaminated by drift, not the code change. Every number below is
therefore from a **single back-to-back sequence** in one session: the
baseline, B, C and D binaries run one after another on the full clip. The
committed `bench/baseline-tracks-mini.json` is used only for the red-line gate.

| metric (mean) | baseline | B | C | D (final) | baseline→D |
|---|---|---|---|---|---|
| propFlicker | 1.72 | 2.39 | 2.48 | **0.87** | ↓ 49 % |
| maskComponents | 7.71 | 10.66 | 10.17 | **4.25** | ↓ 45 % |
| propArea | 12.2k | 14.2k | 19.8k | **21.4k** | ↑ 75 % |
| objectAttached | 1.30 | 1.46 | 2.15 | 2.01 | ↑ |
| objPersistence | 0.345 | 0.437 | 0.412 | 0.431 | ↑ |
| objPhotoResidual | 33.7 | 31.8 | 24.2 | 24.0 | ↓ (better fits) |
| bgFalseRate | 0.043 | 0.041 | 0.037 | 0.036 | ↓ (no red line) |
| maskTemporalIoU | 0.931 | 0.925 | 0.919 | 0.928 | ≈ |
| shadowLikeInProps | 0.115 | 0.086 | 0.086 | 0.087 | ↓ 24 % |
| floorContactLeak | 0.013 | 0.017 | 0.049 | 0.056 | ↑ (worse) |
| msTotal | 263 | 269 | 273 | 300 | +37 ms |

**B — contact-point co-motion** (`ObjectClusters`). Co-motion ranked subject
tracks by distance to the object's centroid, so a barbell was compared against
the shoulder/head tracks nearest its middle rather than the hands that carry
it. Now it ranks by distance to the members in contact with the subject. This
attaches more of the real props (objectAttached, objPersistence, propArea all
up) but, on its own, trades flicker and components up (2.39, 10.66) because the
extra attached objects grow fragmented regions — a cost D removes. The frame-90
background debris disappears and the left plate is recovered where B had
neither.

**C — feature supply inside attached objects' hulls** (partial). A second,
lower-threshold Shi–Tomasi pass confined to attached objects' hulls feeds
low-contrast objects the corners the global floor misses. propArea +39 % with
bgFalseRate *falling* — the charter's C gate met. But the "left plate on ≥5/7
keyframes" target was not: it reaches 2/7. The left plate is geometrically
isolated (the body occludes the bar between it and the right plate), so the bar
object's hull never dilates far enough to seed it; supply reinforces it only
once it has independently formed. A global lower threshold *would* reach it but
raised bgFalseRate and lowered propArea (background corners cluster into false
objects), so it was rejected — the charter's warning confirmed.

**D — minimum rendered support.** Three gates on emergent properties (no
category prior): carry an occluded object through the gap only once it has a
confirmed rigid/deformable kind; morphological-close a *rigid* object's mask so
a dark plate's difference-gated speckle becomes one disc (the deformable band
is left hollow, per the charter); drop connected components below
`minRenderArea`. maskComponents 10.2→4.25 and propFlicker 2.48→0.87 while
propArea is preserved — the shattered plate is now a solid disc and the
per-frame speckle is gone. The ~4 remaining components are the real props
(person + band + bar + plate), not fragments: raising `minRenderArea` to 800
does not lower the count.

**E — object-level shadow rejection** (not met, reverted). The charter's
prescribed NCC-vs-plate texture test was applied per pixel inside the grown
mask, then (after it was undone by D's rigid close) as a final pass on each
object's mask. It reduced floorContactLeak ~0.012 and shadowLikeInProps ~0.015
but could not approach the ≤0.04 target and left the keyframes visually
unchanged, because the dominant shadow is the deep-squat/left-plate-edge cast
shadow on flat dark floor and at the frame edge — exactly where the test has no
textured, in-bounds plate to correlate against (the same limitation the Round
one assessment flagged for the chromaticity test). It also destabilised
propFlicker. Reverted; `floorContactLeak` 0.056 remains the round's open
defect. A height/floor-plane gate (ankles → floor line) is the likely fix, but
it is a scene prior beyond the NCC approach the milestone specified.

**Verdict.** The mask is substantially better than the Round-two baseline on
this machine: half the flicker, half the components, three-quarters more true
prop area (the left plate and both plates now render solid), better object
fits, and lower background leak — all with zero bar/disc/band knowledge. The
one regression is shadow/floor leakage under the props (floorContactLeak,
shadowLikeInProps flat), which E could not remove with the texture test alone.

Final render: `~/IMG_0974-rotoscope.mov` (+ `-preview.mp4`), default params,
`--evidence tracks`, HEAD of `claude/rotoscope-vision-metrics`.

## Round four (Mini): presence — H diagnosis (cause → frames)

Phase two replaced the seven-keyframe contact sheet with `Presence.swift` (an
evaluation-only truth proxy: the band is orange, the plates are dark discs
beyond the hands along the pose bar line) and `scripts/presence.py`, which
lists every frame where the mask covers < 50 % of an object's truth. The
sheet had hidden all of this. Baseline (`bench/baseline-presence-mini.json`):

| object | mean recall | missing | frames |
|---|---|---|---|
| band | 0.858 | 21 | 17–19, 22, 24, 83–84, 88, 93–100, 192, 194–197 |
| right plate | 0.738 | 42 | 4–23, 45–50, 57–61, 124–134 |
| left plate | 0.150 | 163 | 0–80, 82, 87, 112, 114, 118–122, 125–197 |

Reading the worst frames' `-presence.png` (R band truth, G plate truth, B
mask; magenta/cyan = covered) with `-objects.json` and `-tracks.csv`, every
missing frame falls into one of three causes:

| # | cause | objects × frames | count | why | milestone |
|---|---|---|---|---|---|
| 1 | **No object exists yet** — nothing is tracked in the first `motionWindow` (15) frames, so no object of any kind has formed (`-objects.json` is empty on frames 0–15). | left plate 0–15; right plate 4–15 | ~28 | tracker warm-up | **I** |
| 2 | **No object ever forms for the left plate** — an object exists (bar + right plate) but the left plate never gets its own: the body occludes the bar between the two plates so it is not rigidly linked in-frame, and a dark rim on a dark rack yields too few corners to cluster alone. Recovered only 83–120, where it is low, visible and moving. | left plate 16–80, 125–197 | ~147 | isolation | **K** |
| 3 | **Object attached, then loses its tracks → occluded past `occlusionGrace` → no longer rendered** while the object is still on screen. Right plate: the low-contrast plate at chest height (45–61) and through the descent (124–134) sheds its corners; the object goes occluded and the grace window expires. Band: it is covered on most frames only because Vision's person segmentation swallows it — where Vision drops it (start 17–24, deep-squat foreshortening 83–100, end 192–197) the deformable band object has also lost its tracks, so nothing covers it. | right plate 16–23, 45–61, 124–134; band 17–24, 83–100, 192–197 | ~55 | track loss / Vision-seg gap | **J** (band), **I/K** (plates) |

Key confirmations from the stills: at f60 both plates are pure green (truth
present, mask absent) though the bar object is attached — the plate objects
have gone occluded and retired; at f8 the whole `-objects.json` is empty and
only the band (magenta, via person-seg) is covered; at f96 the plates are cyan
(covered) but the band is red — Vision drops the foreshortened band and its
object is gone. Fix order by frames recoverable is the charter's I → J → K,
except the left-plate isolation (cause 2) is the largest single bucket (~147
frames) and the hardest.

### I — clip-start warm-up (attempted; deferred as disproportionate)

Diagnosed on the data, not theorised. At frame 5 the tracker already holds 73
tracks on the right plate and 60 on the left (`0005-tracks.csv`); they are not
missing for lack of detection. Their `plateAgreement` is 0.20–0.40 — they
plainly disagree with the background plate — versus 0.6–0.74 for the true
background beside them, so appearance *can* tell them apart. They are labelled
`background` only because they are not **moving**: the person holds the bar
still through frames 0–23, so the classifier's motion cue never fires and the
`else` branch defaults an unknown track to background.

The trouble is that three successive gates are all motion-gated, and the clip
start has no motion to give them:
1. **Candidacy** requires `label ∈ {moving, attached}` — a still prop is
   `background`, excluded.
2. **Clustering** is gated `frame ≥ motionWindow` and rigid links require both
   tracks to have moved ≥ 2 px (the guard that stops the whole static
   background clustering into one object — CONTEXT).
3. **Attachment** is `0.35·contactFrac + 0.65·max(0,comotion)`; with no motion
   `comotion = 0`, so `attachScore ≤ 0.35 < attachEnter (0.6)` — even a formed
   object would not attach, so it would not render.

Relaxing 1–3 to admit still, plate-disagreeing tracks was ruled out: it
re-opens exactly the "static background becomes an object" failure the CONTEXT
warns about, and the attachment gate still blocks rendering. The only correct
fix is the charter's first option — a **backward/lookahead warm-up**: run the
tracker forward past the first motion (~frame 30), let the plate object form
with real motion, then back-project it onto frames 0–W (each member track's
`position(at:k)` is known from frame 0, so a read-only `grow` covers the
plate). Design sketched and mostly written (`TrackEvidence.retroObjectAlphas`,
a startup buffer in the main loop). It was **not shipped** because it needs a
fixed-lag output: the main loop consumes each frame's mask immediately into
paint → video → ~20 temporal metric state vars, and the shared `OpticalFlow`
object is advanced by `analyze`, so a naive W-frame buffer computes every
flow-based metric (propFlicker, maskTemporalIoU, paintTemporalDelta — including
a red line) against the wrong frame's flow field unless the flow field is also
buffered per frame. That is a large, correctness-fraught change to the whole
pipeline for the smallest bucket (~28 frames), so per the charter's
stop-and-write-up rule it is deferred in favour of J and K, which are
self-contained and where K is the largest bucket (~147 frames). A future
session should implement the pre-roll with per-frame flow buffering.

### J — band in the deep squat (premise invalidated on this run)

The charter's J assumes "the deformable [band] object loses tracks as the band
foreshortens." On this machine's run that is not what happens: a deformable
object exists only on frames **111–148** (`-objects.json` across the clip), so
during the entire band-missing window 83–100 there is **no band object at
all** — nothing with a learned histogram to revive. The band is covered on
80–92 and 101+ purely because Vision's person segmentation swallows it, and it
is missing on 83–84, 88, 93–100 exactly where Vision drops the foreshortened
band. So "revive the object on chroma back-projection" has no object to act on.

The real cause is the same class as the left plate: the band, compressed and
thin at the bottom of the squat, does not yield enough distinct orange tracks
to cluster into a deformable object (the plate/subject tracks nearby dominate
that region), so no object forms and the person-seg gap is left uncovered. A
chroma-persistence layer that re-adds a recently-covered colour region when the
person mask drops it would recover it, but that is a new mechanism, not the
charter's object-revival, and it risks bgFalseRate. J is therefore folded into
the feature-supply work rather than done as specified; K is taken next.

## Reproduce

```bash
cd rotoscope_vision && swift build -c release
B=.build/release/rotoscope-vision
# Phase two: presence is the truth. Run, then list missing frames per object:
$B ~/IMG_0974.mov --subject held --evidence tracks --metrics runs/presence --out-dir runs/presence \
   --no-mov --stills runs/presence --stills-every 1 --baseline bench/baseline-presence-mini.json
python3 scripts/presence.py runs/presence/metrics.jsonl   # NNNN-presence.png: R band, G plate, B mask
# Round three: judge a change by running two binaries BACK TO BACK (Vision drifts across builds)
$B ~/IMG_0974.mov --subject held --evidence tracks --metrics runs/tracks --out-dir runs/tracks --no-mov \
   --stills runs/tracks --stills-every 30 --baseline bench/baseline-tracks-mini.json
$B ~/IMG_0974.mov --subject held --evidence legacy --metrics runs/legacy --out-dir runs/legacy --no-mov
$B ~/IMG_0974.mov --subject held --evidence soft   --metrics runs/soft   --out-dir runs/soft   --no-mov \
   --baseline runs/legacy/summary.json --objective bench/objective.json
python3 scripts/sweep.py ~/IMG_0974.mov --frames 60 --out runs/sweep --keys diffCenter,priorWeight,structWeight,smoothRadius
$B ~/IMG_0974.mov --subject held --evidence tracks --metrics runs/tracks --out-dir runs/tracks --no-mov \
   --baseline bench/baseline-tracks.json --stills runs/tracks --stills-every 30   # NNNN-tracks.csv/.png, NNNN-objects.json
```
