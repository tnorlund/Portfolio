# Sprouts graphics fidelity: red baseline

- renderer commit: `e517d33fdc043abfcbff5f70e87dfd21bccc8952`
- merchant: `Sprouts Farmers Market`
- receipt: `00ded398-af6f-4a49-86f7-c79ccb554e48#1`
- truth: `sprouts_farmers_market` v1
  `18dc387fbfd985fbc236e413ca051959613fe6e66c263f2a9a23fe67551cb74b`
- table: read-only dev `ReceiptsTable-dc5be22`

Command:

```text
python synthesis_loop/full_fidelity_eval.py run \
  "Sprouts Farmers Market" \
  00ded398-af6f-4a49-86f7-c79ccb554e48 1 sprouts \
  --out-root /tmp/sprouts-graphics-before
```

Graphics result:

```json
{
  "verdict": "FAIL",
  "real": [
    {
      "kind": "1d",
      "payload": "99022003402972471754",
      "symbology": "code128",
      "x_frac": 0.0,
      "y_frac": 1.0
    }
  ],
  "synth": [],
  "matched": [],
  "missing_in_synth": [
    {
      "kind": "1d",
      "payload": "99022003402972471754",
      "symbology": "code128",
      "x_frac": 0.0,
      "y_frac": 1.0
    }
  ],
  "phantom_in_synth": []
}
```

Passing guard metrics at the same baseline were tokens, logo, and
arithmetic. The synthetic image visibly contained a barcode-like bar field
above the matching HRI caption, but the production Vision detector decoded no
symbol from it.
