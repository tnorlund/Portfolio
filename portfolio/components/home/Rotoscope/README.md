# Browser rotoscope

The browser implementation follows Tyler Norlund, Iain Murphy, and Vivek K.
Pallipuram's 2017 best-features rotoscope pipeline. The paper/MATLAB behavior is
canonical; the later MPI/OpenCV port is useful performance history but is not a
golden implementation.

The production demo uses **single-image mode** because the homepage portrait has
no matching clean-background frame. A low-frequency blurred copy replaces that
one input, after which the original stages remain recognizable:

1. source-vs-blurred-copy difference map;
2. analytical Shi-Tomasi minimum-eigenvalue scores;
3. spatially suppressed markers with explicit face/body/background quotas;
4. deterministic eight-neighbor marker-controlled watershed flooding;
5. one mean source RGB color per watershed region.

`algorithm.ts` is the readable scalar reference and correctness oracle for the
optimized WebAssembly kernel. The authored normalized focus geometry belongs to
the image configuration rather than the numerical stages, keeping the engine
reusable for future portraits.

Homepage processing stays 960×720. `portraitConfig.ts` sets a periocular
`focus.face` ellipse around both Vision eyes (plus lid/brow) so the face quota
lands in the irises instead of the whole skull; leftover head/shoulders sit on
`focus.body` at coarser spacing. Quotas are `{face: 0.30, body: 0.64,
background: 0.06}` with spacing `{face: 1, body: 4, background: 8}`. Engine
defaults in `algorithm.ts` stay `{face: 0.5, body: 0.3, background: 0.2}` for
the explainer and lab.

Regenerate the `/rotoscope` article stills and the homepage basin fallback after
changing homepage size or quotas:

```sh
npx tsx scripts/generate-rotoscope-explainer-stills.ts
```

`generate-rotoscope-basins.ts` still rebuilds only `rotoscope-basins.webp`.

`public/rotoscope-basins.webp` is the no-JavaScript / worker-unavailable
fallback: a 960×720 watershed outline of the same homepage pass. JavaScript
keeps that image out of the frame during idle, processing, and Replay so the
canvas sits on the dark page background. Unpainted canvas pixels stay
transparent. The worker still groups flat-color regions into catchment basins
and assigns a 36-step reveal schedule: Apple Vision's eyes, nose, and mouth
regions begin first, the rest of the person mask follows, and its inverse
background finishes the sequence. Each basin grows radially from its own stable
interior point over ~1100ms. The homepage figure itself is the Replay
control; there is no caption overlay. Paper and source notes live on
`/rotoscope`. `/rotoscope-lab` stays unlinked.

Production keeps all computation off the main thread. The versioned worker
decodes and resizes the source once, caches the authored focus map, and runs the
allocation-free scalar Wasm kernel in one reusable arena. If Wasm fetch,
compilation, export validation, allocation, or execution fails, the same worker
falls back to the TypeScript oracle. Worker protocol
v5 embeds the compact Vision person mask and transfers the final pixels plus one
byte per pixel of reveal phases; the main thread reuses one Canvas2D frame and
only paints when a phase advances.

The worker currently routes Firefox directly to that scalar oracle. Production
browser medians showed its JavaScript JIT completing this workload much faster
than its scalar Wasm backend; Chromium and WebKit retain the faster Wasm path.
The checked-in benchmark makes that routing decision repeatable instead of
assuming one backend wins everywhere.

Build the committed Wasm and worker artifacts with Node 22:

```sh
npm run build:rotoscope-wasm
npm run build:rotoscope-worker
```

After a production export is being served, measure normal and forced-fallback
paths across the installed browser engines with at least five warmups and 20
recorded runs:

```sh
npm run benchmark:rotoscope -- http://127.0.0.1:3202
```

There is intentionally no separate SIMD artifact yet. The scalar Wasm module is
small, broadly compatible, and remains the only optimized path until a verified
SIMD build wins browser medians enough to justify another download.

The paper's stage structure remains canonical. Two numerical kernels are
intentional display-resolution optimizations and are covered by exact fixtures:
the browser uses an integer 3x3 Sobel instead of the MATLAB Gaussian derivative
at sigma 0.6, and a 3x3 structure-tensor window instead of its 7x7 window.
