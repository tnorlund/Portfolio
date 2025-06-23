# Receipt Processing

This folder contains helper functions used after OCR results are
available.  Each function ingests the initial OCR output and stores
receipt data in DynamoDB while uploading images to S3.

## Purpose

The helpers transform raw OCR into receipt level records.  They also
create new OCR jobs when multiple receipts are detected so that each
receipt can be processed individually.

## Functions

### `process_photo`
Use for smartphone photos or other pictures that may contain several
receipts or require perspective correction. The function downloads the
OCR JSON and the original image from S3, clusters OCR lines to detect
individual receipts and warps each receipt. For every detected receipt
it uploads cropped images to the raw and site buckets, stores the image
and OCR data in DynamoDB and enqueues a refinement OCR job on the
provided SQS queue.

Parameters:
- `raw_bucket` – destination for raw images.
- `site_bucket` – destination for CDN formats.
- `dynamo_table_name` – table used by `receipt_dynamo`.
- `ocr_job_queue_url` – queue where refinement jobs are sent.
- `ocr_routing_decision` – routing decision describing the OCR JSON
  location.
- `ocr_job` – original OCR job record.
- `image` – ignored (image is fetched from S3).

Side effects: uploads multiple images to S3, writes Image, Line, Word and
Receipt entities to DynamoDB and sends new OCR job messages to SQS.

### `process_scan`
Use for scanned images that already show receipts from a top‑down view.
It operates similarly to `process_photo` but the receipts are detected by
clustering along the X axis and using affine transforms instead of
perspective warps. All parameters are the same as for `process_photo` and
it performs the same uploads, DynamoDB writes and SQS messages.

### `process_native`
Use when the OCR results already correspond to a single receipt (for
example digital receipts). The function converts image level OCR to
receipt level objects and stores them. Images are uploaded in PNG, JPEG,
WebP and AVIF formats but no additional OCR jobs are created.

Parameters:
- `raw_bucket`, `site_bucket`, `dynamo_table_name`, `ocr_job_queue_url`
  (unused), `image`, `lines`, `words`, `letters`, `ocr_routing_decision`,
  `ocr_job`.

Side effects: uploads the image to S3 and writes image and receipt OCR
records to DynamoDB; updates the routing decision.

### `refine_receipt`
Accepts the final OCR for a single receipt and simply records the receipt
lines, words and letters to DynamoDB. The routing decision status is set
to `COMPLETED`.

Parameters:
- `dynamo_table_name`, `receipt_lines`, `receipt_words`,
  `receipt_letters`, `ocr_routing_decision`.

## AWS Dependencies

All functions use `boto3` to access S3, DynamoDB and SQS. AWS credentials
must therefore be available in the environment. The S3 buckets for raw
and site images, the DynamoDB table used by `receipt_dynamo` and the SQS
queue URL must exist. Optional AVIF uploads require the
`pillow-avif-plugin` package.
