#!/usr/bin/env python3
"""
Clusters and groups lines of text into receipts, then uploads them to DynamoDB.

This script expects a PNG image and a corresponding JSON file in an S3 bucket:
- The PNG file is the input image.
- The JSON file contains OCR results (from a SwiftOCR model).

Steps:
1. The image, lines, words, and letters items are added to DynamoDB. The original image is uploaded to a CDN bucket.
2. Lines are then clustered using DBSCAN (sklearn) to identify receipts.
3. Each cluster (receipt) is transformed to fit into a 1x1 box.
4. The bounding box is cropped/warped from the original image and uploaded to another S3 bucket.
5. Receipt-level entities (receipt lines, words, letters) are added to DynamoDB.
"""

import os
import json
import hashlib
import logging
import tempfile
from datetime import datetime, timezone
from math import atan2, sin, cos, dist, atan
from typing import Any, Dict, List, Tuple

import boto3
import cv2
import numpy as np
from sklearn.cluster import DBSCAN
from botocore.exceptions import ClientError

# Local imports (assuming these modules exist in the same project)
from dynamo import (
    DynamoClient,
    Line,
    Word,
    Letter,
    Image,
    Receipt,
    ReceiptLine,
    ReceiptWord,
    ReceiptLetter,
)

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Read environment variables
DYNAMO_DB_TABLE = os.getenv("DYNAMO_DB_TABLE")
S3_BUCKET = os.getenv("S3_BUCKET")
CDN_S3_BUCKET = os.getenv("CDN_S3_BUCKET")
CDN_PATH = os.getenv("CDN_PATH")

if not all([DYNAMO_DB_TABLE, S3_BUCKET, CDN_S3_BUCKET, CDN_PATH]):
    missing_vars = [
        name
        for name, val in [
            ("DYNAMO_DB_TABLE", DYNAMO_DB_TABLE),
            ("S3_BUCKET", S3_BUCKET),
            ("CDN_S3_BUCKET", CDN_S3_BUCKET),
            ("CDN_PATH", CDN_PATH),
        ]
        if not val
    ]
    raise ValueError(f"Missing environment variables: {', '.join(missing_vars)}")


def process_ocr_dict(
    ocr_data: Dict[str, Any], image_id: int
) -> Tuple[List[Line], List[Word], List[Letter]]:
    """
    Convert OCR data from SwiftOCR into lists of Line, Word, and Letter objects.

    Args:
        ocr_data: A dictionary containing OCR information.
        image_id: Unique ID for the image these lines/words/letters belong to.

    Returns:
        A tuple of (lines, words, letters).
    """
    lines, words, letters = [], [], []
    for line_idx, line_data in enumerate(ocr_data.get("lines", []), start=1):
        line_obj = Line(
            image_id=image_id,
            id=line_idx,
            text=line_data["text"],
            bounding_box=line_data["bounding_box"],
            top_right=line_data["top_right"],
            top_left=line_data["top_left"],
            bottom_right=line_data["bottom_right"],
            bottom_left=line_data["bottom_left"],
            angle_degrees=line_data["angle_degrees"],
            angle_radians=line_data["angle_radians"],
            confidence=line_data["confidence"],
        )
        lines.append(line_obj)

        for word_idx, word_data in enumerate(line_data.get("words", []), start=1):
            word_obj = Word(
                image_id=image_id,
                line_id=line_idx,
                id=word_idx,
                text=word_data["text"],
                bounding_box=word_data["bounding_box"],
                top_right=word_data["top_right"],
                top_left=word_data["top_left"],
                bottom_right=word_data["bottom_right"],
                bottom_left=word_data["bottom_left"],
                angle_degrees=word_data["angle_degrees"],
                angle_radians=word_data["angle_radians"],
                confidence=word_data["confidence"],
            )
            words.append(word_obj)

            for letter_idx, letter_data in enumerate(
                word_data.get("letters", []), start=1
            ):
                letter_obj = Letter(
                    image_id=image_id,
                    line_id=line_idx,
                    word_id=word_idx,
                    id=letter_idx,
                    text=letter_data["text"],
                    bounding_box=letter_data["bounding_box"],
                    top_right=letter_data["top_right"],
                    top_left=letter_data["top_left"],
                    bottom_right=letter_data["bottom_right"],
                    bottom_left=letter_data["bottom_left"],
                    angle_degrees=letter_data["angle_degrees"],
                    angle_radians=letter_data["angle_radians"],
                    confidence=letter_data["confidence"],
                )
                letters.append(letter_obj)

    return lines, words, letters


def calculate_sha256(file_path: str) -> str:
    """
    Calculate the SHA-256 hash of a file.

    Args:
        file_path: The path to the file to hash.

    Returns:
        The SHA-256 hash in hexadecimal format.
    """
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


def euclidean_dist(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    """Return the Euclidean distance between two points a and b."""
    return dist(a, b)


def rotate_point(
    x: float, y: float, cx: float, cy: float, theta: float
) -> Tuple[float, float]:
    """
    Rotate point (x, y) around center (cx, cy) by theta radians.

    Returns:
        (x_rot, y_rot): The rotated x, y coordinates.
    """
    # Translate to origin
    x_trans = x - cx
    y_trans = y - cy
    # Rotate
    x_rot = x_trans * cos(theta) - y_trans * sin(theta)
    y_rot = x_trans * sin(theta) + y_trans * cos(theta)
    # Translate back
    return (x_rot + cx, y_rot + cy)


def add_initial_image(
    s3_path: str, uuid: str, image_id: int, cdn_path: str
) -> Tuple[Image, List[Line], List[Word], List[Letter]]:
    """
    Download image & OCR data from S3, compute SHA-256, and store them in DynamoDB.

    Args:
        s3_path: The path (prefix) to the .json file in S3.
        uuid: Unique identifier for the file names.
        image_id: Unique ID for this image in DynamoDB.
        cdn_path: The path (prefix) to use when uploading to the CDN bucket.

    Returns:
        A tuple of:
            (image_object, list_of_lines, list_of_words, list_of_letters)
    """
    dynamo_client = DynamoClient(DYNAMO_DB_TABLE)
    s3_client = boto3.client("s3")

    # Download the PNG from S3 to a temp file
    png_key = f"{s3_path}{uuid}.png"
    local_png_path = None

    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_png:
            local_png_path = temp_png.name
            logger.info(f"Downloading {png_key} from s3://{S3_BUCKET}/...")
            s3_client.download_file(S3_BUCKET, png_key, local_png_path)
    except ClientError as exc:
        logger.error(f"Failed to download PNG from S3: {exc}")
        raise

    if local_png_path is None:
        raise ValueError("Temporary PNG file was not created.")

    # Compute SHA256 and read image size
    sha256 = calculate_sha256(local_png_path)
    img_cv = cv2.imread(local_png_path)
    if img_cv is None:
        raise ValueError("Downloaded image is invalid or cannot be opened by OpenCV.")

    height, width, _ = img_cv.shape

    # Upload the original image to the CDN bucket
    cdn_s3_key = f"{cdn_path}{uuid}.png"
    try:
        s3_client.upload_file(local_png_path, CDN_S3_BUCKET, cdn_s3_key)
        logger.info(f"Uploaded file to s3://{CDN_S3_BUCKET}/{cdn_s3_key}")
    except ClientError as exc:
        logger.error(f"Failed to upload PNG to CDN bucket: {exc}")
        raise

    # Create and store Image in DynamoDB
    image = Image(
        id=image_id,
        width=width,
        height=height,
        timestamp_added=datetime.now(timezone.utc).isoformat(),
        raw_s3_bucket=S3_BUCKET,
        raw_s3_key=png_key,
        sha256=sha256,
        cdn_s3_bucket=CDN_S3_BUCKET,
        cdn_s3_key=cdn_s3_key,
    )
    dynamo_client.addImage(image)

    # Read the JSON (OCR data) from S3
    json_key = f"{s3_path}{uuid}.json"
    local_json_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as temp_json:
            local_json_path = temp_json.name
            logger.info(f"Downloading {json_key} from s3://{S3_BUCKET}/...")
            s3_client.download_file(S3_BUCKET, json_key, local_json_path)
    except ClientError as exc:
        logger.error(f"Failed to download JSON from S3: {exc}")
        raise

    if local_json_path is None:
        raise ValueError("Temporary JSON file was not created.")

    with open(local_json_path, "r", encoding="utf-8") as f:
        ocr_data = json.load(f)

    lines, words, letters = process_ocr_dict(ocr_data, image_id)
    logger.info(
        "Adding %d lines, %d words, %d letters, and the image to DynamoDB.",
        len(lines),
        len(words),
        len(letters),
    )
    dynamo_client.addLines(lines)
    dynamo_client.addWords(words)
    dynamo_client.addLetters(letters)

    return image, lines, words, letters


def cluster_image(lines: List[Line]) -> Dict[int, List[Line]]:
    """
    Cluster lines of text using DBSCAN based on their x-centroids.

    Returns:
        A dictionary mapping cluster_id -> list_of_lines.
    """
    if not lines:
        return {}

    # Calculate average angle to roughly align text horizontally
    avg_angle = sum(ln.angle_radians for ln in lines) / len(lines)

    # Rotate lines so they are axis-aligned
    rotated_lines = lines.copy()
    for ln in rotated_lines:
        ln.rotate(-avg_angle, 0.5, 0.5, use_radians=True)

    # Gather x-coordinates of centroids for DBSCAN
    X = np.array([line.calculate_centroid()[0] for line in rotated_lines]).reshape(
        -1, 1
    )

    # Run DBSCAN
    db = DBSCAN(eps=0.08, min_samples=2)
    db.fit(X)
    labels = db.labels_

    # Adjust labels so -1 stays -1, but other labels become positive and 1-indexed
    offset_labels = [label if label == -1 else label + 1 for label in labels]

    # Assign cluster_ids back to original lines
    for i, line in enumerate(lines):
        line.cluster_id = offset_labels[i]

    # Group lines by cluster_id
    cluster_dict: Dict[int, List[Line]] = {}
    for line in lines:
        if line.cluster_id == -1:
            continue  # skip noise
        if line.cluster_id not in cluster_dict:
            cluster_dict[line.cluster_id] = []
        cluster_dict[line.cluster_id].append(line)

    logger.info("Found %d receipts (clusters).", len(cluster_dict))
    return cluster_dict


def original_entities_to_receipt_entities(
    cluster_entities: Tuple[List[Line], List[Word], List[Letter]],
    min_x: float,
    min_y: float,
    scale_x: float,
    scale_y: float,
    cluster_id: int,
) -> None:
    """
    Transform Lines, Words, Letters into the coordinate space of a single receipt.
    Then write them as ReceiptLine, ReceiptWord, ReceiptLetter to DynamoDB.
    """
    logger.info("Transforming cluster %d to receipt entities.", cluster_id)
    cluster_lines, cluster_words, cluster_letters = cluster_entities

    receipt_lines: List[ReceiptLine] = []
    receipt_words: List[ReceiptWord] = []
    receipt_letters: List[ReceiptLetter] = []

    # Lines
    for ln in cluster_lines:
        ln.translate(-min_x, -min_y)
        ln.scale(scale_x, scale_y)
        receipt_lines.append(
            ReceiptLine(
                receipt_id=int(cluster_id),
                image_id=ln.image_id,
                id=ln.id,
                text=ln.text,
                bounding_box=ln.bounding_box,
                top_right=ln.top_right,
                top_left=ln.top_left,
                bottom_right=ln.bottom_right,
                bottom_left=ln.bottom_left,
                angle_degrees=ln.angle_degrees,
                angle_radians=ln.angle_radians,
                confidence=ln.confidence,
            )
        )

    # Words
    for wd in cluster_words:
        wd.translate(-min_x, -min_y)
        wd.scale(scale_x, scale_y)
        receipt_words.append(
            ReceiptWord(
                receipt_id=int(cluster_id),
                image_id=wd.image_id,
                line_id=wd.line_id,
                id=wd.id,
                text=wd.text,
                bounding_box=wd.bounding_box,
                top_right=wd.top_right,
                top_left=wd.top_left,
                bottom_right=wd.bottom_right,
                bottom_left=wd.bottom_left,
                angle_degrees=wd.angle_degrees,
                angle_radians=wd.angle_radians,
                confidence=wd.confidence,
                tags=wd.tags,
            )
        )

    # Letters
    for lt in cluster_letters:
        lt.translate(-min_x, -min_y)
        lt.scale(scale_x, scale_y)
        receipt_letters.append(
            ReceiptLetter(
                receipt_id=int(cluster_id),
                image_id=lt.image_id,
                line_id=lt.line_id,
                word_id=lt.word_id,
                id=lt.id,
                text=lt.text,
                bounding_box=lt.bounding_box,
                top_right=lt.top_right,
                top_left=lt.top_left,
                bottom_right=lt.bottom_right,
                bottom_left=lt.bottom_left,
                angle_degrees=lt.angle_degrees,
                angle_radians=lt.angle_radians,
                confidence=lt.confidence,
            )
        )

    logger.info(
        "Adding %d receipt lines, %d receipt words, and %d receipt letters to DynamoDB.",
        len(receipt_lines),
        len(receipt_words),
        len(receipt_letters),
    )
    dynamo_client = DynamoClient(DYNAMO_DB_TABLE)
    dynamo_client.addReceiptLines(receipt_lines)
    dynamo_client.addReceiptWords(receipt_words)
    dynamo_client.addReceiptLetters(receipt_letters)


def transform_cluster(
    cluster_id: int,
    cluster_lines: List[Line],
    cluster_words: List[Word],
    cluster_letters: List[Letter],
    image_cv: np.ndarray,
    image_obj: Image,
) -> None:
    """
    Given a cluster of lines, create a bounding box transform that crops and warps
    the corresponding region from the original image. Upload the cropped image to S3,
    then store a Receipt object in DynamoDB.
    """
    logger.info("Transforming cluster %d into bounding box.", cluster_id)
    # Compute cluster’s centroid from line centroids
    sum_x = 0.0
    sum_y = 0.0
    for ln in cluster_lines:
        cx, cy = ln.calculate_centroid()
        sum_x += cx
        sum_y += cy

    cluster_center_x = sum_x / len(cluster_lines)
    cluster_center_y = sum_y / len(cluster_lines)

    # Translate lines so centroid moves to (0.5, 0.5)
    dx = 0.5 - cluster_center_x
    dy = 0.5 - cluster_center_y
    for ln in cluster_lines:
        ln.translate(dx, dy)

    # Rotate around (0.5, 0.5) by -avg_angle
    avg_angle_radians = sum(ln.angle_radians for ln in cluster_lines) / len(
        cluster_lines
    )
    for ln in cluster_lines:
        ln.rotate(-avg_angle_radians, 0.5, 0.5, use_radians=True)

    # Compute bounding box in transformed space
    all_points = []
    for ln in cluster_lines:
        all_points.extend(
            [
                (ln.top_left["x"], ln.top_left["y"]),
                (ln.top_right["x"], ln.top_right["y"]),
                (ln.bottom_left["x"], ln.bottom_left["y"]),
                (ln.bottom_right["x"], ln.bottom_right["y"]),
            ]
        )

    min_x = min(pt[0] for pt in all_points)
    max_x = max(pt[0] for pt in all_points)
    min_y = min(pt[1] for pt in all_points)
    max_y = max(pt[1] for pt in all_points)

    # Scale lines so bounding box fits within [0, 1]
    range_x = max_x - min_x
    range_y = max_y - min_y
    scale_x = 1.0 / range_x if range_x != 0 else 1.0
    scale_y = 1.0 / range_y if range_y != 0 else 1.0

    original_entities_to_receipt_entities(
        (cluster_lines, cluster_words, cluster_letters),
        min_x,
        min_y,
        scale_x,
        scale_y,
        cluster_id,
    )

    # Reverse transformations to find original bounding box corners
    def reverse_transform(point: Tuple[float, float]) -> Tuple[float, float]:
        """
        1) Rotate back by +avg_angle_radians around (0.5, 0.5)
        2) Translate back by (-dx, -dy)
        """
        x_rot, y_rot = rotate_point(point[0], point[1], 0.5, 0.5, +avg_angle_radians)
        return x_rot - dx, y_rot - dy

    top_left = reverse_transform((min_x, min_y))
    top_right = reverse_transform((max_x, min_y))
    bottom_left = reverse_transform((min_x, max_y))
    bottom_right = reverse_transform((max_x, max_y))

    # Convert normalized points to absolute image coordinates
    pt_bl = (top_left[0] * image_obj.width, (1 - top_left[1]) * image_obj.height)
    pt_br = (top_right[0] * image_obj.width, (1 - top_right[1]) * image_obj.height)
    pt_tl = (bottom_left[0] * image_obj.width, (1 - bottom_left[1]) * image_obj.height)
    pt_tr = (
        bottom_right[0] * image_obj.width,
        (1 - bottom_right[1]) * image_obj.height,
    )

    width_top = euclidean_dist(pt_tl, pt_tr)
    width_bot = euclidean_dist(pt_bl, pt_br)
    height_left = euclidean_dist(pt_tl, pt_bl)
    height_right = euclidean_dist(pt_tr, pt_br)

    dst_width = int((width_top + width_bot) / 2.0)
    dst_height = int((height_left + height_right) / 2.0)

    src_pts = np.float32([pt_tl, pt_tr, pt_bl, pt_br])
    dst_pts = np.float32(
        [
            (0, 0),
            (dst_width - 1, 0),
            (0, dst_height - 1),
            (dst_width - 1, dst_height - 1),
        ]
    )
    M = cv2.getPerspectiveTransform(src_pts, dst_pts)

    # Warp the original image
    warped = cv2.warpPerspective(image_cv, M, (dst_width, dst_height))
    height, width, _ = warped.shape

    # Save the warped image to a temporary file
    s3_client = boto3.client("s3")
    cdn_s3_key = image_obj.cdn_s3_key.replace(".png", f"_cluster_{cluster_id:05d}.png")
    with tempfile.NamedTemporaryFile(suffix=".png", delete=True) as temp:
        cv2.imwrite(temp.name, warped)
        try:
            s3_client.upload_file(temp.name, CDN_S3_BUCKET, cdn_s3_key)
        except ClientError as exc:
            logger.error(f"Failed to upload warped cluster image to S3: {exc}")
            raise
        sha256 = calculate_sha256(temp.name)

    # Store the Receipt in DynamoDB
    receipt = Receipt(
        image_id=image_obj.id,
        id=int(cluster_id),
        width=width,
        height=height,
        timestamp_added=datetime.now(timezone.utc).isoformat(),
        raw_s3_bucket=image_obj.raw_s3_bucket,
        raw_s3_key=image_obj.raw_s3_key,
        top_left={"x": top_left[0], "y": top_left[1]},
        top_right={"x": top_right[0], "y": top_right[1]},
        bottom_left={"x": bottom_left[0], "y": bottom_left[1]},
        bottom_right={"x": bottom_right[0], "y": bottom_right[1]},
        sha256=sha256,
        cdn_s3_bucket=CDN_S3_BUCKET,
        cdn_s3_key=cdn_s3_key,
    )
    dynamo_client = DynamoClient(DYNAMO_DB_TABLE)
    dynamo_client.addReceipt(receipt)


def write_results(image_id: int) -> None:
    """Write the results as a JSON to the raw S3 bucket."""
    image, lines, words, letters, receipts = DynamoClient(
        DYNAMO_DB_TABLE
    ).getImageDetails(image_id)
    s3_client = boto3.client("s3")
    s3_key = image.raw_s3_key.replace(".png", "_results.json")
    logger.info(f"Writing results JSON to S3 for image_id={image_id} at {s3_key}")
    try:
        s3_client.put_object(
            Bucket=image.raw_s3_bucket,
            Key=s3_key,
            Body=json.dumps(
                {
                    "images": dict(image),
                    "lines": [dict(line) for line in lines],
                    "words": [dict(word) for word in words],
                    "letters": [dict(letter) for letter in letters],
                    "receipts": [
                        {
                            "receipt": dict(receipt["receipt"]),
                            "lines": [dict(line) for line in receipt["lines"]],
                            "words": [dict(word) for word in receipt["words"]],
                            "letters": [dict(letter) for letter in receipt["letters"]],
                        }
                        for receipt in receipts
                    ],
                }
            ),
        )
    except ClientError as exc:
        logger.error(f"Failed to upload results JSON to S3: {exc}")
        raise


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    AWS Lambda entry point.

    Expects an event dictionary with:
      - uuid: The unique identifier for the file (str).
      - s3_path: The prefix for the OCR JSON file in S3 (str).
      - image_id: An integer ID for the image in DynamoDB (int).
    """
    logger.info("Lambda handler invoked! Checking environment ...")
    uuid = event.get("uuid")
    s3_path = event.get("s3_path")
    image_id = event.get("image_id")

    if not all([uuid, s3_path, image_id]):
        raise ValueError("Missing required keys in event: uuid, s3_path, image_id")

    logger.info("Starting cluster process for uuid=%s, image_id=%d", uuid, image_id)
    image_obj, lines, words, letters = add_initial_image(
        s3_path, uuid, image_id, cdn_path=CDN_PATH
    )
    cluster_dict = cluster_image(lines)

    # Download the original image for warping
    s3_client = boto3.client("s3")
    local_original_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp:
            local_original_path = temp.name
            s3_client.download_file(
                image_obj.raw_s3_bucket, image_obj.raw_s3_key, local_original_path
            )
    except ClientError as exc:
        logger.error(f"Failed to download original image for warping: {exc}")
        raise

    if local_original_path is None:
        raise ValueError("Temporary file for original image was not created.")

    img_cv = cv2.imread(local_original_path)
    if img_cv is None:
        raise ValueError("Original image is invalid or cannot be opened by OpenCV.")

    # Transform and store each cluster
    for c_id, c_lines in cluster_dict.items():
        if c_id == -1:
            continue  # skip outliers
        line_ids = [ln.id for ln in c_lines]
        c_words = [w for w in words if w.line_id in line_ids]
        c_letters = [lt for lt in letters if lt.line_id in line_ids]

        logger.info("Processing cluster %d with %d lines.", c_id, len(c_lines))
        transform_cluster(c_id, c_lines, c_words, c_letters, img_cv, image_obj)
    # Write the results back to S3
    write_results(image_id)

    return {
        "statusCode": 200,
        "body": json.dumps(
            f"Processed {len(cluster_dict)} clusters for image_id={image_id}"
        ),
    }
