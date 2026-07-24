# Sprouts graphics: protected-paper integration proof

## Integration red

Combining the measured-column change `bd4cfa21f` with the initial graphics
fix `026bbe2c4` enabled protected-paper texture. The synthetic barcode remained
visible, but the production Vision detector returned no symbols:

```text
image: /tmp/sprouts-integration-3lanes/sprouts_eval/sprouts.syn.png
graphics: FAIL
real:  Code128 99022003402972471754
synth: none
```

The exact Code128 payload fix was still present. The remaining failure was
the in-body sizing pass: `_fit_1d_barcode_tile_to_box` cropped the generator's
scanner quiet zones and stretched the bars across the complete target box.
That symbol decoded at only `0.532922` confidence with the original texture
and stopped decoding when protected-paper processing sharpened the paper/ink
boundary.

## Renderer hardening

Sprouts now retains `python-barcode`'s quiet zones around its HRI-derived
Code128 symbol. The existing crop-to-ink behavior remains the default for
other merchant profiles, so their bytes are unchanged.

A unit test asserts that both outer edges of the prepared Sprouts barcode are
paper, preventing a future crop from silently removing the scanner margins.

## Before and after

| render mode | before | after | detector confidence |
|---|---|---|---:|
| original texture | PASS | PASS | 0.831938 |
| protected paper (`bd4cfa21f`) | FAIL | PASS | 0.845623 |

Both passing detections decode the exact payload:

```text
symbology: Code128
payload: 99022003402972471754
payload_match: true
missing_in_synth: []
phantom_in_synth: []
```

Tokens, logo, and arithmetic remain `PASS` in the standalone lane. The
protected-paper integration run also keeps the measured columns `PASS`.
