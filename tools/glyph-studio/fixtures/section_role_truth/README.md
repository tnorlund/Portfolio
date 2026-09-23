# Section-role truth (synthesis stage S1)

Hand-adjudicated section roles for printed receipt lines. The label-role
audit (`python -m glyphstudio.label_role_audit`) scores its two classifiers
against these records instead of against each other. Stage S3 of
`docs/plans/SYNTHESIS_UNIFIED_PLAN_2026-09-23.md` retires a merchant's regex
rules only when the label classifier (with regex fallback) matches this
truth at least as well as the regexes do.

## Files

One JSON Lines file per merchant slug, `<slug>.jsonl`, one record per
adjudicated line. Snapshot, pipeline, and corpus lines of the same merchant
share the file.

```json
{"source": "pipeline", "merchant": "cvs", "line_key": "8de52018-6bc6-40fb-8e4e-a880eda24e75#1/t:39,40,41,42,43", "text": "TRAN TYPE: SALE AID: A0000000041010", "role": "payment", "note": "card-slip transaction type + AID", "adjudicated_by": "claude-cloud-2026-09-23", "date": "2026-09-23", "pattern": "item/payment", "context": {"prev": "APPROVED# 45530Z REF# 118078", "next": "DCD5E69 TC: CDE8E2B41 TERMINAL# 84268231"}}
```

| field | meaning |
|---|---|
| `source` | `snapshot`, `pipeline`, or `corpus`. `snapshot` covers both groupings (`--snapshot-grouping line_id` and `overlap`); the `line_key` tells them apart. |
| `merchant` | Merchant slug, same as the file name. |
| `line_key` | Which words make up the line, stable across runs (see below). |
| `text` | The line text when adjudicated. If a later run's text differs, the scorer lists the record as stale. |
| `role` | One of `header`, `item`, `savings`, `summary`, `total_line`, `payment`, `section_header`, `footer`, `survey`, `barcode`, `separator`. `null` when the line cannot be decided (say why in `note`). `other` and `unlabeled` are classifier outputs and never valid truth. |
| `note` | Why, in a few words. Required in practice for `null` and for any call against a rule below. |
| `adjudicated_by` | Who decided. Required whenever `role` is not null. |
| `date` | `YYYY-MM-DD` of the adjudication. |
| `pattern` | *Template only, optional.* `label_role/regex_role` the audit saw when the template was written. The scorer ignores it. |
| `context` | *Template only, optional.* `{"prev", "next"}` neighbouring line texts, to adjudicate without opening the receipt. The scorer ignores it. |

### `line_key`

- Snapshot and corpus words: `<image_id>#<receipt_id>/w:<line_id>.<word_id>,...`
  using the snapshot's `geometry_receipt`. Word ids restart on every OCR
  line, so each is qualified by its `line_id`.
- Pipeline `final.labels.json` tokens: `<receipt_key>/t:<index>,...`.

Ids are sorted, so the key depends only on which words the line holds, not
on their order. A row regrouped by bbox overlap from two OCR lines gets its
own key and needs its own record.

## Workflow

```bash
cd tools/glyph-studio/py
# 1. Write disagreeing lines (label role != regex role) as role:null
#    records, most frequent (label_role, regex_role) pattern first, at most
#    --per-pattern lines per pattern per merchant (0 = all).
python -m glyphstudio.label_role_audit adjudicate-template --per-pattern 20
# 2. Fill `role`, `note`, `adjudicated_by`, `date` in place.
# 3. Score both classifiers and the proposed S3 gate.
python -m glyphstudio.label_role_audit score --truth ../fixtures/section_role_truth
```

Re-running the template never loses work. A line that already has a record
keeps it verbatim, and records that are no longer selected are kept at the
end of the file. Both subcommands take the audit's source flags (`--source`,
`--merchant`, `--snapshot-grouping`, `--corpus-dir DIR` for
`DIR/<slug>/*.json` corpus snapshots).

The template holds only lines where the two classifiers disagree. Wherever
they agree, both score the same, so the comparison between them (and the S3
gate) is unaffected. Absolute percentages therefore describe the contested
lines, not the whole receipt.

## Adjudication rules

A line's role is **the printed block it belongs to**. The question is how
the renderer should style it, not what its words mean in isolation. Decide
from the line text and its neighbours (`context`, or the full receipt from
`python -m glyphstudio.label_role_audit --samples 0`). Word labels and the
regex output are not evidence: they are what is being scored.

**Blocks, in print order**

- `header`: merchant name or logo text, store address, phone, website,
  hours, store number. Also any register, cashier, ticket, loyalty-member,
  or date/time line printed *above the first item*.
- `section_header`: department dividers ("GROCERY", "DAIRY") and
  column-caption rows heading the item block ("Item Qty Price Total").
- `item`: purchased item rows and everything attached to them. That covers
  product names, SKUs/UPCs, qty/weight sublines ("WT 1.91 lb @ $0.69/lb"),
  item prices, and tax flags printed on their own ("E", "A", "T"). It also
  covers per-item fees (CRV / "CA REDEMP VA", bottle deposit, bag fee),
  which add to the bill and are not savings, plus item annotations such as
  age-verification lines.
- `savings`: reductions applied to *this* bill, meaning coupon, discount,
  instant-savings and member-savings lines and their amounts ("3.00-A",
  "Member Savings -0.50"). Also the savings recap ("INSTANT SAVINGS
  $12.30", "YOUR SAVINGS / Total 3.50 / Total Savings Value 5%").
- `summary`: bill arithmetic between the items and the grand total, meaning
  subtotal, net sales, tax lines including the tax-rate line ("A 9.75% Tax
  14.83", "T = CA TAX 9.50000 on $9.28"), and "TOTAL TAX". Item counts
  ("TOTAL NUMBER OF ITEMS SOLD = 21") are `summary` wherever they print.
- `total_line`: the grand-total row ("TOTAL", "BALANCE DUE", "TOTAL DUE",
  "**** BALANCE") and its amount.
- `payment`: the tender block and card slip. That covers tender type,
  masked card number, amount tendered ("AMOUNT: $303.83", "DEBIT $10.78",
  "Visa 61.13", "PAYMENT AMOUNT 61.13"), change due ("CHANGE 0.00"), and
  cash back. It also covers approval, auth, AID, TVR/TSI, TRAN TYPE, entry
  method, and any date/terminal line printed *inside* the card slip.
- `footer`: everything after the tender block that is not survey or barcode.
  That covers thank-you text, return and legal policies, pharmacist notices,
  rewards and points balances, post-tender informational tallies such as
  FSA/HSA eligibility, bounce-back coupons for a *future* purchase, careers
  or ad promos, and the register/transaction trailer.
- `survey`: survey or sweepstakes invitation, its URL, and its user
  id/password.
- `barcode`: the digits printed as a barcode caption.
- `separator`: rule lines of `*`, `-`, `=`.

**Tie-breaks**

- *savings vs summary*: savings when the line reports money taken off this
  bill or totals those reductions. Summary when it is subtotal, tax, or a
  count. A savings recap stays savings even when it contains the word
  "Total".
- *tender vs total_line*: the total row appears once, before the card
  slip. An amount equal to the total but printed beside a tender word or
  inside the card slip is `payment`. When OCR splits amounts into a column
  of bare numbers, match them to their row labels in order (subtotal, tax,
  total, tender, change).
- *register metadata*: header when printed above the first item, payment
  when printed inside the card slip, footer when printed after it.
- *footer boilerplate above the items* (a returns policy printed under the
  store address): header, because it belongs to the header block.
- *deposits vs tax*: `CORE_LABELS` files a bottle deposit under TAX (a
  *word* label). The *role* follows the block. A CRV/deposit row printed
  inline under its item is `item`. A deposit total printed with the tax
  lines is `summary`.
- *loyalty*: a member-id line takes the role of the block it prints in. It
  is `savings` only when it carries a reduction amount. This deliberately
  does not pre-decide plan section 6.3 (`LOYALTY_ID -> savings`).
- *OCR fragments*: a fragment of a printed row (a bare price, a lone tax
  flag, a UPC split from its product name) takes that row's role once the
  row is identified from neighbours or from the same receipt's other
  grouping. If the row cannot be identified, use `null` with a note. Do not
  guess.
