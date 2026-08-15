# Rotoscope Lab

`/rotoscope-lab` is an unlinked, `noindex` developer playground for marker-controlled
watershed experiments on the homepage portrait. It intentionally has its own client,
versioned protocol, and scalar worker (`/rotoscope/lab-worker-v3.js`). Nothing in this
directory is imported by the homepage portrait or its production worker.

The lab keeps the canonical grayscale, source-luminance watershed, flood, and regional
mean-color stages. Controls only change marker desirability and selection:

- **Best features** uses the production Shi–Tomasi score field. With no noise it is an
  exact scalar reference run.
- **Radial** builds an anisotropic Gaussian density around a configurable origin. A
  coverage floor and broader body/background tails keep distant regions eligible.
- **Hybrid** linearly blends normalized feature scores with the Gaussian density.
- **Apple Vision** weights a checked-in primary-person mask, all 76 native face
  landmarks, visible pose joints, saliency regions, and bounded contour samples.
- **White**, **value**, and **fractal value (fBm)** noise modulate the density.
- Seeded Gumbel priorities turn density into weighted sampling without replacement;
  deterministic tier quotas and Manhattan suppression provide blue-noise separation.

All noise uses seeded uint32 hashing and explicit `Float32` rounding. The lab client
allows one active request and one replaceable queued request, so rapid slider changes
cannot build an unbounded worker queue.

The basin diagnostic colors regions by the tier of their marker: blue for face, orange
for body, and gray for background. Apple Vision mode outlines the selected primary-
person mask in cyan. Marker and label digests make saved settings easy to compare across
reruns.

Vision is generated offline by the zero-dependency macOS CLI in
`tools/vision-portrait-worker`. The website never uploads the portrait or runs native
code at request time; it validates and consumes the versioned static artifacts only.
