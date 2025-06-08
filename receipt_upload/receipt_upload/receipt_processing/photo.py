from pathlib import Path
from datetime import datetime, timezone
from PIL import Image as PIL_Image
import json
import uuid
import math

from receipt_upload.utils import (
    download_image_from_s3,
    upload_jpeg_to_s3,
    upload_png_to_s3,
    upload_all_cdn_formats,
    calculate_sha256_from_bytes,
    send_message_to_sqs,
    download_file_from_s3,
)
from receipt_upload.geometry import (
    compute_hull_centroid,
    compute_receipt_box_from_skewed_extents,
    convex_hull,
    find_hull_extents_relative_to_centroid,
    find_perspective_coeffs,
)
from receipt_upload.cluster import dbscan_lines
from receipt_upload.ocr import process_ocr_dict_as_image
from receipt_dynamo.entities import (
    Image,
    Receipt,
    OCRJob,
    OCRRoutingDecision,
)
from receipt_dynamo.constants import OCRStatus, OCRJobType, ImageType
from receipt_dynamo import DynamoClient


def process_photo(
    raw_bucket: str,
    site_bucket: str,
    dynamo_table_name: str,
    ocr_job_queue_url: str,
    ocr_routing_decision: OCRRoutingDecision,
    ocr_job: OCRJob,
    image: PIL_Image.Image,
) -> None:
    """
    Process a photo of a receipt.

    This function takes the OCR JSON file and the image file, and processes
    the photo to extract the receipt lines and words. It uploads the raw image
    to the raw and site buckets. It then clusters the OCR lines into receipts,
    and submits another OCR job for each receipt. It also updates the OCR
    routing decision with the number of receipts found.
    """
    dynamo_client = DynamoClient(dynamo_table_name)
    image_id = ocr_job.image_id

    # Download the OCR JSON
    json_s3_key = ocr_routing_decision.s3_key
    json_s3_bucket = ocr_routing_decision.s3_bucket
    ocr_json_path = download_file_from_s3(
        json_s3_bucket, json_s3_key, Path("/tmp")
    )
    with open(ocr_json_path, "r") as f:
        ocr_json = json.load(f)
    ocr_lines, ocr_words, ocr_letters = process_ocr_dict_as_image(
        ocr_json, image_id
    )

    # Download the raw image
    raw_image_s3_key = ocr_job.s3_key
    raw_image_s3_bucket = ocr_job.s3_bucket
    raw_image_path = download_image_from_s3(
        raw_image_s3_bucket, raw_image_s3_key, Path("/tmp"), image_id
    )
    image = PIL_Image.open(raw_image_path)

    # Upload the raw image to the raw bucket
    raw_image_s3_key = f"raw/{image_id}.png"
    upload_png_to_s3(image, raw_bucket, raw_image_s3_key)

    # Upload all CDN formats to site bucket
    cdn_keys = upload_all_cdn_formats(image, site_bucket, f"assets/{image_id}")

    ocr_image = Image(
        image_id=image_id,
        width=image.width,
        height=image.height,
        timestamp_added=datetime.now(timezone.utc),
        raw_s3_bucket=raw_bucket,
        raw_s3_key=f"raw/{image_id}.png",
        cdn_s3_bucket=site_bucket,
        cdn_s3_key=cdn_keys["jpeg"],
        cdn_webp_s3_key=cdn_keys["webp"],
        cdn_avif_s3_key=cdn_keys["avif"],
        sha256=calculate_sha256_from_bytes(image.tobytes()),
        image_type=ImageType.PHOTO,
    )
    # Add the image and OCR data to the database
    dynamo_client.addImage(ocr_image)
    dynamo_client.addLines(ocr_lines)
    dynamo_client.addWords(ocr_words)
    dynamo_client.addLetters(ocr_letters)

    # Get the average diagonal length of the lines
    avg_diagonal_length = sum(
        [line.calculate_diagonal_length() for line in ocr_lines]
    ) / len(ocr_lines)

    # Cluster the lines using DBSCAN
    clusters = dbscan_lines(
        ocr_lines, eps=avg_diagonal_length * 2, min_samples=10
    )
    # Drop noise clusters
    clusters = {k: v for k, v in clusters.items() if k != -1}
    # Process each cluster (receipt) in the image
    for cluster_id, cluster_lines in clusters.items():
        line_ids = [line.line_id for line in cluster_lines]
        cluster_words = [w for w in ocr_words if w.line_id in line_ids]

        # Get corners for perspective transform
        all_word_corners = []
        for word in cluster_words:
            corners = word.calculate_corners(
                width=image.width,
                height=image.height,
                flip_y=True,
            )
            all_word_corners.extend([(int(x), int(y)) for x, y in corners])

        # Compute hull and transform
        hull = convex_hull(all_word_corners)
        cx, cy = compute_hull_centroid(hull)
        find_hull_extents_relative_to_centroid(hull, cx, cy)
        # Get average angle of lines in cluster
        avg_angle = sum(line.angle_degrees for line in cluster_lines) / len(
            cluster_lines
        )

        # Compute receipt box corners for perspective transform
        receipt_box_corners = compute_receipt_box_from_skewed_extents(
            hull, cx, cy, -avg_angle
        )

        # Estimate dimensions for warped image
        top_w = math.dist(receipt_box_corners[0], receipt_box_corners[1])
        bottom_w = math.dist(receipt_box_corners[3], receipt_box_corners[2])
        source_width = (top_w + bottom_w) / 2.0

        left_h = math.dist(receipt_box_corners[0], receipt_box_corners[3])
        right_h = math.dist(receipt_box_corners[1], receipt_box_corners[2])
        source_height = (left_h + right_h) / 2.0

        # Create warped image
        warped_width = int(round(source_width))
        warped_height = int(round(source_height))
        dst_corners = [
            (0, 0),
            (warped_width - 1, 0),
            (warped_width - 1, warped_height - 1),
            (0, warped_height - 1),
        ]
        transform_coeffs = find_perspective_coeffs(
            src_points=receipt_box_corners, dst_points=dst_corners
        )
        warped_img = image.transform(
            (warped_width, warped_height),
            PIL_Image.PERSPECTIVE,
            transform_coeffs,
            resample=PIL_Image.BICUBIC,
        )

        # Upload the warped image to the raw bucket
        upload_png_to_s3(
            warped_img,
            raw_bucket,
            f"raw/{image_id}_RECEIPT_{cluster_id:05d}.png",
        )

        # Upload all CDN formats to site bucket
        receipt_cdn_keys = upload_all_cdn_formats(
            warped_img,
            site_bucket,
            f"assets/{image_id}_RECEIPT_{cluster_id:05d}",
        )

        # Convert the receipt_box_corners from pixel coordinates to normalized
        # coordinates and format them as dictionaries
        top_left = {
            "x": receipt_box_corners[0][0] / image.width,
            "y": 1
            - (receipt_box_corners[0][1] / image.height),  # Flip Y coordinate
        }
        top_right = {
            "x": receipt_box_corners[1][0] / image.width,
            "y": 1 - (receipt_box_corners[1][1] / image.height),
        }
        bottom_right = {
            "x": receipt_box_corners[2][0] / image.width,
            "y": 1 - (receipt_box_corners[2][1] / image.height),
        }
        bottom_left = {
            "x": receipt_box_corners[3][0] / image.width,
            "y": 1 - (receipt_box_corners[3][1] / image.height),
        }

        receipt = Receipt(
            image_id=image_id,
            receipt_id=cluster_id,
            width=warped_img.width,
            height=warped_img.height,
            timestamp_added=datetime.now(timezone.utc),
            raw_s3_bucket=raw_bucket,
            raw_s3_key=f"raw/{image_id}_RECEIPT_{cluster_id:05d}.png",
            top_left=top_left,
            top_right=top_right,
            bottom_left=bottom_left,
            bottom_right=bottom_right,
            sha256=calculate_sha256_from_bytes(warped_img.tobytes()),
            cdn_s3_bucket=site_bucket,
            cdn_s3_key=receipt_cdn_keys["jpeg"],
            cdn_webp_s3_key=receipt_cdn_keys["webp"],
            cdn_avif_s3_key=receipt_cdn_keys["avif"],
        )

        # Add the receipt to DynamoDB
        dynamo_client.addReceipt(receipt)

        # Submit a new OCR job for the receipt
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
        dynamo_client.addOCRJob(new_ocr_job)

        # Send a message to the OCR job queue
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
    ocr_routing_decision.receipt_count = len(clusters)
    ocr_routing_decision.updated_at = datetime.now(timezone.utc)
    dynamo_client.updateOCRRoutingDecision(ocr_routing_decision)
