import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image as PIL_Image
from receipt_upload.cluster import (
    dbscan_lines_x_axis,
    join_overlapping_clusters,
    reorder_box_points,
)
from receipt_upload.geometry import box_points, invert_affine, min_area_rect
from receipt_upload.ocr import process_ocr_dict_as_image
from receipt_upload.utils import (
    calculate_sha256_from_bytes,
    download_file_from_s3,
    download_image_from_s3,
    send_message_to_sqs,
    upload_all_cdn_formats,
    upload_jpeg_to_s3,
    upload_png_to_s3,
)

from receipt_dynamo.constants import ImageType, OCRJobType, OCRStatus
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities import Image, OCRJob, OCRRoutingDecision, Receipt


def process_scan(
    raw_bucket: str,
    site_bucket: str,
    dynamo_table_name: str,
    ocr_job_queue_url: str,
    ocr_routing_decision: OCRRoutingDecision,
    ocr_job: OCRJob,
    image: PIL_Image.Image,
):
    """Process a scanned image and queue OCR jobs for each receipt.

    This function downloads the OCR JSON and the raw image, uploads the raw
    image to S3, and stores the OCR data in DynamoDB. It clusters the OCR
    lines to locate each receipt, crops the receipts, uploads them to the raw
    and site buckets, creates new OCR refinement jobs, and updates the OCR
    routing decision with the number of receipts detected.

    Args:
        raw_bucket: S3 bucket for raw images
        site_bucket: S3 bucket for processed images
        dynamo_table_name: DynamoDB table name
        ocr_job_queue_url: SQS queue URL for OCR jobs
        ocr_routing_decision: OCR routing decision object
        ocr_job: OCR job object
        image: PIL Image object
    """
    logger = logging.getLogger(__name__)
    dynamo_client = DynamoClient(dynamo_table_name)
    image_id = ocr_job.image_id

    # Download the OCR JSON
    json_s3_key = ocr_routing_decision.s3_key
    json_s3_bucket = ocr_routing_decision.s3_bucket
    ocr_json_path = download_file_from_s3(json_s3_bucket, json_s3_key, Path("/tmp"))
    with open(ocr_json_path, "r", encoding="utf-8") as f:
        ocr_json = json.load(f)
    ocr_lines, ocr_words, ocr_letters = process_ocr_dict_as_image(ocr_json, image_id)
    logger.info(
        "[scan] Parsed OCR: lines=%d, words=%d, letters=%d for image=%s",
        len(ocr_lines),
        len(ocr_words),
        len(ocr_letters),
        image_id,
    )

    # Download the raw image
    raw_image_s3_key = ocr_job.s3_key
    raw_image_s3_bucket = ocr_job.s3_bucket
    raw_image_path = download_image_from_s3(
        raw_image_s3_bucket, raw_image_s3_key, image_id
    )
    image = PIL_Image.open(raw_image_path)

    # Upload the raw image to the raw and site buckets
    raw_image_s3_key = f"raw/{image_id}.png"
    logger.info(
        "[scan] Uploading raw image to s3://%s/%s (w=%d h=%d)",
        raw_bucket,
        raw_image_s3_key,
        image.width,
        image.height,
    )
    upload_png_to_s3(image, raw_bucket, raw_image_s3_key)

    # Generate and upload all CDN variants (jpeg/webp/avif) with size variants
    ocr_image_cdn_keys = upload_all_cdn_formats(
        image, site_bucket, f"assets/{image_id}", generate_thumbnails=True
    )
    logger.info("[scan] Uploaded CDN variants for image %s", image_id)

    ocr_image = Image(
        image_id=image_id,
        width=image.width,
        height=image.height,
        timestamp_added=datetime.now(timezone.utc),
        raw_s3_bucket=raw_bucket,
        raw_s3_key=f"raw/{image_id}.png",
        cdn_s3_bucket=site_bucket,
        cdn_s3_key=ocr_image_cdn_keys.get("jpeg"),
        cdn_webp_s3_key=ocr_image_cdn_keys.get("webp"),
        cdn_avif_s3_key=ocr_image_cdn_keys.get("avif"),
        # Add thumbnail versions
        cdn_thumbnail_s3_key=ocr_image_cdn_keys.get("jpeg_thumbnail"),
        cdn_thumbnail_webp_s3_key=ocr_image_cdn_keys.get("webp_thumbnail"),
        cdn_thumbnail_avif_s3_key=ocr_image_cdn_keys.get("avif_thumbnail"),
        # Add small versions
        cdn_small_s3_key=ocr_image_cdn_keys.get("jpeg_small"),
        cdn_small_webp_s3_key=ocr_image_cdn_keys.get("webp_small"),
        cdn_small_avif_s3_key=ocr_image_cdn_keys.get("avif_small"),
        # Add medium versions
        cdn_medium_s3_key=ocr_image_cdn_keys.get("jpeg_medium"),
        cdn_medium_webp_s3_key=ocr_image_cdn_keys.get("webp_medium"),
        cdn_medium_avif_s3_key=ocr_image_cdn_keys.get("avif_medium"),
        sha256=calculate_sha256_from_bytes(image.tobytes()),
        image_type=ImageType.SCAN,
    )
    # Add the image and OCR data to the database
    dynamo_client.add_image(ocr_image)
    dynamo_client.add_lines(ocr_lines)
    dynamo_client.add_words(ocr_words)
    dynamo_client.add_letters(ocr_letters)

    # Get the average diagonal length of the lines
    cluster_dict = dbscan_lines_x_axis(ocr_lines)
    cluster_dict = join_overlapping_clusters(
        cluster_dict, image.width, image.height, iou_threshold=0.01
    )

    # Process each cluster (receipt) in the image
    for cluster_id, cluster_lines in cluster_dict.items():
        # Skip noise clusters
        if cluster_id == -1:
            continue

        # 1) Collect cluster points in absolute image coordinates
        points_abs = []
        for ln in cluster_lines:
            for corner in [
                ln.top_left,
                ln.top_right,
                ln.bottom_left,
                ln.bottom_right,
            ]:
                x_abs = corner["x"] * image.width
                y_abs = (1 - corner["y"]) * image.height  # flip Y
                points_abs.append((x_abs, y_abs))

        if not points_abs:
            raise ValueError("No points found for cluster transformation.")

        # 2) Use your existing min_area_rect to find the bounding box
        (cx, cy), (rw, rh), angle_deg = min_area_rect(points_abs)
        w = int(round(rw))
        h = int(round(rh))
        if w < 1 or h < 1:
            raise ValueError("Degenerate bounding box for cluster.")

        # The Receipts are always portrait, so we need to rotate the bounding
        # box if it's landscape.
        if w > h:
            # Rotate the bounding box by -90° so the final warp is 'portrait'
            angle_deg -= 90.0
            # Swap the width & height so the final image is portrait
            w, h = h, w
            # Also swap rw, rh so our box_points() call below is correct
            rw, rh = rh, rw

        # Recompute the four corners for the (possibly) adjusted angle & size
        box_4 = box_points((cx, cy), (rw, rh), angle_deg)
        box_4_ordered = reorder_box_points(box_4)

        # For convenience, name the corners we need for the transform
        src_tl = box_4_ordered[0]
        src_tr = box_4_ordered[1]
        src_bl = box_4_ordered[3]

        # 3) Build the Pillow transform (dst->src) matrix
        if w > 1:
            a_i = (src_tr[0] - src_tl[0]) / (w - 1)
            d_i = (src_tr[1] - src_tl[1]) / (w - 1)
        else:
            a_i = d_i = 0.0

        if h > 1:
            b_i = (src_bl[0] - src_tl[0]) / (h - 1)
            e_i = (src_bl[1] - src_tl[1]) / (h - 1)
        else:
            b_i = e_i = 0.0

        c_i = src_tl[0]
        f_i = src_tl[1]

        # Invert it to get the forward transform for lines, words, etc.
        a_f, b_f, c_f, d_f, e_f, f_f = invert_affine(a_i, b_i, c_i, d_i, e_i, f_i)

        # 4) Warp the image using the "inverse" (dst->src) matrix
        affine_img = image.transform(
            (w, h),
            PIL_Image.AFFINE,
            (a_i, b_i, c_i, d_i, e_i, f_i),
            resample=PIL_Image.BICUBIC,
        )

        # Upload the warped image to the raw and site buckets
        upload_png_to_s3(
            affine_img,
            raw_bucket,
            f"raw/{image_id}_RECEIPT_{cluster_id:05d}.png",
        )

        # Generate and upload all CDN variants for this receipt
        receipt_cdn_keys = upload_all_cdn_formats(
            affine_img,
            site_bucket,
            f"assets/{image_id}_RECEIPT_{cluster_id:05d}",
            generate_thumbnails=True,
        )

        # 5) Create the Receipt for DynamoDB
        final_w, final_h = affine_img.size
        receipt = Receipt(
            receipt_id=cluster_id,
            image_id=image_id,
            width=final_w,
            height=final_h,
            timestamp_added=datetime.now(timezone.utc),
            raw_s3_bucket=raw_bucket,
            raw_s3_key=f"raw/{image_id}_RECEIPT_{cluster_id:05d}.png",
            top_left={
                "x": box_4_ordered[0][0] / image.width,
                "y": 1 - box_4_ordered[0][1] / image.height,
            },
            top_right={
                "x": box_4_ordered[1][0] / image.width,
                "y": 1 - box_4_ordered[1][1] / image.height,
            },
            bottom_right={
                "x": box_4_ordered[2][0] / image.width,
                "y": 1 - box_4_ordered[2][1] / image.height,
            },
            bottom_left={
                "x": box_4_ordered[3][0] / image.width,
                "y": 1 - box_4_ordered[3][1] / image.height,
            },
            sha256=calculate_sha256_from_bytes(affine_img.tobytes()),
            cdn_s3_bucket=site_bucket,
            cdn_s3_key=receipt_cdn_keys.get("jpeg"),
            cdn_webp_s3_key=receipt_cdn_keys.get("webp"),
            cdn_avif_s3_key=receipt_cdn_keys.get("avif"),
            # Add thumbnail versions
            cdn_thumbnail_s3_key=receipt_cdn_keys.get("jpeg_thumbnail"),
            cdn_thumbnail_webp_s3_key=receipt_cdn_keys.get("webp_thumbnail"),
            cdn_thumbnail_avif_s3_key=receipt_cdn_keys.get("avif_thumbnail"),
            # Add small versions
            cdn_small_s3_key=receipt_cdn_keys.get("jpeg_small"),
            cdn_small_webp_s3_key=receipt_cdn_keys.get("webp_small"),
            cdn_small_avif_s3_key=receipt_cdn_keys.get("avif_small"),
            # Add medium versions
            cdn_medium_s3_key=receipt_cdn_keys.get("jpeg_medium"),
            cdn_medium_webp_s3_key=receipt_cdn_keys.get("webp_medium"),
            cdn_medium_avif_s3_key=receipt_cdn_keys.get("avif_medium"),
        )
        dynamo_client.add_receipt(receipt)

        # 6) Submit a new OCR job for the receipt
        new_ocr_job = OCRJob(
            image_id=image_id,
            job_id=str(uuid.uuid4()),
            s3_bucket=raw_bucket,
            s3_key=f"raw/{image_id}_RECEIPT_{cluster_id:05d}.png",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            status=OCRStatus.PENDING,
            job_type=OCRJobType.REFINEMENT,
            receipt_id=cluster_id,
        )
        dynamo_client.add_ocr_job(new_ocr_job)

        # 7) Send a message to the OCR job queue
        send_message_to_sqs(
            ocr_job_queue_url,
            json.dumps(
                {
                    "job_id": new_ocr_job.job_id,
                    "image_id": new_ocr_job.image_id,
                }
            ),
        )

    ocr_routing_decision.status = OCRStatus.COMPLETED.value
    ocr_routing_decision.receipt_count = len(cluster_dict)
    ocr_routing_decision.updated_at = datetime.now(timezone.utc)
    dynamo_client.update_ocr_routing_decision(ocr_routing_decision)
