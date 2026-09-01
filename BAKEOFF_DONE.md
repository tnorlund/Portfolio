# Card D — stream freshening (claude)

Branch `cards/D-claude`, three logical commits + this report. Scope:
`receipt_dynamo_stream/` only — no infra/ changes, no index operations,
no dev/prod table writes (moto/fakes only in tests).

## Self-report per item

### 1. Embedding skip guard (audit-evidenced defect) — DONE

Reproduced first on pre-change code (venv with `receipt_dynamo` +
`receipt_dynamo_stream` installed):

```
detect_entity_type("RECEIPT#00001#LINE#00002#EMBEDDING")              -> "RECEIPT_LINE"
detect_entity_type("RECEIPT#00001#LINE#00002#WORD#00003#EMBEDDING")   -> "RECEIPT_WORD"
parse_stream_record(<line-embedding INSERT record>)                    -> ParsedStreamRecord(entity_type="RECEIPT_LINE")
  + logged "Failed to parse entity" stack trace
    (ValueError: Item is missing required keys: {'confidence', 'top_right', ...})
  + metric ("EntityParsingError", 1, {entity_type: RECEIPT_LINE, image_type: new})
```

Fix (`receipt_dynamo_stream/receipt_dynamo_stream/parsing/parsers.py`):
`is_embedding_sk()` (SK ends with `#EMBEDDING`) checked FIRST —
`detect_entity_type` returns `None` for embedding SKs, and
`parse_stream_record` skips them with a debug log and an
`EmbeddingStreamRecordSkipped` counter before any parsing. Regression
tests (`tests/unit/test_embedding_skip_guard.py`) build stream records
from real `ReceiptLineEmbedding`/`ReceiptWordEmbedding.to_item()`
images (1536-float vectors) and assert: skip + counter, zero Error
metrics, zero SQS messages through `build_messages_from_records`, and
no overreach onto ordinary LINE/WORD/LABEL SKs.

### 2. Vector-attr freshening leg (inline, no new queues) — DONE

`receipt_dynamo_stream/receipt_dynamo_stream/vector_freshening.py`,
exported as `apply_vector_freshening(records, metrics, *,
dynamo_client=None, table_name=None) -> FresheningStats`.

- **RECEIPT_PLACE** (INSERT/MODIFY with merchant_name or place_id
  changed): enumerates the receipt's line-embedding items (base-table
  Query, `TYPE = RECEIPT_LINE_EMBEDDING` filter, SK-only projection,
  paginated, fan-out capped) and UpdateItems
  `merchant_name`/`place_id` on each.
  **Anchors finding (card asked this be studied):** the backfill
  (`scripts/backfill_receipt_embeddings.py:328-357`) computes
  `normalized_phone_10`/`normalized_full_address` via
  `enrich_row_metadata_with_anchors` from the **row words'
  `extracted_data`** — not from any RECEIPT_PLACE field. A place change
  therefore never invalidates the anchors, and the leg deliberately
  leaves them untouched (documented in the module docstring and pinned
  by a test asserting anchors survive a place freshen).
- **RECEIPT_WORD_LABEL** (INSERT/REMOVE, or MODIFY with
  validation_status changed): recomputes `label_status` from the
  word's *current* label set (live Query of `…WORD#nnnnn#LABEL#`
  items) using the backfill's exact rule — any VALID → `validated`,
  else any PENDING → `pending`, else `none` — then one UpdateItem on
  the word's embedding item.
- **RECEIPT_SECTION** (INSERT/MODIFY/REMOVE, no-op when section_type
  and line_ids are unchanged): stamps `section_type` on the section
  lines' embedding items; lines dropped from the section, or all lines
  on REMOVE, clear to `""`. Embeddings exist only at each visual row's
  primary line, so conditional misses on non-primary lines are counted
  as expected skips.

Properties: every write is an absolute-value UpdateItem with
`ConditionExpression attribute_exists(PK)` → idempotent, and a missing
embedding is **skipped, never created**. Per-record work is bounded
(`MAX_UPDATES_PER_RECORD = 500` cap on query fan-out and section
line_ids, truncation counted). Throttle codes → warn + counter +
continue; other client errors → counter + log; a per-record broad
catch guarantees the stream handler can never crash. All existing
routing (summary, line-item, chroma lines/words legs) is byte-for-byte
untouched — `message_builder.py`, `sqs_publisher.py`,
`change_detection/`, `backfill.py` have zero diffs; the chroma-leg
prune is teardown-phase, not this card.

**Wiring note:** the live handler
(`infra/chromadb_compaction/lambdas/stream_processor.py`) and its
Pulumi-managed env live under `infra/`, which this card fences ("no
infra/ Pulumi changes"). The leg is one call —
`apply_vector_freshening(event["Records"], metrics)` — plus a
`DYNAMO_TABLE_NAME` env var on the stream-processor Lambda. Until
wired it is inert by design (warn + `VectorFresheningNotConfigured`
counter, zeroed stats — covered by a test), so the wiring commit is a
safe two-liner for the card that owns infra/.

### 3. Tests — DONE

24 new tests; full package suite 174 passed (150 pre-existing stayed
green, zero modified).

- Parser-guard regression: 6 tests (real embedding-item records,
  INSERT/MODIFY/REMOVE, counter, no Error metrics, no overreach).
- Freshening unit (`tests/unit/test_vector_freshening.py`, 5):
  unconfigured env is inert; irrelevant place MODIFY and
  non-freshening entities make zero DynamoDB calls (exploding-client
  proof); throttle → skip-and-report; unexpected RuntimeError
  contained per record.
- Freshening moto (`tests/integration/test_vector_freshening_with_moto.py`,
  13): place MODIFY updates all line embeddings while word embeddings
  and anchors stay untouched; idempotent rerun (byte-identical items);
  INSERT with no embeddings clean; word/label items under the LINE#
  prefix never touched by the place query; label-status aggregation
  (a PENDING label can't demote a VALID word), REMOVE recompute from
  remaining labels, missing-embedding skip asserts the item is NOT
  created; section INSERT/MODIFY/REMOVE incl. non-primary-line misses
  and dropped-line clears; unchanged-section no-op.

## Verify commands (fresh checkout)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install --upgrade pip wheel
pip install -e receipt_dynamo
pip install --no-deps -e receipt_dynamo_stream
pip install boto3 pytest pytest-mock pytest-cov pytest-xdist pytest-timeout pytest-rerunfailures moto
cd receipt_dynamo_stream && python -m pytest tests -q          # expect: 174 passed
```

(Identical to the CI `receipt_dynamo_stream` matrix job's install.
Also verified with the equivalent
`pip install -e receipt_dynamo -e "receipt_dynamo_stream[test]"`.)

Defect reproduction (pre-fix only — run on `main` or with commit
`fcad810a9` reverted):

```bash
python -c 'from receipt_dynamo_stream import detect_entity_type as d; print(d("RECEIPT#00001#LINE#00002#EMBEDDING"))'
# main: RECEIPT_LINE   |   this branch: None
```

Formatting: `black --check` and `isort --check-only` (pyproject
settings) clean on all touched files.

## Not verified locally

- Live dev/prod stream behavior (no table writes allowed this card;
  all DynamoDB interaction tested against moto/stub clients).
- The handler wiring + `DYNAMO_TABLE_NAME` env (fenced under infra/).
- Local venv ran Python 3.14 (CI pins the Lambda's 3.13 minor); no
  3.13-specific syntax was used, but the suite was not re-run on 3.13.
- pylint/mypy were not run locally (not part of this package's CI test
  job); black/isort were.
