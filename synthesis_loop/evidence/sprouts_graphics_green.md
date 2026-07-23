# Sprouts graphics fidelity: green proof

## Root cause

The fallback renderer found the 20-digit HRI and drew a bar field, but it did
not encode the printed value:

1. `scripts/render_synthetic_receipts.py` converted the exact HRI
   `99022003402972471754` to a hyphenated visual-density surrogate.
2. `python-barcode`'s Code128 optimizer interpreted a leading numeric data
   pair of `99` as a charset-switch opcode and removed it. An isolated render
   decoded as `022003402972471754`, proving that the symbol itself had lost the
   first pair.
3. After the normal crop and paper-texture passes, Vision could not decode the
   hyphenated surrogate at all, producing the graphics red baseline.

The renderer now keeps Sprouts' printed HRI as the Code128 payload and
preserves a leading `99` data pair in the encoder. Other merchants retain the
existing visual-density fallback.

## Fidelity result

The same read-only dev receipt and truth tuple from the red baseline were
evaluated with:

```text
python synthesis_loop/full_fidelity_eval.py run \
  "Sprouts Farmers Market" \
  00ded398-af6f-4a49-86f7-c79ccb554e48 1 sprouts \
  --out-root /tmp/sprouts-graphics-after --allow-dirty
```

Before:

```text
graphics FAIL
real  = Code128 99022003402972471754
synth = none
```

After:

```text
graphics PASS
real  = Code128 99022003402972471754
synth = Code128 99022003402972471754
payload_match = true
missing_in_synth = []
phantom_in_synth = []
```

The passing guard metrics stayed green:

```text
tokens     PASS -> PASS
logo       PASS -> PASS
arithmetic PASS -> PASS
```

The full receipt remains overall `FAIL` only because the independent columns,
style, and separators lanes are still red on this branch.

## Regression guards

```text
pytest tests/test_render_systemic_fixes.py
15 passed

corpus_regression_gate.py check --json
{"ok": true, "findings": []}

render_regression_guard.py check /tmp/sprouts-graphics-render-guard
costco_golden: byte-identical
costco_new: byte-identical
vons_golden: byte-identical
sprouts: CHANGED (expected)
```

The intended Sprouts change is confined to the barcode box:

```text
baseline SHA256 d19f03193d9b808e6e89e022ebdd5daba60e75ff4b526051321e146e2807028b
current  SHA256 f0d84b17e1c224b7d7039664fcf812e16e24c3cca81f72d454ac3c9d3bea3cf6
changed bbox [194, 1633, 640, 1725]
changed pixels 38181 / 1897720 (2.0119%)
pixel MAD 2.019530
```

Formatting, focused MyPy, and focused Pylint checks also pass.
