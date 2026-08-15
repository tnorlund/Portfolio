# Vision portrait worker

This zero-dependency macOS command-line tool runs Apple Vision against the
checked-in rotoscope portrait. It writes deterministic, browser-friendly
metadata using normalized top-left coordinates plus a compact run-length
encoded mask for the primary person.

The website never uploads a photo or runs Swift at request time. Next.js and
Linux CI consume the checked-in artifacts and do not need Apple frameworks.

From the repository root:

```bash
swift run --package-path tools/vision-portrait-worker vision-portrait-worker \
  --input portfolio/public/rotoscope-portrait.jpg \
  --manifest portfolio/public/rotoscope/vision-portrait-v1.json \
  --mask portfolio/public/rotoscope/vision-person-mask-v1.json
```

Verify that the artifacts still match the source portrait and installed Vision
revisions:

```bash
swift run --package-path tools/vision-portrait-worker vision-portrait-worker \
  --input portfolio/public/rotoscope-portrait.jpg \
  --manifest portfolio/public/rotoscope/vision-portrait-v1.json \
  --mask portfolio/public/rotoscope/vision-person-mask-v1.json \
  --check
```

CI validates the checked-in manifest, source hash, and mask hash without
rerunning model inference (which may vary across macOS releases):

```bash
swift run --package-path tools/vision-portrait-worker vision-portrait-worker \
  --input portfolio/public/rotoscope-portrait.jpg \
  --manifest portfolio/public/rotoscope/vision-portrait-v1.json \
  --mask portfolio/public/rotoscope/vision-person-mask-v1.json \
  --validate-only
```

The generator pins stable request revisions. Face landmarks use revision 3 and
the 76-point constellation because the current installed SDK does not expose
the beta 98-point revision.
