# LayoutLM ↔ sections ↔ the band-block decoder: where they should join

Read-only design study, 2026-08-05. Every number below was measured against
live `ReceiptsTable-d7ff76a` (prod) and `ReceiptsTable-dc5be22` (dev) during
this study; nothing was written. Where a claim could not be measured, it says
so.

> **Revised 2026-08-05 after owner input.** Earlier drafts read the 2026-07-30
> narrowing of the LayoutLM label set as an accidental regression and made
> "restore the previous bundle" their top recommendation. **That was wrong.**
> The narrowing was deliberate: the wide model's product labels were not good
> enough for production, and the section → line-items sprint exists because of
> that. The narrow model is the intended configuration and the deterministic
> decoder is the intended product labeller. The recommendation has been
> removed and the framing corrected throughout; §2.2 now rests on the frozen
> heldout metrics rather than on the val-F1 comparison, which does not support
> a conclusion in either direction. The cohort analysis, the
> sections-as-detector finding, the `propose_line_item_labels` correction and
> the delivery bugs in §7 are unaffected and stand.

---

## 0. The short version

The framing question — *when should LayoutLM's product labels be joined with
sections and the decoder?* — rests on a false premise. **LayoutLM is not the
product labeller and is not meant to become one.** The currently deployed model
is an 8-class header/footer model, and that narrow scope is the **intended
production configuration**, confirmed by the project owner: the wide model's
product labels were not good enough to ship, and the section → line-items
sprint exists *because* of that. There are no LayoutLM product labels to join,
by design.

The division of labour the system is actually built around:

- **LayoutLM** owns the header/footer fields it does well — `MERCHANT_NAME`,
  `DATE`, `TIME`, `WEBSITE`, `STORE_HOURS`, `PAYMENT_METHOD`, and the merged
  `AMOUNT` / `ADDRESS` classes.
- **The deterministic band-block decoder** owns `PRODUCT_NAME`, `LINE_TOTAL`,
  `UNIT_PRICE`, `QUANTITY`, `SUBTOTAL`, `TAX`, `GRAND_TOTAL`, `DISCOUNT`. It is
  the **intended** source of those labels, not a stopgap awaiting a better
  model.

The evidence in this document supports that split rather than contradicting it.
At v30's own best epoch on the frozen heldout set, `PRODUCT_NAME` F1 was
**0.245** (n=939), `UNIT_PRICE` **0.328** (n=193), `QUANTITY` **0.359**
(n=141), `LINE_TOTAL` **0.665** (n=407). Those are the numbers behind "not good
enough for prod", and they are the honest ones — see §2.2 for why the headline
`0.531 → 0.737` val-F1 jump is *not* evidence of anything in either direction.

So the recommendation reduces to three things:

1. **Fix the one genuine bug in the LayoutLM path** — the 2026-06-16 (#958)
   `CORE_LABELS` write filter, which silently discards `AMOUNT` and `ADDRESS`.
   Those are two of the eight classes the active model is *trained to emit*, so
   the model's two highest-support outputs never reach DynamoDB. **~20 lines of
   Swift.** This is about making the narrow model work as designed; it has
   nothing to do with product labels.
2. **The join runs decoder → LayoutLM, not LayoutLM → decoder.** The decoder's
   labels are arithmetically proven; the corpus the model is scored against is
   not (`UNIT_PRICE` 82% INVALID). If LayoutLM is ever widened again, the
   decoder's proven labels — not the existing corpus — are what it should be
   trained on. That is a future option, not a current recommendation.
3. **Sections are already a strong scope, and should be used as a *filter*, not
   as a new model input.** 94% of VALID PRODUCT_NAME words fall inside the
   ITEMS section; 97% of GRAND_TOTAL words fall outside it. §C.7 goes further:
   section position is a *label-quality detector* that needs neither a model nor
   the decoder.

**Do not restore the 22-class bundle.** An earlier draft of this document
recommended exactly that, as its cheapest and highest-leverage step. It was
wrong: the swap was a deliberate product decision, and a single `aws s3 cp`
would have reverted it. That recommendation has been removed.

---

## 1. What actually runs today

### 1.1 The pipeline as it exists

```
                     Mac worker (receipt-ocr, macOS only)
 ┌──────────────────────────────────────────────────────────────────────┐
 │  SQS ocr-job-queue                                                   │
 │       ↓                                                              │
 │  VisionOCREngine.performOCR ─── words + boxes                        │
 │       ↓                                                              │
 │  LayoutLMInference.predict(lines:)      ← CoreML bundle from S3      │
 │       ↓                                    layoutlm-coreml-bundle.zip │
 │  [LinePrediction: tokens/labels/confidences]                         │
 │       ↓                                                              │
 │  ReceiptWordLabel.fromLinePredictions                                │
 │       ├── strip B-/I- prefix                                         │
 │       ├── drop "O"                                                   │
 │       └── ✂ DROP anything not in coreLabels  ←──── THE BREAK (§2.3)  │
 │       ↓                                                              │
 │  dynamo.addReceiptWordLabels(...)   PENDING / "auto-inference"       │
 │       ↓                                                              │
 │  S3 ocr_results/*.json  +  SQS ocr-results-queue                     │
 └──────────────────────────────────────────────────────────────────────┘
                                  ↓
       upload-images-process-ocr-image-{dev,prod}  (Lambda)
 ┌──────────────────────────────────────────────────────────────────────┐
 │  merchant_resolution/embedding_processor.py                          │
 │   1. _prepare_pending_core_labels("non_core_label_guard")            │
 │        AMOUNT → classify_amount_labels() → LINE_TOTAL/SUBTOTAL/…     │
 │        other  → normalize_label_alias()                              │
 │        (dead code in practice since 2026-06-16 — nothing arrives)    │
 │   2. dedupe_grand_total() / reclassify_mislabeled_totals()           │
 │   3. propose_line_item_labels()   → "geometry_line_items"            │
 │   4. propose_product_names()      → Chroma kNN proposals             │
 │   5. Chroma lightweight validator → LLM validator                    │
 │        ✂ OVERWRITES label_proposed_by in place:                      │
 │          "llm_valid" / "llm_needs_review" / "llm_invalid" /          │
 │          "llm_corrected:<old>"        ←──── PROVENANCE LOSS (§1.3)   │
 └──────────────────────────────────────────────────────────────────────┘
                                  ↓
                            DynamoDB word labels

   ── entirely separate, via the DynamoDB stream ──
       RECEIPT_SECTION rows      (upload-determinism-v1, PENDING)
       RECEIPT_LINE_ITEM rows    (band-block decoder, reconciliation_status)
       NEW #1372: decoder_reconciled word labels (backfill script only,
                  not yet wired into ingest)
```

### 1.2 Which stage's output survives

Prod word labels, all 60,105 rows, grouped by `label_proposed_by`:

| Source | Prod | Dev | What it is |
|---|---:|---:|---|
| `simple_receipt_analyzer` | 19,580 | 19,351 | **Deleted code.** Lived in the `receipt_label` package, removed in #532. 19,330 of its prod rows were written in 2025-11 alone. |
| `label-evaluator-llm` | 9,725 | 8,956 | Cloud LLM evaluator |
| `COMPLETION_BATCH` | 7,354 | 7,309 | 2025-03/04/05 only |
| `mcp-claude-review` | 7,241 | 8,295 | Agent/human MCP edits |
| `llm_valid` | 3,700 | 3,695 | **Validator, not a producer** — see §1.3 |
| `reasoning_addition_validator` | 3,376 | 3,336 | 2025-08 one-off |
| `regional_reocr_revalidation` | 1,901 | 2,045 | Re-OCR repair loop |
| `geometry_line_items` | 85 | 85 | `propose_line_item_labels` |
| `geometry_trusted` | 20 | 21 | arithmetic-locked geometry |
| `decoder_reconciled` | **0** | **59** | #1372, dev-only, 3 receipts |
| `auto-inference` | **5** | **5** | **The Swift LayoutLM path** |
| …119 other sources | | | mostly `<merchant>_analyzer_llm` |

**Historical product labels came from `simple_receipt_analyzer`, not LayoutLM.**
It produced 5,290 of prod's 9,179 PRODUCT_NAME, 1,313 of 2,821 UNIT_PRICE and
954 of 1,716 QUANTITY. It stopped when the `receipt_label` package was deleted
(#532). Nothing replaced it at the same volume. That is the whole answer to
"product labels *were* produced at some point — by which path, and when did it
stop".

### 1.3 The LLM pass destroys provenance — so `auto-inference: 5` is misleading

`receipt_upload/receipt_upload/label_validation/llm_runner.py:224,241,254,299,316`
**mutates** the existing row:

```python
label.validation_status = ValidationStatus.VALID.value
label.label_proposed_by = "llm_valid"      # ← the proposer is erased
dynamo.update_receipt_word_label(label)
```

So `llm_valid` / `llm_needs_review` / `llm_invalid` are **validator verdicts
wearing the proposer field**. The 5 surviving `auto-inference` rows are the
handful the validator never reached. The worker's LayoutLM predictions *are*
persisted — they are just relabelled on the way through.

**This answers the crux directly: the labels the lead observed stamped
`llm_valid` on the three fresh receipts ARE the worker's LayoutLM output.**

The proof is exact. On the three newest dev receipts (`90af9793`, `67b149c0`,
`2b630bec`, all 2026-08-05), **39 of 39** llm-attributed labels are drawn from
exactly six types:

```
PAYMENT_METHOD 14 · STORE_HOURS 9 · MERCHANT_NAME 6 · DATE 4 · TIME 3 · WEBSITE 3
```

Those six are precisely the deployed model's eight classes minus the two the
Swift filter drops. Not a coincidence — a fingerprint.

---

## 2. The deployed model, the bug, and the deliberate scope change

### 2.1 The model deployed *right now* has no product classes — by design

`layoutlm_model_s3_key = coreml/layoutlm-coreml-bundle.zip`. Downloaded and
unzipped during this study; `label_map.json` reads:

```
O, B/I-ADDRESS, B/I-AMOUNT, B/I-DATE, B/I-MERCHANT_NAME,
B/I-PAYMENT_METHOD, B/I-STORE_HOURS, B/I-TIME, B/I-WEBSITE
```

Eight entity classes. No PRODUCT_NAME, no LINE_TOTAL, no UNIT_PRICE, no
QUANTITY.

**Correction to the brief:** `LayoutLMConfig.swift` does *not* contain a label
vocabulary — it deserialises `id2label` from the bundle's `config.json`. The
22-name list in the brief is `coreLabels` in
`receipt_ocr_swift/Sources/ReceiptOCRCore/Models/ReceiptWordLabel.swift:18`,
which is the *write filter*, not the model's vocabulary. The capability does
not exist in the shipped model.

### 2.1a A wider model was live until 2026-07-30, and was retired on purpose

Credit to the parallel study (`join-study-2`) for finding this; every figure
below was re-derived independently here before being written down.

**Read this section as history, not as a proposal.** An earlier draft treated
the 07-30 swap as an accidental regression and recommended restoring the
previous bundle. The project owner has confirmed it was deliberate — the wide
model's product labels were not good enough for production, and this sprint is
the response. The deployment history below is worth keeping because it explains
what each ingest cohort saw and therefore how to read the corpus; it is not a
menu of bundles to roll back to.

The `pre-*-backup.zip` chain in `s3://layoutlm-training-dev-68164770/coreml/`
is the deployment history of `layoutlm-coreml-bundle.zip`. Each backup is the
bundle that was live *before* the named version replaced it. Reading
`label_map.json` out of each (range-read, no 415 MB downloads):

| backup | S3 date | entity classes | product-capable |
|---|---|---:|---|
| `pre902-backup-20260615` | 2026-06-15 | 8 | no |
| `pre-v18-backup` | 2026-06-17 | 8 | no |
| `pre-v21-backup` | 2026-06-18 | 9 | no |
| `pre-v30-backup` | 2026-07-14 | **20** | **yes** |
| `pre-v31-backup` | 2026-07-30 | **22** | **yes** (adds PRODUCT_NAME, LOYALTY_ID) |
| `layoutlm-coreml-bundle.zip` (**current**) | 2026-07-30 | 8 | **no — deliberate** |

So a 20-class model was live ~2026-06-18 → 2026-07-14, a 22-class model
2026-07-14 → 2026-07-30, and the 8-class model since. The last transition is
the product decision described above; the earlier ones are the training
programme working through scopes.

**The four-cohort natural experiment.** Grouping prod receipts by ingest date
(`min(timestamp_added)`), counting only LayoutLM-origin rows
(`llm_valid`/`llm_needs_review`/`llm_invalid`/`llm_corrected:*`):

| cohort | model | receipts | LayoutLM rows | VALID | product labels | `ADDRESS`/`AMOUNT` raw |
|---|---|---:|---:|---:|---|---:|
| **A** ≤06-16 | 8/9-class, no filter | 133 | 3,070 | 64% | LINE_TOTAL 131, PRODUCT_NAME 7 | 771 / 585 |
| **B** 06-17→07-13 | 20-class | 58 | 1,585 | 86% | **PRODUCT_NAME 348**, LINE_TOTAL 58, QUANTITY 12 | 0 / 0 |
| **C** 07-14→07-29 | 22-class | 52 | 1,286 | 87% | **PRODUCT_NAME 324**, LINE_TOTAL 88, QUANTITY 25 | 8 / 12 |
| **D** ≥07-30 | 8-class + filter | 3 | 39 | 87% | **none** | 0 / 0 |

This cleanly separates the **bug** from the **scope decision** — which is the
whole reason to keep the table:

- **A vs D** run the same 8-class AMOUNT-merged model family with opposite
  outcomes on the merged classes (771/585 raw rows vs zero). The model is held
  constant, so this isolates **#958's filter — the genuine bug**, and is the
  evidence for §5 Step 1.
- **B/C vs D** shows the effect of the **deliberate scope change**: the product
  classes disappear the day the wide bundle was retired. This is expected
  behaviour, not a defect. Its practical use is as a **dating tool** — a
  receipt's label profile tells you which cohort it was ingested in, which
  matters when reading the corpus (see §C.5, where it settles §4.2).

`geometry_line_items` (the deterministic fallback proposer) tracks the same
shape — 0 in A, 31 in B, 54 in C, 0 in D. §C.6 shows the exact mechanism, and
that it is a hard early return rather than a correlation. Note the consequence:
under the intended narrow-model configuration this proposer **cannot** fire,
because it needs a `SUBTOTAL`/`TAX`/`GRAND_TOTAL` label that the narrow model
never emits. That is an argument for the decoder owning those labels, which is
what this sprint does — not an argument for widening the model.

### 2.2 Why the narrow scope is right — and which number actually shows it

The active model is `layoutlm-v31-nonproduct-clean-20260729`, tagged
`active_model: true`, trained with:

```
allowed_labels = MERCHANT_NAME,DATE,TIME,AMOUNT,ADDRESS,WEBSITE,STORE_HOURS,PAYMENT_METHOD
label_merges   = {"AMOUNT": [LINE_TOTAL, SUBTOTAL, TAX, GRAND_TOTAL],
                  "ADDRESS": [ADDRESS_LINE, PHONE_NUMBER]}
```

**The number that justifies the narrow scope is the frozen heldout set, not the
headline val F1.** `v30-fullcore` — the widest model ever deployed — recorded a
`heldout_label_*` series alongside its validation metrics. At its own best
epoch (29):

| class | heldout F1 | precision | recall | support |
|---|---:|---:|---:|---:|
| `LINE_TOTAL` | 0.665 | 0.572 | 0.794 | 407 |
| `QUANTITY` | 0.359 | 0.349 | 0.369 | 141 |
| `UNIT_PRICE` | 0.328 | 0.387 | 0.285 | 193 |
| `PRODUCT_NAME` | **0.245** | 0.265 | 0.228 | **939** |

with `heldout_windowed_product_detail_macro_f1 = 0.399` and
`heldout_windowed_f1 = 0.564`. A 0.245 F1 on the highest-support class in the
model, on a frozen set, is a clear and sufficient answer to "is this good enough
to ship". **That is the evidence behind the scope decision, and it stands
without qualification.**

**Do not cite the `0.531 → 0.737` val-F1 jump as supporting it.** That
comparison is not apples-to-apples in either direction:

- `v30` was trained with
  `val_keys_s3 = s3://…/adversarial_val_keys_v2_20260708.json` — a fixed,
  adversarial split. **`v31` has no `val_keys_s3` hyperparameter at all**
  (verified in `describe-training-job`), so it validated against a different,
  self-selected population with roughly half the per-class support.
- Both splits are drawn from the corpus whose `UNIT_PRICE` is 82% INVALID and
  `QUANTITY` 69% INVALID (§3.2). A model penalised for disagreeing with wrong
  labels scores badly however good it is; dropping those classes removes the
  penalty without measuring capability.

The jump is a measurement artefact of two different splits and tells you nothing
about which model is better. The frozen-heldout numbers above are the
trustworthy signal, and they point the same way as the product decision.

**One thing genuinely cannot be compared, in either direction.** `v31` recorded
**zero** `heldout_label_*` metrics — only `heldout_windowed_*` aggregates. So
there is no per-class frozen-heldout comparison between the narrow and wide
models, and this document does not attempt one. If a future run wants to revisit
the scope, recording `heldout_label_*` for the narrow model is the prerequisite.

Cohort C's production numbers (324 `PRODUCT_NAME` labels at 87% VALID) are **not**
a counter-argument, and an earlier draft was wrong to read them as one. That 87%
is the acceptance rate of a downstream LLM validator scored against the same
corrupt corpus — it measures agreement with the existing labelling stack, not
correctness. It is reported in §2.1a as a description of what each cohort
contains, which is what makes the cohort table useful for dating receipts.

### 2.3 The bug: the model's two best classes never land

`v31`'s two highest-support classes are the merged ones:

```
AMOUNT   f1 = 0.771  support 408   ← the highest-support class in the model
ADDRESS  f1 = 0.558  support 216
```

Neither `AMOUNT` nor `ADDRESS` is in `CORE_LABELS`
(`receipt_dynamo/receipt_dynamo/constants.py:191`) or in the identical Swift
`coreLabels` set. `fromLinePredictions` drops them before the write:

```swift
// ReceiptWordLabel.swift:205
if strippedLabel == "O" || !coreLabels.contains(strippedLabel) { continue }
```

That filter landed **2026-06-16 in #958 ("Fix receipt label hygiene")**. The
corpus shows the effect precisely — monthly counts of prod rows carrying those
labels:

| label | 2026-02 | 2026-05 | 2026-06 | 2026-07 |
|---|---:|---:|---:|---:|
| `ADDRESS` | 163 | 429 | 453 | **8** |
| `AMOUNT` | 13 | 275 | 346 | **12** |
| `llm_corrected:ADDRESS` (new CORE labels won) | 47 | 199 | 130 | **0** |
| `llm_corrected:AMOUNT` (new CORE labels won) | 8 | 163 | 323 | **11** |

**There was a working LayoutLM → LLM refinement join, and #958 turned it off.**
The model proposed a merged `AMOUNT`; the pipeline disambiguated it into a
specific CORE label. That produced 505 `llm_corrected:AMOUNT` and 376
`llm_corrected:ADDRESS` labels in prod — ~880 real labels — before it stopped.

Worse, the Python side is still built for it and now runs on empty:
`_prepare_pending_core_labels` in `embedding_processor.py:474` has a whole
`AMOUNT` branch calling `classify_amount_labels()` deterministically. Dead
code since June.

### 2.4 Two smaller findings worth recording

- **Prod's model key does not exist.** `pulumi stack output --stack prod` gives
  `layoutlm_model_s3_bucket = layoutlm-training-prod-68164770`,
  `layoutlm_model_s3_key = coreml/layoutlm-coreml-bundle.zip`. `head-object`
  on that key returns **404**. Only a loose, unzipped `coreml/LayoutLM.mlpackage/`
  from 2026-05-24 is there. A worker started with `--env prod` cannot download
  a model; only `--env dev` works. Every model observation in this document is
  therefore about the *dev* bundle, which is what the running workers use.
- **No confidence threshold anywhere.** `LayoutLMInference` takes the argmax and
  writes it. Sparse output means the model genuinely predicts `O`, not that a
  gate is filtering.
- **The model cache never invalidates, and it is shared across environments.**
  `ModelDownloader.ensureModelDownloaded` returns early on
  `isModelCached(at:)` (`ModelDownloader.swift:41`), which checks only that
  `vocab.txt`, `config.json` and some `*.mlpackage` directory exist — no ETag,
  no version, no manifest comparison. **Once any bundle is on disk the worker
  never downloads again.** The default cache path is
  `.models/layoutlm` (`Config.swift:142`) — *relative to cwd*, and identical
  for `--env dev` and `--env prod`.

  Two consequences. First, **"which model is deployed" is not the same question
  as "which model is running"**: a worker that cached the 22-class bundle
  before 2026-07-30 is still running it today. Second, it explains why the dev
  and prod copies of the same three images produced byte-identical
  predictions — the same cached bundle served both. Any measurement of "what
  the model does" must state which *machine* it was taken on.

---

## 3. The join: decoder → LayoutLM

The brief suspected the arrow runs opposite to the framing. It does, and the
numbers are stronger than the brief's.

### 3.1 The dry run reproduces exactly

Re-ran `scripts/backfill_decoder_word_labels.py` (dry-run, no writes) against
prod at `fb22a2967`:

```
receipts_scanned = 730
gates: ok 505 | not-matched 196 | no-items 29
would mint: 807   (PRODUCT_NAME 664, LINE_TOTAL 75, QUANTITY 50,
                   GRAND_TOTAL 9, SUBTOTAL 5, DISCOUNT 4)
collisions: 5563  →  5399 agree / 164 disagree  =  97.1%
```

Independently confirmed. Two figures the brief did not mention matter more than
the mint count:

- **Only 180 of the 505 gated receipts produce any *new* label.** The other 325
  are already fully covered. The mint count (807) is small; the *cross-check*
  count (5,563) is the real product.
- **The full derived label set is 6,370 labels over 505 receipts** (mean 12.6),
  of which PRODUCT_NAME 4,056 and LINE_TOTAL 1,453. That is the training set, not
  the 807.

### 3.2 What 97.1% actually says about each side

It says the *decoder* is good, and it says less about the corpus than it looks
like. Prod VALID rates for the labels being cross-checked:

| label | rows | VALID | INVALID | VALID rate |
|---|---:|---:|---:|---:|
| PRODUCT_NAME | 9,179 | 5,316 | 3,461 | 57.9% |
| LINE_TOTAL | 4,002 | 1,710 | 2,174 | 42.7% |
| QUANTITY | 1,716 | 534 | 1,114 | 31.1% |
| UNIT_PRICE | 2,821 | 497 | 2,223 | **17.6%** |

A corpus where the majority verdict on UNIT_PRICE is INVALID is not a reference
standard. 97.1% agreement means the decoder reproduces the *surviving* corpus
consensus — which is worth knowing, but the decoder's warrant is its own
arithmetic, not this number.

### 3.3 The 164 disagreements split cleanly into two kinds

This is the most important finding in this section, and it changes the design.

**(a) Where the decoder is right — price/text confusions.** Every one of these
is a legacy label error:

```
derived LINE_TOTAL  → existing PRODUCT_NAME  (10)
   Sprouts '4.99' '9.99' '13.99' '2.49' | Target '$2.69' | Wild Fork '14.98' …
derived PRODUCT_NAME → existing LINE_TOTAL  (5)
   Sprouts 'CHIPS' 'LEMONS' 'ORG' | Neighborly 'Meal'
```

A bare currency figure is not a product name; the word `LEMONS` is not a line
total. **Trust the decoder.**

**(b) Where the decoder is merely indifferent — name-span boundaries.** The
single largest disagreement class, `PRODUCT_NAME → QUANTITY` (23):

```
'LB' ×6  'OZ'  'CT'  '2LB'  '1LB'  '12PK'  '13PC' ×2  '6pcs'
'E' 'OG' 'S' ×6           ← Costco/Vons taxability & organic flags
```

These are unit-of-measure and pack-size tokens *inside* the product name, plus
single-letter merchant flags. Reconciliation proves the **name→price pairing**;
it does **not** prove where the name span ends. The decoder sweeps these into
the name because the arithmetic does not care. On `2LB` the existing QUANTITY
label is arguably better; on `E`/`S`/`OG` both sides are wrong.

**Design consequence:** the decoder's *row partition* (which rows are items) and
its *price assignment* are arithmetically warranted. Its *per-token name span*
is not. Train on the first two. For PRODUCT_NAME, treat the derived span as a
span-level target with soft edges, and **do not** use the derived labels to
overturn an existing QUANTITY/UNIT_PRICE label on a unit token.

### 3.4 The training-set bias is real and measurable

Gate pass rate per merchant over all 730 prod receipts:

| merchant | pass | gated | rate |
|---|---:|---:|---:|
| Sprouts Farmers Market | 153 | 38 | 80% |
| In-N-Out Burger | 14 | 2 | 88% |
| Target | 25 | 4 | 86% |
| Vons | 25 | 5 | 83% |
| Costco Wholesale | 27 | 11 | 71% |
| Gelson's Westlake Village | 4 | 3 | 57% |
| Smith's | 4 | 5 | 44% |
| CVS (both spellings) | 3 | 11 | 21% |
| **The Home Depot** | **0** | **18** | **0%** |

The proven cohort is **30% Sprouts, 41% top-3, 48% top-5** across 149 merchants,
and contains **zero Home Depot receipts** — the merchant whose items are
multi-row blocks, i.e. the layout the model most needs to learn. Training on
gate-passing receipts only teaches "receipts that already reconcile".

**Mitigation that costs nothing:** cap per-merchant contribution when building
the training split (e.g. ≤10% from any one merchant), and hold out a
merchant-stratified validation set. The `_load_receipt_allowlist()` hook in
`receipt_layoutlm/receipt_layoutlm/data_loader.py:569` already exists for
exactly this.

### 3.5 One mechanical blocker

`load_datasets(label_status=ValidationStatus.VALID.value)` filters by a
**single** validation status and has **no** `label_proposed_by` filter. The
backfill writes `decoder_reconciled` labels as `PENDING`. So today, training on
them would either see nothing (status VALID) or drag in every unrelated PENDING
label (981 in prod). Either promote them to VALID at write time, or add a
proposer filter to the loader. This is a real decision, not a detail: writing
them VALID means the arithmetic gate is asserted as ground truth with no
further review.

---

## 4. Sections as scope

### 4.1 Sections are already an excellent scope — measured

Prod, VALID labels only, across the 730 receipts that have an ITEMS section:

| label | inside ITEMS | outside | outside % |
|---|---:|---:|---:|
| PRODUCT_NAME | 4,961 | 318 | 6.0% |
| LINE_TOTAL | 1,598 | 95 | 5.6% |
| QUANTITY | 474 | 52 | 9.9% |
| UNIT_PRICE | 437 | 57 | 11.5% |
| DISCOUNT | 188 | 66 | 26.0% |
| SUBTOTAL | 36 | 402 | 91.8% |
| TAX | 34 | 454 | 93.0% |
| GRAND_TOTAL | 32 | 923 | **96.6%** |

| header/footer label | inside ITEMS | outside | inside % |
|---|---:|---:|---:|
| MERCHANT_NAME | 1 | 1,588 | 0.1% |
| STORE_HOURS | 3 | 1,258 | 0.2% |
| ADDRESS_LINE | 40 | 5,248 | 0.8% |
| WEBSITE | 6 | 754 | 0.8% |
| DATE | 18 | 1,108 | 1.6% |
| PAYMENT_METHOD | 59 | 3,384 | 1.7% |
| LOYALTY_ID | 52 | 107 | 32.7% |

Sections separate the two populations at 94–99% on both sides without any
model. `LOYALTY_ID` is the one genuine exception — loyalty numbers really are
printed inside the items block on several merchants; leave it alone.

### 4.2 The "ITEMS over-reach" signal is mostly not over-reach

The brief read the derived-PRODUCT_NAME × existing-WEBSITE (8) and
× existing-PAYMENT_METHOD (7) collisions as ITEMS sections reaching into footer
rows. Pulling the actual words says otherwise:

```
Costco     PAYMENT_METHOD on 'SNAP' 'E' 'NF' 'OG'   ← taxability/organic flags
                                                       on genuine product rows
Vons       WEBSITE on 'S' ×8                        ← a single letter labelled
                                                       WEBSITE is legacy noise
Sprouts    MERCHANT_NAME on 'SPROUTS' (lines 34-44) ← store-brand product rows
Restaurants MERCHANT_NAME on 'Ramen' 'Rice' 'Taco'  ← dish name contains the
           'Frida'                                     merchant name
```

Of the 69 affected receipts, the only convincing boundary error is **East Coast
Bagel Co.**, where `Open` (a STORE_HOURS word) falls inside ITEMS on 6
receipts, plus one DIY Home Center receipt where `4:23PM` does. **~7 receipts of
730, not a systemic section defect.** The other 62 are legacy label errors that
the derived labels are correctly contradicting.

This is still a useful detector — it found real bad labels — but it should be
framed as *label* triage, not *section* triage.

### 4.3 Prod sections and dev sections are different populations

| | prod | dev |
|---|---|---|
| rows | 6,517 over 825 receipts | 5,944 over 813 receipts |
| `model_source` | 99.6% `upload-determinism-v1` | 30+ sources: `section-qa-v2/v3`, `section-knn-v2-gen4`, `section-tail-repair-v3`, … |
| `validation_status` | **6,516 PENDING, 1 VALID** | 5,679 VALID, 248 PENDING, 17 INVALID |
| `swift-worker-v1` | 23 | 23 |

Prod sections are ingest-time deterministic output that has never been reviewed;
dev's are the curated product of the section campaigns. **Any plan that treats
sections as trusted scope must say which table it means.** In prod they are
unvalidated — but §4.1 shows they are *empirically* accurate anyway, which is a
better argument than their status field.

---

## 5. Recommended sequence

Each step states its decision point. Steps are ordered so that each one's
evidence justifies the next; stop wherever the evidence stops.

**Steps 1–3 are the work. Steps 4–5 are optional future experiments and are not
part of this sprint.** Step 3 — wiring `derive_labels` into ingest — is the
sprint's actual deliverable; Steps 1 and 2 are small independent fixes that make
the rest measurable.

> **Step 0 was removed.** An earlier draft opened with "find out why the bundle
> went from 22 classes to 8, and restore `pre-v31-backup.zip` if it was
> accidental". **It was not accidental** — the wide model's product labels were
> not good enough for production, and this sprint is the response. Restoring
> that bundle would revert a deliberate product decision with a single
> `aws s3 cp`, which is why the step is called out here rather than silently
> deleted. **Do not restore it.** What remains from that investigation is three
> genuine bugs, now in §7 as risks 8–10; none of them is about model scope.

### Step 1 — Stop dropping `AMOUNT` and `ADDRESS`

Change `fromLinePredictions` to pass through the active model's declared
vocabulary rather than `coreLabels`, so `_prepare_pending_core_labels`'s
existing `AMOUNT`/`ADDRESS` handling receives input again. The safest form:
allow `coreLabels ∪ {labels the loaded config declares}`, keep dropping `O` and
anything neither set names.

- **Cost:** ~20 lines of Swift plus a contract test. No model work, no infra.
- **Expected movement:** restores ~450 ADDRESS + ~350 AMOUNT proposals/month
  and, downstream, the ~330 `llm_corrected:ADDRESS` + ~490
  `llm_corrected:AMOUNT` CORE labels/quarter that stopped in July.
- **Decision point:** after 20 freshly-ingested receipts, do AMOUNT-derived
  labels land as `llm_corrected:*` CORE labels at ≥60% of the May–June rate? If
  not, the disambiguation path has rotted too and needs its own fix before
  anything else.

> **Correction (see C.6).** This step as originally written implied that
> restoring the pass-through would also restore `propose_line_item_labels`
> (the `geometry_line_items` column in §2.1a). **It will not.** That proposer
> needs a `SUBTOTAL`/`TAX`/`GRAND_TOTAL` label to exist *before* it runs, and
> the only `AMOUNT` disambiguation that has ever worked in production is the
> LLM one, which runs **after** it. The deterministic branch that would have
> run before it has a measured hit rate of **0 out of 585**. So Step 1 recovers
> the merged-class labels and their LLM-disambiguated CORE children, and
> nothing else. `geometry_line_items` will stay at zero under the intended
> narrow-model configuration, because that proposer needs unmerged totals the
> narrow model does not emit. **That is fine and expected** — the decoder owns
> those labels now (Step 3). Judge Step 1 on `llm_corrected:*` yield alone, and
> do **not** treat a flat `geometry_line_items` count as a failed fix.

**Fix the prod bundle key at the same time** (§2.4) or accept that `--env prod`
runs blind.

### Step 2 — Make provenance survive validation

Add a `label_original_proposed_by` (or append rather than overwrite) so
`llm_valid` stops erasing who proposed the label. Without this, every question
in this document had to be answered by inference from label *vocabulary*.

- **Cost:** one entity field, one migration-free write-path change.
- **Decision point:** none — this is unconditional. It is the instrumentation
  that makes steps 3–5 measurable at all.

### Step 3 — Wire `derive_labels` into the ingest path (not just the backfill)

`receipt_upload/receipt_upload/line_items/labels.py` already exists and is
tested. Today only `scripts/backfill_decoder_word_labels.py` calls it. Call it
from the stream-driven line-item stage so every new receipt that reconciles
gets its product labels at ingest.

- **Decision point:** does the rate of receipts landing with ≥1 PRODUCT_NAME
  label go from ~0 to ≥60%? (505/730 = 69% of the prod corpus passes the gate;
  new ingest should be similar or better.) If it lands below 40%, the gate is
  tighter on fresh receipts than on the backfilled corpus and step 4 has no
  fuel.

### Step 4 — *Optional, and not part of this sprint* — retrain LayoutLM with decoder-proven product labels

**Framing.** The narrow model is the intended configuration and the decoder is
the intended product labeller. This step is not a plan to undo that; it is the
one experiment that would be worth running *if* someone later wants to revisit
model scope, and its value is that it changes the input rather than the
architecture. Every previous attempt trained against the corpus; this one would
train against arithmetic. **Nothing downstream depends on it, and Step 3 is the
sprint's actual deliverable.**

Only after step 3 has been running long enough to produce labels on new
receipts, and only with the two guards from §3.3–3.4:

- **Merchant cap** in the training split (≤10% per merchant) and a
  merchant-stratified val set that *includes* Home Depot-style multi-row layouts
  even though they never pass the gate. Use `_load_receipt_allowlist()`.
- **Do not** train PRODUCT_NAME per-token against unit tokens (`LB`, `OZ`,
  `2LB`, `12PK`) or merchant flags (`E`, `S`, `OG`, `NF`). Either exclude those
  tokens from the loss or keep the existing QUANTITY label where one exists.
- Resolve §3.5 first: promote `decoder_reconciled` to VALID, or add a
  `label_proposed_by` filter to `load_datasets`.

The bar to beat is `v30-fullcore` on the **frozen heldout** set at its best
epoch (§2.2): PRODUCT_NAME **0.245**, LINE_TOTAL **0.665**, UNIT_PRICE
**0.328**, QUANTITY **0.359**. Those are the numbers the scope decision was
made on, so they are the numbers a challenger has to clear.

**Do not score against the adversarial val split as it stands.** Per §2.2 it is
drawn from the corpus this whole document argues is unreliable. Build the
val split from **decoder-proven labels held out of training** — receipts that
pass the reconciliation gate but are excluded from the training set — so the
target is arithmetic rather than corpus consensus. That is a prerequisite of
this step, not a refinement of it. Record `heldout_label_*` metrics for whatever
model results, since the active model has none (§2.2) and the comparison is
otherwise impossible.

- **Decision point:** on the proven-label val split, does PRODUCT_NAME F1 clear
  **0.50** and LINE_TOTAL clear **0.75**? **If no, stop, and stop permanently** —
  the decoder is the product extractor of record and the narrow model stays as
  it is. **If yes, that is still not a decision to widen the deployed model** —
  it is evidence worth taking to the owner, who has already judged the wide
  model's output unfit for prod on stronger grounds than an F1 delta. The
  decoder's labels are arithmetically proven; a model that predicts them well is
  a convenience, not a replacement.

### Step 5 — *Also optional* — only if step 4 clears: LayoutLM on the receipts the decoder can't reach

The 225 prod receipts that fail the gate (196 not-matched, 29 no-items) are the
target. Predict there, and gate the predictions on section scope from §4.1
(product labels only inside ITEMS) and on not contradicting a decoder-derived
label where one exists.

- **Decision point:** on a sample of 30 gated receipts adjudicated by hand, is
  precision ≥0.80? Below that, the labels are worse than no labels — they will
  pollute the next training round, which is exactly how the corpus got into its
  current state.

### Cheap, independent, do it whenever

**The disagreement detector from §4.2 as a triage report.** Running
`derive_labels` over the corpus and listing collisions where the derived label
contradicts an existing VALID one found 164 cases, of which ~15 (`4.99`
labelled PRODUCT_NAME, `LEMONS` labelled LINE_TOTAL) are unambiguous corpus
errors and ~7 are real section boundary errors. That is a standing, zero-model,
zero-LLM quality signal over labels *and* sections. It costs one read-only
script run.

---

## 6. What not to build

- **A restore of the 22-class bundle.** The 2026-07-30 swap was a deliberate
  product decision (§2.1a). `pre-v31-backup.zip` is intact in S3 and one
  `aws s3 cp` from the active key, which makes this the easiest wrong thing in
  the whole system to do. Don't.
- **A product-labelling LayoutLM, new or restored.** The frozen-heldout numbers
  (§2.2 — PRODUCT_NAME 0.245 over 939 support) are why the scope was narrowed,
  and four product-only training runs peaked at 0.338. The decoder owns these
  labels. Step 4 exists only as an optional future experiment with a different
  *input*, and it is explicitly outside this sprint.
- **Sections as a model input feature.** They already separate the populations
  at 94–99% (§4.1). Use them as a boolean filter. Nothing to learn.
- **Any LLM pass over line items.** PLAN.md killed this; nothing here changes
  that. The 97.1% agreement figure is not an argument for adding an LLM
  adjudicator — the disagreements split into "decoder right" and "taxonomy
  question", and an LLM helps with neither.
- **Auto-correcting existing labels from derived ones.** Tempting given §3.3(a),
  but §3.3(b) shows 23+ cases where the derived label is worse. Report, don't
  overwrite.
- **A confidence threshold on LayoutLM output.** There is nothing to threshold —
  the model isn't producing low-confidence product predictions, it has no
  product classes.
- **Anything that treats `geometry_line_items` returning to zero as a
  regression.** Under the intended narrow model it cannot fire (§C.6). That is
  the design, not a fault.
- **Reviving `simple_receipt_analyzer`.** It produced the corpus whose UNIT_PRICE
  is 82% INVALID.

---

## 7. Risks

1. **Training on decoder output bakes in decoder bugs.** The `OFF`-substring bug
   (fixed 2026-08-04 in #1369) matched inside `COFFEE`/`TOFFEE`/`Office` and
   misclassified **15 real prod items** as discounts for months — and it had
   propped up a passing test (`test_guard_refuses_a_forced_bad_extension`) for
   the wrong reason. Had a training round run before #1369, the model would have
   learned it, and unlike the decoder the model cannot be fixed by a one-line
   regex change. **Mitigation:** pin the decoder commit in the training job's
   metadata; retrain from scratch after any decoder semantics change; never
   fine-tune incrementally on top of an older decoder's labels.

2. **Proven-only data is biased toward easy receipts.** Measured in §3.4: 30%
   Sprouts, 0% Home Depot, and Home Depot is exactly the multi-row-block layout
   the model most needs. Unmitigated, step 4 will produce a model that is
   excellent on Sprouts and useless where it matters.

3. **The name-span problem is structural, not incidental.** Reconciliation
   proves pairing, not span boundaries (§3.3b). Per-token PRODUCT_NAME training
   on decoder spans teaches the model to swallow `LB`, `2LB`, `12PK` into names.
   If the downstream consumer needs QUANTITY separately, this actively hurts.

4. **Writing `decoder_reconciled` labels VALID asserts arithmetic as truth with
   no review.** Any decoder regression then silently rewrites ground truth
   corpus-wide. Prefer PENDING + an explicit proposer filter in the loader.

5. **`reconciliation_status` is not stable across code versions.** Prod line
   items today: 1,450 match / 252 near / 528 mismatch / 298 no-baseline — and
   PLAN.md's item 1 (the 822-receipt summary regen) is expected to move prod
   mismatch 848 → ~450. The gate population will shift under any measurement
   taken now. Re-measure after that sweep before sizing step 4's dataset.

### Three open bugs, distinct from the model-scope decision

Risks 8–10 came out of the bundle investigation. **None of them is about which
model should be deployed** — they are defects in how the model is delivered and
tracked, and they would matter identically if the narrow model never changed
again.

8. **Prod's configured model key 404s.**
   `layoutlm_model_s3_bucket = layoutlm-training-prod-68164770`,
   `layoutlm_model_s3_key = coreml/layoutlm-coreml-bundle.zip` —
   `head-object` on that key returns **404** (§2.4). Only a loose, unzipped
   `coreml/LayoutLM.mlpackage/` from 2026-05-24 is present. A worker started
   with `--env prod` on a cold cache cannot download a model at all; on a warm
   cache it silently uses whatever is on disk. Every model observation in this
   document is therefore about the *dev* bundle.

9. **The running model is unknown, unrecorded, and probably heterogeneous.**
   `isModelCached()` is an existence-only check — `vocab.txt` + `config.json` +
   any `*.mlpackage` directory — with no ETag, version or manifest comparison
   (§2.4), so a Mac never re-downloads once cached. The cache path is
   cwd-relative `.models/layoutlm` and **not env-scoped**, so `--env dev` and
   `--env prod` launched from one directory share a bundle. Nothing records
   which bundle produced a label. Cohort D is three receipts and may describe
   one machine rather than the fleet. Every claim here about "the deployed
   model" is a claim about S3, not about what is executing.
   **Mitigation:** version-check `isModelCached`, env-scope the cache path, log
   the loaded `label_map` entity count at worker start, and write the bundle
   ETag into the OCR result JSON the worker already uploads (§C.8).

10. **The v31 `COREML_EXPORT` record has been `PENDING` since 2026-07-29
    despite the bundle being live.** Record
    `COREML_EXPORT#14c1d65a-b6c3-4ee7-8a7a-086b40d35ad1`, job
    `a56741b7-…` (`layoutlm-v31-nonproduct-clean-20260729`), created
    `2026-07-29T19:32:18Z`, `status = PENDING` with `completed_at`,
    `bundle_s3_uri`, `mlpackage_s3_uri`, `model_size_bytes` and
    `export_duration_seconds` all null — yet
    `coreml/layoutlm-coreml-bundle.zip` was written 2026-07-30T16:29:40Z. The
    export happened; the record never closed. This is not isolated: **129 of
    144 `COREML_EXPORT` rows are `PENDING`**, 14 SUCCEEDED, 1 FAILED, including
    both v30 rows from 2026-07-14. The export-status table cannot currently be
    used to answer "which bundle is live", which is part of why §2.1a had to be
    reconstructed from S3 object timestamps.

11. **Nothing alarms on "a label class stopped arriving."** The 07-30 scope
    change was intended, but an *unintended* one would look identical in the
    corpus and nothing would flag it. The cohort table in §2.1a is the
    detector — labels-per-receipt by class, by ingest week. It is cheap and
    should be standing regardless of what the model is scoped to.

---

## 8. Reconciling with `SYNTHESIS_CROSSOVER.md`

That note argues synthesis cannot improve the deterministic decoder but can feed
the neural side, and specifically names LayoutLM training data as "the half that
actually learns", most valuable "for label classes that are rare or
systematically mis-labeled in the real corpus (UNIT_PRICE ~82% INVALID,
QUANTITY ~69% INVALID)".

**I agree with its direction and I can now confirm its diagnosis with
independent numbers.** Measured here: UNIT_PRICE 82.4% non-VALID, QUANTITY
68.9% non-VALID in prod — matching its figures almost exactly. And the training
history it did not have access to shows the consequence: 0.304 and 0.363 F1 on
those two classes.

**One disagreement, and it is about priority.** That note reaches for synthesis
to supply clean product labels. The decoder now supplies **6,370 real, arithmetically-
proven product labels on 505 real receipts** — real paper, real OCR noise, real
merchant quirks — for the cost of a script run. Synthetic labels are clean but
render clean, which is the same objection that note itself raises against
synthetic golden data. **Decoder-derived labels should be tried before synthetic
ones**, and synthesis reserved for the classes the decoder genuinely cannot
reach: UNIT_PRICE (only 9 derived labels across 505 receipts — the decoder
almost never identifies a unit price word) and DISCOUNT (29).

That is a narrow, well-defined role for synthesis, and it is a better one than
"supply product labels generally".

---

## Appendix — how each number was obtained

| Claim | Method |
|---|---|
| Label source/label/status/month tables | Full `GSITYPE = RECEIPT_WORD_LABEL` query, both tables (60,105 prod / 60,320 dev rows). Label name parsed from SK (`…#LABEL#<name>`) — it is not a top-level attribute. |
| Deployed model vocabulary | `aws s3 cp s3://layoutlm-training-dev-68164770/coreml/layoutlm-coreml-bundle.zip`, read `label_map.json` / `config.json` |
| Prod bundle 404 | `aws s3api head-object --bucket layoutlm-training-prod-68164770 --key coreml/layoutlm-coreml-bundle.zip` |
| Training F1 and per-class F1 | `mcp receipt-tools list_training_jobs` / `get_active_model`; per-class from `JOB_METRIC` rows at each run's `best_epoch` |
| Hyperparameters / `allowed_labels` | `aws sagemaker describe-training-job` |
| Dry-run gate + agreement figures | `scripts/backfill_decoder_word_labels.py --table ReceiptsTable-d7ff76a --json-out …` at `fb22a2967`, **no `--apply`** (the script constructs no `DynamoClient` without it) |
| Disagreement word texts, merchant bias | per-receipt JSONL from that run, joined to `RECEIPT_SUMMARY.merchant_name` |
| Section ↔ label placement | Full `GSITYPE = RECEIPT_SECTION` query (6,517 prod rows), `line_ids` joined against label `line_id` |
| `#958` / `#1369` / `#1372` attribution | `git log -S`, `git show` |

| Bundle vocabulary history | Range-read `label_map.json` out of each `pre-*-backup.zip` in `s3://layoutlm-training-dev-68164770/coreml/` via the zip central directory — no full downloads |
| Four-cohort experiment | Prod label dump grouped by `min(timestamp_added)` per receipt, LayoutLM-origin rows = `llm_valid`/`llm_needs_review`/`llm_invalid`/`llm_corrected:*` |
| Cache-invalidation behaviour | `ModelDownloader.swift:41,75`; `Config.swift:142` |

**Correction to the first draft.** It stated that per-receipt LayoutLM
predictions before the `coreLabels` filter were "never persisted". **They are.**
The worker uploads the full OCR JSON — including the `layoutlm_predictions`
array — to `s3://<raw-bucket>/ocr_results/*.json` (`OCRWorker.swift:448`).
`join-study-2` pulled these and measured the discard directly: the
`coreLabels` filter drops **19 of 37, 12 of 19, and 18 of 32** non-`O`
predictions on the three 2026-08-04 receipts — roughly half the model's output —
and the surviving set matches the landed DynamoDB rows on exact
`(line_id, word_id, label)` **39/39 in prod and 39/39 in dev**. That is a hard
identity proof of §1.3, superseding the vocabulary-fingerprint inference used
there. Those S3 objects are the right instrument for any future question about
what the model actually predicted.

Still not measured, and stated as such: whether the Mac worker's LayoutLM
inference ever *fails* silently (`VisionOCREngine` catches the error and
`print`s to stdout, which the structured logger does not capture); and which
bundle each physical runner currently has cached (§2.4) — that requires
inspecting the Macs, not S3 or DynamoDB.

---

## Appendix C — primary evidence from the parallel pass

The measurements §2.1a, §2.4 and the four-cohort table were reproduced from a
summary. This appendix records them at full fidelity so they are reproducible,
states one place where the two passes disagree, and closes the two questions
left open above.

### C.1 The `ocr_results` objects — exact method

The Mac worker uploads its complete OCR JSON to
`s3://<raw-bucket>/ocr_results/<image-basename>-<job_id>.json`
(`OCRWorker.swift`, `let resultKey = "ocr_results/\(resultURL.lastPathComponent)"`).
`ReceiptOutput`'s `CodingKeys` (`Models/ReceiptDetection.swift:219`) serialises
`layoutlm_predictions` into it — per receipt, per line, with `tokens`, `labels`
and `confidences`. That array is the model's output **before**
`fromLinePredictions` applies the B-/I- strip, the `O` drop and the `coreLabels`
filter. It is the only place raw predictions are retained.

Buckets: `raw-image-bucket-0facc78` (**prod**), `raw-image-bucket-c779c32`
(**dev**). The six objects read for this study, all from the 2026-08-04 ingest:

```
prod  ocr_results/IMG_3404-721b0a24-f8df-4153-a132-d7a562fb90fb.json
prod  ocr_results/IMG_3411-6d83a877-21a9-48e4-bed4-63dbe91bdd35.json
prod  ocr_results/IMG_3420-2deb7296-2f16-43fe-84f1-975af136c7e3.json
dev   ocr_results/IMG_3404-55dd0ec2-0aab-4e7a-9fd3-512d380be2b1.json
dev   ocr_results/IMG_3411-6268c8cb-8eb0-446d-97ba-f13dbdae0b61.json
dev   ocr_results/IMG_3420-28d91d9d-2783-4b38-b008-d617014c5c68.json
```

### C.2 The full prediction histograms

Raw `labels` counts across all prediction lines, before any filtering:

```
IMG_3404   51 lines, 113 words, 51 prediction lines
  O 76 · I-ADDRESS 8 · B-AMOUNT 8 · B-ADDRESS 3 · I-STORE_HOURS 3
  B-PAYMENT_METHOD 3 · I-PAYMENT_METHOD 3 · B-MERCHANT_NAME 2
  I-MERCHANT_NAME 2 · B-STORE_HOURS 1 · B-DATE 1 · I-DATE 1
  B-TIME 1 · B-WEBSITE 1

IMG_3411   34 lines, 91 words, 34 prediction lines
  O 72 · I-ADDRESS 7 · B-AMOUNT 4 · B-PAYMENT_METHOD 3 · B-ADDRESS 1
  I-PAYMENT_METHOD 1 · B-DATE 1 · B-TIME 1 · B-WEBSITE 1

IMG_3420   53 lines, 113 words, 53 prediction lines
  O 81 · B-AMOUNT 9 · I-ADDRESS 7 · I-STORE_HOURS 4 · B-PAYMENT_METHOD 3
  B-ADDRESS 2 · B-MERCHANT_NAME 1 · I-MERCHANT_NAME 1 · B-STORE_HOURS 1
  I-PAYMENT_METHOD 1 · B-DATE 1 · B-TIME 1 · B-WEBSITE 1
```

| receipt | words | non-`O` predictions | dropped by `coreLabels` | written |
|---|---:|---:|---:|---:|
| IMG_3404 | 113 | 37 | 19 (ADDRESS 11, AMOUNT 8) | **18** |
| IMG_3411 | 91 | 19 | 12 (ADDRESS 8, AMOUNT 4) | **7** |
| IMG_3420 | 113 | 32 | 18 (ADDRESS 9, AMOUNT 9) | **14** |
| **total** | 317 | **88** | **49 — 55.7%** | **39** |

The identity check (§1.3): the surviving set was compared against the
`RECEIPT_WORD_LABEL` rows on the full key `(line_id, word_id, label)`.

| | kept | landed prod / dev | exact key overlap |
|---|---:|---|---:|
| IMG_3404 | 18 | 18 / 18 | **18 / 18** |
| IMG_3411 | 7 | 7 / 7 | **7 / 7** |
| IMG_3420 | 14 | 14 / 14 | **14 / 14** |

Set difference is empty in both directions. `timestamp_added` on every landed
row is `2026-08-05T02:50:32Z`, the second the worker uploaded the OCR object
(`2026-08-04 19:50:33 PDT`), so the rows were **created by the worker** and the
validator overwrote `label_proposed_by` and `validation_status` in place without
touching `timestamp_added`. The only non-LayoutLM rows on those receipts are
dev's 59 `decoder_reconciled` rows, added at `04:36:19Z` by the #1372 backfill.

Note what the two dropped classes are: `AMOUNT` and `ADDRESS` are exactly the
`allowed_labels` the active model was *trained* to emit
(`allowed_labels = MERCHANT_NAME,DATE,TIME,AMOUNT,ADDRESS,WEBSITE,STORE_HOURS,PAYMENT_METHOD`).
The training configuration and the write path disagree about the vocabulary, and
nothing in either codebase compares them.

### C.3 Cohort A — the two passes disagree on the denominator

This pass defines cohort A as receipts whose `min(timestamp_added)` falls in
**2026-06-01 … 2026-06-16** → **45 prod receipts, 1,117 LayoutLM-origin rows**
(ADDRESS 343, AMOUNT 337, PAYMENT_METHOD 172, MERCHANT_NAME 78, TIME 74,
DATE 62, WEBSITE 28, STORE_HOURS 23; 597 VALID / 520 INVALID), yielding 85
product-class and 179 totals-class CORE labels via `llm_corrected:AMOUNT`.

The parallel pass cut at `>= 2026-05` and reports 133 receipts with 771/585 raw
ADDRESS/AMOUNT rows. **Two definitions differ, not one:** the date floor
(2026-06-01 here, 2026-05-01 there) and the LayoutLM-origin predicate — §2.1a
counts `llm_corrected:*` as LayoutLM-origin, this pass counts only
`llm_valid`/`llm_needs_review`/`llm_invalid` and reports the `llm_corrected:*`
rows separately as the *disambiguation* output, since they are rows the
validator **created** rather than rows it re-stamped. **Both are correct for
their own definitions; neither is the "right" one.** The June-only cut is the
tighter control because it sits entirely
after the last product-capable-model change and entirely before #958, so nothing
but the filter differs between it and cohort D. The May-inclusive cut has more
receipts but spans a bundle change. Stated here rather than reconciled, because
the choice of floor is a judgement and the argument does not depend on it: both
windows show the same thing — the 8-class model's `AMOUNT` output reaching CORE
labels through disambiguation, and cohort D showing zero of it.

Cohorts B, C and D agreed between the two passes to within one label.

### C.4 The 9-class transitional bundle — the open item

Exact UTC upload times (`head_object`), which the date-only table above rounds:

```
2026-06-15T18:34:06Z   pre902-backup-20260615.zip    8 classes
2026-06-17T18:39:44Z   pre-v18-backup.zip            8 classes
2026-06-18T03:21:46Z   pre-v21-backup.zip            9 classes
2026-07-14T14:03:57Z   pre-v30-backup.zip           20 classes
2026-07-30T16:14:56Z   pre-v31-backup.zip           22 classes
2026-07-30T16:29:40Z   layoutlm-coreml-bundle.zip    8 classes   ← live
```

Each `pre-vN` archive preserves what `vN` replaced, so the **9-class bundle was
live from 2026-06-17T18:39Z to 2026-06-18T03:21Z — 8 hours 42 minutes.** Its
vocabulary differs from its 8-class predecessor in exactly one respect:

```
8-class:  ADDRESS,      AMOUNT, DATE, MERCHANT_NAME, PAYMENT_METHOD, STORE_HOURS, TIME, WEBSITE
9-class:  ADDRESS_LINE, AMOUNT, DATE, MERCHANT_NAME, PAYMENT_METHOD, PHONE_NUMBER, STORE_HOURS, TIME, WEBSITE
                ▲                                                    ▲
                └── split into two names that ARE in CORE_LABELS ────┘
```

#958 merged **2026-06-16T23:58Z**. Nineteen hours later the deployed bundle
stopped emitting `ADDRESS` and started emitting `ADDRESS_LINE` + `PHONE_NUMBER`
— both `CORE_LABELS` members, both of which survive the new filter. `AMOUNT` was
left merged, and therefore still dropped.

**Reading, offered as inference and labelled as such:** this looks like a
half-adaptation to #958 — the `ADDRESS` half of the filter breakage was worked
around by changing the model's label scheme, the `AMOUNT` half was not. It was
superseded within nine hours by the 20-class line (v21…v29), which unmerges
`AMOUNT` into `SUBTOTAL`/`TAX`/`GRAND_TOTAL`/`LINE_TOTAL` and so sidesteps the
other half by the same route. Not measured: any commit, PR or job note stating
that intent — the training jobs for v18–v20 are not in the last 40 SageMaker
runs, so the inference rests on the vocabulary change and its timing alone.

**Why this history is worth keeping, now that the scope question is settled.**
It shows the `CORE_LABELS` filter has been shaping the *model's label scheme*
for six weeks — vocabularies were chosen partly to get past a write filter
rather than purely on what the model should predict. `v31` returns to the merged
`AMOUNT`/`ADDRESS` scheme on its merits, which re-exposes the same filter
breakage the 9-class bundle was working around. **That is the argument for §5
Step 1**: fix the filter so the label scheme can be chosen on modelling grounds
instead of being constrained by a client-side allow-list nobody is comparing
against `allowed_labels`. It is not an argument about which classes the model
should have.

### C.5 Settling §4.2 — the ITEMS over-reach is legacy, and now cohort-attributed

§4.2 argues the header/footer labels sitting inside ITEMS are mostly legacy
label noise rather than section boundary errors, and asks whether some might
instead be recent LayoutLM output from cohorts B/C. **Measured: they are not.**

Taking every prod header/footer-class label whose `line_id` falls inside its
receipt's ITEMS section (a wider population than §4.2's derived-label
collisions — 794 labels over the 730 receipts with an ITEMS section):

| ingest cohort | labels inside ITEMS | receipts affected | LayoutLM-origin share |
|---|---:|---|---:|
| A — ≤ 2026-06-16 | **742 (93.5%)** | 233 / 711 | 25/742 = **3%** |
| B — 06-17…07-13 | 14 | 10 / 58 | 10/14 = 71% |
| C — 07-14…07-29 | 36 | 5 / 52 | 3/36 = 8% |
| D — ≥ 2026-07-30 | 2 | 1 / 3 | 2/2 = 100% |

Producer breakdown of the 742 cohort-A labels: `label-evaluator-llm` 297,
`simple_receipt_analyzer` 235, `regional_reocr_revalidation` 63,
`claude-header-cleanup` 59 — the legacy analyzer stack, not the model. Across
all four cohorts only **40 of 794 (5.0%)** are LayoutLM-origin, and **582 of 794
(73%) are already `INVALID`**: the corpus has itself rejected most of them.

The per-receipt rate also falls monotonically with cohort — 33% of cohort-A
receipts carry at least one, 17% of B, 10% of C. §4.2's conclusion holds and is
strengthened: this is label triage, not section triage, and the labels it finds
are overwhelmingly pre-#958 analyzer output. The alternative reading is
measurable and small.

### C.6 Why `geometry_line_items` tracks the cohorts — the exact mechanism

§2.1a notes the deterministic proposer's counts (0 / 31 / 54 / 0) are
"consistent with the product labels providing the anchors it needs". The
mechanism is stricter than consistency — it is a hard early return.
`propose_line_item_labels` (`line_items/reconstructor.py:403`) bounds the
line-item band between a header anchor and a totals anchor, where

```python
_TOTALS = {"SUBTOTAL", "TAX", "GRAND_TOTAL"}     # :31
...
totals = [cy(w) for w in placed if raw(w) & _TOTALS]
if not totals:
    return []                                     # :453
```

`AMOUNT` is not in that set and never reaches DynamoDB anyway, so a receipt
labelled by an AMOUNT-merged model has **no** totals anchor and the function
returns empty before doing any geometry. `propose_product_names` then has no
product rows to seed its kNN from, and also yields nothing (`semantic_product_name`:
**0 rows in prod, 0 in dev**).

So the blast radius of the `coreLabels` filter is not one write — it is the
whole deterministic proposal chain downstream of it. Cohorts A and D (0
proposals) versus B and C (31, 54) are that early return firing and not firing.

**There is one way this could have been wrong, and it was checked.**
`_prepare_pending_core_labels` runs at step 1 of `embedding_processor.py`,
*before* the proposer at step 3, and it carries its own **deterministic**
AMOUNT branch — `classify_amount_labels()` (`embedding_processor.py:495`),
which on a hit deletes the AMOUNT row and writes a VALID
`SUBTOTAL`/`TAX`/`GRAND_TOTAL` stamped
`non_core_label_guard:<label>:deterministic`. Had that fired at any rate, it
would have supplied `_TOTALS` anchors before the early return.

**It has never fired.** Zero rows whose `label_proposed_by` contains
`non_core_label_guard` or `:deterministic`, in either table, ever — against
**585 real AMOUNT rows that landed in prod during cohort A**, every one of
which fell through to the LLM fallback and became one of the 505
`llm_corrected:AMOUNT` rows (503 in dev). A **0-of-585** deterministic hit rate.
So the only AMOUNT disambiguation that works in practice is the LLM one, and it
runs after the proposer.

Consequence for §5 Step 1, which has been corrected in place: restoring
`AMOUNT` pass-through alone does **not** make `propose_line_item_labels` fire.
This is not "the ordering suggests it shouldn't work" — it is "it has never
worked, over 585 opportunities".

**Under the intended narrow-model configuration, it never will**, because that
proposer needs an unmerged `SUBTOTAL`/`TAX`/`GRAND_TOTAL` label and the narrow
model emits only merged `AMOUNT`. The remedy is not a wider model — it is that
the **decoder** now owns those labels, which is what §5 Step 3 wires up. Treat
`geometry_line_items` and `semantic_product_name` as **dead paths under the
current design**, not as capabilities to restore; if they are ever revived it
should be by moving the proposer after validation, not by changing the model.

### C.7 Section position predicts label correctness — a free quality signal

§4.1's containment table is VALID-only. Splitting it by `validation_status`
turns it from a description of where labels sit into a **detector**. Prod, all
730 receipts with an ITEMS section, `line_ids` as an exact set-membership test:

| label | % of **VALID** rows inside ITEMS | % of **INVALID** rows inside ITEMS |
|---|---:|---:|
| PRODUCT_NAME | **94.0%** | 63.4% |
| LINE_TOTAL | **94.4%** | 48.5% |
| QUANTITY | 90.1% | 74.2% |
| UNIT_PRICE | 88.5% | 67.2% |
| TAX | 7.0% | **43.4%** |
| SUBTOTAL | 8.2% | 22.0% |
| GRAND_TOTAL | 3.4% | 12.6% |
| ADDRESS_LINE | 0.8% | 5.7% |
| WEBSITE | 0.8% | **11.0%** |
| PAYMENT_METHOD | 1.7% | 8.0% |
| MERCHANT_NAME | **0.1%** | 5.7% |

Every row moves the same direction: labels the corpus has already judged
INVALID are far more likely to be on the wrong side of the ITEMS boundary than
labels judged VALID. That makes "label class disagrees with section position" a
predictor that the corpus itself has already validated, at zero cost. Precision
of the flag, measured against the corpus's own verdicts:

```
MERCHANT_NAME inside ITEMS   184 INVALID /  192  = 95.8%   (1 VALID)
WEBSITE       inside ITEMS   106 INVALID /  115  = 92.2%   (6 VALID)
LINE_TOTAL   outside ITEMS  1069 INVALID / 1177  = 90.8%
PRODUCT_NAME outside ITEMS  1214 INVALID / 1627  = 74.6%
PAYMENT_METHOD inside ITEMS  122 INVALID /  185  = 65.9%
```

**This strengthens §4's "sections as a boolean filter" and §4.2's triage
recommendation, and it needs neither the decoder nor a model.** It is a `Query`
over two entity types. Two uses:

1. **Pre-training filter.** Whatever labels feed the next LayoutLM run, drop
   PRODUCT_NAME/LINE_TOTAL outside ITEMS and header-class labels inside it. On
   PRODUCT_NAME that removes 1,627 rows of which 1,214 are already INVALID.
2. **Standing triage report**, wider than §4.2's derived-label collisions: it
   covers every label on every receipt with an ITEMS section, not just the 505
   that reconcile.

Caveat, stated plainly: this is **circular as an evaluation metric**. The
INVALID verdicts were largely produced by the same LLM stack whose output §3.2
shows is unreliable, and some of them will have been made *because* a reviewer
saw the label in the wrong place. It is sound as a *filter* (it removes rows
that two independent signals both dislike) and unsound as *proof* that either
signal is right. Do not report it as an accuracy number.

For the gate population §3.1 depends on, the stored rows corroborate the dry
run: prod `RECEIPT_LINE_ITEM` is 2,528 rows over 702 receipts —
`reconciliation_status` match 1,450 / mismatch 528 / no-baseline 298 / near 252,
with **492 receipts all-match**; dev is 2,203 rows over 659 receipts, **515
all-match**. The dry run's 505 gate-`ok` of 730 scanned is consistent with 492
stored all-match, the difference being that the gate re-decodes rather than
reading stored status. §7's warning that this population will shift under
PLAN.md's 822-receipt summary regen applies to both numbers.

### C.8 One addition to §5 — make the model attributable

Every cohort in §2.1a had to be reconstructed by *inferring* which bundle was
live from the label vocabulary plus S3 object timestamps. That inference works
today only because one Mac does the ingesting. Given §2.4 — the cache never
invalidates and is not keyed by env — two runners with different caches would
make the corpus unattributable, silently, with no way to reconstruct it after
the fact.

**Cheap fix, no schema change:** write the bundle's S3 ETag (or `num_labels` +
the `label_map.json` hash) into the OCR result JSON the worker already uploads.
It is one field in an object that is already written on every receipt, it needs
no DynamoDB migration, and it turns every future cohort analysis from an
inference into a lookup. Worth doing **before the next bundle promotion of any
kind**, so that promotion's effect is attributable by construction rather than
by argument — this is §7 risk 9, and it is independent of what the model is
scoped to.

It also compounds with §7 risk 10: the `COREML_EXPORT` table cannot answer
"which bundle is live" either, since 129 of its 144 rows are stuck `PENDING`,
including the v31 record for the bundle that has been serving since 2026-07-30.
Between them, the two gaps are why the deployment history in §2.1a had to be
reverse-engineered from S3 object timestamps rather than read from a record.

---

## Contributors

The bundle version history (§2.1a), the four-cohort experiment, the
`ocr_results` prediction histograms and the cache-invalidation finding (§2.4)
were measured independently by the parallel study `join-study-2` and are
reproduced here after independent re-verification. They corrected one
conclusion of the first draft — that pre-filter predictions were unrecoverable
— and produced the deployment history.

**Both studies then got the same thing wrong**, in opposite directions and for
the same reason: neither had access to why the label set was narrowed, and both
reasoned from the artefacts alone. The first draft called the narrow model a
settled design decision on the strength of a val-F1 comparison that turns out
not to be apples-to-apples; the second overturned that and recommended
restoring the wide bundle. The owner's account settled it — the narrowing was
deliberate and correct, and the val-F1 comparison supports neither position.
The frozen-heldout metrics in §2.2 (`PRODUCT_NAME` F1 0.245 over 939 support),
which neither pass had surfaced, are the number that actually justifies the
scope. **Recorded because the failure mode is instructive: two independent
read-only passes over the same corpus can converge on a confident,
well-evidenced, wrong conclusion when the missing input is an intent that
leaves no trace in the data.**

**Appendix C** is that study's primary evidence, written by it: the full
prediction histograms and the prediction↔row identity check (C.1–C.2), its own
cohort-A definition and the one place the two passes disagree (C.3), the
9-class transitional bundle that closes §2.1a's open step (C.4), the cohort
split that settles §4.2's open question (C.5), the exact mechanism behind
§2.1a's `geometry_line_items` counts including the 0-of-585 deterministic
branch (C.6), section position as a label-quality detector (C.7), and one
addition to §5 (C.8). The `0/585` figure and the `non_core_label_guard` check
in C.6 were contributed back by the first study.

Sections 0–8 are the first study's, with two exceptions. The correction block
in **§5 Step 1** was added by the second pass at the first pass's explicit
request after it was stood down, with the original text left intact above it.
The **2026-08-05 revision** described in the header note — §0, §2.1a, §2.2,
the removal of Step 0, §6, §7 risks 8–11, and the corresponding notes in C.4,
C.6 and C.8 — was made by the second pass at the coordinator's direction after
the owner clarified intent. Where the two passes' measurements differ, both
numbers are printed rather than reconciled.

Appendix D lists the per-class and export-record measurements added by that
revision.

---

## Appendix D — measurements added by the 2026-08-05 revision

| Claim | Method |
|---|---|
| v30 frozen-heldout per-class F1 (PRODUCT_NAME 0.245 / n=939, LINE_TOTAL 0.665 / n=407, UNIT_PRICE 0.328 / n=193, QUANTITY 0.359 / n=141) | `JOB_METRIC` rows under `PK = JOB#e5a9a687-…`, metric names `heldout_label_*`, filtered to `epoch = 29` (`best_epoch` from the `JOB` row's `results` map). Distinct from the `label_*` series, which is the validation split and gives PRODUCT_NAME 0.254 / n=939, UNIT_PRICE 0.304 / n=209, QUANTITY 0.363 / n=154, LINE_TOTAL 0.625 / n=418 — the two series must not be mixed. |
| `heldout_windowed_product_detail_macro_f1` 0.399, `heldout_windowed_f1` 0.564 | same query, aggregate metric names |
| v31 has **no** `heldout_label_*` metrics | same query under `PK = JOB#a56741b7-…`: 0 metric names with that prefix; only `heldout_windowed_*` aggregates and the `label_*` validation series |
| v31 has no `val_keys_s3` hyperparameter; v30 does | `aws sagemaker describe-training-job --query HyperParameters` for both jobs |
| `COREML_EXPORT` 129 PENDING / 14 SUCCEEDED / 1 FAILED; v31 record `14c1d65a-…` PENDING since `2026-07-29T19:32:18Z` | `GSITYPE = COREML_EXPORT` query, dev table, all 144 rows |

Not measured: why `COREML_EXPORT` records are left `PENDING` (the export worker
runs on a Mac and its completion path was not traced); and whether any physical
runner currently holds a cached bundle other than the live one — that requires
inspecting the Macs, not S3 or DynamoDB.
