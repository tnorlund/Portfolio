# RUNBOOK: Vision-Scout Triage Pass (P1)

How to run the read-only triage pass that writes dossier v2 files. One
agent handles ~15 receipts; ~8 agents run in parallel over the backlog
(dev's mismatch + near + no-baseline receipts). Agents are READ-ONLY:
no DynamoDB writes, no S3 writes; the only output is dossier files
under `.dev-harness/dossiers/`.

## Hard rules (from the pilot's risk findings)

1. **Full-res images only.** View the receipt image from the dev site
   bucket at `assets/<image_id>/<receipt_id>.jpg` — NEVER a thumbnail
   or `_thumbnail`/`_small` variant. The pilot read cent digits at the
   limit of downscaled JPEGs (pilot risk #3); a triage verdict made on
   a thumbnail is not evidence.
2. **Transcribe before you target.** Transcribe the item rows from the
   image BEFORE computing or looking at the target sum (pilot risk #2:
   sum-to-target confirmation bias — a transcription made while
   knowing the target is not independent confirmation). Record the
   transcription in `visual_evidence` first, then do the arithmetic.
3. **Vision answers "are these rows products?"** — it never proposes a
   number. Numbers come from the dry-run simulation only.
4. **High confidence requires justification.** The pilot returned
   "high" on 14/14, which means an unjustified confidence signal
   carries no information. `confidence: "high"` without
   `confidence_justification` is invalid.
5. **An honest abstention beats a guess.** If no verdict is supported,
   emit `verdict_recommendation: "flag"` with the reason, or omit the
   proposal entirely.

## Per-receipt procedure (<= 3 tool calls)

```
1. python scripts/agentic_triage_helpers.py \
     --image-id <image_id> --receipt-id <receipt_id>
   -> JSON digest: words/lines with section membership, sections,
      decoded line items vs baseline, summary (incl. tender_class,
      bank_amount, tip, is_proven), and extension candidates already
      dry-run simulated through the real guard.
2. Read the full-res image: assets/<image_id>/<receipt_id>.jpg
   (dev site bucket). Transcribe the item rows (rule 2), then check
   them against the digest.
3. (optional) One targeted re-check, e.g. re-run the helper with
   --max-simulations for a wider candidate sweep.
```

Then write `.dev-harness/dossiers/<image_id>-<receipt_id>.json` in the
schema below. `--emit-dossier-skeleton` writes a pre-filled stub.

## Agent prompt template

```
You are a read-only vision-scout for receipt <image_id>/<receipt_id>
(dev table ReceiptsTable-dc5be22). Produce one dossier v2 JSON file;
write nothing else anywhere.

1. Run: python scripts/agentic_triage_helpers.py \
     --image-id <image_id> --receipt-id <receipt_id> \
     --emit-dossier-skeleton .dev-harness/dossiers
   Study the digest: reconciliation status/delta, section boundaries,
   unclaimed priced lines, and the simulated extension candidates
   (each already carries the real guard's verdict).
2. View the FULL-RES image at assets/<image_id>/<receipt_id>.jpg.
   NEVER a thumbnail. FIRST transcribe every item row you can read
   (name + price) into visual_evidence, BEFORE computing any sum or
   comparing against the printed/stored totals.
3. Only after transcribing: classify the failure mode (A–J taxonomy in
   agentic_taxonomy.md), decide whether each candidate extension's
   added lines are actually products (vision_products_confirmed), and
   fill in the dossier. Policy: printed fees/deposits/CRV ARE items; a
   positive bank gap <= ~25% at a food-category merchant is a tip, not
   a mismatch; PROVEN means exact-to-the-cent on both hops.
4. Fill signals_concurring with only the signals you actually
   verified: "arithmetic" (guard-passed candidate or exact sum),
   "bank" (bank_amount agrees), "vision" (your transcription agrees).
   Recommend "golden" only when all three concur.
5. If the photograph itself is the problem (crop, blur, reverse-video
   total), set image_suspect true and say why in visual_evidence.
   If the fix would require deleting or merging records, set
   destructive true. Never propose applying anything.
```

## Dossier schema v2

Extends the v1 dossiers (`.dev-harness/dossiers/*.json`) additively.
One file per receipt: `<image_id>-<receipt_id>.json`.

```jsonc
{
  "schema": "dossier-v2",
  "image_id": "uuid",
  "receipt_id": 1,
  "merchant": "Smith's",
  "mode": "H-clean-extension",        // A–J taxonomy id; letter prefix is the class
  "recon": {                           // from the digest, verbatim
    "status": "mismatch",              // match | near | mismatch | no-baseline
    "items_sum": 55.70,
    "baseline": 117.55,
    "delta": -61.85
  },
  "bank": {                            // null fields when absent
    "amount": 117.55,
    "match_confidence": 0.9,
    "tip": null,
    "tender_class": "card"
  },
  "image_suspect": false,              // the PHOTO is the problem
  "destructive": false,                // fix implies delete/merge of records
  "duplicate_group": null,             // group id when part of a duplicate-scan group
  "proposal": {                        // omit entirely when abstaining from a fix
    "add_line_ids": [3],
    "contiguous": true,                // adjacent run, no claimed line skipped
    "verified": true,                  // the guard (dry-run) accepted it
    "before": {"status": "mismatch", "delta": -4.0},
    "after": {"status": "match", "delta": 0.0},
    "vision_products_confirmed": true  // EVERY added line read as a product row
  },
  "visual_evidence": [                 // v2: transcription FIRST, then reasoning
    "Transcribed rows: APPLES 3.00, BANANAS 2.00, ORANGES 4.00",
    "Printed TOTAL reads 9.72; tax line 0.72"
  ],
  "verdict_recommendation": "approve-fix",  // confirm | flag | approve-fix | golden
  "confidence": "high",                // high | medium | low
  "confidence_justification": "all rows legible; transcription sums to printed subtotal independently",  // REQUIRED when confidence is high
  "signals_concurring": ["arithmetic", "bank", "vision"],  // subset actually verified
  "verdict_by": "agent:<pass-id>"      // provenance; never a bare model name
}
```

Field notes:

- `visual_evidence` is an ordered list; the first entries must be the
  independent transcription (rule 2), later entries the interpretation.
- `verdict_recommendation`: `confirm` = green row is genuinely right;
  `flag` = something is wrong and no safe fix exists; `approve-fix` =
  the attached proposal should be applied (pending tier routing);
  `golden` = candidate for the golden fixture set (requires all three
  signals; never auto-applied).
- `signals_concurring` values are exactly `arithmetic`, `bank`,
  `vision`.
- `verdict_by` is `agent:<pass-id>` for scout output; the adjudicator
  and writer preserve it end-to-end.

## What happens next

`scripts/agentic_adjudicate.py --pass-id <pass-id>` routes every
dossier to T0/T1/T2/abstain (see OPERATING_MODEL.md) and writes the
verdicts + batch digest. The writer applies T0 and human-approved T1
groups only.
