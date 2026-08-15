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
worker-produced color result expands radially from the centered face over this
map, so the page starts with the catchment basins instead of flashing the source
photo. Regenerate the projection whenever the portrait, processing size, or
marker configuration changes.

The paper's stage structure remains canonical. Two numerical kernels are
intentional display-resolution optimizations and are covered by exact fixtures:
the browser uses an integer 3x3 Sobel instead of the MATLAB Gaussian derivative
at sigma 0.6, and a 3x3 structure-tensor window instead of its 7x7 window.
