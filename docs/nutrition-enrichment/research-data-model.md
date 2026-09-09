# Research: existing line-item data model and product extraction

> Revision note (2026-09-08): this is historical research at `64058f299`.
> [SPEC.md](SPEC.md) is the current implementation contract. Counts and live
> probes below have not been independently rerun. Main `dd737e154` adds the
> line-item decoder visualization (#1391), so the former frontend inventory
> is now incomplete. Reconciliation is not identity/quantity ground truth.
> Quantity units are discarded by the current row shape; the enrichment
> adapter must retain explicit evidence or abstain. A queue delay alone
> cannot ensure consistency; the current SPEC uses one document per receipt
> with compare-and-swap, a parent-timestamp condition, and read-time
> fingerprint validation (the earlier generation/fencing draft is superseded).

Companion research note for the nutrition-enrichment plan. Every path and line
number below was verified against a clean `origin/main` worktree
at commit `64058f299` (branch `claude/nutrition-enrichment-plan`, tracking
`origin/main`). Paths are relative to that root.

**Do not trust findings taken from a stale feature-branch checkout.** The one used earlier
sits on `claude/rotoscope-pixel-story`, 141 commits behind main with a dirty
worktree. It still contains `receipt_chroma/`, `infra/chromadb_compaction/`,
and a `TargetQueue` with `LINES`/`WORDS` members. All three are deleted on main.

## 0. Corrections to prior assumptions

| Assumption | Reality on main |
|---|---|
| Prod lacks line-item and section rows | Prod has 2,539 RECEIPT_LINE_ITEM and 6,625 RECEIPT_SECTION rows |
| Chroma still backs line search | `receipt_chroma/` deleted; `receipt_embeddings/` + DynamoDB SearchVectors replaced it |
| `search_product_lines` is broken | It works, but semantic-only; the substring/text mode was retired, not ported |
| `infra/chromadb_compaction/` wires the stream | Replaced by `infra/receipt_update_queues/` (722 lines) |
| MerchantCatalogItem is a usable product catalog | 321 rows in dev, **0 in prod**, one owner-gated writer |

Live row counts (queried via GSITYPE, 2026-09-08):

| TYPE | dev `ReceiptsTable-dc5be22` | prod `ReceiptsTable-d7ff76a` |
|---|---|---|
| RECEIPT_LINE_ITEM | 2,469 | 2,539 |
| RECEIPT_SECTION | 6,690 | 6,625 |
| RECEIPT_SUMMARY | 921 | 838 |
| RECEIPT_PLACE | 916 | 838 |
| MERCHANT_CATALOG_ITEM | 321 | 0 |
| RECEIPT_BARCODE | 43 | 19 |
| RECEIPT_LINE_EMBEDDING | 30,943 | 29,646 |
| RECEIPT_WORD_EMBEDDING | 111,594 | 105,821 |

## 1. The entities that represent product lines

### 1.1 ReceiptLineItem — the product line

`receipt_dynamo/receipt_dynamo/entities/receipt_line_item.py:32`

Keys (`:218` for `key`, `:231` for `gsi1_key`):

```
PK     = IMAGE#{image_id}
SK     = RECEIPT#{receipt_id:05d}#LINE_ITEM#{item_index:05d}
TYPE   = RECEIPT_LINE_ITEM
GSI1PK = MERCHANT#{slugify_merchant(merchant_name)}          (sparse)
GSI1SK = LINE_ITEM#{normalize_product_text(name)}#{image_id}#{receipt_id:05d}#{item_index:05d}
```

Fields (dataclass at `:95`-`:113`):

| Field | Type | Notes |
|---|---|---|
| `name` | str | `""` when `name_quality == "low"` |
| `price` | str | decimal string, negative for discounts |
| `quantity` | float or None | parsed, see 2.3 |
| `unit_price` | float or None | parsed or implied |
| `is_discount` | bool | negative price or discount keyword |
| `raw_text` | str | the band text the item parsed from |
| `line_ids` | list[int] | OCR line ids the item's bands span |
| `name_quality` | str | `"ok"` or `"low"` |
| `merchant_name` | str or None | for the GSI1 rollup |
| `source_section_status` | str or None | VALID / PENDING / INVALID |
| `source_model_source` | str or None | the ITEMS section's producer |
| `reconciliation_status` | str or None | match / near / mismatch / no-baseline |
| `collapsed_banding` | bool | degenerate banding flag |
| `baseline_figures_agreeing` | int or None | 1..3 graded confidence |
| `extractor_version` | str | e.g. `line-items-blocks-v2` |
| `extracted_at` | datetime | |

**There is no category, UPC, unit of measure, serving size, or normalized
product name on this row.** `name` is raw OCR text.

GSI1 is deliberately sparse (`:231`-`:247`): rows with `name_quality == "low"`
or no merchant omit the GSI keys, so junk names never pollute the product
index. That guard is also a free rate-limiter for any per-product API call.

Two normalizers live at module scope and are the closest thing this repo has to
product canonicalization:

- `slugify_merchant` at `:18` — lowercase, `[^a-z0-9]+` to `-`
- `normalize_product_text` at `:24` — strip `[^A-Za-z0-9 ]`, collapse spaces, uppercase

Note the collision: `receipt_dynamo/receipt_dynamo/entities/merchant_catalog_item.py:39`
defines a *different* `normalize_product_text` (uppercase + collapse only, no
punctuation stripping) and a *different* `slugify_merchant` at `:27` (`_`
separator, not `-`). Two names, three behaviors. Do not add a fourth.

### 1.2 Data access

`receipt_dynamo/receipt_dynamo/data/_receipt_line_item.py` (class `_ReceiptLineItem` at `:24`):

| Method | Line | Notes |
|---|---|---|
| `add_receipt_line_item` | 45 | `attribute_not_exists(PK)` |
| `add_receipt_line_items` | 53 | batch |
| `get_receipt_line_item` | 68 | |
| `get_receipt_line_items_from_receipt` | 86 | `PK = :pk and begins_with(SK, :sk)` |
| `delete_receipt_line_items_for_receipt` | 127 | returns count deleted |
| `list_receipt_line_items_by_merchant` | 164 | GSI1 product rollup, `begins_with(GSI1SK, "LINE_ITEM#")` |
| `list_receipt_line_items` | 214 | GSITYPE scan |

`list_receipt_line_items_by_merchant` is the existing "every observation of
every product at a merchant" query. A per-product nutrition join would reuse it.

### 1.3 Sibling entities

| Entity | File | SK | Fields that matter here |
|---|---|---|---|
| ReceiptSection | `entities/receipt_section.py:63` | `RECEIPT#{id:05d}#SECTION#{type}` | `section_type`, `line_ids`, `confidence`, `model_source`, `validation_status` |
| ReceiptSummaryRecord | `entities/receipt_summary_record.py:167` | `RECEIPT#{id:05d}#SUMMARY` | `merchant_name`, `date`, `item_count`, totals, tender/bank |
| ReceiptPlace | `entities/receipt_place.py` | `RECEIPT#{id:05d}#PLACE` | `merchant_category:189`, `merchant_types:190` |
| ReceiptWordLabel | `entities/receipt_word_label.py:114` | `RECEIPT#...#LINE#...#WORD#...#LABEL#{label}` | `PRODUCT_NAME`, `QUANTITY`, `UNIT_PRICE`, `LINE_TOTAL` |
| ReceiptBarcode | `entities/receipt_barcode.py` | `RECEIPT#{id:05d}#BARCODE#{bid:05d}` | `symbology:41`, inherited `text` payload |

Section vocabulary is `SectionType` at `receipt_dynamo/receipt_dynamo/constants.py:76`:
`STOREFRONT`, `ADDRESS`, `ITEMS`, `SECTION_HEADER`, `SUMMARY`, `TOTAL_LINE`,
`PAYMENT`, `SURVEY`, `FOOTER`, `BARCODE`, `TRANSACTION_INFO`, plus three
deprecated legacy values.

Line-item word labels are defined in `constants.py:184`-`:187` inside
`CORE_LABELS`.

### 1.4 Structural gap: ReceiptLineItem is not on GSI4

`to_item` (`receipt_line_item.py:249`) writes only GSI1. GSI4 is the
single-query receipt-details fan-in used by `data/_receipt.py:599`
(`get_receipt_details`), whose sort keys run `0_`..`6_BARCODE`
(`receipt_place.py:404` is `1_PLACE`, `receipt_barcode.py` is `6_BARCODE#...`).
Line items are absent from that read, and `RECEIPT_LINE_ITEM` never appears in
`data/_receipt_details_processor.py`. A new per-receipt nutrition entity would
take the next free GSI4 prefix; adding line items to GSI4 is a separate call.

## 2. Where line items are produced

The pipeline is deterministic geometry with no LLM in the loop. Orientation
docs: `docs/line-items/STATE_OF_THE_SYSTEM.md` and `docs/line-items/PLAN.md`.

### 2.1 Flow, OCR words to stored rows

```
OCR words (Vision, Swift worker or container)
  -> ReceiptSection rows (ITEMS zone)          section_assignment.py
  -> PENDING word labels                       line_items/reconstructor.py, semantic_proposer.py
  -> ReceiptSummary written / updated
  -> DynamoDB stream -> SQS line_item_queue    receipt_dynamo_stream/*
  -> line-item updater Lambda                  infra/receipt_line_item_updater/
       band-block decode -> reconcile -> delete-all + rewrite RECEIPT_LINE_ITEM
```

The trigger is a RECEIPT_SUMMARY insert or modify, documented at
`infra/receipt_line_item_updater/handler.py:1`-`:8` as "the point at which
words, sections, summary and merchant all exist for a receipt". That is the
natural hook for any per-receipt enrichment.

### 2.2 Word-label proposers (the "PRODUCT_NAME at ingest" path)

- `receipt_upload/receipt_upload/line_items/reconstructor.py:460`
  `propose_line_item_labels`. Bounds the item region using the receipt's own
  anchor labels (`_HEADER` at `:30`, `_TOTALS` at `:31`), then labels prices
  (`LINE_TOTAL`, `UNIT_PRICE`) and product descriptions (`PRODUCT_NAME`) by
  geometry. Recovers split-OCR prices (`$3.` + `99`) and tax-flag suffixes
  (`15.59T`). Emits PENDING labels, stamped `geometry_line_items` (`:56`).
- `receipt_upload/receipt_upload/line_items/semantic_proposer.py:204`
  `propose_product_names`. k-NN over already-validated PRODUCT_NAME words.
  Module docstring records geometry recall at about 0.42 and the k-NN at
  F1 0.84. It now imports `receipt_embeddings.VectorSearchClient` and
  `WORD_INDEX` (`:30`-`:33`), so it runs on DynamoDB vector search, not Chroma.
  Searches **unscoped by merchant** on purpose; merchant scoping hurt recall.
  `_FIELD_KEYWORDS` at `:46` blocks structural tokens from being proposed.
- Both are called from
  `receipt_upload/receipt_upload/merchant_resolution/embedding_processor.py:781`
  and `:794`.
- `receipt_upload/receipt_upload/line_items/labels.py:869` `derive_labels`
  is the reverse direction: it derives word labels from decoded items, using
  `name_word_ids` / `price_word_id` / `qty_word_ids`.

### 2.3 The decoder and what a line item ends up with

`receipt_upload/receipt_upload/line_items/geometry.py` (2,233 lines) is the
canonical decoder, with `blocks.py` (976 lines) holding the band-block model.

- `extract_items` at `geometry.py:1161` delegates to
  `blocks.decode_band_blocks` with golden-trained priors, then
  `constrain_items_to_baseline` (`:1787`) and a shattered-price retry that
  only ships on a cent-exact hit against the printed total.
- `parse_band` at `geometry.py:787` returns the item dict:
  `name`, `quantity`, `unit_price`, `price`, `is_discount`, `raw_text`,
  `band`, `name_word_ids`, `price_word_id`, `qty_word_ids`, `n_amounts`
  (return literal at `:956`-`:975`).
- Name construction is at `:892`-`:955`: drop words consumed by price and
  quantity, drop taxability flags (`[TFNOAB]X?`), drop one trailing single
  letter when at least four name words precede it and it is not `S`. The
  comments record the empirical bounds and the counter-examples.

Quantity parsing is real and thorough. Regexes at `geometry.py:32`-`:57`:

| Pattern | Handles |
|---|---|
| `QTY_AT_RE:34` | `2 @ 3.99`, `1.23 lb @ 4.99/lb`, `18.871 @ $5.299/Gal` |
| `:41` | `4 FOR 1.00`, `2 @ 2 FOR 3.00` (unit = X/M) |
| `:48` | `$2.00 FOR 3` amount-first deals |
| `QTY_AT_OCR_RE:51` | OCR reading `@` as `g` |
| `QTY_MULT_RE:506` | leading standalone integer quantity |
| `:534` | OCR-tolerant `@` glyph set for split words |

`quantity_candidates` (`:972`) enumerates every pair the glyphs could support
and `accept_quantity_pair` (`:1030`) accepts one only when quantity times unit
price equals the line price to the cent (`:537` documents the tolerance).
`implied_unit_price` is at `:1048`.

Non-product guards: `is_settlement_row:183`, `is_tender_row:226`,
`is_column_header_row:337`, `is_unit_rate_row:427`, `_is_non_product_row:1917`,
`_is_skippable_annotation_row:1933`, `is_for_deal_annotation:1110`.

### 2.4 The writer

`infra/receipt_line_item_updater/line_item_processor.py:224`
`_recompute_receipt_line_items`:

1. Read words and sections; pick the non-INVALID canonical ITEMS section,
   preferring VALID over PENDING (`:246`-`:259`).
2. Read the summary **before** extraction, for the non-product band filter and
   the reconciliation baseline (`:283`-`:303`). `EntityNotFoundError` means
   no-baseline; other errors propagate so SQS retries.
3. Optionally extend the ITEMS section (`_maybe_extend_items_section:586`).
4. `extract_items` then `reconcile_extracted_items`.
5. Build `ReceiptLineItem` entities at `:336`, stamping `merchant_name` from
   the summary and `reconciliation_status` from the receipt-level reconcile.
6. `_reconcile_with_worker_rows` at `:443` merges the Mac worker's own decode,
   emitting `LINE_ITEM_DIVERGENCE` (`:406`) when they disagree.
7. Delete every existing row for the receipt, then write (`:372`-`:376`).
   **The rewrite is destructive and unconditional.** Any enrichment written
   onto a ReceiptLineItem row would be erased on the next recompute. Nutrition
   data must live on a separate item, not as extra attributes here.
8. Trigger a capped regional re-OCR (`_maybe_trigger_items_reocr:649`) or a
   Swift refine job (`_maybe_trigger_line_item_refine:794`), never both.

`EXTRACTOR_VERSION` is the stamp on every row.

### 2.5 Swift worker

- `receipt_ocr_swift/Sources/ReceiptOCRCore/LineItems/LineItemDecoder.swift`
  is the port; `DecodedLineItem` at `:52`, regex table at `:76`.
- `receipt_ocr_swift/Sources/ReceiptOCRCore/AWS/ReceiptStructureItems.swift:55`
  `lineItemItem` writes the identical DynamoDB shape, SK format at `:67`,
  `TYPE` at `:71`, `name_quality` at `:79`, GSI1SK at `:112`.
  Rows are stamped `swift-worker-v1+line-items-blocks-v2`.
- Parity fixtures regenerate from live Python via
  `receipt_ocr_swift/Scripts/generate_line_items_parity.py`; CI runs it in
  check mode. Never hand-edit the expectation JSON. A decoder change must port
  the Swift side and regenerate fixtures in the same PR.

### 2.6 Actual prod data quality

Sampled across all 2,539 prod rows:

| Signal | Count |
|---|---|
| name_quality ok | 2,425 |
| name_quality low | 114 |
| reconciliation match | 1,589 |
| reconciliation near | 240 |
| reconciliation mismatch | 426 |
| reconciliation no-baseline | 284 |
| has quantity | 446 |
| has unit_price | 177 |
| is_discount | 145 |
| extractor `line-items-blocks-v2` | 2,517 |
| extractor `swift-worker-v1+line-items-blocks-v2` | 22 |

The figures below are aggregate counts and name shapes observed on the dev
table; they carry no prices, timestamps, or receipt identifiers.

Top merchants by row count: Sprouts Farmers Market 611, The Home Depot 238,
Costco Wholesale 168, Vons 134, Target 101, Wild Fork 100. Note `TRADER JOE'S`
(77) and `Trader Joe's` (77) are distinct raw merchant strings. The existing
lowercase line-item slug already maps these case-only variants together.

Representative name shapes, from real rows:

```
BUR FLOUR TORTILLAS
HOTHOUSE TOMATOES              (weighed: qty 0.9 lb with a unit rate)
STO BABY ROMAINE
GROUND BEEF KEBABS MIDDL
271600043 Poppi TFP            (leading retailer item number)
678885210687 SPRAY PAINT «A,U* (leading UPC, OCR noise)
Pro Xtra Preferred Pricing     (discount line, negative price)
SC YOU SAVED                   (not a product)
Animal Fry                     (qty 1)
```

Prices are omitted here; the shapes are what matter.

So: abbreviated, sometimes SKU-prefixed, occasionally not a product at all
(`SC YOU SAVED`, `NLP Savings`, `BAKERY`), and inconsistently cased.

## 3. Enrichment patterns a nutrition step can copy

### 3.1 Google Places: the canonical external-API pattern

| Piece | File |
|---|---|
| v1 client | `receipt_places/receipt_places/client_v1.py`, `BASE_URL:72`, header auth `X-Goog-Api-Key:172`, `DEFAULT_FIELD_MASK:74` |
| legacy client | `receipt_places/receipt_places/client.py`, `_make_request:113`, tenacity retry on timeouts only `:108` |
| config | `receipt_places/receipt_places/config.py:16`, `env_prefix="RECEIPT_PLACES_"`, `api_key: SecretStr:26`, `cache_ttl_days:48`, `AliasChoices` at `:34` and `:41` |
| cache | `receipt_places/receipt_places/cache.py:26` |

The field mask at `client_v1.py:74` is explicit cost control, with a comment
recording that omitting `primaryType` once left every resolved place with an
empty `merchant_category`. Copy that discipline for any metered nutrition API.

Secret plumbing: Pulumi config key `portfolio:GOOGLE_PLACES_API_KEY` in
`infra/Pulumi.dev.yaml` and `infra/Pulumi.prod.yaml`, read via
`config.require_secret(...)` and injected as a Lambda env var
(`infra/mcp_server_lambda/infrastructure.py`, `infra/fix_place_lambda/infrastructure.py`,
`infra/upload_images/infra.py`).

Cache behavior worth copying verbatim:

- `cache.py:89` `get` -> `_is_cache_valid:166`. TTL is checked in application
  code, not only by DynamoDB.
- `cache.py:183` treats a cached `status` of `NO_RESULTS` or `INVALID` as a
  miss. Negative results are stored but do not poison reads.
- `cache.py:194` `put`, TTL computed at `:227`-`:228`.
- `cache.py:259` `_should_cache` is a poisoning guard that refuses to cache
  area-only searches and address-shaped junk.
- `get_by_place_id:348` is the reverse lookup through GSI1.

### 3.2 PlacesCache entity and DAL

`receipt_dynamo/receipt_dynamo/entities/places_cache.py`

```
PK     = PLACES#{search_type}          search_type in {ADDRESS, PHONE, URL}
SK     = VALUE#{padded_search_value}
TYPE   = PLACES_CACHE
GSI1PK = PLACE_ID
GSI1SK = PLACE_ID#{place_id}
```

Fields: `search_type`, `search_value`, `place_id`, `places_response` (raw dict),
`last_updated`, `query_count`, `normalized_value`, `value_hash`,
`time_to_live` (`:60`, validated at `:121`).

**`time_to_live` is already the table-wide TTL attribute** at
`infra/dynamo_db.py:59`, so a nutrition cache reusing that attribute name gets
automatic expiry with no infra change.

SK padding is at `:147`-`:162`; `_MAX_ADDRESS_LENGTH = 400` at `:48`. Address
keys are `{md5[:8]}_{value padded}`.

DAL: `receipt_dynamo/receipt_dynamo/data/_places_cache.py` — `add:45`,
`put:67` (unconditional, for refresh), `update:87`, `get:197`,
`get_by_place_id:227`, `list:257`, `increment_query_count:123` (a raw
`update_item` with `if_not_exists`), `invalidate_old_cache_items:292`.

Known defect: `invalidate_old_cache_items` queries attribute names `GSI2_PK`
and `GSI2_SK`, but the table defines `GSI2PK` and `GSI2SK`
(`infra/dynamo_db.py:28`, `:32`), and `PlacesCache.to_item` never writes GSI2
at all. Do not copy that method.

### 3.3 The per-receipt enrichment write

`receipt_upload/receipt_upload/merchant_resolution/embedding_processor.py:2049`
`_enrich_receipt_place` is the shape to imitate: fetch or accept a prefetched
entity, build a sparse `updates` dict that fills only empty fields, update when
non-empty, otherwise construct fresh, and wrap the whole thing in a
`try/except Exception` that logs and does not raise. The comment at `:2148`
explains why: it is a dual-write and must never fail the caller.

### 3.4 The async stream-to-queue-to-updater pattern (rewired on main)

`infra/chromadb_compaction/` no longer exists. The component is now
`infra/receipt_update_queues/__init__.py` (722 lines), class
`ReceiptUpdateQueues` at `:57`.

| Resource | Line |
|---|---|
| `summary_dlq` | 89 |
| `summary_queue` | 103 |
| `line_item_dlq` | 123 |
| `line_item_queue` | 137 |
| `summary_queue_policy` | 160 |
| `stream_processor_function` | 401 |
| `summary_updater_function` | 503 |
| `line_item_updater_function` | 616 |
| stream event source mapping (batch 10) | 669 |
| summary mapping (batch 100) | 682 |
| line-item mapping (batch 50) | 691 |

Stream side, `receipt_dynamo_stream/`:

- `change_detection/detector.py:13` — the allowlist is now named
  **`UPDATE_RELEVANT_FIELDS`**, not `CHROMADB_RELEVANT_FIELDS`. Entries:
  `RECEIPT_PLACE` (`merchant_name`, `merchant_category`, `formatted_address`,
  `phone_number`, `place_id`), `RECEIPT_WORD_LABEL` (5 fields),
  `RECEIPT_SUMMARY` (`timestamp_computed` only, with a comment explaining the
  nested summary dataclass is not JSON serializable), `RECEIPT_SECTION`
  (`section_type`, `line_ids`, `confidence`, `validation_status`).
  Accessor `get_update_relevant_changes` at `:41`.
- `models.py:30` — `TargetQueue` now has exactly two members:
  `RECEIPT_SUMMARY = "receipt_summary"` and `LINE_ITEMS = "line_items"`.
  The `LINES` and `WORDS` members are gone.
- `message_builder.py` — extractors `_extract_receipt_place:146`,
  `_extract_receipt_word_label:160`, `_extract_receipt_summary:178`
  (routes to `LINE_ITEMS` at `:193`), `_extract_receipt_section:196`
  (appends `LINE_ITEMS` at `:212` only for canonical ITEMS sections);
  routing table `_ENTITY_EXTRACTORS` at `:231`.
- `sqs_publisher.py` — env vars `RECEIPT_SUMMARY_QUEUE_URL:61` and
  `LINE_ITEM_QUEUE_URL:70`; `publish_messages:38`, `_message_to_dict:78`,
  `_build_sqs_entry:101`, `send_batch_to_queue:133`.
- Lambda entry point: `infra/receipt_update_queues/lambdas/stream_processor.py`.

Both updaters return `{"batchItemFailures": [...]}` and seed that list with
malformed-message ids, so bad messages fail the batch rather than vanish
(`receipt_line_item_processor.deduplicate_messages:1021`).

Adding a nutrition queue means touching: `models.py:30`, `message_builder.py:231`,
`sqs_publisher.py:61`, `detector.py:13` if a new trigger field is needed, and
`infra/receipt_update_queues/__init__.py` for the queue, DLQ, function, and
mapping.

### 3.5 Product catalog, aliases, canonicalization

`MerchantCatalogItem` — `receipt_dynamo/receipt_dynamo/entities/merchant_catalog_item.py`

```
PK   = MERCHANT_CATALOG#{slug}
SK   = ITEM#{category}#{normalized_product_text}
TYPE = MERCHANT_CATALOG_ITEM
```

Fields: `merchant_name`, `product_text`, `price` (modal), `category`
(default `UNCATEGORIZED`), `taxable` (tri-state, `None` means no signal),
`source` (validated to be exactly `"observed"` at `:107`), `observed_count`,
**`upc` at `:87`**, `source_receipt_keys`, `last_updated`.

The module docstring is emphatic that there is no curated or online catalog
source, by owner decision. The surviving writer is
`scripts/ingest_merchant_catalog.py`, gated behind `--apply` (`:27` documents
that the default run performs zero writes), and it never sets `upc`.

**Correction worth acting on: 9 of the 321 dev rows DO carry a real 14-digit
GTIN**, written by the removed online-catalog experiment (commit `f44c735cb`,
"Mac Mini launcher for parallel online-catalog research", which is NOT an
ancestor of main). Those rows also have clean, sized product names, unlike
anything the miner produces:

```
00646670529627  SPROUTS ORG WHOLE MILK 1GAL
00646670513046  SPROUTS CAGE FREE EGGS 12CT
00052159000011  STONYFIELD ORG YGT PLN 32OZ
00093966005011  ORG VLY GRASSMILK CHEDDAR 8OZ
```

They are the only GTIN-to-product-name pairs in the system and the closest
thing to nutrition-ready seed data. Prod holds zero catalog rows.

Dev catalog composition: Sprouts Farmers Market 246 rows, Costco Wholesale 75.
Categories: UNCATEGORIZED 99, PRODUCE 61, DAIRY 59, GROCERY 51, BAKERY 18,
MEAT 14, BULK 12, DELI 5. `taxable` is False on 246, None on 70, True on 5.
Mining quality is visibly poor in places: one row's `product_text` is
`GROCERY PENNE SPROUTS RIGATE CAN PASTA TOMATOES`, a multi-row jumble from the
naive y-band pairing.

DAL: `receipt_dynamo/receipt_dynamo/data/_merchant_catalog_item.py` —
`add:48`, `add_many:57`, `put_many:65`, `get:74`, `list_for_merchant:94`,
`list_all:114`, `delete_items:129`, `delete_merchant:137`.

Mining logic in `scripts/ingest_merchant_catalog.py`: `NAME_LABELS:80`
(`PRODUCT_NAME`, `ITEM_NAME`), `PRICE_LABELS:81` (`LINE_TOTAL`, `ITEM_TOTAL`),
`SECTION_KEYWORDS:82`, `NON_PRODUCT:107`, `mine_receipt:462`, category
assignment from the nearest preceding section header at `:507`.

Alias tables that exist:

- Merchant aliases: `receipt_dynamo/receipt_dynamo/merchant_truth_loader.py`
  `normalize_merchant_alias:117`, `build_fleet_alias_map:128`.
- Label aliases: `receipt_dynamo/receipt_dynamo/constants.py:210`
  `NON_CORE_LABEL_ALIASES`, with `normalize_label_alias` refusing unknown
  values rather than guessing.
- **No product alias table exists.** The "CVS alias" in prior notes is a
  merchant alias in `scripts/merchant_profiles.json`, not a product one.

## 4. Food vs non-food, and merchant category

**No food/non-food flag exists on any entity.** What exists instead:

### 4.1 Merchant-level category (well populated, usable)

`ReceiptPlace.merchant_category` (`entities/receipt_place.py:189`) holds the
Google Places v1 `primaryType`. `merchant_types` (`:190`) holds the full types
array. Derivation is `_derive_category` in
`receipt_agent/receipt_agent/subagents/place_finder/tools/receipt_place_finder.py`,
which returns `primary_type` if present, else the first entry in `types` that
is not in a generic set. That generic set explicitly includes `"food"`, so
`"food"` is discarded as a category.

Live prod distribution across 838 ReceiptPlace rows:

| merchant_category | count |
|---|---|
| grocery_store | 318 |
| restaurant | 58 |
| department_store | 37 |
| warehouse_store | 36 |
| hamburger_restaurant | 24 |
| deli | 21 |
| (empty) | 18 |
| home_improvement_store | 18 |
| bar | 15 |
| cafe | 13 |
| hardware_store | 13 |
| cannabis_store | 13 |
| pharmacy | 12 |
| ice_cream_shop | 11 |

Coverage is 820 of 838 places. This is the only reliable food signal available
today, and it is merchant-level, not item-level.

`merchant_types` is effectively empty in prod: only about 5 places have any
entries. Two writers set it to `[]` unconditionally
(`infra/fix_place_lambda/lambdas/fix_place.py`, `scripts/run_place_finder.py`).
Do not build on `merchant_types`.

### 4.2 Item-level department category (mining-script only)

`scripts/ingest_merchant_catalog.py:82` `SECTION_KEYWORDS` is a store-department
vocabulary: `PRODUCE`, `DAIRY`, `GROCERY`, `BAKERY`, `MEAT`, `SEAFOOD`,
`FROZEN`, `DELI`, `BULK`, `VITAMINS`, `BODY`, `HOUSEHOLD`, `BEVERAGES`,
`SNACKS`, `PANTRY`, `REFRIGERATED`, `WELLNESS`. It mixes food departments with
non-food ones, so it is a coarse proxy at best. It lives only inside that
script and reaches DynamoDB only through `MerchantCatalogItem.category`, which
has zero prod rows.

`SECTION_HEADER` sections do exist in prod (495 rows), so the raw department
divider text is recoverable per receipt even though nothing parses it into a
category today.

### 4.3 The non-product stoplist

`scripts/ingest_merchant_catalog.py:107` `NON_PRODUCT` is roughly 60 uppercase
tokens that carry a price but are not products: `CREDIT`, `TAX`, `SUBTOTAL`,
`COUPON`, `CRV`, `BAGFEE`, `ITEMSSOLD`, and similar. The comment at `:101`
explains it exists because the y-band miner would otherwise inject fake
products. Any nutrition lookup needs this guard or an equivalent, since prod
line items demonstrably include rows like `SC YOU SAVED` and `NLP Savings`.

## 5. MCP tools and the DynamoDB vector search that replaced Chroma

### 5.1 `get_receipt_line_items`

`scripts/receipt_mcp_server.py:5273` `get_receipt_line_items_impl`, tool schema
at `:2047`, dispatch at `:2440`. Input is `{image_id, receipt_id}`.

Return keys: `image_id`, `receipt_id`, `item_count`, `items`, `summary`,
`items_sum`, `delta`, `reconciliation_status`, `items_section_line_ids`,
`items_section_status`.

Each element of `items` carries `item_index`, `name`, `price` (float),
`quantity`, `unit_price`, `is_discount`, `line_ids`, `name_quality`,
`reconciliation_status`, `extractor_version`. `summary` is
`{subtotal, grand_total, tax, merchant_name}` or `None`. The receipt-level
`reconciliation_status` is the worst per-item status by severity.

Related: `list_reconciliation_worklist` at `:5599` (schema `:2135`) builds a
repair queue; `extend_items_section` applies an arithmetic-guarded ITEMS
boundary extension.

### 5.2 `search_product_lines` works, but semantic only

`scripts/receipt_mcp_server.py:2860` `search_product_lines_impl`, schema at
`:451`. It is in `VECTOR_TOOLS` at `:70`, not any Chroma gate. The server has
zero occurrences of "chroma".

The `search_type` enum was narrowed to `semantic` only. The substring/text mode
returns `_mode_unavailable` (`:73`). So **there is no keyword or substring
search over product line text anywhere in the system today.** The
chroma-removal spec assigned that capability to a future DynamoDB text query
but it was never built. That is a real gap for any "find every receipt
containing milk" style nutrition rollup.

Results carry `text`, `price` (regex-extracted, last `\d+\.\d{2}` wins),
`similarity`, `has_price_label`, `merchant`, `image_id`, `receipt_id`. No
product identity, no normalization, no catalog join.

### 5.3 `list_categories` is a merchant category, not a product one

`scripts/receipt_mcp_server.py` `list_categories_impl` paginates every
`ReceiptPlace` row and tallies `merchant_category`. It returns
`{"total_categories": N, "categories": [{"category", "receipt_count"}]}`.
It is a full scan-and-tally of the Google Places `primaryType` per receipt.
Label-category tools are separate: `label_validation_summary`,
`list_words_by_label`, `get_label_distribution`.

### 5.4 receipt_embeddings, the Chroma replacement (landed on main)

Package `receipt_embeddings/` with `keys.py`, `backend.py`, `vector_client.py`,
`label_consensus.py`, `writer.py`, `section_labels.py`, `sweep.py`,
`service_limits.py`.

`receipt_embeddings/receipt_embeddings/service_limits.py`:

```
MAX_SEARCH_RESULTS = 100          # Chroma allowed 300
LINE_INDEX = "line-embeddings"
WORD_INDEX = "word-embeddings"
INDEX_FILTER_ATTRIBUTES = {LINE_INDEX: {"section_type"}, WORD_INDEX: {"label_status"}}
INDEX_VECTOR_ATTRIBUTES = {LINE_INDEX: "line_vector", WORD_INDEX: "word_vector"}
```

Embedding item keys follow
`RECEIPT#{r:05d}#LINE#{l:05d}#EMBEDDING` and
`RECEIPT#{r:05d}#LINE#{l:05d}#WORD#{w:05d}#EMBEDDING`, types
`RECEIPT_LINE_EMBEDDING` / `RECEIPT_WORD_EMBEDDING`. Both environments are
fully backfilled: dev holds 30,943 line and 111,594 word embeddings, prod holds
29,646 and 105,821.

Constraints that bound any nutrition feature built on vector search: equality
filters only (no ranges, no `in`, no negation, so post-filter client side),
100 results max per query, distance function and INCLUDE projections immutable
after index creation, and async indexing after write.

A live consumer to copy: `infra/routes/address_similarity_cache_generator/lambdas/index.py:226`
issues `SearchVectors` against `IndexName="line-embeddings"`.

### 5.5 The house plan format

`docs/chroma-removal/SPEC.md` (575 lines) is the template for a spec:
Summary, Goals/non-goals, Target architecture (storage, indexes, metadata,
write path, read-path consumer-by-consumer disposition, backfill), What gets
deleted, Naming rules, Phasing as a PR sequence, Landmines, Open questions,
Validation gates, Rough sizing.

`docs/chroma-removal/AGENT_PLAN.md` (114 lines) is the template for the build
plan: Principles, Test tiers table, the harness built first, then task cards in
a table with columns ID / Task / Base / Eval-and-acceptance, split into stacks,
plus review cadence. Its stated rules matter here: harness and golden fixtures
before implementations, agents are done only when the eval says so, stacked PRs
merge with `--merge` at parents and squash at leaves, one agent per worktree.

Supporting inventories exist as separate files: `inventory-write-path.md`,
`inventory-read-path.md`, `inventory-infra.md`, `inventory-tests-ci-scripts.md`,
plus `BAKEOFF.md` and `research-dynamodb-vector-search.md`.

## 6. Frontend surfaces

Root is `portfolio/`. Pages Router, no `app/` directory.

`portfolio/next.config.js:9` sets `output: "export"`, so **the site is a static
export and cannot host a Next.js API route.** Any new data must come from an
API Gateway route or a pre-baked S3 JSON cache. Dev-only rewrites proxy
`/api/*` to `dev-api.tylernorlund.com` at `next.config.js:33`.

### 6.1 Where receipts render

`portfolio/pages/receipt.tsx` (848 lines) is the single long-form page.
Figures are imported at `:13`-`:34` from
`portfolio/components/ui/Figures/index.ts` as `next/dynamic` `ssr:false`
exports, and mounted inside `FigureBoundary` (defined locally at
`pages/receipt.tsx:85`, an IntersectionObserver plus `content-visibility` lazy
mount).

Mount points relevant to a nutrition panel:

| Component | Mounted at | Data |
|---|---|---|
| `ReceiptHealthExplorer` | `pages/receipt.tsx:430` | `fetchReceiptHealth` + `fetchReceiptHealthIssues` |
| `WordSimilarity` | `pages/receipt.tsx:580` | `fetchWordSimilarity` |

### 6.2 The API layer

Single client module `portfolio/services/api/index.ts` (537 lines). Relevant
methods: `fetchWordSimilarity:261`, `fetchReceiptHealth:401` (hits
`/label_evaluator/receipt_health` at `:419`), `fetchReceiptHealthIssues:430`.

Handlers are Lambdas under `infra/routes/*/lambdas/index.py`, most of which
just serve pre-baked JSON from an S3 cache bucket. The receipt-health payload
is produced by
`infra/label_evaluator_step_functions/lambdas/build_viz_cache.py`.

**No API Gateway route serves line items.** A nutrition panel needs a new route
plus its cache producer, or an extension of the receipt-health payload.

### 6.3 Does any line-item data reach the browser today

Partly, and never as real product rows.

- `portfolio/types/api.ts:300` `MilkSummaryRow` has `merchant`, `product`,
  `size`, `count`, `avg_price`, `total`. Rendered as a table by
  `components/ui/Figures/WordSimilarity.tsx`. This is one hardcoded
  similarity query, not general line items.
- `portfolio/types/api.ts:1136` `ReceiptHealthLineItemAmountEvidenceRow` and
  `:1145` `ReceiptHealthLineItemAmountEvidence` carry only counts
  (`item_amount_row_count:1146` and siblings) plus raw row text. Rendered by
  `LineItemTokenMap` at
  `components/ui/Figures/ReceiptHealthExplorer/index.tsx:1164`, mounted at
  `:1351`. Statistics, not product values.
- Word-label styling for line-item labels already exists:
  `components/ui/Figures/labelStyles.ts:19` (`PRODUCT_NAME`), `:24`
  (`LINE_TOTAL`), `:25` (`UNIT_PRICE`), `:35` (`QUANTITY`), with display names
  at `:53`-`:58`.
- `components/ui/Figures/RandomReceiptWithLabels.tsx:32` already defines a
  `lineItems` legend group listing `PRODUCT_NAME`, `QUANTITY`, `UNIT_PRICE`
  (`:35`). That component is built but not mounted on any page.

This was the inventory at the research commit. Main #1391 now renders
decoded product names/prices from a static walkthrough export; it does not
provide a general live nutrition read route.

### 6.4 How to add a panel

Create `components/ui/Figures/<Name>/index.tsx` plus a colocated
`<Name>.module.css`, export it as a `dynamic(..., { ssr: false })` entry in
`components/ui/Figures/index.ts`, import it in `pages/receipt.tsx:13`-`:34`,
and drop a `<FigureBoundary>` into the prose flow near `:430` or `:580`.

Styling is CSS Modules, no Tailwind. Global design tokens are in
`portfolio/styles/globals.css:14` onward (`--background-color`, the
`--color-*` palette at `:33`). Data fetching uses TanStack Query or a plain
`useEffect` plus `useInView`.

Standing rule from `docs/line-items/STATE_OF_THE_SYSTEM.md`: never merge
frontend or visual changes without the owner's review.

### 6.5 How the site actually gets receipt data

There are exactly two patterns behind the API Gateway routes, and the choice
matters for a nutrition panel.

**Pattern 1, direct DynamoDB read in the request Lambda.** Used where the query
is cheap and bounded.

- `infra/routes/receipts/handler/index.py:14` `handler` constructs a
  `DynamoClient` at `:19` and calls `client.list_receipts(...)` at `:37`.
- `infra/routes/merchant_counts/handler/index.py:13` builds a module-level
  `DynamoClient` and `fetch_merchant_counts:24` walks every `ReceiptPlace`,
  normalizing `merchant_name` to uppercase at `:44` and tallying at `:51`.
  This is the closest thing the site has to a spend or merchant rollup, and
  it is a count of receipts per merchant, not a spend total.
- Same shape in `infra/routes/random_receipt_details/`, `image_count`,
  `receipt_count`, `images`.

**Pattern 2, thin Lambda serving precomputed JSON from S3.** Used for anything
expensive or aggregated. The request Lambda does nothing but `get_object`.

- `infra/routes/word_similarity/handler/index.py:14` reads `S3_CACHE_BUCKET`
  from the environment, and `handler:21` does a single
  `s3_client.get_object(Bucket=S3_CACHE_BUCKET, Key=CACHE_KEY)` at `:49`,
  returning a 404-ish path when the cache is missing at `:66`.
- The payload is produced by a paired generator Lambda,
  `infra/routes/word_similarity_cache_generator/lambdas/index.py`
  (1,029 lines), which holds `S3_CACHE_BUCKET:31` and the product-name
  cleanup described in section 7.
- The same split exists for `image_details_cache` /
  `image_details_cache_generator`, `address_similarity` /
  `address_similarity_cache_generator`, `layoutlm_inference` /
  `layoutlm_inference_cache_generator`, and `label_validation_timeline` /
  `label_validation_timeline_cache`.
- `infra/routes/label_evaluator_viz_cache/lambdas/index.py` is one Lambda
  serving every `label_evaluator/*` path from S3 prefixes, including
  `receipt-health/`. Its producer is the Step Functions job
  `infra/label_evaluator_step_functions/lambdas/build_viz_cache.py`.

Full route inventory under `infra/routes/`: `address_similarity`,
`address_similarity_cache_generator`, `ai_usage`, `health_check`,
`image_count`, `image_details_cache`, `image_details_cache_generator`,
`images`, `job_training_metrics`, `label_evaluator_viz_cache`,
`label_validation_count`, `label_validation_timeline`,
`label_validation_timeline_cache`, `label_validation_viz_cache`,
`layoutlm_epochs`, `layoutlm_inference`, `layoutlm_inference_cache_generator`,
`merchant_counts`, `process`, `qa_viz_cache`, `random_image_details`,
`random_receipt_details`, `reader_summary`, `receipt_count`, `receipts`,
`word_similarity`, `word_similarity_cache_generator`.

None of them serves line items, and there is no spend-total route at all.

For a nutrition panel, pattern 2 is the right shape: the join between line
items and any nutrition source is expensive and should be precomputed, not
done per request. That means a new `<name>_cache_generator` Lambda plus a thin
`<name>` reader, following the word-similarity pair exactly.

### 6.6 The dev-harness pattern for local-only pages

**This is not on main.** `portfolio/dev-harness/` exists only on branch
`codex/geometric-reader` (commit `a569fb6ec`, "feat(dev): /dev/validation
line-item review workstation", 2026-07-31). It is worth documenting because it
is the established way to build a data-heavy review UI against real DynamoDB
without shipping anything.

How it works:

- `portfolio/dev-harness/validation_shim.py` (519 lines) is a stdlib
  `http.server` process on port 8787, run as
  `python portfolio/dev-harness/validation_shim.py [--port 8787]`. Its
  docstring is explicit that nothing ships: no Lambda imports the module.
- `portfolio/next.config.js` wires the routes ONLY inside
  `PHASE_DEVELOPMENT_SERVER`, as rewrites:
  `/api/line_item_decode` to `http://127.0.0.1:8787/line_item_decode`, and
  `/api/validation/:path*` to `http://127.0.0.1:8787/:path*`.
  Because the production build is `output: "export"`, these routes simply do
  not exist in the deployed site.
- The page lives under `portfolio/pages/dev/`, and its components under
  `portfolio/components/dev/validation/` (`MerchantList.tsx`,
  `ReceiptCanvas.tsx`, `TruthPanel.tsx`, `truthChain.ts`, `client.ts`,
  `types.ts`, `Validation.module.css`, plus `TruthPanel.test.tsx`).
- The rule that makes it trustworthy: **it reimplements no math.** The shim
  loads `get_receipt_line_items_impl` and `_summary_baseline` directly out of
  `scripts/receipt_mcp_server.py`, so the harness and the agent-facing MCP
  tools cannot disagree. Geometry and image payloads reuse the deployed
  `line_item_decode` handler's helpers.
- It reads a 90-second cached index built from two type-GSI scans (about 2
  seconds over 653 dev receipts) to serve `/merchants` and `/worklist`.
- Agent output reaches the reviewer as files, never as rows:
  `queues/<name>.json`, `dossiers/<image_id>-<receipt_id>.json`,
  `verdicts/<pass-id>.jsonl`, `verdicts/<pass-id>/digest.json`. The process
  writes exactly three things: the review log,
  `approvals/<pass-id>.json`, and `freeze/<class>` markers.
- Environment: `DYNAMODB_TABLE_NAME` defaults to the dev table
  `ReceiptsTable-dc5be22`; `VALIDATION_HARNESS_DIR` defaults to
  `<repo>/.dev-harness`.

If the nutrition work needs a human review surface (confirming product-to-food
matches, for instance), this is the pattern to copy rather than adding a
public figure: a local stdlib shim, dev-only rewrites, components under
`components/dev/`, and math imported from the canonical module instead of
reimplemented.

## 7. Gaps that constrain the plan

1. **No nutrition implementation found in the audited checkout.** This
   search does not establish the contents of every other branch or worktree.
2. **No product identity.** Line-item `name` is raw abbreviated OCR text
   (`BUR FLOUR TORTILLAS`, `STO BABY ROMAINE`). There is no canonical product
   name, no alias table, and no catalog with prod rows. Any nutrition join
   needs a resolution layer that does not exist yet.
3. **Effectively no UPC or GTIN.** `ReceiptBarcode` stores a raw Vision
   payload and `symbology` but is never linked to a line item, and nothing
   normalizes `EAN13`/`ITF14` payloads into a GTIN. Prod holds 19 barcode rows
   total. `MerchantCatalogItem.upc` is populated on exactly 9 dev rows, all
   left over from a removed online-catalog experiment; no current writer sets
   it and prod has zero catalog rows.
4. **The line-item rewrite is destructive.** `line_item_processor.py:372`
   deletes every row for the receipt before rewriting. Nutrition data must not
   live as extra attributes on `ReceiptLineItem`.
5. **Quantity coverage is thin.** 446 of 2,539 prod rows have a quantity and
   177 have a unit price, so per-serving cost is computable for a minority of
   rows without further inference.
6. **No unit of measure or serving size is stored**, even though the decoder's
   regexes recognize `lb`, `oz`, `kg`, `gal`, `ea`, `ct`, `pk`
   (`geometry.py:412`). The unit is matched and discarded. One partial
   precedent exists and is worth reusing:
   `infra/routes/word_similarity_cache_generator/lambdas/index.py` has
   `strip_upc_prefix:307` (drops a leading 8+ digit item code from a product
   name) and `infer_size:313`, driven by `_EXPLICIT_SIZE_TOKENS:287`
   (gallon/quart/pint tokens) with a price-range fallback at
   `_GENERIC_MILK_RANGES:301`. It is milk-specific and lives in a viz-cache
   Lambda, not in a shared package.
7. **No text search over line items.** Semantic vector search only, capped at
   100 results, equality filters only.
8. **Sections are almost entirely PENDING in prod** (6,624 of 6,625, with a
   single VALID row). A gate requiring VALID sections would return nothing.
9. **Merchant strings vary.** The reported case variants are distinct raw
   values, but the existing lowercase line-item slug maps them to the same
   partition. Semantic merchant aliases still need the fleet alias map.
10. **MerchantCatalogItem is empty in prod**, so the one existing place a
    per-product enrichment could attach has no production data behind it.

## 8. Quick reference: files most likely to be touched

| Concern | File |
|---|---|
| Line-item entity | `receipt_dynamo/receipt_dynamo/entities/receipt_line_item.py` |
| Line-item DAL | `receipt_dynamo/receipt_dynamo/data/_receipt_line_item.py` |
| Cache entity template | `receipt_dynamo/receipt_dynamo/entities/places_cache.py` |
| Cache DAL template | `receipt_dynamo/receipt_dynamo/data/_places_cache.py` |
| External client template | `receipt_places/receipt_places/{client_v1,config,cache}.py` |
| Decoder | `receipt_upload/receipt_upload/line_items/{geometry,blocks}.py` |
| Updater Lambda | `infra/receipt_line_item_updater/{handler,line_item_processor}.py` |
| Stream routing | `receipt_dynamo_stream/receipt_dynamo_stream/{models,message_builder,sqs_publisher}.py`, `change_detection/detector.py` |
| Queue and Lambda infra | `infra/receipt_update_queues/__init__.py` |
| Table and TTL | `infra/dynamo_db.py` |
| MCP tools (both copies) | `scripts/receipt_mcp_server.py`, `infra/mcp_server_lambda/lambdas/receipt_mcp_server_server.py` |
| Vector search | `receipt_embeddings/receipt_embeddings/service_limits.py` |
| Site page | `portfolio/pages/receipt.tsx`, `portfolio/components/ui/Figures/index.ts` |
| Site types | `portfolio/types/api.ts`, `portfolio/services/api/index.ts` |
