# Receipt Letter Font Chroma Pilot

Date: 2026-06-22

## Run Summary

- Dynamo table: `ReceiptsTable-d7ff76a`
- Local Chroma DB: `/tmp/receipt-letter-font-chroma-pilot-rich`
- Collection: `letter_font_glyphs`
- Analysed receipts: 12
- Embedded letters stored in Chroma: 6505
- Letter style clusters: 8
- Noise letters: 916 (14.1%)
- Median letters per receipt: 463.5
- Median letter clusters per receipt: 1.0
- Line signatures: 526
- Line style clusters: 25
- Noise lines: 227 (43.2%)
- Assigned lines: 299
- Median line clusters per receipt: 4.5
- Letter cluster section purity: 45.5%
- Line cluster section purity: 54.5%
- Letter DBSCAN `eps`: 0.15
- Letter DBSCAN `min_samples`: 5
- Line DBSCAN `eps`: 0.85
- Line DBSCAN `min_samples`: 2
- Errors: 0
- Metrics per letter: 64
- Raw vector dimension: 316
- Style vector dimension: 333
- Metrics per line: 218
- Line vector dimension: 884

## Line Threshold Sweep

| Line eps | Clusters | Assigned lines | Noise | Section purity | Median clusters/receipt |
|---:|---:|---:|---:|---:|---:|
| 0.58 | 10 | 125 | 76.2% | 58.4% | 2 |
| 0.70 | 13 | 176 | 66.5% | 59.1% | 2.0 |
| 0.85 (selected) | 25 | 299 | 43.2% | 54.5% | 4.5 |
| 1.00 | 11 | 431 | 18.1% | 47.6% | 1.5 |

## Feature Inventory

Each letter embedding combines deterministic visual features before any clustering:

- normalized glyph bitmap
- OCR character-centered residual vector
- bbox size/aspect and OCR confidence
- ink density and content coverage
- edge density and horizontal/vertical transition rates
- row and column projection bins
- HOG-style unsigned gradient orientation bins
- stroke-width run estimates
- top/bottom/left/right contour profile stats
- horizontal and vertical symmetry
- connected component count and largest component ratio
- enclosed hole count and hole area ratio

Each line signature then aggregates those letter features with line geometry, letter spacing, word/letter counts, OCR character mix, dominant letter-cluster mix, and averaged letter style-vector centroids/spread.

## Chroma Neighbor Check

- Same-character queries checked: 200
- Top-1 same-cluster rate: 97.0%
- Top-3 any same-cluster rate: 99.5%

The neighbor check queries Chroma with a letter style vector, filtered to the same OCR character, and checks whether nearest neighbors share the discovered style cluster.

## Letter Section Patterns

| Section | Assigned letters | Avg height | Avg ink | Avg edge | Top style labels |
|---|---:|---:|---:|---:|---|
| `body_other` | 2542 | 0.0361 | 0.155 | 0.328 | `large wide medium` (2450), `small wide medium` (34), `small wide dark` (28), `small wide light` (23), `regular wide medium` (7) |
| `footer` | 1073 | 0.0285 | 0.145 | 0.312 | `large wide medium` (1032), `small wide medium` (17), `small wide dark` (10), `small wide light` (10), `regular wide medium` (4) |
| `header` | 958 | 0.0422 | 0.135 | 0.292 | `large wide medium` (944), `small wide medium` (8), `small wide light` (5), `regular wide medium` (1) |
| `totals_payments` | 793 | 0.0331 | 0.155 | 0.332 | `large wide medium` (777), `small wide light` (8), `small wide medium` (5), `regular wide medium` (3) |
| `line_items` | 223 | 0.0235 | 0.147 | 0.322 | `large wide medium` (222), `regular wide medium` (1) |

## Line Section Patterns

| Section | Assigned lines | Dominant cluster share | Avg line height | Avg letter height | Avg ink | Gap/height | Top line labels |
|---|---:|---:|---:|---:|---:|---:|---|
| `body_other` | 138 | 68.1% | 0.0657 | 0.0426 | 0.151 | 0.000 | `large-line wide medium tight` (94), `regular-line wide medium tight` (27), `small-line wide medium tight` (13), `regular-line normal medium tight` (3), `large-line normal light tight` (1) |
| `totals_payments` | 49 | 71.4% | 0.0508 | 0.0307 | 0.151 | 0.000 | `large-line wide medium tight` (35), `regular-line wide medium tight` (12), `large-line normal light tight` (2) |
| `footer` | 49 | 65.3% | 0.0517 | 0.0349 | 0.153 | 0.000 | `large-line wide medium tight` (32), `small-line wide medium tight` (8), `regular-line wide medium tight` (8), `regular-line normal medium tight` (1) |
| `header` | 42 | 76.2% | 0.0797 | 0.0453 | 0.166 | 0.000 | `large-line wide medium tight` (34), `regular-line wide medium tight` (6), `small-line wide medium tight` (1), `regular-line normal medium tight` (1) |
| `line_items` | 21 | 42.9% | 0.0232 | 0.0224 | 0.152 | 0.000 | `regular-line wide medium tight` (9), `large-line wide medium tight` (9), `regular-line normal medium tight` (2), `small-line wide medium tight` (1) |

## Largest Letter Style Clusters

| Cluster | Label | Letters | Chars | Sections |
|---:|---|---:|---|---|
| 1 | `large wide medium` | 5425 | `e` `a` `*` `0` `o` `t` `r` `i` | `body_other` (2450), `footer` (1032), `header` (944), `totals_payments` (777), `line_items` (222) |
| 6 | `small wide medium` | 56 | `d` `a` `e` `D` `A` `p` `r` `0` | `body_other` (29), `footer` (14), `header` (8), `totals_payments` (5) |
| 7 | `small wide light` | 46 | `T` `l` `I` `t` `,` `C` `f` `A` | `body_other` (23), `footer` (10), `totals_payments` (8), `header` (5) |
| 3 | `small wide dark` | 30 | `*` `4` | `body_other` (22), `footer` (8) |
| 8 | `regular wide medium` | 10 | `d` `e` `4` `A` `E` `R` `b` `g` | `body_other` (5), `totals_payments` (2), `footer` (2), `header` (1) |
| 4 | `small wide dark` | 8 | `*` `4` | `body_other` (6), `footer` (2) |
| 5 | `small wide medium` | 8 | `e` `g` `8` `B` `p` | `body_other` (5), `footer` (3) |
| 2 | `regular wide medium` | 6 | `e` `9` `g` `h` `o` | `body_other` (2), `footer` (2), `totals_payments` (1), `line_items` (1) |

## Largest Line Style Clusters

| Cluster | Label | Lines | Sections | Examples |
|---:|---|---:|---|---|
| 9 | `large-line wide medium tight` | 202 | `body_other` (94), `totals_payments` (35), `footer` (32), `header` (32), `line_items` (9) | Cafe does not cover; additional; costs for |
| 2 | `regular-line wide medium tight` | 38 | `body_other` (20), `totals_payments` (7), `footer` (4), `line_items` (4), `header` (3) | 180 Promenade Way; Ordered:; ESPRESSO |
| 3 | `small-line wide medium tight` | 8 | `body_other` (5), `footer` (2), `header` (1) | Server: Lachlan B; C (EMV Chip Read); Food & Drinks: For |
| 20 | `small-line wide medium tight` | 5 | `footer` (5) | ****************************************; ****************************************; ********** |
| 6 | `regular-line wide medium tight` | 3 | `line_items` (3) | $4.80; $0.96; $21.00 |
| 13 | `regular-line wide medium tight` | 3 | `body_other` (3) | ************2777; ********************; + Tip:: |
| 17 | `large-line normal light tight` | 3 | `totals_payments` (2), `body_other` (1) | TOTAL:; GRATUITY:; TOTAL: |
| 24 | `regular-line normal medium tight` | 3 | `body_other` (2), `header` (1) | Main St Provisions; Las Vegas, NU 89109; Server: Makella |
| 1 | `large-line wide medium tight` | 2 | `header` (2) | Lava; band |
| 4 | `regular-line wide medium tight` | 2 | `totals_payments` (2) | Subtotal; Subtotal |
| 5 | `regular-line wide medium tight` | 2 | `totals_payments` (2) | Total; Total |
| 7 | `small-line wide medium tight` | 2 | `body_other` (2) | items, please; item, please email |

## Interpretation

This end-to-end pilot proves the mechanics work: individual OCR letter crops can be embedded, persisted in a separate Chroma DB, queried by same-character nearest neighbors, clustered into visual style groups, and aggregated into line-level font signatures.

Line-level signatures are the better unit for section inference because they carry stable typography and layout context. The purity scores above compare how often each discovered cluster is dominated by a single heuristic receipt section.

It is not yet an exact font-family recognizer. The current output is relative font/style clustering: size, width, stroke darkness, and visual similarity. Exact font names would require a reference library of rendered fonts and a calibration step against that library.

## Next Improvements

- Tune `eps` per dataset or switch to HDBSCAN when available.
- Prefer persisted `ReceiptSection` entities over the heuristic line classifier used in this pilot.
- Add a reference-font corpus to map clusters to likely font family names.
- Use line signatures as features for a supervised section model once section labels exist.
