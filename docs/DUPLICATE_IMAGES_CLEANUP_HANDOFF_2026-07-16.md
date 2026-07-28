# Duplicate image cleanup handoff (dev)

**Generated:** 2026-07-16 20:34 UTC  
**Env:** portfolio dev DynamoDB `ReceiptsTable-dc5be22` (us-east-1)  
**Status:** Detected only — **no bulk delete run yet**. Another agent should confirm keep/drop then clean.

---

## Goal

Find **byte-identical raw images** that were ingested more than once under different `image_id` UUIDs, then delete the extra copies so the IMAGE → RECEIPT hierarchy is not polluted with re-uploads.

This is **not** the same as:
- **Over-segmented receipts** on one image (fix with `merge_receipts`)
- **Same merchant, different visits** (different raw bytes / different order numbers)

---

## Hierarchy reminder

```
IMAGE#{image_id}          ← UUID assigned at upload; re-upload of same file = NEW uuid
  IMAGE                   (root row)
  RECEIPT#00001 / #00002  …
    LINE / WORD / LABEL / PLACE / SUMMARY
  raw:  s3://raw-image-bucket-c779c32/raw/{image_id}.…
  cdn:  s3://sitebucket-ad92f1f/assets/{image_id}…
```

Duplicates share the **same raw object bytes** (same S3 ETag) but different `image_id` partitions.

---

## Detection method

1. Query Dynamo `GSITYPE` where `TYPE = IMAGE` (all image root rows).
2. For each row, `HeadObject` on `raw_s3_bucket` + `raw_s3_key`.
3. Group by raw **ETag** (for single-part uploads this is effectively content MD5).
4. Any ETag with **≥2 distinct `image_id`s** is a duplicate group.

**Caveats:**
- 21 images had raw head failures (`ClientError`) — not included in etag groups (no CDN-only dups found among those).
- Same *transaction* photographed twice with different files will **not** appear here (different ETags). Example: Neighborly order `689410` exists as both `8362b52a` and `3cd5a28e` with **different** raw ETags — not in this list.
- Multipart ETags (rare) would not be pure MD5; none observed as multi-image groups here.

**Reproduce:**

```bash
# Report artifact from discovery run:
cat /tmp/duplicate_images_report.json
# Or re-run the fleet etag head + group script from session notes.
```

---

## Summary stats

| Metric | Count |
|--------|------:|
| Total IMAGE rows | 663 |
| With raw ETag | 642 |
| **Duplicate groups** | **26** |
| Images involved in a group | 58 |
| **Extra copies** (n−1 per group) | **32** |

Group size histogram: 21 groups of 2, 4 groups of 3, 1 group of 4.

---

## Cleanup policy (recommended)

Per ETag group:

1. **Keep one** image_id — prefer:
   - Highest label quality / most complete RECEIPT tree (VALID labels, place, summaries)
   - Non-zero `receipt_count` and real merchants (avoid `rc=0` empty reprocesses)
   - If still tied: **earliest** `timestamp_added`
2. **Delete the rest** with MCP `delete_image` (or `DynamoClient.delete_image_details`) then remove that image’s S3 raw + CDN prefix keys only (do **not** delete the keep image’s keys).
3. Dry-run each delete first; verify keep image still has RECEIPT_WORD / place after.
4. Do **not** merge across image_ids — merge only works for two `receipt_id`s on the **same** image.

### Suggested keep heuristic (scriptable)

```
score(image) =
  +1000 if receipt_count > 0
  + 100 * count(VALID RECEIPT_WORD_LABEL)
  +  50 * count(RECEIPT_PLACE)
  +  10 * count(RECEIPT_WORD)
  -  age_rank  # optional: prefer older or newer — pick one and stick to it
keep = argmax(score); drop = others
```

---

## Related Neighborly context (recent session)

- Cookie visit **order 160065** = ETag group `db053e5447858199…` (**4 images**). Header+money splits were merged **within** `5988b5be` and `527ab69d` (→ rid 4 each). Those two remain **duplicate images** of each other and of `d5a15b22` / `e296ec27`.
- Mini Kabob **order 944834** = ETag group `a530cdac85ff7b93…` (`36f99370` + `22a614d3`).
- Order `689410` (Armen wrap + pesto) is a **same-visit different photo** pair — **not** in the ETag list; decide separately if you want only one training image.

---

## All duplicate groups

Sorted by group size (desc), then earliest upload.

For each group: **KEEP** is a suggestion only (earliest non-empty when obvious); confirming agent must validate before delete.

### Group 1 — n=4 — raw size 1,972,419 bytes

- **raw_etag:** `db053e5447858199987aab8cda0c7fa4`
- **Suggested KEEP:** `e296ec27-4200-4880-a2c4-06ecb1b12290` (rc=3.0, ts=2026-02-20T04:34:53, merchants=['Whole Foods Market', 'Sushi & Wasabi', 'Neighborly'])
- **Suggested DROP (3):**
  - `d5a15b22-d73e-4cec-b3bd-18ebb79a19b3` — ts=2026-02-19T04:36:59, type=SCAN, rc=2.0, merchants=['?', 'Whole Foods Market', 'Neighborly']
  - `5988b5be-335f-4dbe-a826-1335e72a488c` — ts=2026-02-20T04:25:36, type=SCAN, rc=2.0, merchants=['Neighborly', 'Whole Foods Market']
  - `527ab69d-ba7a-403c-a42c-e37596cc7d98` — ts=2026-02-20T06:48:11, type=SCAN, rc=2.0, merchants=['Whole Foods Market', 'Neighborly']

| image_id | timestamp_added | type | receipt_count | merchants | raw_key |
|----------|-----------------|------|---------------|-----------|---------|
| `d5a15b22-d73e-4cec-b3bd-18ebb79a19b3` | 2026-02-19T04:36:59 | SCAN | 2.0 | ?, Whole Foods Market, Neighborly | `raw-receipts/receipt.png` |
| `5988b5be-335f-4dbe-a826-1335e72a488c` | 2026-02-20T04:25:36 | SCAN | 2.0 | Neighborly, Whole Foods Market | `raw-receipts/receipt.png` |
| `e296ec27-4200-4880-a2c4-06ecb1b12290` | 2026-02-20T04:34:53 | SCAN | 3.0 | Whole Foods Market, Sushi & Wasabi, Neighborly | `raw-receipts/receipt.png` |
| `527ab69d-ba7a-403c-a42c-e37596cc7d98` | 2026-02-20T06:48:11 | SCAN | 2.0 | Whole Foods Market, Neighborly | `raw-receipts/527ab69d-ba7a-403c-a42c-e37596cc7d98/receipt.png` |

### Group 2 — n=3 — raw size 2,085,805 bytes

- **raw_etag:** `74d3a37a7bb8ffad50ad9c8bbe0bbba0`
- **Suggested KEEP:** `b77f9d00-8799-4e63-90fa-294def514a96` (rc=1.0, ts=2026-06-19T22:20:32, merchants=['Whole Foods Market'])
- **Suggested DROP (2):**
  - `de60c6a8-3776-4440-9389-7b17df7e417e` — ts=2026-06-20T17:19:20, type=PHOTO, rc=1.0, merchants=['Whole Foods Market']
  - `51245ed8-9930-4e86-ad81-ff25079afe90` — ts=2026-06-20T18:22:00, type=PHOTO, rc=1.0, merchants=['Whole Foods Market']

| image_id | timestamp_added | type | receipt_count | merchants | raw_key |
|----------|-----------------|------|---------------|-----------|---------|
| `b77f9d00-8799-4e63-90fa-294def514a96` | 2026-06-19T22:20:32 | PHOTO | 1.0 | Whole Foods Market | `raw-receipts/b77f9d00-8799-4e63-90fa-294def514a96/IMG_2828.png` |
| `de60c6a8-3776-4440-9389-7b17df7e417e` | 2026-06-20T17:19:20 | PHOTO | 1.0 | Whole Foods Market | `raw-receipts/de60c6a8-3776-4440-9389-7b17df7e417e/IMG_2828.png` |
| `51245ed8-9930-4e86-ad81-ff25079afe90` | 2026-06-20T18:22:00 | PHOTO | 1.0 | Whole Foods Market | `raw-receipts/51245ed8-9930-4e86-ad81-ff25079afe90/IMG_2828.png` |

### Group 3 — n=3 — raw size 1,744,037 bytes

- **raw_etag:** `b7824bc2c8c422ba82c24a66e9a7fb32`
- **Suggested KEEP:** `93d1a557-6c6a-406f-b21e-8c84b5ee3c65` (rc=1.0, ts=2026-06-22T17:40:40, merchants=['In-N-Out Burger'])
- **Suggested DROP (2):**
  - `c518b0f2-9c85-4904-aeb4-56279a5b1108` — ts=2026-06-22T22:07:38, type=PHOTO, rc=1.0, merchants=['In-N-Out Burger']
  - `acf89aff-b226-4894-a45c-08f854bc0308` — ts=2026-07-07T21:21:29, type=PHOTO, rc=1.0, merchants=['In-N-Out Burger']

| image_id | timestamp_added | type | receipt_count | merchants | raw_key |
|----------|-----------------|------|---------------|-----------|---------|
| `93d1a557-6c6a-406f-b21e-8c84b5ee3c65` | 2026-06-22T17:40:40 | PHOTO | 1.0 | In-N-Out Burger | `uploads/test-93d1a557-6c6a-406f-b21e-8c84b5ee3c65.png` |
| `c518b0f2-9c85-4904-aeb4-56279a5b1108` | 2026-06-22T22:07:38 | PHOTO | 1.0 | In-N-Out Burger | `uploads/test-c518b0f2-9c85-4904-aeb4-56279a5b1108.png` |
| `acf89aff-b226-4894-a45c-08f854bc0308` | 2026-07-07T21:21:29 | PHOTO | 1.0 | In-N-Out Burger | `raw-receipts/acf89aff-b226-4894-a45c-08f854bc0308/IMG_2842.png` |

### Group 4 — n=3 — raw size 1,697,170 bytes

- **raw_etag:** `84f6d47b33fe9bae46e6a9c511690290`
- **Suggested KEEP:** `b54530e9-9bd1-4a60-8e09-6faf60545b18` (rc=1.0, ts=2026-06-22T20:11:02, merchants=['CVS'])
- **Suggested DROP (2):**
  - `1ac815c4-4baf-407d-9c09-0504bd5f930c` — ts=2026-06-24T23:22:00, type=PHOTO, rc=1.0, merchants=['CVS pharmacy']
  - `c019638a-5f16-4df5-be23-2211cf3c3723` — ts=2026-07-07T21:22:50, type=PHOTO, rc=1.0, merchants=['CVS']

| image_id | timestamp_added | type | receipt_count | merchants | raw_key |
|----------|-----------------|------|---------------|-----------|---------|
| `b54530e9-9bd1-4a60-8e09-6faf60545b18` | 2026-06-22T20:11:02 | PHOTO | 1.0 | CVS | `uploads/test-b54530e9-9bd1-4a60-8e09-6faf60545b18.png` |
| `1ac815c4-4baf-407d-9c09-0504bd5f930c` | 2026-06-24T23:22:00 | PHOTO | 1.0 | CVS pharmacy | `uploads/test-1ac815c4-4baf-407d-9c09-0504bd5f930c.png` |
| `c019638a-5f16-4df5-be23-2211cf3c3723` | 2026-07-07T21:22:50 | PHOTO | 1.0 | CVS | `raw-receipts/c019638a-5f16-4df5-be23-2211cf3c3723/IMG_2844.png` |

### Group 5 — n=3 — raw size 1,620,010 bytes

- **raw_etag:** `84098cf11ff4403c667696c4217f5740`
- **Suggested KEEP:** `29fd8b01-9d27-42d6-ad6c-c6e678e9af6f` (rc=1.0, ts=2026-06-22T20:11:11, merchants=['Dollar Tree'])
- **Suggested DROP (2):**
  - `703c5cd7-7182-49ee-b760-45d7e10d68ea` — ts=2026-06-24T23:23:00, type=PHOTO, rc=1.0, merchants=['Dollar Tree']
  - `0944ee8d-4a0d-464e-8109-de35a9ba379a` — ts=2026-07-07T21:21:33, type=PHOTO, rc=1.0, merchants=['Dollar Tree']

| image_id | timestamp_added | type | receipt_count | merchants | raw_key |
|----------|-----------------|------|---------------|-----------|---------|
| `29fd8b01-9d27-42d6-ad6c-c6e678e9af6f` | 2026-06-22T20:11:11 | PHOTO | 1.0 | Dollar Tree | `uploads/test-29fd8b01-9d27-42d6-ad6c-c6e678e9af6f.png` |
| `703c5cd7-7182-49ee-b760-45d7e10d68ea` | 2026-06-24T23:23:00 | PHOTO | 1.0 | Dollar Tree | `uploads/test-703c5cd7-7182-49ee-b760-45d7e10d68ea.png` |
| `0944ee8d-4a0d-464e-8109-de35a9ba379a` | 2026-07-07T21:21:33 | PHOTO | 1.0 | Dollar Tree | `raw-receipts/0944ee8d-4a0d-464e-8109-de35a9ba379a/IMG_2845.png` |

### Group 6 — n=2 — raw size 1,618,580 bytes

- **raw_etag:** `666359a8549db293c41425422fa3f8aa`
- **Suggested KEEP:** `425c38c4-f0d2-4ec6-b802-cdc267a60e23` (rc=1.0, ts=2025-02-04T16:29:20, merchants=['Target Grocery'])
- **Suggested DROP (1):**
  - `bbf833ba-d485-4600-8a00-af80ce97e285` — ts=2026-02-24T07:07:47, type=SCAN, rc=1.0, merchants=['Target Grocery']

| image_id | timestamp_added | type | receipt_count | merchants | raw_key |
|----------|-----------------|------|---------------|-----------|---------|
| `425c38c4-f0d2-4ec6-b802-cdc267a60e23` | 2025-02-04T16:29:20 | SCAN | 1.0 | Target Grocery | `raw/425c38c4-f0d2-4ec6-b802-cdc267a60e23.png` |
| `bbf833ba-d485-4600-8a00-af80ce97e285` | 2026-02-24T07:07:47 | SCAN | 1.0 | Target Grocery | `raw-receipts/bbf833ba-d485-4600-8a00-af80ce97e285/425c38c4.png` |

### Group 7 — n=2 — raw size 1,987,589 bytes

- **raw_etag:** `643bb96bcc6bc272233144e38418423d`
- **Suggested KEEP:** `49a1a1e7-348a-4cd9-af96-2312e838476e` (rc=2.0, ts=2025-05-17T19:50:01, merchants=['DIY Home Center', 'SPEEDWAY'])
- **Suggested DROP (1):**
  - `a63a6c84-d399-452f-9e56-08ff664c8f35` — ts=2025-10-27T04:34:11, type=SCAN, rc=2.0, merchants=['DIY HOME CENTER AGOURA', 'SPEEDWAY']

| image_id | timestamp_added | type | receipt_count | merchants | raw_key |
|----------|-----------------|------|---------------|-----------|---------|
| `49a1a1e7-348a-4cd9-af96-2312e838476e` | 2025-05-17T19:50:01 | SCAN | 2.0 | DIY Home Center, SPEEDWAY | `raw/49a1a1e7-348a-4cd9-af96-2312e838476e.png` |
| `a63a6c84-d399-452f-9e56-08ff664c8f35` | 2025-10-27T04:34:11 | SCAN | 2.0 | DIY HOME CENTER AGOURA, SPEEDWAY | `raw/a63a6c84-d399-452f-9e56-08ff664c8f35.png` |

### Group 8 — n=2 — raw size 15,189,731 bytes

- **raw_etag:** `d183d2d1ae85b8150fd5afa3c42dda85`
- **Suggested KEEP:** `4e180507-c996-4766-979d-4af3221a68a3` (rc=1.0, ts=2025-06-10T16:45:32, merchants=['614 Gravier St'])
- **Suggested DROP (1):**
  - `88e95723-3beb-4dcb-ba4b-cf24b3b70565` — ts=2025-06-22T22:49:32, type=PHOTO, rc=1.0, merchants=['614 Gravier St']

| image_id | timestamp_added | type | receipt_count | merchants | raw_key |
|----------|-----------------|------|---------------|-----------|---------|
| `4e180507-c996-4766-979d-4af3221a68a3` | 2025-06-10T16:45:32 | PHOTO | 1.0 | 614 Gravier St | `raw/4e180507-c996-4766-979d-4af3221a68a3.png` |
| `88e95723-3beb-4dcb-ba4b-cf24b3b70565` | 2025-06-22T22:49:32 | PHOTO | 1.0 | 614 Gravier St | `raw/88e95723-3beb-4dcb-ba4b-cf24b3b70565.png` |

### Group 9 — n=2 — raw size 1,955,317 bytes

- **raw_etag:** `2e588c2c5a7e84ddf4f56d310cf50ced`
- **Suggested KEEP:** `8945c5e8-5c2c-4abf-a2de-078d7ab8f0ff` (rc=2.0, ts=2025-09-13T07:27:16, merchants=['Eastwood', '?'])
- **Suggested DROP (1):**
  - `7c90cb12-4a5b-4282-ac59-6475c3fbaa10` — ts=2026-02-20T06:48:15, type=SCAN, rc=2.0, merchants=['Eastwood']

| image_id | timestamp_added | type | receipt_count | merchants | raw_key |
|----------|-----------------|------|---------------|-----------|---------|
| `8945c5e8-5c2c-4abf-a2de-078d7ab8f0ff` | 2025-09-13T07:27:16 | SCAN | 2.0 | Eastwood, ? | `raw/8945c5e8-5c2c-4abf-a2de-078d7ab8f0ff.png` |
| `7c90cb12-4a5b-4282-ac59-6475c3fbaa10` | 2026-02-20T06:48:15 | SCAN | 2.0 | Eastwood | `raw-receipts/7c90cb12-4a5b-4282-ac59-6475c3fbaa10/8945c5e8-raw .png` |

### Group 10 — n=2 — raw size 2,672,676 bytes

- **raw_etag:** `a94a5f4983573512e21eec96c52e5585`
- **Suggested KEEP:** `20576ddd-8a2c-4aea-841e-da553b8a7ff1` (rc=2.0, ts=2025-10-25T06:54:44, merchants=['Costco Wholesale', 'AIM Mail Center'])
- **Suggested DROP (1):**
  - `53e716ed-49bf-4562-90e7-fc8c860cb985` — ts=2025-10-25T06:58:32, type=SCAN, rc=0.0, merchants=['?']

| image_id | timestamp_added | type | receipt_count | merchants | raw_key |
|----------|-----------------|------|---------------|-----------|---------|
| `20576ddd-8a2c-4aea-841e-da553b8a7ff1` | 2025-10-25T06:54:44 | SCAN | 2.0 | Costco Wholesale, AIM Mail Center | `raw/20576ddd-8a2c-4aea-841e-da553b8a7ff1.png` |
| `53e716ed-49bf-4562-90e7-fc8c860cb985` | 2025-10-25T06:58:32 | SCAN | 0.0 | ? | `raw/53e716ed-49bf-4562-90e7-fc8c860cb985.png` |

### Group 11 — n=2 — raw size 2,236,715 bytes

- **raw_etag:** `effc2dc63e6215c1496d43965bd613f9`
- **Suggested KEEP:** `b4791435-f9a7-4fb3-ac5f-35f8c933e3fe` (rc=3.0, ts=2025-10-25T06:58:06, merchants=['Sprouts Farmers Market', 'MOODY MARKET AND PROVISIONS'])
- **Suggested DROP (1):**
  - `cb22100f-44c2-4b7d-b29f-46627a64355a` — ts=2025-10-25T07:23:07, type=SCAN, rc=3.0, merchants=['Sprouts Farmers Market', 'Moody Market and Provisions', 'Moody Market & Provisions']

| image_id | timestamp_added | type | receipt_count | merchants | raw_key |
|----------|-----------------|------|---------------|-----------|---------|
| `b4791435-f9a7-4fb3-ac5f-35f8c933e3fe` | 2025-10-25T06:58:06 | SCAN | 3.0 | Sprouts Farmers Market, MOODY MARKET AND PROVISIONS | `raw/b4791435-f9a7-4fb3-ac5f-35f8c933e3fe.png` |
| `cb22100f-44c2-4b7d-b29f-46627a64355a` | 2025-10-25T07:23:07 | SCAN | 3.0 | Sprouts Farmers Market, Moody Market and Provisions, Moody Market & Provisions | `raw/cb22100f-44c2-4b7d-b29f-46627a64355a.png` |

### Group 12 — n=2 — raw size 1,356,002 bytes

- **raw_etag:** `a530cdac85ff7b933d729462ae63edce`
- **Suggested KEEP:** `36f99370-5bd3-4253-944d-316599865c52` (rc=2.0, ts=2026-02-06T05:10:59, merchants=['Tan L.A.', 'Neighborly'])
- **Suggested DROP (1):**
  - `22a614d3-fb37-43be-9c91-94a6d09e3fb5` — ts=2026-02-06T05:16:02, type=SCAN, rc=2.0, merchants=['Tan L.A.', 'Neighborly']

| image_id | timestamp_added | type | receipt_count | merchants | raw_key |
|----------|-----------------|------|---------------|-----------|---------|
| `36f99370-5bd3-4253-944d-316599865c52` | 2026-02-06T05:10:59 | SCAN | 2.0 | Tan L.A., Neighborly | `raw-receipts/receipt_ 3.png` |
| `22a614d3-fb37-43be-9c91-94a6d09e3fb5` | 2026-02-06T05:16:02 | SCAN | 2.0 | Tan L.A., Neighborly | `raw-receipts/receipt_ 3.png` |

### Group 13 — n=2 — raw size 2,369,428 bytes

- **raw_etag:** `d2db13dfc6fa26c3c91b84f372397cef`
- **Suggested KEEP:** `7381dc76-b4d7-46f8-aa31-27df6f5db09a` (rc=1.0, ts=2026-02-06T05:10:59, merchants=['Sprouts Farmers Market'])
- **Suggested DROP (1):**
  - `87589280-36d1-4495-9ea2-9a4c5d921ba3` — ts=2026-02-06T05:16:36, type=SCAN, rc=1.0, merchants=['Sprouts Farmers Market', '?']

| image_id | timestamp_added | type | receipt_count | merchants | raw_key |
|----------|-----------------|------|---------------|-----------|---------|
| `7381dc76-b4d7-46f8-aa31-27df6f5db09a` | 2026-02-06T05:10:59 | SCAN | 1.0 | Sprouts Farmers Market | `raw-receipts/receipt_ 1.png` |
| `87589280-36d1-4495-9ea2-9a4c5d921ba3` | 2026-02-06T05:16:36 | SCAN | 1.0 | Sprouts Farmers Market, ? | `raw-receipts/receipt_ 1.png` |

### Group 14 — n=2 — raw size 2,111,173 bytes

- **raw_etag:** `3bc3d5f0b2f9a213d991032b6fa4414d`
- **Suggested KEEP:** `bf801942-b2a2-4393-a4a3-4aa2e022a161` (rc=2.0, ts=2026-02-06T05:10:59, merchants=['Roast and Rice Kitchen'])
- **Suggested DROP (1):**
  - `df899c1f-e343-4fe4-9185-59f8798830d2` — ts=2026-02-06T05:16:40, type=SCAN, rc=2.0, merchants=['Roast & Rice Asian Fusion', 'Roast and Rice Kitchen', '?']

| image_id | timestamp_added | type | receipt_count | merchants | raw_key |
|----------|-----------------|------|---------------|-----------|---------|
| `bf801942-b2a2-4393-a4a3-4aa2e022a161` | 2026-02-06T05:10:59 | SCAN | 2.0 | Roast and Rice Kitchen | `raw-receipts/receipt_ 2.png` |
| `df899c1f-e343-4fe4-9185-59f8798830d2` | 2026-02-06T05:16:40 | SCAN | 2.0 | Roast & Rice Asian Fusion, Roast and Rice Kitchen, ? | `raw-receipts/receipt_ 2.png` |

### Group 15 — n=2 — raw size 2,428,581 bytes

- **raw_etag:** `e339b445761c01d3a6e04ef593bfb8e3`
- **Suggested KEEP:** `b41971e7-608f-4b55-a3e0-538b1046f750` (rc=1.0, ts=2026-02-06T05:10:59, merchants=['Sprouts Farmers Market'])
- **Suggested DROP (1):**
  - `2050f988-84e6-49b1-8835-dc60ff418781` — ts=2026-02-06T05:16:24, type=SCAN, rc=1.0, merchants=['Sprouts Farmers Market']

| image_id | timestamp_added | type | receipt_count | merchants | raw_key |
|----------|-----------------|------|---------------|-----------|---------|
| `b41971e7-608f-4b55-a3e0-538b1046f750` | 2026-02-06T05:10:59 | SCAN | 1.0 | Sprouts Farmers Market | `raw-receipts/receipt_ 6.png` |
| `2050f988-84e6-49b1-8835-dc60ff418781` | 2026-02-06T05:16:24 | SCAN | 1.0 | Sprouts Farmers Market | `raw-receipts/receipt_ 6.png` |

### Group 16 — n=2 — raw size 2,127,095 bytes

- **raw_etag:** `c5701d114c56934e66c263fe8891502b`
- **Suggested KEEP:** `cd456f8c-111e-4d7c-83ae-9552ad7004a7` (rc=2.0, ts=2026-02-06T05:11:00, merchants=['Moody Market & Provisions'])
- **Suggested DROP (1):**
  - `7b5576c7-a6a0-45b5-98e5-ba3e013e47bf` — ts=2026-02-06T05:16:21, type=SCAN, rc=2.0, merchants=['Moody Market & Provisions', '?']

| image_id | timestamp_added | type | receipt_count | merchants | raw_key |
|----------|-----------------|------|---------------|-----------|---------|
| `cd456f8c-111e-4d7c-83ae-9552ad7004a7` | 2026-02-06T05:11:00 | SCAN | 2.0 | Moody Market & Provisions | `raw-receipts/receipt_ 4.png` |
| `7b5576c7-a6a0-45b5-98e5-ba3e013e47bf` | 2026-02-06T05:16:21 | SCAN | 2.0 | Moody Market & Provisions, ? | `raw-receipts/receipt_ 4.png` |

### Group 17 — n=2 — raw size 2,176,822 bytes

- **raw_etag:** `b155a7f48682338c776760ac8bc1380e`
- **Suggested KEEP:** `99526556-f9de-46a1-8061-131463a43459` (rc=1.0, ts=2026-02-06T05:11:06, merchants=['La La Land Kind Cafe'])
- **Suggested DROP (1):**
  - `14587777-4f26-4bd2-a160-1d0a85fe7796` — ts=2026-02-06T05:17:09, type=SCAN, rc=1.0, merchants=['La La Land']

| image_id | timestamp_added | type | receipt_count | merchants | raw_key |
|----------|-----------------|------|---------------|-----------|---------|
| `99526556-f9de-46a1-8061-131463a43459` | 2026-02-06T05:11:06 | SCAN | 1.0 | La La Land Kind Cafe | `raw-receipts/receipt_ 5.png` |
| `14587777-4f26-4bd2-a160-1d0a85fe7796` | 2026-02-06T05:17:09 | SCAN | 1.0 | La La Land | `raw-receipts/receipt_ 5.png` |

### Group 18 — n=2 — raw size 1,637,670 bytes

- **raw_etag:** `c9c8def0838e18d595dc3428d9ce4968`
- **Suggested KEEP:** `1abafdfd-663e-4092-b50d-04326e3f5fc8` (rc=1.0, ts=2026-06-18T04:08:25, merchants=['Smiths'])
- **Suggested DROP (1):**
  - `0840f8d2-d503-4741-a2b6-9cc26063fa84` — ts=2026-06-21T20:11:38, type=SCAN, rc=0.0, merchants=['?']

| image_id | timestamp_added | type | receipt_count | merchants | raw_key |
|----------|-----------------|------|---------------|-----------|---------|
| `1abafdfd-663e-4092-b50d-04326e3f5fc8` | 2026-06-18T04:08:25 | PHOTO | 1.0 | Smiths | `raw-receipts/1abafdfd-663e-4092-b50d-04326e3f5fc8/IMG_2802.png` |
| `0840f8d2-d503-4741-a2b6-9cc26063fa84` | 2026-06-21T20:11:38 | SCAN | 0.0 | ? | `raw-receipts/0840f8d2-d503-4741-a2b6-9cc26063fa84/IMG_2802.png` |

### Group 19 — n=2 — raw size 1,801,252 bytes

- **raw_etag:** `59266a2a82a6ff3391ffb95ff4335e84`
- **Suggested KEEP:** `5dcf582e-cdfe-40f8-89f4-53b1cf3fc893` (rc=1.0, ts=2026-06-18T04:08:32, merchants=["TRADER JOE'S"])
- **Suggested DROP (1):**
  - `7c692583-32b8-4bf5-ac2c-4e371e7d71dd` — ts=2026-06-21T20:11:42, type=SCAN, rc=0.0, merchants=['?']

| image_id | timestamp_added | type | receipt_count | merchants | raw_key |
|----------|-----------------|------|---------------|-----------|---------|
| `5dcf582e-cdfe-40f8-89f4-53b1cf3fc893` | 2026-06-18T04:08:32 | PHOTO | 1.0 | TRADER JOE'S | `raw-receipts/5dcf582e-cdfe-40f8-89f4-53b1cf3fc893/IMG_2800.png` |
| `7c692583-32b8-4bf5-ac2c-4e371e7d71dd` | 2026-06-21T20:11:42 | SCAN | 0.0 | ? | `raw-receipts/7c692583-32b8-4bf5-ac2c-4e371e7d71dd/IMG_2800.png` |

### Group 20 — n=2 — raw size 2,155,164 bytes

- **raw_etag:** `f7d745ea2f2d3573d64322505d6e808a`
- **Suggested KEEP:** `f2446845-6bb0-416c-bfa3-cad9cb4ad80e` (rc=1.0, ts=2026-06-18T04:08:41, merchants=['Costco Wholesale'])
- **Suggested DROP (1):**
  - `5592edb9-421e-4838-8cd9-94d4c2b5459e` — ts=2026-06-21T21:01:04, type=PHOTO, rc=1.0, merchants=['Costco Wholesale']

| image_id | timestamp_added | type | receipt_count | merchants | raw_key |
|----------|-----------------|------|---------------|-----------|---------|
| `f2446845-6bb0-416c-bfa3-cad9cb4ad80e` | 2026-06-18T04:08:41 | PHOTO | 1.0 | Costco Wholesale | `raw-receipts/f2446845-6bb0-416c-bfa3-cad9cb4ad80e/IMG_2808.png` |
| `5592edb9-421e-4838-8cd9-94d4c2b5459e` | 2026-06-21T21:01:04 | PHOTO | 1.0 | Costco Wholesale | `raw-receipts/5592edb9-421e-4838-8cd9-94d4c2b5459e/IMG_2808.png` |

### Group 21 — n=2 — raw size 1,716,812 bytes

- **raw_etag:** `832994616e5f186d6144005151c13281`
- **Suggested KEEP:** `25ce5f40-6999-4e46-9896-4c7699f832c4` (rc=1.0, ts=2026-06-18T04:08:43, merchants=["TRADER JOE'S"])
- **Suggested DROP (1):**
  - `b9d83994-a75e-44a4-9ec4-01ed3d252318` — ts=2026-06-21T21:09:06, type=PHOTO, rc=1.0, merchants=["Trader Joe's"]

| image_id | timestamp_added | type | receipt_count | merchants | raw_key |
|----------|-----------------|------|---------------|-----------|---------|
| `25ce5f40-6999-4e46-9896-4c7699f832c4` | 2026-06-18T04:08:43 | PHOTO | 1.0 | TRADER JOE'S | `raw-receipts/25ce5f40-6999-4e46-9896-4c7699f832c4/IMG_2814.png` |
| `b9d83994-a75e-44a4-9ec4-01ed3d252318` | 2026-06-21T21:09:06 | PHOTO | 1.0 | Trader Joe's | `raw-receipts/b9d83994-a75e-44a4-9ec4-01ed3d252318/IMG_2814.png` |

### Group 22 — n=2 — raw size 1,381,383 bytes

- **raw_etag:** `d9d80671e7ae4f252df85e1e5b81602c`
- **Suggested KEEP:** `7045d3df-6e41-4ae3-b2a8-0da47aeb20dc` (rc=1.0, ts=2026-06-18T04:09:01, merchants=['CVS pharmacy'])
- **Suggested DROP (1):**
  - `de6ae183-b588-4539-b262-84bcb3301707` — ts=2026-06-21T21:23:58, type=PHOTO, rc=1.0, merchants=['CVS pharmacy']

| image_id | timestamp_added | type | receipt_count | merchants | raw_key |
|----------|-----------------|------|---------------|-----------|---------|
| `7045d3df-6e41-4ae3-b2a8-0da47aeb20dc` | 2026-06-18T04:09:01 | PHOTO | 1.0 | CVS pharmacy | `raw-receipts/7045d3df-6e41-4ae3-b2a8-0da47aeb20dc/IMG_2819.png` |
| `de6ae183-b588-4539-b262-84bcb3301707` | 2026-06-21T21:23:58 | PHOTO | 1.0 | CVS pharmacy | `raw-receipts/de6ae183-b588-4539-b262-84bcb3301707/IMG_2819.png` |

### Group 23 — n=2 — raw size 2,144,299 bytes

- **raw_etag:** `4c591679b99287bf7b1abab0374ba790`
- **Suggested KEEP:** `60a24649-c5c8-4c5b-8dd6-ba5d90e8b96b` (rc=1.0, ts=2026-06-20T18:37:01, merchants=['Costco Wholesale'])
- **Suggested DROP (1):**
  - `4109d69e-e785-459b-9efa-983ea98fd0b4` — ts=2026-06-21T20:07:24, type=SCAN, rc=0.0, merchants=['?']

| image_id | timestamp_added | type | receipt_count | merchants | raw_key |
|----------|-----------------|------|---------------|-----------|---------|
| `60a24649-c5c8-4c5b-8dd6-ba5d90e8b96b` | 2026-06-20T18:37:01 | PHOTO | 1.0 | Costco Wholesale | `raw-receipts/60a24649-c5c8-4c5b-8dd6-ba5d90e8b96b/IMG_2829.png` |
| `4109d69e-e785-459b-9efa-983ea98fd0b4` | 2026-06-21T20:07:24 | SCAN | 0.0 | ? | `raw-receipts/4109d69e-e785-459b-9efa-983ea98fd0b4/IMG_2829.png` |

### Group 24 — n=2 — raw size 2,166,939 bytes

- **raw_etag:** `c0bb9cd4acc0c0a3d9a2c2c4d46ab220`
- **Suggested KEEP:** `9c8d00f2-f737-4ad5-9139-3962c2f590d8` (rc=1.0, ts=2026-06-20T18:37:14, merchants=['CJ’s Italian Ice & Custard'])
- **Suggested DROP (1):**
  - `ac9617bf-58a0-4cc2-bfd3-0e37ab5a9fa7` — ts=2026-06-21T20:07:24, type=SCAN, rc=0.0, merchants=['?']

| image_id | timestamp_added | type | receipt_count | merchants | raw_key |
|----------|-----------------|------|---------------|-----------|---------|
| `9c8d00f2-f737-4ad5-9139-3962c2f590d8` | 2026-06-20T18:37:14 | PHOTO | 1.0 | CJ’s Italian Ice & Custard | `raw-receipts/9c8d00f2-f737-4ad5-9139-3962c2f590d8/IMG_2830.png` |
| `ac9617bf-58a0-4cc2-bfd3-0e37ab5a9fa7` | 2026-06-21T20:07:24 | SCAN | 0.0 | ? | `raw-receipts/ac9617bf-58a0-4cc2-bfd3-0e37ab5a9fa7/IMG_2830.png` |

### Group 25 — n=2 — raw size 1,659,359 bytes

- **raw_etag:** `1ee5e64e2d7220edeac85c5fadc4d78a`
- **Suggested KEEP:** `2173f96f-07ca-41cf-afbe-f8a055053d10` (rc=1.0, ts=2026-06-22T20:11:14, merchants=["Smith's"])
- **Suggested DROP (1):**
  - `3b2cb4f3-6977-40e8-90d8-e7d3d1aac3c2` — ts=2026-07-07T21:22:52, type=PHOTO, rc=1.0, merchants=["Smith's"]

| image_id | timestamp_added | type | receipt_count | merchants | raw_key |
|----------|-----------------|------|---------------|-----------|---------|
| `2173f96f-07ca-41cf-afbe-f8a055053d10` | 2026-06-22T20:11:14 | PHOTO | 1.0 | Smith's | `uploads/test-2173f96f-07ca-41cf-afbe-f8a055053d10.png` |
| `3b2cb4f3-6977-40e8-90d8-e7d3d1aac3c2` | 2026-07-07T21:22:52 | PHOTO | 1.0 | Smith's | `raw-receipts/3b2cb4f3-6977-40e8-90d8-e7d3d1aac3c2/IMG_2846.png` |

### Group 26 — n=2 — raw size 2,013,707 bytes

- **raw_etag:** `24fdc0dc7e6ae3387f5e83929d0b9dea`
- **Suggested KEEP:** `36a5a2f0-9709-48f2-9f42-4b6f0956c034` (rc=1.0, ts=2026-06-26T17:36:42, merchants=["Rachel's Kitchen at The District"])
- **Suggested DROP (1):**
  - `170f55ed-6a9c-435b-939e-0bb6f08a181b` — ts=2026-07-07T21:22:54, type=PHOTO, rc=1.0, merchants=["Rachel's Kitchen"]

| image_id | timestamp_added | type | receipt_count | merchants | raw_key |
|----------|-----------------|------|---------------|-----------|---------|
| `36a5a2f0-9709-48f2-9f42-4b6f0956c034` | 2026-06-26T17:36:42 | PHOTO | 1.0 | Rachel's Kitchen at The District | `uploads/test-36a5a2f0-9709-48f2-9f42-4b6f0956c034.png` |
| `170f55ed-6a9c-435b-939e-0bb6f08a181b` | 2026-07-07T21:22:54 | PHOTO | 1.0 | Rachel's Kitchen | `raw-receipts/170f55ed-6a9c-435b-939e-0bb6f08a181b/IMG_2854.png` |

---

## Execution checklist for cleanup agent

- [ ] Re-verify each group: `aws s3api head-object` (or boto3) — confirm ETags still match
- [ ] For keep candidate: spot-check `get_receipt` / label counts / place present
- [ ] For drop candidates: `delete_image` dry_run → apply; delete S3 keys under that image_id only
- [ ] Re-run etag grouping → expect **0** multi-id groups (or only intentional multi-view exceptions)
- [ ] Optional: same-transaction different-ETag pairs (not in this file) — separate decision
- [ ] Do not delete upload-bucket originals unless explicitly scoped

### Tools

- MCP `receipt-tools__delete_image` (`dry_run` first)
- Or `DynamoClient.delete_image_details(image_id)` + S3 prefix delete for `raw/{id}*` and `assets/{id}*`
- Table: `ReceiptsTable-dc5be22`
- Raw bucket: `raw-image-bucket-c779c32`
- CDN bucket: `sitebucket-ad92f1f`

---

## Artifact

Machine-readable discovery output: `/tmp/duplicate_images_report.json` (may not persist across machines — regenerate if missing).

---

## Out of scope / do not confuse

| Issue | Action |
|-------|--------|
| Two receipt_ids on **one** image (header + money) | `merge_receipts` |
| Same order, **different** raw photos | Optional human pick; not auto-etag |
| Empty half-ingest (legacy) | Fix or delete case-by-case |

