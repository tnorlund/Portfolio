# Rotoscope Lab

`/rotoscope-lab` is an unlinked, `noindex` developer playground for marker-controlled
watershed experiments on the homepage portrait. It intentionally has its own client,
versioned protocol, and scalar worker (`/rotoscope/lab-worker-v1.js`). Nothing in this
directory is imported by the homepage portrait or its production worker.

The lab keeps the canonical grayscale, source-luminance watershed, flood, and regional
mean-color stages. Controls only change marker desirability and selection:

- **Best features** uses the production Shi–Tomasi score field. With no noise it is an
  exact scalar reference run.
- **Radial** scores pixels by anisotropic distance from a configurable origin and ellipse.
- **Hybrid** linearly blends normalized feature and radial scores.
- **White**, **value**, and **fractal value (fBm)** noise perturb the score field before
  deterministic tier quotas and Manhattan suppression are applied.

All noise uses seeded uint32 hashing and explicit `Float32` rounding. The lab client
allows one active request and one replaceable queued request, so rapid slider changes
cannot build an unbounded worker queue.

The basin diagnostic colors regions by the tier of their marker: blue for face, orange
for body, and gray for background. Marker and label digests make saved settings easy to
compare across reruns.
