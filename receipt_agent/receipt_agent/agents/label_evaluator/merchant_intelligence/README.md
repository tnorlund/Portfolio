# Merchant intelligence artifacts

Each `<slug>.json` is the output of the merchant research pipeline
(`merchant_research/`).  Files are version-controlled so the tax gate and
synthesis catalog can use the latest validated research without re-running
the agent.

## Schema

```json
{
  "merchant": "Vons",
  "slug": "vons",
  "tax": {
    "taxable_flag": "T",
    "nontaxable_flags": ["S"],
    "jurisdiction": "CA-Ventura",
    "validated_rate": "0.0725",
    "jurisdiction_rates": [],
    "can_support_taxable_edits": true,
    "confidence": "high",
    "provenance": [
      "receipts: 5 receipts, effective rate 7.25-7.26%",
      "web: CA Ventura County base rate 7.25% (verified 2026-06)"
    ]
  },
  "catalog": [
    {"name": "ITEM NAME", "price": "2.99", "taxable": false, "source": "receipt"}
  ],
  "details": {
    "address": "Street, City, ST ZIP",
    "store_id": "1234",
    "category": "grocery"
  },
  "generated_at": "2026-06-26T00:00:00+00:00",
  "sources": {
    "receipts": {"count": 5, "image_ids": ["abc123", "..."]},
    "web": {"urls": ["https://..."], "fetched_at": "2026-06-26"},
    "places": {"place_ids": ["ChIJ..."]}
  }
}
```

## Rules

- `can_support_taxable_edits: true` requires BOTH a reliable per-item taxable
  flag AND a stable, cross-receipt-validated rate. The deterministic gate in
  `merchant_tax_config.py` is the final arbiter — never set this field `true`
  without receipt evidence.
- `jurisdiction` distinguishes single-rate merchants (e.g. `"CA-Ventura"`)
  from multi-jurisdiction ones (e.g. `"multi"` with `jurisdiction_rates`).
- The research pipeline fills `provenance` with the evidence strings that
  justify the values.  Reviewers should check provenance before merging.
- Do NOT hand-edit an artifact to enable `can_support_taxable_edits` — re-run
  the research pipeline with more receipts.
- Monetary/rate fields (`validated_rate`, `jurisdiction_rates[]`, `catalog[].price`)
  are JSON **strings**, not numbers, so `Decimal` values round-trip losslessly
  into the deterministic tax gate.
- An artifact's internal `slug` MUST equal its filename slug; the loader rejects
  a file whose `slug` field disagrees (it would otherwise mis-resolve a merchant).
