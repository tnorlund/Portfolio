# infra/lambda_layer/python/dynamo/data/process_picture.py
import hashlib
import math
import os
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Tuple

import boto3
from botocore.exceptions import ClientError
from PIL import Image as PIL_Image

from receipt_dynamo.data._cluster import dbscan_lines
from receipt_dynamo.data._corner_process import extract_and_save_corner_windows
from receipt_dynamo.data._geometry import (compute_hull_centroid,
    compute_receipt_box_from_skewed_extents,
    convex_hull,
    find_hull_extents_relative_to_centroid,
    find_perspective_coeffs, )
from receipt_dynamo.data._ocr import apple_vision_ocr
from receipt_dynamo.data._pulumi import load_env
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities import (Image,
    Receipt,
    ReceiptLetter,
    ReceiptLine,
    ReceiptWindow,
    ReceiptWord, )


def process_picture(receipt_image_path: Path,
    raw_prefix: str = "raw",
    cdn_prefix: str = "assets",
    pulumi_env: str = "dev", ) -> None:
    s3 = boto3.client("s3")
    if raw_prefix.endswith("/"):
        raw_prefix = raw_prefix[:-1]
    if cdn_prefix.endswith("/"):
        cdn_prefix = cdn_prefix[:-1]
    # Load the environment variables
    env = load_env(pulumi_env)
    # Get the raw image bucket name
    raw_bucket_name = env["raw_bucket_name"]
    # Get the cdn bucket name
    cdn_bucket_name = env["cdn_bucket_name"]
    # Get the dynamo client
    dynamo_client = DynamoClient(env["dynamodb_table_name"])
    # List the images in the receipt image path
    image_paths = list(receipt_image_path.glob("*.png"))

    # Store the DynamoDB entities in lists to then add to the database at the
    # end.
    dynamo_images = []
    dynamo_lines = []
    dynamo_words = []
    dynamo_letters = []
    dynamo_receipts = []
    dynamo_receipt_lines = []
    dynamo_receipt_words = []
    dynamo_receipt_letters = []
    dynamo_receipt_windows = []
    print(f"Processing {len(image_paths)} images: ", end="", flush=True)
    for image_path in image_paths:
        (receipt_images,
            receipt_boxes,
            receipt_lines,
            receipt_words,
            receipt_letters,
            refined_lines,
            refined_words,
            refined_letters,
            windows, ) = _get_ocr_and_windows(image_path)
        image_id = refined_lines[0].image_id
        image = PIL_Image.open(image_path)
        dynamo_lines.extend(refined_lines)
        dynamo_words.extend(refined_words)
        dynamo_letters.extend(refined_letters)
        dynamo_receipt_lines.extend(receipt_lines)
        dynamo_receipt_words.extend(receipt_words)
        dynamo_receipt_letters.extend(receipt_letters)

        # Upload the original image to the raw bucket.
        try:
            s3.put_object(Bucket=raw_bucket_name,
                Key=f"{raw_prefix}/{image_id}.png",
                Body=image.tobytes(),
                ContentType="image/png", )
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "NoSuchBucket":
                raise ValueError(f"Bucket {raw_bucket_name} not found")
            elif error_code == "AccessDenied":
                raise ValueError(f"Access denied to s3://{raw_bucket_name}/{raw_prefix}")
            else:
                raise
        # Upload the original image to the cdn bucket as a JPEG
        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=85)
        buffer.seek(0)
        jpeg_data = buffer.getvalue()
        try:
            s3.put_object(Bucket=cdn_bucket_name,
                Key=f"{cdn_prefix}/{image_id}.jpg",
                Body=jpeg_data,
                ContentType="image/jpeg", )
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "NoSuchBucket":
                raise ValueError(f"Bucket {cdn_bucket_name} not found")
            elif error_code == "AccessDenied":
                raise ValueError(f"Access denied to s3://{cdn_bucket_name}/{cdn_prefix}")
            else:
                raise
        # Upload each receipt image to the raw bucket
        for i, receipt_image in enumerate(receipt_images, start=1):
            try:
                s3.put_object(Bucket=raw_bucket_name,
                    Key=f"{raw_prefix}/{image_id}_RECEIPT_{i:05d}.png",
                    Body=receipt_image.tobytes(),
                    ContentType="image/png", )
            except ClientError as e:
                error_code = e.response["Error"]["Code"]
                if error_code == "NoSuchBucket":
                    raise ValueError(f"Bucket {raw_bucket_name} not found")
                elif error_code == "AccessDenied":
                    raise ValueError(f"Access denied to s3://{raw_bucket_name}/{raw_prefix}")
                else:
                    raise
        # Upload each receipt image to the cdn bucket as a JPEG
        for i, receipt_image in enumerate(receipt_images, start=1):
            buffer = BytesIO()
            receipt_image.save(buffer, format="JPEG", quality=85)
            buffer.seek(0)
            jpeg_data = buffer.getvalue()
            try:
                s3.put_object(Bucket=cdn_bucket_name,
                    Key=f"{cdn_prefix}/{image_id}_RECEIPT_{i:05d}.jpg",
                    Body=jpeg_data,
                    ContentType="image/jpeg", )
            except ClientError as e:
                error_code = e.response["Error"]["Code"]
                if error_code == "NoSuchBucket":
                    raise ValueError(f"Bucket {cdn_bucket_name} not found")
                elif error_code == "AccessDenied":
                    raise ValueError(f"Access denied to s3://{cdn_bucket_name}/{cdn_prefix}")
                else:
                    raise

        # Build our top-level Image object
        dynamo_images.append(Image(image_id=image_id,
                width=image.width,
                height=image.height,
                timestamp_added=datetime.now(timezone.utc),
                raw_s3_bucket=raw_bucket_name,
                raw_s3_key=f"{raw_prefix}/{image_id}.png",
                cdn_s3_bucket=cdn_bucket_name,
                cdn_s3_key=f"{cdn_prefix}/{image_id}.jpg",
                sha256=_calculate_sha256_from_bytes(image.tobytes()), ))
        # Build each receipt object
        for i, receipt_image in enumerate(receipt_images, start=1):
            # Get the matching receipt box.
            receipt_box = next((box for box in receipt_boxes if box["receipt_id"] == i), None)
            if receipt_box is None:
                raise ValueError(f"Receipt box not found for receipt {i}")
            # Convert coordinates from [x,y] lists to {x:x, y:y} dicts
            top_left = {"x": receipt_box["receipt_box_corners"][0][0],
                "y": receipt_box["receipt_box_corners"][0][1], }
            top_right = {"x": receipt_box["receipt_box_corners"][1][0],
                "y": receipt_box["receipt_box_corners"][1][1], }
            bottom_right = {"x": receipt_box["receipt_box_corners"][2][0],
                "y": receipt_box["receipt_box_corners"][2][1], }
            bottom_left = {"x": receipt_box["receipt_box_corners"][3][0],
                "y": receipt_box["receipt_box_corners"][3][1], }
            dynamo_receipts.append(Receipt(receipt_id=i,
                    image_id=image_id,
                    width=image.width,
                    height=image.height,
                    timestamp_added=datetime.now(timezone.utc),
                    raw_s3_bucket=raw_bucket_name,
                    raw_s3_key=f"{raw_prefix}/{image_id}_RECEIPT_{i:05d}.png",
                    top_left=top_left,
                    top_right=top_right,
                    bottom_left=bottom_left,
                    bottom_right=bottom_right,
                    sha256=_calculate_sha256_from_bytes(receipt_image.tobytes()),
                    cdn_s3_bucket=cdn_bucket_name,
                    cdn_s3_key=f"{cdn_prefix}/{image_id}_RECEIPT_{i:05d}.jpg", ))

        # Get the matching receipt window.
        receipt_window = next((window for window in windows if window["receipt_id"] == i), None)
        if receipt_window is None:
            raise ValueError(f"Receipt window not found for receipt {i}")
        # Build each receipt window object
        for corner_name in ["top_left",
            "top_right",
            "bottom_left",
            "bottom_right", ]:
            window = receipt_window[corner_name]
            # Upload the window image to the raw bucket
            try:
                s3.put_object(Bucket=raw_bucket_name,
                    Key=f"{raw_prefix}/{image_id}_RECEIPT_{i:05d}_RECEIPT_WINDOW_{corner_name.upper()}.png",
                    Body=window["image"].tobytes(),
                    ContentType="image/png", )
            except ClientError as e:
                error_code = e.response["Error"]["Code"]
                if error_code == "NoSuchBucket":
                    raise ValueError(f"Bucket {raw_bucket_name} not found")
                elif error_code == "AccessDenied":
                    raise ValueError(f"Access denied to s3://{raw_bucket_name}/{raw_prefix}")
                else:
                    raise

            # Upload the window image to the cdn bucket as a JPEG
            buffer = BytesIO()
            window["image"].save(buffer, format="JPEG", quality=85)
            buffer.seek(0)
            jpeg_data = buffer.getvalue()
            try:
                s3.put_object(Bucket=cdn_bucket_name,
                    Key=f"{cdn_prefix}/{image_id}_RECEIPT_{i:05d}_RECEIPT_WINDOW_{corner_name.upper()}.jpg",
                    Body=jpeg_data,
                    ContentType="image/jpeg", )
            except ClientError as e:
                error_code = e.response["Error"]["Code"]
                if error_code == "NoSuchBucket":
                    raise ValueError(f"Bucket {cdn_bucket_name} not found")
                elif error_code == "AccessDenied":
                    raise ValueError(f"Access denied to s3://{cdn_bucket_name}/{cdn_prefix}")
                else:
                    raise

            # Build the receipt window object
            dynamo_receipt_windows.append(ReceiptWindow(image_id=image_id,
                    receipt_id=i,
                    cdn_s3_bucket=cdn_bucket_name,
                    cdn_s3_key=f"{cdn_prefix}/{image_id}_RECEIPT_{i:05d}_RECEIPT_WINDOW_{corner_name.upper()}.jpg",
                    corner_name=corner_name,
                    width=window["width"],
                    height=window["height"],
                    inner_corner_coordinates=window["inner_corner"],
                    gpt_guess=None, ))
        print(".", end="", flush=True)
    print()  # End the line after all chunks are processed.

    # Add all the entities to the database.
    _upload_entities_in_chunks(dynamo_client.addImages, dynamo_images, "Images")
    _upload_entities_in_chunks(dynamo_client.addLines, dynamo_lines, "Lines")
    _upload_entities_in_chunks(dynamo_client.addWords, dynamo_words, "Words")
    _upload_entities_in_chunks(dynamo_client.addLetters, dynamo_letters, "Letters")
    _upload_entities_in_chunks(dynamo_client.addReceipts, dynamo_receipts, "Receipts")
    _upload_entities_in_chunks(dynamo_client.addReceiptLines, dynamo_receipt_lines, "ReceiptLines")
    _upload_entities_in_chunks(dynamo_client.addReceiptWords, dynamo_receipt_words, "ReceiptWords")
    _upload_entities_in_chunks(dynamo_client.addReceiptLetters,
        dynamo_receipt_letters,
        "ReceiptLetters", )
    _upload_entities_in_chunks(dynamo_client.addReceiptWindows,
        dynamo_receipt_windows,
        "ReceiptWindows", )


def _upload_entities_in_chunks(upload_method, entities, entity_name):
    total = len(entities)
    # Print the initial message without a newline.
    print(f"Adding {total} {entity_name}: ", end="", flush=True)
    # Process entities in chunks of 25.
    for i in range(0, total, 25):
        chunk = entities[i : i + 25]
        upload_method(chunk)
        print(".", end="", flush=True)
    print()  # End the line after all chunks are processed.


def _get_ocr_and_windows(image_path: Path, ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """ """
    # Raise an error if the image path does not exist.
    if not image_path.exists():
        raise FileNotFoundError(f"Image path {image_path} does not exist.")
    image = PIL_Image.open(image_path)
    # Run the OCR
    ocr_dict = apple_vision_ocr([image_path])
    image_id = list(ocr_dict.keys())[0]
    lines, words, letters = ocr_dict[image_id]

    # Get the average diagonal length of the lines.
    avg_diagonal_length = sum([line.calculate_diagonal_length() for line in lines]) / len(lines)

    # Cluster the lines using our DBSCAN implementation.
    # Here we say that a receipt must have at least 10 lines.
    clusters = dbscan_lines(lines, eps=avg_diagonal_length * 2, min_samples=10)
    # Drop all clusters with an key of -1 (noise).
    clusters = {k: v for k, v in clusters.items() if k != -1}

    # For each cluster, we will need to store the following:
    # The images
    receipt_images = []
    # Each receipt bounding box.
    receipt_boxes = []
    # The receipt lines, words, and letters.
    receipt_lines = []
    receipt_words = []
    receipt_letters = []
    # The cluster will also provide refined lines, words, and letters.
    refined_lines = []
    refined_words = []
    refined_letters = []
    # The cluster will also have it's own windows.
    windows = []
    for cluster_id, cluster in clusters.items():
        warp_image_path = image_path.with_suffix("").with_name(f"{image_path.stem}_RECEIPT_{cluster_id}.png")
        cluster_lines = cluster
        line_ids = [line.line_id for line in cluster_lines]
        cluster_words = [w for w in words if w.line_id in line_ids]

        # Get the average angle of the lines.
        avg_angle_degrees = sum([line.angle_degrees for line in cluster_lines]) / len(cluster_lines)

        # Convert OCR coords to top-left for geometry
        all_word_corners_image_coordinates = []
        for w in cluster_words:
            corners_image_coordinates = w.calculate_corners(width=image.width,
                height=image.height,
                flip_y=True,  # produce top-left pixel coords)
            corners_int = [(int(x), int(y)) for (x, y) in corners_image_coordinates]
            all_word_corners_image_coordinates.extend(corners_int)

        # Compute hull, centroid, etc. in top-left pixel coords
        hull = convex_hull(all_word_corners_image_coordinates)
        cx, cy = compute_hull_centroid(hull)
        find_hull_extents_relative_to_centroid(hull, cx, cy)

        # Compute the perspective coefficients for the cluster.
        receipt_box_corners = compute_receipt_box_from_skewed_extents(hull, cx, cy, -avg_angle_degrees)
        receipt_boxes.append({"image_id": image_id,
                "receipt_id": cluster_id + 1,
                "receipt_box_corners": receipt_box_corners, })
        # Estimate average width/height of receipt box
        top_w = math.dist(receipt_box_corners[0], receipt_box_corners[1])
        bottom_w = math.dist(receipt_box_corners[3], receipt_box_corners[2])
        source_width = (top_w + bottom_w) / 2.0

        left_h = math.dist(receipt_box_corners[0], receipt_box_corners[3])
        right_h = math.dist(receipt_box_corners[1], receipt_box_corners[2])
        source_height = (left_h + right_h) / 2.0

        # Decide how big the warped image is
        scale = 1.0
        warped_image_width = int(round(source_width * scale))
        warped_image_height = int(round(source_height * scale))

        # Build destination corners (top-left coords)
        dst_corners = [(0, 0),
            (warped_image_width - 1, 0),
            (warped_image_width - 1, warped_image_height - 1),
            (0, warped_image_height - 1), ]
        a_i, b_i, c_i, d_i, e_i, f_i, g_i, h_i = find_perspective_coeffs(src_points=receipt_box_corners, dst_points=dst_corners)
        inverse_coeffs = (a_i, b_i, c_i, d_i, e_i, f_i, g_i, h_i)

        # Warp the image to only show the cluster.
        warped_img = image.transform((warped_image_width, warped_image_height),
            PIL_Image.PERSPECTIVE,
            inverse_coeffs,
            resample=PIL_Image.BICUBIC, )
        receipt_images.append(warped_img)
        warped_img.save(warp_image_path)
        # Perform OCR on the warped image.
        refined_lines, refined_words, refined_letters = list(apple_vision_ocr([warp_image_path]).values())[0]
        # Delete the warped image.
        os.remove(warp_image_path)

        # Convert the refined OCR results to Receipt-level objects.
        receipt_lines.extend([ReceiptLine(**{**dict(line),
                        "image_id": image_id,
                        "receipt_id": cluster_id + 1, })
                for line in refined_lines])
        receipt_words.extend([ReceiptWord(**{**dict(word),
                        "image_id": image_id,
                        "receipt_id": cluster_id + 1, })
                for word in refined_words])
        receipt_letters.extend([ReceiptLetter(**{**dict(letter),
                        "image_id": image_id,
                        "receipt_id": cluster_id + 1, })
                for letter in refined_letters])

        # Warp the refined lines back to the original image.
        for line in refined_lines:
            line.warp_transform(*inverse_coeffs,
                src_width=warped_image_width,
                src_height=warped_image_height,
                dst_width=image.width,
                dst_height=image.height, )
            line.image_id = image_id
        for word in refined_words:
            word.warp_transform(*inverse_coeffs,
                src_width=warped_image_width,
                src_height=warped_image_height,
                dst_width=image.width,
                dst_height=image.height, )
            word.image_id = image_id
        for letter in refined_letters:
            letter.warp_transform(*inverse_coeffs,
                src_width=warped_image_width,
                src_height=warped_image_height,
                dst_width=image.width,
                dst_height=image.height, )
            letter.image_id = image_id

        # Crop each region from the original image.
        windows.append({**extract_and_save_corner_windows(image=image,
                    receipt_box_corners=receipt_box_corners,
                    offset_distance=300,
                    max_dim=512, ),
                "image_id": image_id,
                "receipt_id": cluster_id + 1, })

    return (receipt_images,
        receipt_boxes,
        receipt_lines,
        receipt_words,
        receipt_letters,
        refined_lines,
        refined_words,
        refined_letters,
        windows, )


def _calculate_sha256_from_bytes(data: bytes) -> str:
    sha256_hash = hashlib.sha256(data)
    return sha256_hash.hexdigest()
