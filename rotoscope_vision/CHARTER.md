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
