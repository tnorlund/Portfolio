# Merchant intelligence artifacts

Each `<slug>.json` is the output of the merchant research pipeline
(`merchant_research/`).  Files are version-controlled so the tax gate and
synthesis catalog can use the latest validated research without re-running
the agent.

## How these are generated (do not hand-edit)

Every committed artifact is emitted by the deterministic builder in
`merchant_research/known_merchants.py`, which feeds per-merchant research
evidence (receipts via MCP, web/jurisdiction rate, Places) through
`research.assemble_merchant_intelligence`.  Regenerate with:

```bash
python -m receipt_agent.agents.label_evaluator.merchant_research.known_merchants \
  --generated-at <ISO-timestamp>
```

`test_known_merchant_artifacts.py` enforces that the committed set of artifacts
EXACTLY equals the builder's output, so a hand-added or hand-edited artifact
fails CI.  To add a NEW merchant, add a `MerchantResearchInput` to
`NEW_MERCHANTS` and re-run the builder — never drop a JSON file in by hand.

The tax gate resolves an artifact by exact slug first, then by brand-prefix on a
`_` boundary (so `CVS Pharmacy` / `Sprouts Farmers Market #123` resolve to the
`cvs` / `sprouts_farmers_market` artifact), with the hardcoded
`MERCHANT_TAX_PROFILES` dict as the final fallback.

## Human-approval gate

Each artifact carries a `review` block `{status, reasons, approved_by,
approved_at}`. **The status is recomputed deterministically in the loader on
every read — the stored block is informational only, so hand-editing it to
`auto_approved` enables nothing.**

| status | meaning | drives the gate? |
| --- | --- | --- |
| `auto_approved` | high confidence, ≥2 independent sources, matches a validated config OR single-jurisdiction with ≥3 taxed receipts | **yes** |
| `needs_review` | medium confidence / new merchant or jurisdiction / <2 sources / multi-jurisdiction / too few taxed receipts | no — generated but **parked** |
| `rejected` | sources contradict / unreliable, or no tax evidence | no |
| `approved` | a human signed off a `needs_review` artifact (see below) | **yes** |

**Only `auto_approved` or human-`approved` intelligence may enable taxable edits
or parameterize synthesis.** A parked artifact returns nothing to the gate, so
known merchants fall back to the hand-validated `MERCHANT_TAX_PROFILES` (a prior
human validation) and new merchants stay disabled until approved.

Human sign-offs live in `_approvals.json`, **keyed by `(slug, sha256(tax
block))`** — so changing any tax fact yields a new hash and the merchant reverts
to `needs_review`, and a research regeneration never wipes a sign-off (it only
rewrites the `<slug>.json` files, not the ledger).

`_approvals.json` **is** the approval authority: it is a version-controlled,
code-review-gated ledger, not a tamper-resistant store. Treat a change to it
like a code change — anyone who can commit a matching entry can approve. The
content-hash binding limits any single entry to one exact tax block.

```bash
# See what is parked awaiting review:
python -m receipt_agent.agents.label_evaluator.merchant_research.known_merchants list --needs-review
# Sign off on a merchant's current tax block:
python -m receipt_agent.agents.label_evaluator.merchant_research.known_merchants approve <slug> --by <name> --note "..."
```

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
