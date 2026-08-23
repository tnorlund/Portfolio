# rotoscope-vision

Runs the best-feature rotoscope over a movie on macOS, with Apple Vision
supplying the focus tiers and lifting the subject off the background.

```
┌──────────────┐   ┌──────────────────────────────┐   ┌─────────────────────────────┐
│ AVAssetReader│──▶│ Vision per frame              │──▶│ Engine (port of algorithm.ts)│
│ upright BGRA │   │  person / subject mask        │   │  gray → masked box blur      │
│              │   │  face landmarks → eye ellipse │   │  → |Δ| → Sobel → Shi–Tomasi  │
│              │   │  ⇒ per-pixel focus tiers      │   │  → tiered markers            │
│              │   │    face / body / background   │   │  → watershed (bg = barrier)  │
│              │   │                               │   │  → mean color per basin      │
└──────────────┘   └──────────────────────────────┘   └──────────────┬──────────────┘
                                                                     ▼
                                     <name>-rotoscope.mov   ProRes 4444 + alpha + source audio
                                     <name>-rotoscope-preview.mp4   H.264 over white/black matte
```

The engine (`Sources/RotoscopeVisionCore/Engine.swift`) is a line-for-line
port of `portfolio/components/home/Rotoscope/algorithm.ts` — same Rec. 601
integer gray, same box blur, integer 3×3 Sobel, Shi–Tomasi on a 3×3 tensor
window, the same tier quota / diamond-suppression marker selection, and the
same 256-bucket minimum-barrier watershed. Two additions make Vision useful:

- Focus tiers arrive as a **per-pixel map** instead of an ellipse + polygon.
  `FocusAnalyzer` builds it from `VNGeneratePersonInstanceMaskRequest` (or
  subject lift / all-people segmentation) plus `VNDetectFaceLandmarksRequest`:
  the periocular ellipse around the detected eyes is `face`, the rest of the
  subject mask is `body`, everything else is `background`.
- When the background is removed, background pixels are a **watershed
  barrier** (never flooded, alpha 0) and the box blur is **mask-normalized**,
  so the subject's silhouette against the dropped background does not become
  the strongest "texture" in the difference image and steal the markers.

## Build & run

```bash
cd rotoscope_vision
swift build -c release
./.build/release/rotoscope-vision ~/IMG_0974.mov
# → ~/IMG_0974-rotoscope.mov (ProRes 4444, alpha) and ~/IMG_0974-rotoscope-preview.mp4
```

A 720×1280 @ 30 fps clip processes at roughly 0.06 s/frame on Apple silicon
(Vision is most of it).

| Flag | Default | Meaning |
|------|---------|---------|
| `--out-dir DIR` | next to the input | where the two movies go |
| `--width W` | source width | downscale the processing/output frame |
| `--subject person\|foreground\|people\|none` | `person` | how the subject is lifted: largest person instance, Vision subject-lift (keeps props like a barbell), every person, or no mask |
| `--keep-background` | off | rotoscope the whole frame; background gets a 10 % marker share |
| `--budget N` | 1200 | marker budget per frame |
| `--face-quota F` | 0.3 | share of the budget inside the eye ellipse (rest is body) |
| `--spacing F,B,BG` | 2,4,8 | Manhattan suppression radius per tier |
| `--blur R` | 3 | box-blur radius of the stand-in background |
| `--matte white\|black` | white | what the MP4 preview composites transparent pixels over |
| `--stills DIR --stills-every N` | — | dump `NNNN-rotoscope.png` (over the matte) and `NNNN-focus.png` (mask + ellipse + markers by tier) |
| `--max-frames N` | all | stop early (trial runs) |
| `--no-mov`, `--no-audio` | — | skip the ProRes file / the audio passthrough |

Frames where Vision finds no face spend the face share on the body; frames
where the mask is empty fall back to rotoscoping the whole frame so nothing
goes blank.

## Tests

`Tests/RotoscopeVisionCoreTests` covers the engine with XCTest (needs Xcode).
On a machine with only Command Line Tools, the same checks run standalone:

```bash
swiftc -O Sources/RotoscopeVisionCore/Engine.swift scripts/engine_check/main.swift -o /tmp/engine_check && /tmp/engine_check
```
