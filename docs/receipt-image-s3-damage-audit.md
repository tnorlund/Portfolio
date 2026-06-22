# Receipt Image S3 Damage Audit

Date: 2026-06-22

## Scope

Audited all live DynamoDB `IMAGE` and `RECEIPT` rows in:

- dev table: `ReceiptsTable-dc5be22`
- prod table: `ReceiptsTable-d7ff76a`

For each row, I checked whether the referenced raw S3 object exists using S3 `HeadObject`. For rows whose raw object was missing, I also checked the Dynamo CDN fields:

- full-size: `cdn_s3_key`, `cdn_webp_s3_key`, `cdn_avif_s3_key`
- thumbnail, small, and medium variants for JPEG/WebP/AVIF

## Summary

| Environment | Entity | Rows | Raw S3 missing | Raw missing rate | Recovery status |
|---|---:|---:|---:|---:|---|
| dev | `IMAGE` originals | 583 | 0 | 0.0% | All original raw uploads exist |
| dev | `RECEIPT` crops | 738 | 160 | 21.7% | All missing raw crops have full CDN copies; all parent originals exist |
| prod | `IMAGE` originals | 490 | 25 | 5.1% | All missing raw originals have full CDN copies |
| prod | `RECEIPT` crops | 649 | 117 | 18.0% | All missing raw crops have full CDN copies |

Across both tables:

- Total rows audited: 2,460
- Row-level missing raw S3 references: 302
- Unique missing raw S3 objects: 192

The row/object difference is because dev and prod share many of the same missing `upload-images-image-bucket-4bcea7e/receipts/...` receipt crop keys.

## Main Findings

The damage is real S3 `404`, not missing Dynamo metadata. All affected rows have bucket/key values.

The biggest issue is missing generated receipt crop raw files under:

```text
s3://upload-images-image-bucket-4bcea7e/receipts/...
```

These are concentrated in May and June 2026:

- dev missing receipt crops: 87 from 2026-05, 69 from 2026-06, 4 from 2026-02
- prod missing receipt crops: 81 from 2026-05, 36 from 2026-06

Prod also has missing original raw upload objects:

- 21 old originals in `s3://raw-image-bucket-0facc78/raw/...` from 2025-02
- 4 newer originals in `s3://upload-images-image-bucket-4bcea7e/raw-receipts/...` from 2026-06

## Recoverability

Every missing raw object checked has CDN artifacts available.

For each missing row, all 12 CDN variants existed:

- full JPEG
- full WebP
- full AVIF
- thumbnail JPEG/WebP/AVIF
- small JPEG/WebP/AVIF
- medium JPEG/WebP/AVIF

That means none of the audited rows are completely image-less. The raw source object may be gone, but a full-size derived image still exists.

## Parent Image Correlation

For missing `RECEIPT.raw_s3_*` rows:

- dev: 160 missing receipt crops, all 160 parent `IMAGE.raw_s3_*` objects exist
- prod: 117 missing receipt crops; 113 parent `IMAGE.raw_s3_*` objects exist, 4 parent raw originals are also missing

The 4 prod receipt rows whose parent raw image is also missing are:

| Date | Image ID | Receipt ID |
|---|---|---:|
| 2026-06-15 | `5578d3d6-d036-421f-8b42-8cc143b62ab4` | 1 |
| 2026-06-15 | `5ff2d6b4-0ca8-4903-a428-e70236f422d3` | 1 |
| 2026-06-15 | `9942d0ed-a516-4d2b-9a99-900481d57767` | 1 |
| 2026-06-15 | `b9800d3c-bd5f-4246-861c-b07ac6247756` | 1 |

Those four still have full CDN copies for both the image row and receipt row.

## Implications For Font Analysis

For font/style clustering, avoid assuming `RECEIPT.raw_s3_*` exists.

Recommended lookup order:

1. Use parent `IMAGE.raw_s3_*` when available, with OCR geometry to crop the text regions.
2. If the parent raw image is missing, use the image full-size CDN object.
3. If analyzing receipt-level crops, use `RECEIPT.raw_s3_*` only when it exists.
4. If `RECEIPT.raw_s3_*` is missing, fall back to the receipt full-size CDN object.

This is enough for the ML-style font clustering work because the current method can use OCR letter/word geometry plus pixel features from whatever full-size image artifact is available.

## Likely Cause Pattern

The missing receipt crops are not randomly distributed. They are all in the newer upload bucket and cluster heavily in May and June 2026. That points toward a recent upload/cleanup/migration issue around generated receipt crop raw objects, not an OCR segmentation issue and not a general S3 permission issue.

## Raw Counts

```text
dev IMAGE:
  records=583
  ok=583
  missing_object=0
  missing_field=0

dev RECEIPT:
  records=738
  ok=578
  missing_object=160
  missing_field=0

prod IMAGE:
  records=490
  ok=465
  missing_object=25
  missing_field=0

prod RECEIPT:
  records=649
  ok=532
  missing_object=117
  missing_field=0
```

