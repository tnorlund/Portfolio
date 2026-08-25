# Charter — rotoscope-vision, tracks path (Mac Mini session)

You are a Claude Code session running on the Mac Mini, branch
`claude/rotoscope-vision-metrics`, worktree `~/Portfolio-rotoscope-vision`,
package `rotoscope_vision/`. Your job is to make the **mask** better on
`~/IMG_0974.mov` using the `--evidence tracks` path, prove each improvement
with the committed metrics, and push every proven step with a commit message
that says *why* it is better. Read `CONTEXT.md` first, then `ASSESSMENT.md`
("Round two"), then `PLAN.md`.

## Mission

A general system: "rotoscope this video of me holding things" — the subject
plus whatever moves with them, lifted off the background, stable over time.
The barbell and the band are the test case, **not** the design: nothing in
the tracks path may know what a bar, a disc, or a band is. Stay in Swift and
Apple frameworks.

## Ground rules

1. **Re-baseline on this machine first.** Vision output differs between macOS
   builds; the MacBook's `bench/baseline-tracks.json` gave propFlicker 1.81
   there and 2.27 here. Before changing code, run the reproduce command below
   and commit the result as `bench/baseline-tracks-mini.json`. Compare every
   later run against that file, not the MacBook's.
2. **A change ships only with numbers.** Every commit that touches
   `Sources/` must state, in the message body, the before → after of the
   metrics it targets (mean, from `summary.json`), confirm the red lines were
   not crossed (`--baseline` prints them), and say in one or two sentences
   why the mechanism is more correct — not just that the number moved.
3. **Look at the pictures.** `contact.png` (source | focus | tracks | paint)
   and the keyframe stills are the truth the metrics approximate. A metric
   win with a visibly worse keyframe is not a win; say so and revert.
4. **No category priors.** No disc fitting, no bar lines, no "band is
   orange". If a fix only works because of the shape or colour of these
   props, it is the wrong fix.
5. **Commit small, push immediately.** After each proven step:
   `codex review --base origin/claude/rotoscope-vision-metrics` → fix
   HIGH/MEDIUM → commit → `git push`. Never leave work local.
6. **Keep the legacy/soft paths compiling** until milestone M4 (see
   PLAN.md); do not delete them earlier.

## Ordered milestones

Each has a metric target and a picture check. Do them in order; stop and
write up if one cannot be met after a reasonable attempt, and move on.

| # | Work | Proof |
|---|---|---|
| A | Re-baseline (`bench/baseline-tracks-mini.json`, `runs/` ignored) | committed file; note the MacBook deltas in the message |
| B | **Contact-point co-motion.** `ObjectClusters` compares an object's motion with the 10 subject tracks nearest its *centroid*; for a long object those are shoulder/head tracks. Compare with the subject tracks nearest the object's members that are in contact (`distance < contactRadius`). | right plate stays attached through the descent (frames 130–170); `objectAttached` steadier, `objPersistence` up, `propFlicker` down |
| C | **Feature supply for low-contrast objects.** The left plate has ~2 tracks at frame 0 (dark rim on a dark rack). Generic options: a second, lower `trackMinScore` pass restricted to cells inside an attached object's hull (dilated by `hullRadius`); or multi-scale Shi–Tomasi on a half-res gray. Keep `trackBudget`. | left plate present on ≥ 5 of 7 keyframes; `propArea` up without `bgFalseRate` rising |
| D | **Minimum rendered support.** Objects with fewer than N agreeing tracks or tiny grown regions produce fragments (`maskComponents` 7–9 vs soft 2.3). Gate rendering on tracks × area, or on `objPersistence` of the object. | `maskComponents` ≤ 4 with `propFlicker` not worse |
| E | **Object-level shadow rejection.** `shadowLikeInProps` is 0.08 (soft 0.037): grown regions catch the plate's shadow. Use the NCC-vs-plate texture test already in `TrackClassifier` at the *pixel* level inside `ObjectRender.grow` (reject basins whose patch correlates with the plate and is darker). | `shadowLikeInProps` ≤ 0.04, `floorContactLeak` unchanged |
| F | Update `ASSESSMENT.md` with a "Round three (Mini)" table: baseline-mini → after each of B–E; render `~/IMG_0974-rotoscope.mov` + preview with the final params. | table + videos; push |
| G | If B–E all hold: **M4** from PLAN.md — `bench/objective-tracks.json`, sweep keys for the tracks params, then delete legacy/soft code paths and their params. | tracks beats soft on propFlicker, shadowLikeInProps, maskTemporalIoU with equal bgFalseRate |

## Phase two: presence (added 2026-08-25)

`Presence.swift` (evaluation only) now grades the mask per frame against a
truth proxy for each held object; `scripts/presence.py runs/x/metrics.jsonl`
lists the frames where the band / left plate / right plate are missing
(recall < 0.5). `Objective.presence` is the default objective in tracks
mode: the three recalls carry most of the weight and are red-lined (no
recall may drop > 0.02 vs the baseline). **These lists are the truth now;
the contact sheet only samples every 30th frame and hid all of this.**

Baseline on this machine (`runs/presence` when you re-run it; commit it as
`bench/baseline-presence-mini.json`): band missing 21/198 (17–19, 22, 24,
83–84, 88, 93–100, 192, 194–197; mean recall 0.86); right plate missing 42
(4–23, 45–50, 57–61, 124–134; 0.74); left plate missing 163 (0–80, 82, 87,
112, 114, 118–122, 125–197; 0.15).

Key fact learned: the band is in the output on most frames because Vision's
person segmentation includes it, not because the tracks found it. The
missing band frames are exactly where Vision drops it and the track object
does not catch it. Look at `NNNN-presence.png` (R truth band, G truth
plates, B mask) on the worst frames before theorising.

| # | Work | Proof (full clip, back-to-back binaries) |
|---|---|---|
| H | Commit `bench/baseline-presence-mini.json`. Then, per object, open the worst frames' `-presence.png` + `-objects.json` + `-tracks.csv` and write in ASSESSMENT.md *why* each is missing (no object / object not attached / object attached but not rendered / rendered but wrong place). Three or four causes will cover everything; fix in order of frames recovered. | a table of cause → frames |
| I | **Clip start (frames 4–23, both plates; 17–24 band).** Nothing is tracked yet in the first `motionWindow` frames, so no object can exist. Generic options: run the tracker warm-up over the first N frames *backwards* (seed from frame N, propagate to 0) or let objects form from `plateAgreement` alone before motion is available. | plates present from frame ≤ 5; bandRecall on 17–24 > 0.5 |
| J | **Band during the deep squat (93–100).** Vision drops it, the deformable object loses tracks as the band foreshortens. Candidates: revive from the object's colour histogram (already learned) when tracks die; keep the object alive on chroma back-projection for `occlusionGrace` frames. | bandRecall > 0.5 on 83–100; no bgFalseRate change |
| K | **Left plate (0–80, 125–197).** Still the isolation problem. If C's hull seeding cannot reach it, try seeding supply along the *rigid object's extrapolated extent*: a rigid object's template edge that ends at the person boundary continues on the other side — grow the supply mask by mirroring the object's canonical extent through the occluder. Generic (any rigid object partially occluded by the subject). | plateRecallLeft > 0.5 on ≥ 60 % of frames |
| L | Re-render `~/IMG_0974-rotoscope.mov` + preview, ASSESSMENT.md "Round four" with the before/after missing-frame lists, push. | |

Rules from phase one still apply: no category priors in `Sources/` (the
truth proxy in `Presence.swift` is the *only* place allowed to know what a
band or plate looks like, and it must never be read by the mask path),
codex review before every commit, numbers + why in every message, push
immediately.

## Phase three: motion-independent attachment (added 2026-08-25)

Round four proved the ceiling: candidacy, clustering and attachment are all
motion-gated, so a held object that is stationary (clip start), occluded
between its visible parts (left plate), or too compressed to have its own
motion (band in the squat) can never become an attached object — however
clearly it disagrees with the background plate and touches the subject. The
goal restated without motion: **an attached object is a coherent cluster of
tracks that is not background (disagrees with the plate), is not another
person, touches the subject, and persists.** Motion becomes corroborating
evidence, not the gate. Still no category priors.

Design (each step is generic; the barbell/band only test it):

1. **Foreign cue in `TrackClassifier`.** A track is *foreign* when its EMA
   `plateAgreement` stays < `foreignAgreement` (new param, ~0.45) for
   `foreignHold` frames (~10) and it is not person-core or `others`. Static
   foreign tracks keep the label `.background` today; add `.foreign` (static,
   not background) as a label with the same hysteresis as the others.
   Background that was in the plate median agrees with the plate by
   construction, so this cannot fire on the rack or floor.
2. **Candidacy in `ObjectClusters`**: candidates = moving ∨ attached ∨
   foreign. Clustering may start at frame ≥ `foreignHold` for foreign-only
   clusters (the `motionWindow` warm-up stays for motion-based ones).
3. **Links between two foreign tracks do not require motion** (the "both
   moved ≥ 2 px" guard exists to stop the static background clustering; foreign
   tracks are not background). Keep `rigidDrift` distance stability over the
   window and `clusterLink` range.
4. **Appearance attachment path.** `attachScore = max(motionScore,
   appearanceScore)` with `appearanceScore = contactFrac × foreignFrac ×
   persistence`, where foreignFrac = fraction of members that are foreign,
   persistence = min(1, age / motionWindow). Same `attachEnter/attachExit`
   and hold. Log which path attached (`ObjectReport.attachPath`).
5. **Object-level co-rigid merge.** Two attached/candidate objects whose
   relative transform is stable over the window (drift < `rigidDrift`, both
   foreign-dominated) merge into one object even beyond `clusterLink` — a
   rigid thing seen in two pieces around an occluder is one thing. Identity
   keeps the older id.
6. **Do not retire a rigid object whose template still explains the pixels.**
   Occlusion today is "tracks gone"; if the template's photometric residual
   under the held transform stays < `photoTolerance`, keep it attached and
   re-seed tracks inside it (cause 3).

| # | Work | Proof (full clip, back-to-back binaries, vs `bench/baseline-presence-mini.json`) |
|---|---|---|
| M | Steps 1–2 (foreign label, candidacy). Add `foreignTrackCount` to metrics. | on frames 0–23 the plate tracks are `.foreign` in `-tracks.csv`; `objects.json` non-empty from frame ≤ `foreignHold`; bgFalseRate unchanged |
| N | Steps 3–4 (foreign links, appearance attachment). | plateRecallRight missing frames 4–23 and 45–61 recovered (recall > 0.5); band 17–24 recovered; bgFalseRate red line holds; propFlicker not worse |
| O | Step 6 (template-keeps-object). | right plate 124–134 recovered; objectOccluded down |
| P | Step 5 (co-rigid merge). | plateRecallLeft > 0.5 on ≥ 60 % of frames |
| Q | Band in the squat (83–100): with foreign candidacy the band's own tracks (orange on dark floor disagree with the plate) should form a deformable object before Vision drops it. Verify; if not, say why. | bandRecall > 0.5 on 83–100 |
| R | Round five in ASSESSMENT.md with the before/after missing-frame lists per object; re-render `~/IMG_0974-rotoscope.mov` + preview; push. | |

Guard rails for this phase: the danger is false objects — a still bystander
(handled by `others`), someone's bag on the floor beside the subject that was
absent from the plate median, a shadow (no texture of its own; the NCC
shadow test already exists — apply it to foreign candidacy). Watch
`bgFalseRate`, `maskComponents` and the frame-0/30/60 stills for anything
that is not the person, the bar, the plates, or the band. If a step recovers
frames but adds a false object, it does not ship; write it up.

## Phase four: flow propagation across gaps (added 2026-08-25)

Rounds three to five made bad frames a little better one at a time. The
presence lists say the failures are *gaps between good frames*: the left
plate is present on 90–120 and missing on 0–80 and 125–197 (it rests while
missing — zero motion); the band is missing for 8 frames (93–100) between
frames that have it; the right plate for 10 (124–134). Optical flow cannot
find any of these, but it can **carry a label we already established across
frames that do not contradict it**. Dense flow is computed every frame
already (`OpticalFlow`: `warp(_:fill:)` warps the previous frame's field into
the current one, `warpRGBA` does the same for colour, `backwardVector` /
`forward` give the vectors). It is used only to seed tracks and to carry an
occluded alpha for 5 frames. This phase lets it bridge real gaps, in both
directions, with a verifier as the gate. Offline two-pass processing is fine.

Design — generic, no object identity required:

1. **Propagated layer** = the non-person part of the final mask:
   `mask ∧ ¬dilate(person, 2)`. Whatever the pipeline (tracks *or* Vision's
   person segmentation) put outside the person is a held-object label. This
   deliberately includes the band on the frames where only Vision had it.
   The person mask itself is never propagated (Vision is reliable there).
2. **Pass 1** (today's pipeline) additionally stores per frame to a scratch
   directory (`--scratch DIR`, default `runs/<name>/scratch`): the final
   `mask` alpha, `person`, `difference`, the pose JSON, and the forward flow
   field (Float16 or Int16 fixed-point ×16 to halve the size; ~2 GB for the
   clip is acceptable, it is git-ignored). Frames are re-read from the movie
   in later passes, never stored.
3. **Pass 2 — propagate.** For each frame t and each connected component C
   of the propagated layer at t (area ≥ `minRenderArea`), warp C **forward**
   frame by frame with the stored flow (`warp`), and **backward** by gather:
   `C_{t-1}(p) = C_t(p + flow_{t-1→t}(p))` (bilinear). At every step verify:
   warp the *colour* along with the label (`warpRGBA` / the same gather) and
   compute the mean max-channel residual between the warped colour and the
   actual frame inside the warped label; stop when it exceeds
   `propagatePhotoTolerance` (~`photoTolerance`), when the warped area falls
   below `minRenderArea`, when it enters `others`, or after
   `propagateMaxGap` frames (param; the left plate needs ≥ 90 backward).
   Pixels under the person mask at the destination frame are dropped (the
   person occludes). Skip steps whose destination already covers ≥ 90 % of
   the warped label (nothing to add). The result per frame is the union of
   the original layer and every verified propagation reaching it; when
   forward and backward propagations overlap, take the max.
4. **Pass 3 — render + metrics** from the propagated masks: the existing
   writers and `FrameMetrics` (presence included) run over the final masks,
   so every number is about what is in the video.
5. Still nothing category-specific: this is "held-object labels persist
   through frames that do not contradict them". Shadows and floor leaks
   that exist today will propagate too where verification passes — watch
   `floorContactLeak`, `shadowLikeInProps`, `bgFalseRate` and stills every
   15 frames (`--stills-every 15`), and cap with `propagateMaxGap` if needed.

| # | Work | Proof (full clip, vs `bench/baseline-presence-mini.json` and the current single-pass run back-to-back) |
|---|---|---|
| S | Scratch store + `--two-pass` plumbing with propagation *disabled* (`propagateMaxGap 0`). | pass-3 output and every metric byte-identical to single-pass; `swift build` clean; scratch size logged |
| T | Propagation with verification (steps 3–4). Try `propagateMaxGap` 30 / 90 / 150 and report all three. | left plate recall ≥ 0.6 (missing ≤ 80 frames); band missing frames 83–100 recovered; right plate 124–134 recovered; bgFalseRate red line holds; no new false objects in the every-15 stills; report floorContactLeak/shadowLikeInProps honestly |
| U | If T ships: make `--two-pass` the default in tracks mode; ASSESSMENT.md "Round six" with the before/after missing-frame lists and the three maxGap results; re-render `~/IMG_0974-rotoscope.mov` + preview; push. | |

Process for this phase: `codex review` is broken on this machine — push
after every milestone regardless and say in the commit message that the
diff is self-reviewed; the MacBook session reviews from there. Keep every
proof same-binary back-to-back (Vision drift), and quote the presence lists
verbatim in every commit message.

## No-touch

- Anything outside `rotoscope_vision/`.
- `portfolio/`, `infra/`, the Python packages — other sessions own them.
- `bench/baseline-soft.json`, `bench/baseline-legacy.json`,
  `bench/baseline-tracks.json` (MacBook numbers — history, not targets).

## Reproduce / measure

```bash
cd ~/Portfolio-rotoscope-vision/rotoscope_vision
swift build -c release
B=.build/release/rotoscope-vision
$B ~/IMG_0974.mov --subject held --evidence tracks --metrics runs/x --out-dir runs/x --no-mov \
   --stills runs/x --stills-every 30 --baseline bench/baseline-tracks-mini.json
# runs/x: summary.json, metrics.jsonl, contact.png, NNNN-tracks.csv/.png, NNNN-objects.json
# Numeric unit checks (no XCTest on CLT-only machines; the Mini has Xcode, so `swift test` also works):
swiftc -O Sources/RotoscopeVisionCore/{Engine,LucasKanade,ObjectModel,Params,FeatureTracker,BackgroundPlate,TrackClassifier}.swift \
   scripts/tracks_check/main.swift -o /tmp/tracks_check && /tmp/tracks_check
```

Full-clip run is ~75 s on this machine. `runs/` is git-ignored.
