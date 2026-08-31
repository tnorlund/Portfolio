# Golden similarity fixtures

Reference answers captured from live Chroma Cloud (dev) by
`scripts/similarity_harness/capture_golden.py`, graded by
`scripts/similarity_harness/evaluate.py`. See
`docs/chroma-removal/AGENT_PLAN.md` ("The harness") and `SPEC.md` §8.

## Files

| File | Contents |
|---|---|
| `manifest.json` | capture provenance (timestamp, table, top-k settings), receipt list, counts |
| `merchant.json` | per-receipt Tier-2 query lines, top-20 line neighbors + distances, decision (tier/merchant/place), Dynamo place reference |
| `words.json` | per-receipt sampled words, top-30 word neighbors + distances |
| `sections.json` | per-receipt row queries, neighbor VALID-section labels, verifier votes and per-section AGREED/DISAGREED/ABSTAINED statuses |
| `vectors.json.gz` | every query + neighbor vector (rounded), for offline fake-backend replay |

## Determinism & tolerance

- All JSON is sorted-keys, fixed-indent; gzip is written with `mtime=0`.
- Distances are rounded to **6 decimals**, vector components to **6
  decimals** (`fixtures_io.DISTANCE_DECIMALS` / `VECTOR_DECIMALS`). Anything
  agreeing within 5e-7 is byte-identical after rounding.
- Two capture runs minutes apart are byte-identical in
  `merchant/words/sections/vectors` **provided the corpus did not change
  between runs**; dev ingest between runs legitimately changes neighbor sets
  — that is real data drift, not capture noise. `manifest.json` differs only
  in `captured_at` (provenance is deliberate).
- Neighbor lists preserve backend rank order; equal-confidence merchant
  candidates are tie-broken by key in the pure decision replay.

## Regeneration

Online-only (needs `CHROMA_CLOUD_*` + dev-table AWS creds):

```bash
python scripts/similarity_harness/capture_golden.py \
  --table ReceiptsTable-dc5be22 \
  --extra-receipts <known-merchant batch JSON> \
  --out tests/fixtures/similarity
```

Per BAKEOFF.md Round A, the canonical set is captured ONCE by the winning
harness after selection; interim captures from candidate branches are not
blessed reference data.
