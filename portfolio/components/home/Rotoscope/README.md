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

`public/rotoscope-basins.webp` is the immediate first-paint projection of that
same 480x360 result: region boundaries are dark and interiors are neutral. The
map remains fully visible while the worker-produced color result arrives. The
worker groups the final flat-color regions into catchment basins and assigns a
36-step reveal schedule: Apple Vision's eyes, nose, and mouth regions begin
first, the rest of the person mask follows, and its inverse background finishes
the sequence. Each basin grows radially from its own stable interior point. This
avoids a single page-wide wipe while preserving the basin map as the first frame.

Production keeps all computation off the main thread. The versioned worker
decodes and resizes the source once, caches the authored focus map, and runs the
allocation-free scalar Wasm kernel in one reusable arena. If Wasm fetch,
compilation, export validation, allocation, or execution fails, the same worker
falls back to the TypeScript oracle. The basin projection stays visible during
idle initialization and remains the no-JavaScript experience. Worker protocol
v4 transfers the final pixels plus one byte per pixel of reveal phases; the main
thread reuses one Canvas2D frame and only paints when a phase advances.

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
