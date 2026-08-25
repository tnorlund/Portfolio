# Context — what the tracks path is and what has already been learned

Distilled from the MacBook sessions that built `--evidence tracks`. Read
this before touching code; most of it cost hours to learn.

## How the pipeline flows (held mode, tracks evidence)

`FocusAnalyzer.analyze` per frame:
1. Vision person instance mask (`person`), `others` (other people),
   optical flow update, pose (diagnostic only).
2. `FrameRegistrar` → homography to the background plate;
   `plateDifference` → `difference` (tolerant, ±16 px slack) and the
   `warpedPlate` RGBA.
3. `TrackEvidence.compute` (`Sources/RotoscopeVisionCore/TrackEvidence.swift`):
   - `FeatureTracker.step` — LK-refines every live track with three seeds
     (dense flow, object-transform prediction, zero motion), forward–backward
     check, revival of lost tracks, Shi–Tomasi re-detection in empty
     `trackSpacing` cells. ≈1000 tracks, FB error p95 0.016 px, 34 ms.
   - `TrackClassifier.classify` — background-consensus similarity
     (`Similarity.fitRobust` over non-subject tracks), per-track
     `staticScore`, windowed displacement vs the composed background,
     `plateAgreement` (strict 3×3 patch vs plate within ±2 px — the ±16 px
     pixel tolerance let a 12-px bar "match" the floor), NCC shadow test,
     labels with hysteresis (`labelHold`).
   - `ObjectClusters.update` — union-find over rigid links (pairwise distance
     drift < `rigidDrift` over `motionWindow`, both tracks moved ≥ 2 px,
     within `clusterLink` px), identity by shared track ids, canonical
     coordinates + similarity fit, rigid/deformable verdict by median
     residual, expulsion of persistent outliers (`outlierExpel`), attachment
     = any-member contact (`contactRadius`, chamfer DT of the person mask)
     weighted 0.35 + co-motion with nearest subject tracks weighted 0.65
     (enter `attachEnter`, exit `attachExit` after `labelHold`), occlusion
     holds the transform (no extrapolation) for `occlusionGrace` frames.
   - Rendering (`ObjectRender.swift`): deformable/undecided objects → crop
     watershed seeded by member tracks (object), border + `others` + quiet
     plate pixels beyond 2·`hullRadius` (background), person (subject);
     object basins kept where `difference ≥ plateThreshold/3` or within
     8 px of a track; components must touch a track. Rigid objects → template
     captured at best support after `templateDelay`, rendered through
     `transform · captureTransform⁻¹`; if the photometric residual exceeds
     `photoTolerance` the frame falls back to growth and support decays so a
     re-capture happens. Nothing renders past 5 occluded frames.
   - `LabeledMask` = person (1) + object labels (2+); `FocusAnalyzer` takes
     `labeled.mask(person:)` as the alpha when tracks produced anything.
4. Engine paint (Shi–Tomasi → markers → watershed) over the masked frame.

## Metrics you will read

`--metrics DIR` → `metrics.jsonl` (per frame), `summary.json` (mean/p95/max
per key, objective, red lines), `contact.png`. Keys that matter for the
mask: `propFlicker` (prop components appearing/vanishing per frame),
`maskTemporalIoU`, `maskComponents`, `bgFalseRate` (red line), `propArea`,
`shadowLikeInProps`, `floorContactLeak`; per-object: `objectAttached`,
`objectIdChurn`, `objPersistence`, `objGeomResidualMean`,
`objPhotoResidualMean`, `objColorDriftMean`, `objAreaDeltaMean`. With
`--stills DIR --stills-every 30` you also get `NNNN-tracks.png` (overlay:
track colour = label, ring = object), `NNNN-tracks.csv`
(id,x,y,label,status,age,static,plate,fb,ssd,object) and
`NNNN-objects.json` (one `ObjectReport` per object: kind, status,
liveTracks, attachScore, contactFrac, comotion, area, photoResidual, …).
The fastest way to understand a bad keyframe is: which objects are
`attached` in the JSON, and where are their tracks in the CSV.

MacBook reference (macOS 26.x, `bench/baseline-tracks.json`): propFlicker
1.81, maskTemporalIoU 0.931, maskComponents 7.2, bgFalseRate 0.042,
shadowLikeInProps 0.078, floorContactLeak 0.008, propArea 12.2k, msTotal
365. The Mini's first run gave propFlicker 2.27, maskComponents 9.4,
bgFalseRate 0.039 — different Vision output, hence the re-baseline rule.

## Things that were wrong once and why (do not re-learn)

- **Templates flew off** (grey discs mid-air): occlusion extrapolated
  velocity every frame. Now the transform is held. Do not reintroduce
  extrapolation without a bound.
- **Bar tracks labelled background**: the tolerant difference (±16 px)
  matched the bar to the floor beside it. Track-level plate agreement is a
  strict ±2 px 3×3 comparison. Keep the two tolerances separate.
- **Whole background became one rigid object** during still phases: rigid
  links now require both tracks to have moved ≥ 2 px over the window and
  clustering waits `motionWindow` frames. "Both still ⇒ co-moving" is not
  evidence — that rule attached a bystander who paused beside the subject.
- **Bystander + plate chained into one object** while both were briefly
  still; identity then kept them merged. Fix was expulsion of persistent
  fit outliers (`outlierStreak ≥ outlierExpel`), looser bound (3×) for
  deformable objects so the band does not fragment.
- **Plate interior lost**: "quiet" background seeds (difference below
  threshold) were planted inside the black plate on the dark floor. Seeds
  now stay 2·`hullRadius` away from tracks. Watch this if you change
  `hullRadius`.
- **RGBA warped per pixel through `warp(_:fill:)`** silently produced
  garbage under `-Ounchecked`; use `OpticalFlow.warpRGBA`.
- `bench/baseline-soft.json` has `paintTemporalDelta` 95.7 from that bug;
  the real value is ≈ 10.8. Ignore that key in soft comparisons.
- The classifier once called static background "shadow-like" because the
  median plate is darker than any single frame; the static test runs first.

## Known open defects (the milestones in CHARTER.md)

1. Right plate detaches mid-descent: at frame 150 the object had
   `contactFrac` 0.7 but `comotion` −0.3 — its centroid's nearest subject
   tracks are on the shoulder/head, moving differently from the hands.
2. Left plate: two tracks in its region at frame 0; when it does form an
   object (frame 90) it renders as a clean disc, so it is feature supply.
3. Fragments: 3–5-track objects grow small regions → `maskComponents` 7–9.
4. Shadow at the plate rim inside grown regions → `shadowLikeInProps` 0.08.

## Working conventions on this machine

- Worktree `~/Portfolio-rotoscope-vision` (branch
  `claude/rotoscope-vision-metrics`, HTTPS origin, `gh` authenticated).
  `~/Portfolio` is another checkout on `main` with someone else's
  uncommitted infra work — do not touch it.
- Build: `swift build -c release` (≈15 s). Xcode is installed, so
  `swift test` works here (the MacBook only had CLT).
- Full clip: ≈75 s. Use `--max-frames 100` for quick iterations, but prove
  a change on the full clip before committing.
- `codex` is installed: `codex review --base origin/claude/rotoscope-vision-metrics`
  before every commit; fix HIGH/MEDIUM findings first.
- Commit message shape:

  ```
  [Rotoscope] tracks: <what>

  Why: <mechanism, 1–2 sentences>
  propFlicker 2.27 → 1.90, maskComponents 9.4 → 6.1, bgFalseRate 0.039 → 0.039 (red lines: none)
  Keyframes: <what changed in contact.png>
  ```
- Push after every commit. Cloudflare WARP runs on this box; pushes go out
  fine, but inbound IPv4 SSH is blocked (the MacBook reaches it over
  link-local IPv6) — irrelevant to you, just do not "fix" the network.
