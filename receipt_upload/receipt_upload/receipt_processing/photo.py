import json
import math
import uuid
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image as PIL_Image
from PIL.Image import Resampling, Transform
from receipt_upload.cluster import dbscan_lines
from receipt_upload.geometry import (
    compute_final_receipt_tilt,
    compute_hull_centroid,
    compute_receipt_box_from_boundaries,
    compute_receipt_box_from_skewed_extents,
    convex_hull,
    create_boundary_line_from_points,
    create_boundary_line_from_theil_sen,
    create_horizontal_boundary_line_from_points,
    find_hull_extents_relative_to_centroid,
    find_hull_extremes_along_angle,
    find_line_edges_at_secondary_extremes,
    find_perspective_coeffs,
    refine_hull_extremes_with_hull_edge_alignment,
    theil_sen,
)
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
        raw_image_s3_bucket, raw_image_s3_key, image_id
    )
    image = PIL_Image.open(raw_image_path)

    # Upload the raw image to the raw bucket
    raw_image_s3_key = f"raw/{image_id}.png"
    upload_png_to_s3(image, raw_bucket, raw_image_s3_key)

    # Upload all CDN formats to site bucket with thumbnails
    cdn_keys = upload_all_cdn_formats(
        image, site_bucket, f"assets/{image_id}", generate_thumbnails=True
    )

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
        # Add thumbnail versions
        cdn_thumbnail_s3_key=cdn_keys.get("jpeg_thumbnail"),
        cdn_thumbnail_webp_s3_key=cdn_keys.get("webp_thumbnail"),
        cdn_thumbnail_avif_s3_key=cdn_keys.get("avif_thumbnail"),
        # Add small versions
        cdn_small_s3_key=cdn_keys.get("jpeg_small"),
        cdn_small_webp_s3_key=cdn_keys.get("webp_small"),
        cdn_small_avif_s3_key=cdn_keys.get("avif_small"),
        # Add medium versions
        cdn_medium_s3_key=cdn_keys.get("jpeg_medium"),
        cdn_medium_webp_s3_key=cdn_keys.get("webp_medium"),
        cdn_medium_avif_s3_key=cdn_keys.get("avif_medium"),
        sha256=calculate_sha256_from_bytes(image.tobytes()),
        image_type=ImageType.PHOTO,
    )
    # Add the image and OCR data to the database
    dynamo_client.add_image(ocr_image)
    dynamo_client.add_lines(ocr_lines)
    dynamo_client.add_words(ocr_words)
    dynamo_client.add_letters(ocr_letters)

    # Get the average diagonal length of the lines
    if not ocr_lines:
        # No OCR lines detected - update routing decision and return
        ocr_routing_decision.status = OCRStatus.COMPLETED.value
        ocr_routing_decision.receipt_count = 0
        ocr_routing_decision.updated_at = datetime.now(timezone.utc)
        dynamo_client.update_ocr_routing_decision(ocr_routing_decision)
        return

    avg_diagonal_length = sum(
        [line.calculate_diagonal_length() for line in ocr_lines]
    ) / len(ocr_lines)

    # Cluster the lines using DBSCAN
    clusters = dbscan_lines(
        ocr_lines, eps=avg_diagonal_length * 2, min_samples=10
    )
    # Drop noise clusters
    clusters = {k: v for k, v in clusters.items() if k != -1}

    if not clusters:
        # No valid clusters found - update routing decision and return
        ocr_routing_decision.status = OCRStatus.COMPLETED.value
        ocr_routing_decision.receipt_count = 0
        ocr_routing_decision.updated_at = datetime.now(timezone.utc)
        dynamo_client.update_ocr_routing_decision(ocr_routing_decision)
        return

    # Process each cluster (receipt) in the image
    successful_clusters = 0
    for cluster_id, cluster_lines in clusters.items():
        try:
            # Validate cluster has sufficient data
            if len(cluster_lines) < 3:
                print(
                    f"Skipping cluster {cluster_id}: insufficient lines ({len(cluster_lines)})"
                )
                continue

            line_ids = [line.line_id for line in cluster_lines]
            cluster_words = [w for w in ocr_words if w.line_id in line_ids]

            if len(cluster_words) < 4:
                print(
                    f"Skipping cluster {cluster_id}: insufficient words ({len(cluster_words)})"
                )
                continue

            # Get corners for perspective transform
            all_word_corners = []
            for word in cluster_words:
                corners = word.calculate_corners(
                    width=image.width,
                    height=image.height,
                    flip_y=True,
                )
                all_word_corners.extend([(int(x), int(y)) for x, y in corners])

            if len(all_word_corners) < 4:
                print(
                    f"Skipping cluster {cluster_id}: insufficient corner points ({len(all_word_corners)})"
                )
                continue

            # Compute hull and transform
            hull = convex_hull(all_word_corners)
            if len(hull) < 4:
                print(
                    f"Skipping cluster {cluster_id}: hull has insufficient points ({len(hull)})"
                )
                continue

            cx, cy = compute_hull_centroid(hull)
            find_hull_extents_relative_to_centroid(hull, cx, cy)
            # Get average angle of lines in cluster
            avg_angle = sum(
                line.angle_degrees for line in cluster_lines
            ) / len(cluster_lines)

            # Compute receipt box corners using step-by-step geometry utilities
            final_angle = compute_final_receipt_tilt(
                cluster_lines, hull, (cx, cy), avg_angle
            )
            extremes = find_hull_extremes_along_angle(
                hull, (cx, cy), final_angle
            )
            refined = refine_hull_extremes_with_hull_edge_alignment(
                hull,
                extremes["leftPoint"],
                extremes["rightPoint"],
                final_angle,
            )
            edge_points = find_line_edges_at_secondary_extremes(
                cluster_lines, hull, (cx, cy), final_angle
            )
            boundaries = {
                "top": create_horizontal_boundary_line_from_points(
                    edge_points["topEdge"]
                ),
                "bottom": create_horizontal_boundary_line_from_points(
                    edge_points["bottomEdge"]
                ),
                "left": create_boundary_line_from_points(
                    refined["leftSegment"]["extreme"],
                    refined["leftSegment"]["optimizedNeighbor"],
                ),
                "right": create_boundary_line_from_points(
                    refined["rightSegment"]["extreme"],
                    refined["rightSegment"]["optimizedNeighbor"],
                ),
            }
            receipt_box_corners = compute_receipt_box_from_boundaries(
                boundaries["top"],
                boundaries["bottom"],
                boundaries["left"],
                boundaries["right"],
                (cx, cy),
            )

            # DEBUG: Log the corner points to understand the geometry
            print(f"Cluster {cluster_id} receipt_box_corners:")
            for i, corner in enumerate(receipt_box_corners):
                print(f"  Point {i}: ({corner[0]:.2f}, {corner[1]:.2f})")

            # Reorder corners to match dst_corners order (top-left, top-right, bottom-right, bottom-left)
            # Current geometry pipeline returns: [bottom-left, bottom-right, top-right, top-left]
            # We need: [top-left, top-right, bottom-right, bottom-left]
            # So reorder: [3, 2, 1, 0]
            receipt_box_corners = [
                receipt_box_corners[3],  # top-left
                receipt_box_corners[2],  # top-right
                receipt_box_corners[1],  # bottom-right
                receipt_box_corners[0],  # bottom-left
            ]

            print(f"Cluster {cluster_id} reordered corners:")
            for i, corner in enumerate(receipt_box_corners):
                print(f"  Point {i}: ({corner[0]:.2f}, {corner[1]:.2f})")

            # Check distances between points and detect duplicates
            print(f"Cluster {cluster_id} distances:")
            has_duplicates = False
            for i in range(4):
                for j in range(i + 1, 4):
                    dist = math.dist(
                        receipt_box_corners[i], receipt_box_corners[j]
                    )
                    print(f"  Distance {i}-{j}: {dist:.2f}")
                    if dist < 1.0:  # Points are essentially identical
                        has_duplicates = True
                        print(
                            f"  ⚠️  Points {i} and {j} are too close together!"
                        )

            # Skip if we have duplicate corners
            if has_duplicates:
                print(
                    f"Skipping cluster {cluster_id}: detected duplicate/near-duplicate corners"
                )
                continue

            # Validate receipt box corners are reasonable
            if not all(
                isinstance(corner, (list, tuple)) and len(corner) == 2
                for corner in receipt_box_corners
            ):
                print(
                    f"Skipping cluster {cluster_id}: invalid receipt box corners"
                )
                continue

            # Check for degenerate rectangle (all corners too close together)
            min_x = min(corner[0] for corner in receipt_box_corners)
            max_x = max(corner[0] for corner in receipt_box_corners)
            min_y = min(corner[1] for corner in receipt_box_corners)
            max_y = max(corner[1] for corner in receipt_box_corners)

            if (max_x - min_x) < 10 or (max_y - min_y) < 10:
                print(
                    f"Skipping cluster {cluster_id}: degenerate rectangle ({max_x-min_x}x{max_y-min_y})"
                )
                continue

            # Estimate dimensions for warped image
            top_w = math.dist(receipt_box_corners[0], receipt_box_corners[1])
            bottom_w = math.dist(
                receipt_box_corners[3], receipt_box_corners[2]
            )
            source_width = (top_w + bottom_w) / 2.0

            left_h = math.dist(receipt_box_corners[0], receipt_box_corners[3])
            right_h = math.dist(receipt_box_corners[1], receipt_box_corners[2])
            source_height = (left_h + right_h) / 2.0

            # Validate dimensions are reasonable
            if (
                source_width < 10
                or source_height < 10
                or source_width > 10000
                or source_height > 10000
            ):
                print(
                    f"Skipping cluster {cluster_id}: unreasonable dimensions ({source_width}x{source_height})"
                )
                continue

            # Create warped image
            warped_width = int(round(source_width))
            warped_height = int(round(source_height))
            dst_corners = [
                (0.0, 0.0),
                (float(warped_width - 1), 0.0),
                (float(warped_width - 1), float(warped_height - 1)),
                (0.0, float(warped_height - 1)),
            ]

            # This is where the perspective transform can fail with singular matrix
            try:
                transform_coeffs = find_perspective_coeffs(
                    src_points=receipt_box_corners, dst_points=dst_corners
                )
            except ValueError as e:
                print(
                    f"Perspective transform failed for cluster {cluster_id}: {e}"
                )
                print("Falling back to simple bounding rectangle...")

                # Fallback: Use hull bounding rectangle
                hull_xs = [p[0] for p in hull]
                hull_ys = [p[1] for p in hull]
                min_x, max_x = min(hull_xs), max(hull_xs)
                min_y, max_y = min(hull_ys), max(hull_ys)

                # Add some padding
                padding = 10
                receipt_box_corners = [
                    (min_x - padding, min_y - padding),  # top-left
                    (max_x + padding, min_y - padding),  # top-right
                    (max_x + padding, max_y + padding),  # bottom-right
                    (min_x - padding, max_y + padding),  # bottom-left
                ]

                print(f"Fallback corners: {receipt_box_corners}")

                try:
                    transform_coeffs = find_perspective_coeffs(
                        src_points=receipt_box_corners, dst_points=dst_corners
                    )
                except ValueError as fallback_error:
                    print(
                        f"Even fallback failed for cluster {cluster_id}: {fallback_error}"
                    )
                    continue

            warped_img = image.transform(
                (warped_width, warped_height),
                Transform.PERSPECTIVE,
                transform_coeffs,
                resample=Resampling.BICUBIC,
            )

            # Upload the warped image to the raw bucket
            upload_png_to_s3(
                warped_img,
                raw_bucket,
                f"raw/{image_id}_RECEIPT_{cluster_id:05d}.png",
            )

            # Upload all CDN formats to site bucket with thumbnails
            receipt_cdn_keys = upload_all_cdn_formats(
                warped_img,
                site_bucket,
                f"assets/{image_id}_RECEIPT_{cluster_id:05d}",
                generate_thumbnails=True,
            )

            # Convert the receipt_box_corners from pixel coordinates to normalized
            # coordinates and format them as dictionaries
            # Note: Y coordinates are already in image coordinate system (Y=0 at top)
            # after flip_y=True in word.calculate_corners()
            top_left = {
                "x": receipt_box_corners[0][0] / image.width,
                "y": receipt_box_corners[0][1]
                / image.height,  # No flip needed
            }
            top_right = {
                "x": receipt_box_corners[1][0] / image.width,
                "y": receipt_box_corners[1][1]
                / image.height,  # No flip needed
            }
            bottom_right = {
                "x": receipt_box_corners[2][0] / image.width,
                "y": receipt_box_corners[2][1]
                / image.height,  # No flip needed
            }
            bottom_left = {
                "x": receipt_box_corners[3][0] / image.width,
                "y": receipt_box_corners[3][1]
                / image.height,  # No flip needed
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
                # Add thumbnail versions
                cdn_thumbnail_s3_key=receipt_cdn_keys.get("jpeg_thumbnail"),
                cdn_thumbnail_webp_s3_key=receipt_cdn_keys.get(
                    "webp_thumbnail"
                ),
                cdn_thumbnail_avif_s3_key=receipt_cdn_keys.get(
                    "avif_thumbnail"
                ),
                # Add small versions
                cdn_small_s3_key=receipt_cdn_keys.get("jpeg_small"),
                cdn_small_webp_s3_key=receipt_cdn_keys.get("webp_small"),
                cdn_small_avif_s3_key=receipt_cdn_keys.get("avif_small"),
                # Add medium versions
                cdn_medium_s3_key=receipt_cdn_keys.get("jpeg_medium"),
                cdn_medium_webp_s3_key=receipt_cdn_keys.get("webp_medium"),
                cdn_medium_avif_s3_key=receipt_cdn_keys.get("avif_medium"),
            )

            # Add the receipt to DynamoDB
            dynamo_client.add_receipt(receipt)

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
            dynamo_client.add_ocr_job(new_ocr_job)

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
            successful_clusters += 1

        except Exception as e:
            print(f"Error processing cluster {cluster_id}: {e}")
            continue

    ocr_routing_decision.status = OCRStatus.COMPLETED.value
    ocr_routing_decision.receipt_count = successful_clusters
    ocr_routing_decision.updated_at = datetime.now(timezone.utc)
    dynamo_client.update_ocr_routing_decision(ocr_routing_decision)
