# Receipt Font Analysis Pilot

Date: 2026-06-22

## Scope

Ran the new OCR geometry + raw image pixel font clustering prototype against prod receipt rows whose `RECEIPT.raw_s3_*` objects are known to exist.

Code used:

- `receipt_upload/receipt_upload/font_analysis.py`
- Main function: `analyze_receipt_fonts(...)`

This pilot used receipt-level OCR:

- `ReceiptLine`
- `ReceiptWord`
- `ReceiptLetter`
- raw receipt crop image from S3

## Important Caveat

This does not identify real font family names like Helvetica, Arial, OCR-B, etc. It identifies relative font-style groups from OCR geometry and image pixels:

- relative text size
- condensed/normal/wide glyph shape
- uppercase/numeric/mixed text style
- ink density and crop texture
- similarity between word-level embeddings

To infer actual typeface names, we would need a reference library of known fonts rendered into comparable word crops, then compare receipt clusters against those reference embeddings.

## Section Mapping

The sample did not have persisted `ReceiptSection` entities:

```text
persisted_section_entities_seen=0 out of 30 analysed receipts
```

So section-level results below use a heuristic mapper:

- `header`: top receipt lines and merchant-like top text
- `line_items`: middle lines with price-like values
- `totals_payments`: lines with total/tax/payment/card/cash keywords
- `footer`: bottom receipt lines
- `body_other`: remaining middle/body lines

For production use, this should be replaced with persisted `ReceiptSection` entities or a dedicated section classifier.

## Pilot Results

Prod sample:

```text
analysed_receipts=30
errors=0
total_word_style_samples=2737
total_clusters=445
total_noise_samples=1007
median_samples_per_receipt=77.5
median_clusters_per_receipt=15.0
```

Default clustering radius was `eps=2.2`. That worked but over-segmented receipt crops.

Global top cluster labels:

| Cluster label | Assigned samples |
|---|---:|
| `regular wide mixed` | 815 |
| `regular normal mixed` | 266 |
| `regular condensed mixed` | 213 |
| `regular wide uppercase` | 105 |
| `regular wide numeric` | 48 |
| `small wide mixed` | 43 |
| `small normal mixed` | 34 |
| `large condensed mixed` | 30 |
| `small wide punctuated` | 20 |
| `large condensed uppercase` | 19 |

## Patterns By Section

At `eps=2.2`, section-level assignments looked like this:

| Heuristic section | Samples | Avg size ratio | Avg ink ratio | Avg aspect | Most common style labels |
|---|---:|---:|---:|---:|---|
| `body_other` | 785 | 1.099 | 0.125 | 1.270 | `regular wide mixed`, `regular condensed mixed`, `regular normal mixed`, `regular wide uppercase`, `regular wide numeric` |
| `footer` | 319 | 1.041 | 0.126 | 1.267 | `regular wide mixed`, `regular normal mixed`, `regular condensed mixed`, `small wide mixed`, `small wide punctuated` |
| `header` | 271 | 1.100 | 0.121 | 1.079 | `regular wide mixed`, `regular condensed mixed`, `regular normal mixed`, `regular wide uppercase`, `regular wide numeric` |
| `totals_payments` | 242 | 1.040 | 0.127 | 1.078 | `regular wide mixed`, `regular normal mixed`, `regular wide uppercase`, `regular condensed mixed`, `regular normal uppercase` |
| `line_items` | 113 | 1.022 | 0.112 | 1.193 | `regular wide mixed`, `regular normal mixed`, `small wide mixed`, `large wide numeric`, `small normal mixed` |

Interpretation:

- Body, footer, and line-item text are often the same base receipt font style.
- Header and totals/payment sections show more uppercase/numeric variants, but still often share the same base receipt font.
- Distinct visual styles do show up when present, for example large uppercase recall notices, small punctuated separator rows, numeric price clusters, and authorization/payment footer text.
- Section-level mapping is feasible, but the section labels are currently heuristic.

## Parameter Check

I reran a smaller 10-receipt subset across several DBSCAN radii.

| `eps` | Median clusters/receipt | Median noise samples | Noise rate | Avg cluster section purity |
|---:|---:|---:|---:|---:|
| 2.2 | 14.0 | 31.0 | 0.33 | 0.85 |
| 2.6 | 11.5 | 18.5 | 0.23 | 0.83 |
| 3.0 | 6.5 | 10.5 | 0.17 | 0.82 |
| 3.4 | 5.0 | 9.0 | 0.13 | 0.81 |

Recommendation:

- Use `eps=3.0` for receipt-level crop analysis.
- Keep `eps=2.2` available for finer-grained inspection of one receipt at a time.

`eps=3.0` keeps nearly the same section purity while substantially reducing over-segmentation and noise.

## Example Observations

From one receipt with a recall block:

```text
image=6539deb9-52cc-49a0-81a0-3a64989bee49
receipt=4
lines=83
words=217
letters=1260
samples=207
clusters=19
```

Notable clusters included:

- `regular normal mixed`: main body/receipt text, 102 samples
- `regular wide mixed`: fuel points and footer/body explanatory text, 20 samples
- `small wide mixed`: header/body/totals smaller text, 17 samples
- `small wide punctuated`: separator/star rows, 5 samples
- `large wide uppercase`: `RECALL NOTICE`, 4 samples

This is the clearest evidence that the method can separate distinctive section-specific visual text styles.

## Can We Find Fonts Used In Receipt Sections?

Yes, with the current prototype we can find font-style clusters and attach them to receipt sections.

What works now:

- Find repeated style groups within a receipt.
- Attach each OCR word to a style cluster.
- Summarize which style clusters appear in header, item lines, totals/payment, footer, and body.
- Find visually similar words via embedding similarity.
- Detect distinctive styles such as large uppercase notices, numeric price blocks, small footer/legal text, and separator/punctuation rows.

What does not work yet:

- Exact typeface names.
- Stable cross-merchant global font IDs.
- Reliable section labels without a real section source.

## Recommended Next Step

Run a full prod pass using:

```python
analyze_receipt_fonts(
    receipt_letters,
    words=receipt_words,
    lines=receipt_lines,
    raw_image=receipt_image,
    eps=3.0,
    min_samples=2,
    min_letters_per_sample=2,
)
```

For image loading, use this order:

1. `RECEIPT.raw_s3_*` if the raw receipt crop exists.
2. receipt full-size CDN image if the receipt raw crop is missing.
3. parent `IMAGE.raw_s3_*` plus receipt geometry when analysing from the original uploaded photo.
4. parent image full-size CDN fallback if the parent raw image is missing.

For section mapping, prefer persisted `ReceiptSection` entities if they are available. If not, use a receipt section classifier before treating section-level font assignments as production-quality.

