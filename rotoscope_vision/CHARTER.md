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
