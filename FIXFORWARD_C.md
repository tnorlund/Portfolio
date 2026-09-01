# Round C fix-forward — codex presumptive winner

Applied on `bakeoff/C/codex` on top of the entry commit `8a6ea6f12`, per the
"Round C vacatur + fetch-join ruling" (BAKEOFF.md, 2026-08-31). Five commits,
not pushed, no PR:

1. `08a9d0262` — fetch-join resolver metadata for line embeddings
2. `3128ec605` — OpenAI-free backfill via stored Chroma Cloud vectors
   (cherry-picked from `bakeoff/C/claude`)
3. `5c75c8a84` — fail-closed exits + strong-consistency backfill verification
4. `d0c84b83b` — seam A/B analog, throttle isolation, global-outage tests
5. (follow-up) — fetch-join read units surfaced as the client's
   `request_units` so `evaluate.py` reports `read_request_units_per_query`

## Per-gate status

### Gate: fetch-join design ruling (spec §3.2/§3.3 amendment) — DONE

- `ReceiptLineEmbedding` carries `normalized_phone_10` /
  `normalized_full_address` as ordinary unprojected attributes, **sparse**
  (present only when the anchor exists), mirroring the Chroma metadata
  writer's presence-only anchor keys.
- The backfill computes them with the **same function** the production
  Chroma line-delta writer uses — `enrich_row_metadata_with_anchors` over
  the visual row's words (`receipt_chroma/.../delta/line_delta.py` selects
  `row_words` by `line_id in row_line_ids`; the script mirrors that
  selection exactly) — so stored values are byte-equal to what Chroma
  would have stored.
- `DynamoVectorSearchClient` line-index retrieval is now
  SearchVectors → **strongly consistent BatchGetItem** of the neighbor
  items (bounded UnprocessedKeys retries, vector excluded from the read
  projection) → full metadata dicts to the resolver. Word-index searches
  never join (every word attribute is already projected). A neighbor whose
  item cannot be fetched degrades to its projection metadata rather than
  vanishing. Index projections untouched; **no vector-index
  create/alter/delete anywhere**.
- Metadata-key contract: `RESOLVER_NEIGHBOR_METADATA_KEYS` pins the field
  set the real resolver reads; `receipt_embeddings/tests/test_metadata_contract.py`
  drives the **real receipt_chroma metadata builders** on one side and the
  real entity + stubbed SearchVectors/BatchGetItem join on the other, and
  asserts both backends surface **identical metadata keys** for the same
  neighbor — in the anchored case AND the anchor-less case (sparseness is
  part of the contract), with equal values. Note: full Chroma metadata also
  carries display-only keys (`x`, `y`, `confidence`, …) that were never
  provisioned as Dynamo attributes and that the resolver never reads; the
  ruling names only the two normalized fields, so the pinned contract is
  the resolver-consumed set.

### Gate: real-MerchantResolver A/B through the seam — judge-owned; offline analog DONE

- `receipt_upload/tests/test_merchant_vector_backend.py::test_real_resolver_boosts_on_fetch_joined_phone_metadata`
  drives the **real** `MerchantResolver` similarity path through the
  **real** `DynamoVectorSearchClient` over a botocore-stubbed
  SearchVectors + BatchGetItem join. The stubbed projection carries no
  `normalized_phone_10`; only the join supplies it, and `PHONE_MATCH_BOOST`
  provably fires from the joined metadata (0.75 similarity → 0.95
  confidence). Thresholds, tier logic, and corroboration gates untouched.
- Live read-only evidence (this Mac, populated dev index, canonical
  fixture, 258 queries, two runs): see "Live evaluation" below.
- The full-scale judge-owned A/B on the actual projection+fetch-join
  remains **not verified locally**.

### Gate: wipe → full-scale backfill → idempotent rerun → verified cleanup — judge-run; machinery DONE

- OpenAI-free mode: `--vector-source chroma` reuses stored Chroma Cloud
  dev vectors (read-only; refuses any database but `receipt_dev`);
  `--vector-source fixture` is fully offline; `auto` prefers chroma when
  `CHROMA_CLOUD_*` is set. Codex's strongly consistent exact-key
  idempotency check and bounded retries are unchanged. Under an
  OpenAI-free source, uncovered items are skip-reported
  (`missing_stored_vector` / `not_in_fixture_corpus`), never silently
  re-embedded; realtime mode fails fast when `OPENAI_API_KEY` is absent.
- Skip taxonomy (adopted from claude): categorized receipt skips
  (`receipt_not_found` / `incomplete_receipt_data` /
  `section_metadata_unavailable` / `error:<Type>`) plus
  `receipt_skip_reasons`, `vector_skip_reasons`, and
  `item_failure_reasons` counters in the end-of-run report.
- Live bounded smoke (dev, `--limit 1` and `--limit 2`, fixture source):
  every fixture-covered key already existed in the shared dev table, so
  both applies wrote **zero** items and exited 0 with
  `skipped_existing` = 11 and 23 respectively — the idempotency path
  proven live. **Nothing was written, so there was nothing to clean up**;
  no non-embedding item was touched, nothing touched prod.
- The clean wipe → full-scale backfill → rerun → cleanup sequence by this
  code alone is **not verified locally** (judge-run; no Chroma/OpenAI
  credentials on this machine, and writing synthetic vectors would have
  polluted the shared corpus).

### Gate: strong-consistency item verification — DONE

- `verify_written_items` runs a **strongly consistent BatchGetItem
  existence check over every key the invocation wrote**, keyed by
  (PK, SK), with bounded UnprocessedKeys retries. Anything unaccounted —
  missing item, exhausted retries, read outage — is reported `missing`
  and fails the run: a lookup error can never masquerade as a pass
  (tested: `test_verify_written_items_never_false_passes_a_missing_item`,
  `..._reports_missing_on_read_outage`).
- The bounded SearchVectors probe is kept and reported **separately** as
  `searchability_probe` (indexing is asynchronous; a probe timeout is
  evidence, not an existence failure). Its per-key polling records
  attempts and last errors; a `get_vector` KeyError is a recorded error,
  never a pass.

### Gate: fail-closed exit semantics — DONE

- `determine_exit_code`: zero items written with nonzero per-item
  failures **and nothing skipped-as-existing** (the global
  credential/outage pattern: every attempt failed with no evidence the
  corpus is already there) → **exit 3** (`EXIT_GLOBAL_WRITE_FAILURE`);
  written keys the existence check cannot account for → **exit 4**
  (`EXIT_VERIFICATION_FAILURE`). An idempotent rerun over a completed
  corpus exits 0 even when the same residual unfillable items the first
  run tolerated fail again (skipped-existing items are the evidence that
  distinguishes it from an outage — judge-run-2 regression, fixed
  post-gate), and partial runs where some writes landed still exit 0 —
  per-item failures skip-and-continue. The report also embeds `exit_code`.
- Writer-side proof: a total write outage produces zero writes and one
  attributable `stage=write` failure per item with no exception escaping
  — exactly the shape exit 3 keys on
  (`test_global_write_outage_reports_every_item_and_writes_nothing`).

### Gate: stream-guard evidence — card D's scope, untouched here

### Standing rules — respected

- No vector-index lifecycle calls anywhere (grep-verifiable:
  `create_index|delete_index|update_table.*Vector` has no hits in the
  diff). Dev writes: none actually landed (see smoke above). Round A/B
  fixture and fake semantics unmodified. Dual merchant metrics reporting
  (`merchant_agreement_percent` identity vs
  `merchant_place_agreement_percent` exact place) untouched.

## Offline phase-1 evidence (no regression)

Fake replay of the canonical fixture
(sha256 `199d7f4f…7420144`) is **numerically identical** to the entry's
pre-fix numbers: recall@10 overall `0.86627907`, merchant identity
`100%`, exact place agreement `91.860465%` (= the judge-verified
fixture ceiling), tier decision `100%`, all offline gates true.

## Live evaluation (read-only, populated dev index, this Mac)

`evaluate.py --backend dynamo` over all 258 canonical queries, run twice
(the corpus currently in dev was written by the pre-fix judged run — no
anchor attributes stored, and realtime-OpenAI vectors rather than the
chroma-identical ones, so this is seam/wire/cost evidence, not the graded
phase-2 score):

| metric | run 1 | run 2 |
|---|---|---|
| recall@10 overall | 0.87016 | 0.85543 |
| merchant identity agreement | 97.674% | **98.837%** |
| exact place agreement | 89.535% | 91.860% |
| tier decision agreement | 100% | 100% (all tier deltas 0.0) |
| p50 / p95 wall latency | 471 / 896 ms | 445 / 519 ms |
| SearchVectors request bytes/query | 83,768 | 92,565 |
| fetch-join read units/query | n/a (pre-tweak) | **50.0 RRU** |

- **Fetch-join latency overhead**: p50 445–471 ms with the join from
  this Mac vs the entry's 241 ms search-only baseline — roughly one
  extra sequential round trip (~200–230 ms residential RTT dominated;
  the judge's appendix notes ~630 ms cold calls from the same network).
  Expected to collapse to low tens of ms in-region.
- **Fetch-join cost**: 50 strongly-consistent RRU/query (top-20 line
  neighbors × ~13 KB items ⇒ 4 RRU each, minus word queries which never
  join). At on-demand pricing that is ~\$6.3e-6/query — it dominates the
  SearchVectors byte cost (~\$1.9e-7/query) and is the number the design
  ruling left open; reported separately via
  `read_request_units_per_query`.
- **Run-to-run variance**: live ANN top-k is not deterministic — recall
  0.87016 vs 0.85543 and identity 97.67% vs 98.84% across back-to-back
  runs of identical code. Both runs clear recall ≥ 0.85; the identity
  number straddles 98% on a corpus lacking anchors and with
  non-identical vectors. After the judge's wipe → backfill with THIS
  writer (anchors stored; chroma-identical vectors when
  `--vector-source chroma`), phase-2 conditions are strictly more
  favorable than this evidence run.
- SearchVectors bytes/query on the populated index meters ~2× the
  entry's empty-index baseline (40,458): a service metering observation —
  the join uses BatchGetItem and cannot inflate
  `VectorSearchRequestBytes`; the wire test pins the request as
  byte-identical.

## Verify commands (environment documented)

All local verification used a worktree-local venv exactly like
`.cursor/install.sh` builds (python3.13; editable installs of
receipt_dynamo, receipt_embeddings, and `--no-deps` receipt_chroma /
receipt_upload / receipt_dynamo_stream / receipt_places / receipt_agent;
boto3 1.43.84 ≥ the 1.43.64 floor). From the repo root:

```bash
.cursor/install.sh          # or reuse an existing .venv built by it
source .venv/bin/activate

pytest -q receipt_dynamo/tests/unit                      # 2457 passed
pytest -q receipt_embeddings/tests                       # 92 passed
PYTHONPATH=. pytest -q receipt_chroma/tests/unit         # 442 passed, 5 skipped
PYTHONPATH=. pytest -q receipt_upload/tests -m 'not integration and not end_to_end'
                                                         # 939 passed, 11 skipped

aws s3 cp s3://raw-image-bucket-c779c32/similarity-fixtures/canonical-2026-08-31/golden.json.gz /tmp/canonical-golden.json.gz --no-progress
gunzip -kf /tmp/canonical-golden.json.gz
shasum -a 256 /tmp/canonical-golden.json   # 199d7f4fc16858e1bf6aaea0a748edb6822145a4b2af6fa9078c6f5fd7420144
python scripts/similarity_harness/evaluate.py --backend fake --fixture /tmp/canonical-golden.json --out /tmp/fake-scorecard.json

# live, read-only:
DYNAMODB_TABLE_NAME=ReceiptsTable-dc5be22 AWS_REGION=us-east-1 \
  python scripts/similarity_harness/evaluate.py --backend dynamo --fixture /tmp/canonical-golden.json --out /tmp/dynamo-scorecard.json

# bounded smoke (dry-run is read-only; --apply requires --limit and refuses non-dev tables):
DYNAMODB_TABLE_NAME=ReceiptsTable-dc5be22 AWS_REGION=us-east-1 \
  python scripts/backfill_receipt_embeddings.py --fixture /tmp/canonical-golden.json --limit 1 --vector-source fixture
DYNAMODB_TABLE_NAME=ReceiptsTable-dc5be22 AWS_REGION=us-east-1 \
  python scripts/backfill_receipt_embeddings.py --fixture /tmp/canonical-golden.json --limit 1 --vector-source fixture --apply
```

Formatting: `black --check` clean at each package's configured
line-length (79) for every touched file; the repo has no root isort
config, so import blocks in `scripts/` follow the entry's existing
two-block style rather than an unconfigured root isort pass.

## Not verified locally (judge-owned)

1. The real-resolver A/B through the seam on the actual Dynamo
   projection + fetch-join (judge-owned evaluator).
2. One clean wipe → **full-scale** backfill → idempotent rerun →
   verified cleanup by this code alone (this machine has no
   `CHROMA_CLOUD_*` or `OPENAI_API_KEY`; every fixture-covered key
   already existed in dev, so local applies exercised only the
   idempotency path).
3. `--vector-source chroma` against the real Chroma Cloud dev database
   (unit-tested against a fake collection client only).
4. Phase-2 graded numbers on a corpus written by THIS writer (anchors
   stored, chroma-identical vectors); the live evaluation above ran
   against the pre-fix corpus.
5. In-region (Lambda) fetch-join latency.
