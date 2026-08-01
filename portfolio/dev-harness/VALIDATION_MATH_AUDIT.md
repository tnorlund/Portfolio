# Validation Harness Math and Automation Safety Audit

Status: review guidance, not an automatic validation contract

Evidence snapshot: 2026-07-31, `codex/geometric-reader` at `a81552b`

Scope: the local `/dev/validation` harness and the receipt section/item math
that it exposes

## Executive conclusion

Merchant-level alignment is useful for ranking and explaining review targets,
but the available evidence does not support automatically copying section
boundaries from one receipt to every receipt from the same merchant.

The current system combines:

1. OCR-line grouping into visual rows;
2. a learned semi-Markov row-to-section decoder;
3. merchant-specific priors with a global fallback; and
4. item-sum reconciliation against printed summary figures.

Each layer is useful, but none independently proves that an `ITEMS` boundary
is correct. The safest near-term use is an abstaining suggestion system:
automatically surface structural and arithmetic discrepancies, propose a
template-relative boundary only when the evidence is strong, and leave the
final confirmation to the reviewer.

## Current math

### 1. OCR lines to visual rows

For OCR lines `i` and `j`, let `c_i` be line `i`'s vertical centroid and let
`[y_j_min, y_j_max]` be line `j`'s vertical bounding-box span. The current
same-row relation is:

```text
same_row(i, j) =
    c_i in [y_j_min, y_j_max]
    OR
    c_j in [y_i_min, y_i_max]
```

Union-find turns all connected lines into one visual row. This is implemented
in `receipt_chroma/receipt_chroma/embedding/formatting/line_format.py`.

Important consequence: centroid overlap is not inherently transitive, but
connected-component grouping makes it transitive. If A overlaps B and B
overlaps C, all three become one row even when A and C are far apart. A tall
or malformed OCR box can therefore bridge many physical rows.

### 2. Row features

Rows are sorted top-to-bottom. For row index `i` among `n` rows, the section
decoder uses:

```text
position_i       = i / (n - 1)              # 0.5 when n == 1
x_span_i         = x_max - x_min
alpha_ratio_i    = letters / max(letters + digits, 1)
has_amount_i     = 1 when amount evidence exists, else 0
amount_density_i = mean(has_amount) in a radius-two row window
has_quantity_i   = 1 when a quantity pattern is detected, else 0
tokens_i         = normalized lexical evidence
```

`position_i` is ordinal row rank, not geometric page Y. Adding or removing
item rows changes the position of every row below them, even if the printed
layout is otherwise identical.

The implementation is in
`receipt_upload/receipt_upload/section_assignment.py`.

### 3. Section emission and segment score

For row `i` and section state `s`, the local emission score is the sum of:

```text
E(i, s) =
    sum GaussianLogScore(numeric_feature_i | section_s)
  + sum BernoulliLogScore(binary_feature_i | section_s)
  + sum token_log_odds(token_i, section_s)
```

For a contiguous segment from row `a` through row `b` assigned to section
`s`, the semi-Markov decoder scores:

```text
Segment(a, b, s) =
    sum(E(i, s), i = a..b)
  + GaussianLogScore(log(b - a + 1) | duration_s)
  + log(transition(previous_section, s))
```

Dynamic programming selects the highest-scoring full receipt path, including
start and end transitions.

Merchant selection uses an exact normalized merchant key. Normalization
case-folds the name and strips punctuation, but it does not resolve business
aliases or template variants. A missing exact key falls back to the global
model.

### 4. Reported confidence

The persisted row confidence is a softmax over that row's local emission
scores:

```text
confidence(i, chosen_s) =
    exp(E(i, chosen_s)) / sum(exp(E(i, all_s)))
```

It is not the posterior probability of the decoded sequence, the probability
of the section boundary, or the margin between the best and second-best full
paths. Averaging these local values across a section does not provide a
calibrated boundary confidence.

### 5. Item reconciliation

The reconciliation baseline is:

```text
baseline = subtotal
baseline = grand_total - tax   # only when subtotal is absent
```

The item sum is the rounded sum of extracted non-discount item prices:

```text
item_sum = round(sum(non_discount_item.price), 2)
diff     = abs(item_sum - baseline)
```

Classification is:

```text
match    when diff <= max($0.02, 1% of baseline)
near     when diff <= max($1.00, 10% of baseline)
mismatch otherwise
```

The implementation is in
`receipt_upload/receipt_upload/line_items/geometry.py`.

The section-repair utility searches candidate combinations of at most six
priced bands for the first subset whose price sum closes the gap within the
`match` tolerance. It does not require that the closing subset be unique.

## Evidence snapshot

### Section coverage and row alignment

The local dev row-backfill snapshot contains:

| Measure | Value |
| --- | ---: |
| Receipts | 805 |
| Visual rows | 28,372 |
| Sectioned rows | 18,775 (66.2%) |
| Unsectioned rows | 9,597 (33.8%) |
| Receipts with at least one unsectioned row | 791 (98.3%) |
| Receipts with zero sections | 9 |
| Straddled rows resolved by majority vote | 57 |
| Section entities emptied and deleted | 66 |

The 66 deleted entities included 17 `TOTAL_LINE`, 11 `BARCODE`, 9
`STOREFRONT`, and 8 `SUMMARY` sections. Row atomicity reached 100% after the
backfill, but that metric only proves that each visual row has one owner. It
does not prove that the selected owner is semantically correct.

The straddle log contains direct evidence of connected-component collapse:

| Straddle property | Value |
| --- | ---: |
| Median section-voted lines in a straddled row | 6 |
| 95th percentile | 34 |
| Maximum | 124 |
| Rows with more than 10 voted lines | 18 of 57 |

The 124-line row spans `ITEMS`, `SUMMARY`, `TOTAL_LINE`, `PAYMENT`, and
`FOOTER`; majority voting assigns the entire row to `ITEMS`. This is a
geometry failure that merchant-template consensus would otherwise amplify.

### Merchant-prior support

The committed section-prior model contains 89 merchant-specific priors:

| Merchant receipt support | Merchant count |
| --- | ---: |
| Fewer than 5 receipts | 52 (58.4%) |
| Fewer than 10 receipts | 77 (86.5%) |
| Minimum | 2 |
| Median | 4 |
| Maximum | 201 |

Exact-key selection also splits related identities and formats, including
`cvs` / `cvs pharmacy`, `target` / `target grocery`, and two Wild Fork
variants. Some of these may be real template families rather than aliases;
either way, merchant name alone is not a sufficient template identifier.

Among merchants with an `ITEMS` model, the median standard deviation of the
row-position feature is 0.140. On a typical 33-row receipt, that covers about
4.5 row ranks. This is not a direct boundary-variance measurement—it includes
all rows inside the item block—but it shows that the learned position feature
is too broad to certify a one-row boundary by itself.

### Committed section ground truth

The committed real-receipt section fixture currently contains two cases and
48 labeled rows. Scored as ordinary predictions:

| Metric | Result |
| --- | ---: |
| Overall row agreement | 38 / 48 (79.2%) |
| `ITEMS` recall | 7 / 11 (63.6%) |
| `SURVEY` recall | 0 / 3 (0%) |

The section evaluator's intended acceptance thresholds are 80% overall and
70% `ITEMS` recall. This two-case fixture is too small to estimate corpus-wide
accuracy, but it proves that meaningful errors remain. Its regression test
pins the current deterministic predictions, including known mismatches; it
does not require all labeled rows to be correct.

### Conditional line-item ground truth

The line-item golden fixture contains 33 receipts, 18 merchant strings, and
202 true non-discount items. Running the current extractor with the fixture's
already-curated `items_line_ids` gives:

| Metric | Result |
| --- | ---: |
| Item recall | 88.6% |
| Item precision | 86.1% |
| Exact name among matched items | 67.0% |

These results validate extraction after an `ITEMS` line set has been supplied.
They do not validate merchant alignment or section-boundary selection.

The regression floors are deliberately permissive for known hard formats,
including 40% recall for Costco, 50% precision for Home Depot, 10% exact names
for Target, and 50% precision for Whole Foods. A green test suite therefore
means the current measured floor did not regress, not that the output is ready
for unattended validation.

## Failure modes to detect before manual review

### Geometry failures

- connected visual rows with excessive line counts;
- rows whose vertical span is an outlier relative to neighboring rows;
- transitive chains where the first and last member do not directly overlap;
- rows spanning multiple semantic anchors or multiple receipt bodies;
- sections made from non-contiguous row runs;
- overlapping sections or section rows absent from the image geometry.

### Template failures

- insufficient receipts for a merchant-template family;
- more than one common section sequence for the same merchant;
- merchant aliases or location suffixes splitting support;
- template-version changes over time;
- receipts whose anchor order or boundary offsets are outliers;
- receipts with missing image, rows, sections, or items. These are review
  targets, not records to exclude from denominators.

### Arithmetic failures

- missing, invalid, or non-positive subtotal baselines;
- `grand_total - tax` contaminated by tips, fees, deposits, discounts,
  gratuity, or rounding;
- a real item coincidentally matching subtotal, total, or tax;
- multiple candidate subsets that close the same reconciliation gap;
- a small missing item hidden inside the 1% `match` tolerance;
- a materially incomplete section accepted by the 10% `near` tolerance;
- discount treatment that disagrees with whether the printed subtotal is
  pre-discount or post-discount;
- OCR price errors that make an incorrect boundary appear arithmetically
  consistent.

For example, a $250 baseline permits a $2.50 discrepancy as `match` and a
$25 discrepancy as `near`. Those tolerances are useful for triage but are not
proof of exact line-item coverage.

## Recommended alignment model

### 1. Validate row geometry first

Do not run template alignment across a suspicious connected component. Build
a row-quality gate using direct overlap, component size, vertical span, and
neighbor spacing. Split or abstain on chained components before assigning any
section.

### 2. Learn merchant-template families

Cluster within merchant identity using observable structure rather than
forcing one model per normalized name. Useful clustering evidence includes:

- ordered semantic-anchor signature;
- presence and relative order of subtotal, total, tender, survey, and barcode;
- price-column location;
- normalized receipt width and row spacing;
- stable header/footer tokens; and
- time or location variant when it explains a real printer format.

Merchant aliases should share candidate families, but receipts should only be
pooled after their structural signatures agree.

### 3. Align relative to anchors

Use stable semantic anchors instead of whole-page row rank. For an anchor row
`a`, represent a candidate boundary row `b` as:

```text
offset = b - a
```

Learn separate robust distributions for:

- `ITEMS` start relative to a header or first priced product row;
- `ITEMS` end relative to subtotal/total;
- summary start relative to subtotal;
- payment start relative to printed total; and
- footer start relative to tender or transaction metadata.

Within a template family, use median offsets and median absolute deviation
(MAD), not means alone. Item count is variable, so the lower `ITEMS` boundary
relative to subtotal is generally more stable than its absolute page
position.

### 4. Require unique arithmetic closure

Arithmetic should be a veto and confirmation signal, not a section generator
by itself. For automatic acceptance:

1. require `match`, never `near`;
2. enumerate all eligible closing subsets within the configured limit;
3. require exactly one closing subset;
4. reject candidates containing settlement, total, tax, tender, or tip rows;
5. require no overlap with another section; and
6. require agreement across the available truth chain:
   `items sum -> subtotal -> printed total -> bank amount`.

Missing figures should cause abstention or an explicitly weaker review state,
not silent agreement.

### 5. Measure sequence uncertainty

Replace local-emission confidence for review decisions with a sequence-aware
quantity, such as:

- best-path versus second-best-path margin;
- forward-backward row/section posterior;
- boundary posterior mass within plus/minus one row; or
- leave-one-receipt-out empirical boundary accuracy for the template family.

Confidence should be calibrated against held-out receipts and should expose
both error rate and coverage after abstention.

## Proposed conservative auto-acceptance gate

The following values are starting policy proposals, not measured guarantees:

```text
template receipts                         >= 10
modal structural-signature share          >= 80%
leave-one-out exact boundary accuracy      >= 95%
boundary MAD                              <= 1 row
visual-row geometry gate                  pass
arithmetic status                         match
number of arithmetic-closing subsets      == 1
section overlap                           none
settlement-token contamination            none
truth-chain discrepancies                 none where figures exist
```

Any failed or unavailable gate should produce an abstention with a specific
failure-mode chip in the review harness.

## Evaluation required before automation

The next evaluation should be receipt-held-out and template-held-out. Report:

- `ITEMS` row precision and recall;
- exact start-boundary and end-boundary accuracy;
- boundary accuracy within one row;
- receipt-exact section accuracy;
- item precision, recall, and exact-name accuracy after predicted boundaries;
- arithmetic false-accept and false-reject rates;
- calibrated confidence or path-margin buckets;
- abstention coverage; and
- results by merchant-template family, including missing-data targets.

Overall row agreement alone is insufficient because large sections can hide
small but consequential boundary errors. `ITEMS` precision is especially
important: including a subtotal or tender row can still yield plausible item
output while corrupting the ledger.

## Harness presentation

The local review harness can safely expose this analysis before any write:

- merchant-template family and support count;
- suggested boundary plus anchor-relative offset;
- boundary median, MAD, and held-out accuracy;
- best/second path margin;
- number of arithmetic-closing subsets;
- row-geometry warnings;
- section contiguity and overlap warnings;
- four-figure truth-chain status; and
- a concise abstention/failure-mode reason.

This supports keyboard-first review without hiding missing or malformed
receipts. Confirmation remains a human action until the proposed evaluation
and acceptance gates are satisfied.
