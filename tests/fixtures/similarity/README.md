# Similarity evaluation fixtures

Captured query families used by `scripts/similarity_harness/evaluate.py`
to grade later DynamoDB SearchVectors rounds. The committed files in
this directory are the **offline exact-NN** set produced by:

```
python scripts/similarity_harness/capture_golden.py --synthetic
```

After Round A selection, the winner recaptures once against live Chroma
Cloud; only that blessed set is canonical. Four live capture runs at
different times differ slightly because both Chroma and SearchVectors
are approximate.

## Query families

| File | Contents |
|---|---|
| `golden_set.json` | ≥40 receipts: line-item goldens + 43-image May-26 catalog |
| `merchant_resolution.json` | per receipt: top-20 line neighbors, retrieval tier, merchant decision |
| `word_neighbors.json` | sampled words: top-30 neighbor keys + cosine distances |
| `section_verifier.json` | per receipt rows: AGREED / DISAGREED / ABSTAINED votes |
| `vectors.json` | fixture vectors (16-d synthetic; live capture stores model dim) |

## Determinism

- **Synthetic / FakeVectorIndex:** two capture runs minutes apart are
  bitwise identical (`sort_keys` JSON, distances rounded to 1e-8).
- **Live Chroma ANN:** neighbor **id sets** at k=10 must have Jaccard
  ≥ 0.95; cosine distances must match within absolute tolerance **1e-5**.
  Rank-boundary swaps inside that band are expected ANN noise, not a
  fixture bug.
- `evaluate.py` is a pure function of the fixture JSON plus the backend
  answers. It does not write AWS tables or mutate fixtures.

## Distance

Cosine distance = 1 − cosine similarity, range [0, 2], lower is closer
(SPEC §3.5a). Same quantity Chroma and DynamoDB COSINE return.
