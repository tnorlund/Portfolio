# rotoscope-vision: measurement-first optimization plan

Goal: turn the `held`-mode pipeline from a stack of hand-tuned thresholds
that is judged by eyeballing stills into an instrumented system that an
agent can adjust and optimize against numbers — and fix the two visible
failures (bumper plates phasing in and out, the cast shadow clipping in and
out) with modeling changes rather than more thresholds.

## 1. Diagnosis

What the current pipeline does per frame (`FocusAnalyzer.addHeldProps`):

```
plate difference ─► strong (>56) / weak (>24) ─► shadow test ─► band clamp
  ─► others removed ─► open (erode/dilate) ─► strong-connected-to-person
  ─► temporal carry (prev ∧ weak, dilate 4) ─► island drop ─► component
  expiry (age > 90) ─► hole fill ─► alpha
```

Every stage is a hard threshold on a single scalar followed by binary
morphology. That architecture explains both symptoms directly:

- **Plates phase in and out.** A black bumper plate over the black rack or
  dark floor has |Δ| below the entry threshold across most of its area;
  only its rim (where it crosses a bright bench or the wall) is detected.
  The interior then depends on `fillHoles` finding a *closed* rim. Any gap
  — motion blur, a bar highlight crossing it, a rim segment under 56 — and
  the whole disc drops. Component-level carry/expiry is all-or-nothing, so
  the disc does not degrade, it vanishes and reappears.
- **Shadow clips in and out.** The chromaticity test is a per-pixel yes/no
  with a fixed ratio band (50–100 % of plate brightness). A soft gym shadow
  sits right on that band; pixels flip frame to frame; whichever way the
  majority falls, the strong-connected rule and the carry latch it in (or
  out) for tens of frames. The shadow and the black plate are also
  genuinely ambiguous to that test — that is why loosening it ate the
  plates in one iteration and tightening it let the shadow back in the
  next.
- **No feedback loop.** Twenty tuning iterations were judged from a 7-frame
  contact sheet. There is no per-frame number for "how much background
  leaked", "how stable is the mask", or "how faithful is the paint", so
  every change was a guess about 198 frames from 7.

Registration and the plate are in decent shape (residual after refinement
is visibly small; 46/198 homographies rejected but the coarse+refine path
covers them). They need *measurement*, not rework.

## 2. Principles for the rework

1. **Soft evidence, one decision.** Each cue becomes a per-pixel probability
   in [0, 1]; cues combine into a posterior; a single final decision (a
   threshold, or better, a graph cut that pays for boundary length) makes
   the mask. No stage thresholds mid-pipeline.
2. **Explicit temporal model.** Optical flow (Vision, macOS 14+) warps the
   previous posterior into the current frame as a prior; nothing is carried
   as a binary set.
3. **Structural priors from Vision.** Body pose gives elbows, wrists,
   ankles: the bar is the line through the elbow crooks (Zercher) or
   wrists, the band is between the ankles, plates sit at the bar's ends.
   Shadows lie on the floor plane below the feet.
4. **Numbers before pictures.** Every run writes per-frame metrics; the
   agent reads a summary and a contact sheet, changes one parameter set,
   and compares against a baseline. Everything is deterministic (no RNG,
   fixed sample strides), so a run is reproducible bit for bit.

## 3. Deterministic measurables

All written per frame to `run/metrics.jsonl` and aggregated (mean / p95 /
max / count) into `run/summary.json`. Grouped by what they check.

### 3a. Registration & plate (is the background truly quiet?)
| Metric | Definition | Good |
|---|---|---|
| `bg_residual_median` | median tolerant-difference over pixels outside subject∪props∪others | < 8 |
| `bg_false_rate` | fraction of those pixels with difference > entry threshold | < 0.5 % |
| `reg_accepted` | homography accepted by the gate (0/1) | ≥ 80 % of frames |
| `reg_refine_px` | \|dx\|+\|dy\| from photometric refinement | small, smooth |
| `reg_jump_px` | translation change vs previous frame | < 15 |
| `plate_valid_frac` | fraction of plate pixels with ≥1 sample | > 85 % |

### 3b. Subject mask (no ground truth needed)
| Metric | Definition | Good |
|---|---|---|
| `mask_area` and `mask_area_delta` | subject∪props pixels, and frame-to-frame % change | delta < 5 % except at real motion |
| `mask_temporal_iou` | IoU(mask_t, warp(mask_{t−1}, flow)) — the warping-error idea from video matting, applied to our mask | > 0.95 |
| `mask_components` | 8-connected components of the final mask | 1 |
| `mask_holes` | enclosed background pockets | ~0 |
| `mask_boundary_ratio` | boundary length / sqrt(area) (jaggedness) | stable |
| `mask_soft_band` | fraction of alpha strictly between 16 and 240 | small, stable |

### 3c. Props (bar, plates, band)
| Metric | Definition | Good |
|---|---|---|
| `prop_area` per tracked component | component identity carried by flow overlap | smooth |
| `prop_flicker` | frames where a component's area changes > 30 % or it appears/disappears | 0 |
| `plate_fit` | RANSAC circle fit on each dark prop blob: center, radius, inlier ratio | radius stable ±5 %, inliers > 0.7 |
| `bar_line_fit` | line fit through bar pixels: angle, length, RMS residual | length stable, residual < 2 px |
| `pose_bar_agreement` | distance between fitted bar line and elbow/wrist line from body pose | < 10 px |

### 3d. Shadow
| Metric | Definition | Good |
|---|---|---|
| `shadow_like_in_props` | prop pixels whose chromaticity matches the plate at reduced brightness | ≈ 0 |
| `floor_contact_leak` | prop pixels on the floor plane below the ankle keypoints | ≈ 0 |

### 3e. Paint (the rotoscope itself — superpixel-benchmark metrics)
| Metric | Definition | Good |
|---|---|---|
| `paint_psnr` | PSNR of painted vs source inside the mask (explained variation) | track per budget |
| `paint_boundary_recall` | fraction of strong source edges (Sobel > τ) that lie on a basin boundary | > 0.8 |
| `paint_region_count`, size p5/p50/p95 | basin statistics | stable |
| `paint_temporal_delta` | mean \|color_t − warp(color_{t−1})\| inside mask | low = no basin flicker |
| `marker_persistence` | markers that survive flow-propagation frame to frame | high |

### 3f. Supervised (a handful of keyframes)
Hand-label ~8 keyframes (standing, mid-descent, bottom, ascent, band
visible, plate over bench, plate over rack, shadow prominent) as RGBA masks
with four classes: person, bar, plate, band. Store in
`eval/IMG_0974/keyframes/NNNN.png`. Metrics: per-class IoU, boundary
F-measure at 2 px, and dtSSD between adjacent labeled pairs. These are the
ground truth the unsupervised metrics are calibrated against.

### 3g. Cost
Per-stage wall time so a change that halves flicker but triples runtime is
visible.

## 4. Algorithm changes, ranked by expected impact

1. **Probabilistic evidence + one decision** (fixes phasing at the root).
   - `p_diff`: logistic on the tolerant difference (center = threshold, width
     = 12) instead of strong/weak.
   - `p_shadow`: Cucchiara-style chromaticity — HSV/normalized-RGB distance
     to the plate pixel, brightness ratio in (0.4, 0.95); output a
     probability, subtract from `p_diff` rather than zeroing it.
   - `p_prior`: previous posterior warped by optical flow, blended 0.6.
   - `p_struct`: distance-to-bar-line and distance-to-plate-disc from pose
     (below); soft, radius-shaped.
   - Posterior = weighted product/sum (weights are parameters); decision by
     a min-cut on a 4× downsampled grid with a boundary-length penalty
     (Boykov–Kolmogorov, ~0.2 MP, fast in Swift), then upsampled and
     feathered. This removes hole-fill, opening, component expiry, and the
     carry as separate mechanisms.
2. **Optical flow temporal fusion** (`VNTrackOpticalFlowRequest`, macOS 14):
   warp previous posterior, previous mask, and marker positions. Also gives
   `mask_temporal_iou` for free.
3. **Bumper plates as tracked discs.** Detect dark discs (RANSAC circle on
   the gradient of the difference image, or a Hough disc search along the
   bar line ends); keep a disc track per plate with a constant-velocity
   filter; render the disc as prop evidence. Their paint is trivial
   (black) so this is purely a mask problem.
4. **Bar from pose.** `VNDetectHumanBodyPoseRequest` elbows/wrists → line;
   prop evidence along it with a width prior (12 px); persists through the
   white-bench crossing without any carry.
5. **Shadow as floor-plane evidence.** Ankles from pose + the plate's dark
   floor region define "floor"; shadow probability is raised there and
   suppressed elsewhere, so a dark plate at bar height is never a shadow.
6. **Registration metrics as the gate.** Accept a homography when the
   measured `bg_residual_median` improves, not when its magnitude looks
   plausible; drop the magnitude gate and the jump anchor.
7. **Paint stability.** Propagate marker seeds by flow, re-seed only where
   flow confidence is low or a basin grows past a size limit.
8. **Alternative subject backbone** (measure, do not assume): Robust Video
   Matting CoreML (`rvm_mobilenetv3_1280x720`) gives a temporally coherent
   person alpha; compare `mask_temporal_iou` and keyframe IoU against the
   Vision instance mask.

## 5. The agent loop

- **One config.** Every tunable lives in `Params` (Codable), serialized as
  JSON with defaults, ranges, and a one-line meaning:
  `rotoscope-vision run clip.mov --params p.json --out run/`.
- **One eval command.** `rotoscope-vision eval run/` (or `run` with
  `--eval`) writes `metrics.jsonl`, `summary.json`, keyframe stills, and a
  contact sheet PNG (keyframes × [source | focus | diff | paint]).
- **Objective.** `score = Σ w_i · normalized_metric_i`, with supervised IoU
  dominating when keyframes exist and the unsupervised set (−flicker,
  −bg_false_rate, −shadow_like, +temporal IoU, +boundary recall, +PSNR)
  otherwise. Weights live in `eval/objective.json` so they can be argued
  about in a PR.
- **Sweep.** `scripts/sweep.py`: coordinate descent then random restarts
  over declared ranges, each trial a subprocess run on a 60-frame excerpt
  (fast) with the full clip only for the top 3; writes `leaderboard.csv`.
- **Regression gate.** `eval/baseline.json` is committed; a run that
  worsens any red-line metric (bg_false_rate, prop_flicker, keyframe IoU)
  by more than its tolerance fails, so an agent cannot "improve" the plates
  by leaking the bench.
- **What the agent sees.** `summary.json` + `contact.png` + a diff against
  the baseline. That is enough to decide the next parameter or the next
  modeling change without opening 198 frames.

## 6. Milestones

| # | Deliverable | Behavior change | Exit criterion |
|---|---|---|---|
| M1 | `Params` JSON, `metrics.jsonl`, `summary.json`, contact sheet, `eval/baseline.json` | none | current pipeline has numbers; 8 keyframes labeled |
| M2 | optical flow fusion (mask prior, marker propagation), `mask_temporal_iou` | yes | prop_flicker and paint_temporal_delta drop vs baseline |
| M3 | soft evidence + min-cut decision, shadow probability | replaces carry/expiry/hole-fill | shadow_like_in_props ≈ 0 with plate IoU ≥ baseline |
| M4 | body-pose bar line, plate disc tracks | yes | plate_fit stable on every keyframe; bar present in all standing keyframes |
| M5 | `scripts/sweep.py`, objective, leaderboard, regression gate | tooling | one sweep improves the objective without a red-line regression |
| M6 | RVM CoreML backbone behind a flag | optional | decided by the same metrics |

## 7. Risks and unknowns

- Vision optical flow on a thin chrome bar may be noisy; the bar prior from
  pose is the backstop. Flow at `.high` accuracy costs ~30–60 ms/frame.
- Body pose confidence drops when the bar occludes the torso at the bottom
  of the squat; fall back to the previous line, and measure
  `pose_bar_agreement` to know when.
- Min-cut at full resolution is slow in scalar Swift; run at quarter
  resolution and upsample the label map with the feathered alpha.
- Hand-labeled keyframes are the one non-deterministic input; keep them in
  git and treat edits as data changes with their own PRs.

## References

- Video matting temporal metrics (dtSSD, warping error): Lin et al., Robust
  High-Resolution Video Matting with Temporal Guidance (WACV 2022) —
  https://arxiv.org/abs/2108.11515 ; RVM CoreML models —
  https://github.com/PeterL1n/RobustVideoMatting
- Superpixel quality metrics (boundary recall, undersegmentation error,
  explained variation): Stutz et al., Superpixel Segmentation: A Benchmark —
  https://www.sciencedirect.com/science/article/abs/pii/S0923596517300735
- Chromaticity shadow detection (HSV, brightness-ratio band): Cucchiara et
  al., and the survey at
  https://www.sciencedirect.com/science/article/abs/pii/S0030399213002156
- Apple Vision: `VNTrackOpticalFlowRequest` (macOS 14) —
  https://developer.apple.com/documentation/vision/vntrackopticalflowrequest ;
  `VNDetectHumanBodyPoseRequest` joints —
  https://developer.apple.com/documentation/vision/vnhumanbodyposeobservation/jointname ;
  What's new in Vision (WWDC22, optical flow revision 2) —
  https://developer.apple.com/videos/play/wwdc2022/10024/
