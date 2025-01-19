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
from math import atan2, degrees, sin, cos, dist, atan
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

    # Just gather X-coordinates for clustering (no rotation yet)
    X = np.array([line.calculate_centroid()[0] for line in lines]).reshape(-1, 1)

    db = DBSCAN(eps=0.08, min_samples=2)
    db.fit(X)
    labels = db.labels_

    # Adjust labels so -1 stays -1, but other labels become +1
    offset_labels = [label if label == -1 else label + 1 for label in labels]

    # Assign cluster IDs back to the original lines
    for i, line in enumerate(lines):
        line.cluster_id = offset_labels[i]

    # Group lines by cluster_id
    cluster_dict: Dict[int, List[Line]] = {}
    for line in lines:
        if line.cluster_id == -1:
            continue  # skip noise
        cluster_dict.setdefault(line.cluster_id, []).append(line)

    logger.info("Found %d receipts (clusters).", len(cluster_dict))
    return cluster_dict


def store_cluster_entities(
    cluster_id: int,
    image_id: int,
    lines: List[Line],
    words: List[Word],
    letters: List[Letter],
    M: np.ndarray,
    receipt_width: int,
    receipt_height: int,
    table_name: str,
    image_obj: Image,
) -> None:
    """
    Given a single cluster's lines/words/letters from the *original* image,
    warp them into the cluster's local coordinate space (using the perspective
    transform matrix M), then store them as ReceiptLine, ReceiptWord,
    and ReceiptLetter in DynamoDB.

    Args:
        cluster_id: The integer ID you want to use for 'receipt_id'.
        image_id: The integer ID for the original image.
        lines, words, letters: The original OCR items that belong to this cluster.
        M: The perspective transform matrix from cv2.getPerspectiveTransform()
        receipt_width, receipt_height: The final width/height of the warped cluster.
        table_name: Name of your DynamoDB table with the "ReceiptLine," etc.
    """
    def warp_point(x_abs, y_abs, M):
        arr = np.array([[[x_abs, y_abs]]], dtype="float32")
        warped = cv2.perspectiveTransform(arr, M)
        return (float(warped[0][0][0]), float(warped[0][0][1]))

    def compute_angle_in_warped_space(w_tl, w_tr):
        """
        Compute angle (in radians/degrees) of the top edge,
        given top-left and top-right points in the *warped* coordinate system.
        """
        dx = w_tr[0] - w_tl[0]
        dy = w_tr[1] - w_tl[1]
        angle_radians = atan2(dy, dx)
        angle_degrees = degrees(angle_radians)
        return angle_degrees, angle_radians

    dynamo_client = DynamoClient(table_name)

    receipt_lines = []
    receipt_words = []
    receipt_letters = []

    # For each original line/word/letter, transform the bounding corners:
    # top_left, top_right, bottom_left, bottom_right.

    for ln in lines:
        tl_abs = (ln.top_left["x"] * image_obj.width,
                  (1 - ln.top_left["y"]) * image_obj.height)
        tr_abs = (ln.top_right["x"] * image_obj.width,
                  (1 - ln.top_right["y"]) * image_obj.height)
        bl_abs = (ln.bottom_left["x"] * image_obj.width,
                  (1 - ln.bottom_left["y"]) * image_obj.height)
        br_abs = (ln.bottom_right["x"] * image_obj.width,
                  (1 - ln.bottom_right["y"]) * image_obj.height)

        # Warp each corner
        w_tl = warp_point(*tl_abs, M)
        w_tr = warp_point(*tr_abs, M)
        w_bl = warp_point(*bl_abs, M)
        w_br = warp_point(*br_abs, M)

        angle_degrees, angle_radians = compute_angle_in_warped_space(w_tl, w_tr)

        top_left = {
            "x": w_tl[0] / receipt_width,
            "y": 1.0 - (w_tl[1] / receipt_height),
        }
        top_right = {
            "x": w_tr[0] / receipt_width,
            "y": 1.0 - (w_tr[1] / receipt_height),
        }
        bottom_left = {
            "x": w_bl[0] / receipt_width,
            "y": 1.0 - (w_bl[1] / receipt_height),
        }
        bottom_right = {
            "x": w_br[0] / receipt_width,
            "y": 1.0 - (w_br[1] / receipt_height),
        }

        min_x = min(top_left["x"], top_right["x"], bottom_left["x"], bottom_right["x"])
        max_x = max(top_left["x"], top_right["x"], bottom_left["x"], bottom_right["x"])
        min_y = min(top_left["y"], top_right["y"], bottom_left["y"], bottom_right["y"])
        max_y = max(top_left["y"], top_right["y"], bottom_left["y"], bottom_right["y"])
        bounding_box = {
            "x": min_x,
            "y": min_y,
            "width": (max_x - min_x),
            "height": (max_y - min_y),
        }

        # Create a ReceiptLine that references “cluster_id” as the receipt_id
        receipt_line = ReceiptLine(
            receipt_id=int(cluster_id),
            image_id=image_id,
            id=ln.id,  # or reassign new IDs if you prefer
            text=ln.text,
            bounding_box=bounding_box,
            top_right=top_right,
            top_left=top_left,
            bottom_right=bottom_right,
            bottom_left=bottom_left,
            angle_degrees=angle_degrees,
            angle_radians=angle_radians,
            confidence=ln.confidence,
        )
        receipt_lines.append(receipt_line)

    # Repeat the same approach for Words:
    for wd in words:
        tl_abs = (wd.top_left["x"] * image_obj.width,
                  (1 - wd.top_left["y"]) * image_obj.height)
        tr_abs = (ln.top_right["x"] * image_obj.width,
                  (1 - wd.top_right["y"]) * image_obj.height)
        bl_abs = (ln.bottom_left["x"] * image_obj.width,
                  (1 - wd.bottom_left["y"]) * image_obj.height)
        br_abs = (ln.bottom_right["x"] * image_obj.width,
                  (1 - wd.bottom_right["y"]) * image_obj.height)

        # Warp each corner
        w_tl = warp_point(*tl_abs, M)
        w_tr = warp_point(*tr_abs, M)
        w_bl = warp_point(*bl_abs, M)
        w_br = warp_point(*br_abs, M)

        angle_degrees, angle_radians = compute_angle_in_warped_space(w_tl, w_tr)

        top_left = {
            "x": w_tl[0] / receipt_width,
            "y": 1.0 - (w_tl[1] / receipt_height),
        }
        top_right = {
            "x": w_tr[0] / receipt_width,
            "y": 1.0 - (w_tr[1] / receipt_height),
        }
        bottom_left = {
            "x": w_bl[0] / receipt_width,
            "y": 1.0 - (w_bl[1] / receipt_height),
        }
        bottom_right = {
            "x": w_br[0] / receipt_width,
            "y": 1.0 - (w_br[1] / receipt_height),
        }

        min_x = min(top_left["x"], top_right["x"], bottom_left["x"], bottom_right["x"])
        max_x = max(top_left["x"], top_right["x"], bottom_left["x"], bottom_right["x"])
        min_y = min(top_left["y"], top_right["y"], bottom_left["y"], bottom_right["y"])
        max_y = max(top_left["y"], top_right["y"], bottom_left["y"], bottom_right["y"])
        bounding_box = {
            "x": min_x,
            "y": min_y,
            "width": (max_x - min_x),
            "height": (max_y - min_y),
        }

        # Create a ReceiptWord that references “cluster_id” as the receipt_id
        receipt_word = ReceiptWord(
            receipt_id=int(cluster_id),
            image_id=image_id,
            line_id=wd.line_id,
            id=wd.id,  # or reassign new IDs if you prefer
            text=wd.text,
            bounding_box=bounding_box,
            top_right=top_right,
            top_left=top_left,
            bottom_right=bottom_right,
            bottom_left=bottom_left,
            angle_degrees=angle_degrees,
            angle_radians=angle_radians,
            confidence=wd.confidence,
        )
        receipt_words.append(receipt_word)

    # And for Letters:
    for lt in letters:
        tl_abs = (lt.top_left["x"] * image_obj.width,
                  (1 - lt.top_left["y"]) * image_obj.height)
        tr_abs = (ln.top_right["x"] * image_obj.width,
                  (1 - lt.top_right["y"]) * image_obj.height)
        bl_abs = (ln.bottom_left["x"] * image_obj.width,
                  (1 - lt.bottom_left["y"]) * image_obj.height)
        br_abs = (ln.bottom_right["x"] * image_obj.width,
                  (1 - lt.bottom_right["y"]) * image_obj.height)

        # Warp each corner
        w_tl = warp_point(*tl_abs, M)
        w_tr = warp_point(*tr_abs, M)
        w_bl = warp_point(*bl_abs, M)
        w_br = warp_point(*br_abs, M)

        angle_degrees, angle_radians = compute_angle_in_warped_space(w_tl, w_tr)

        top_left = {
            "x": w_tl[0] / receipt_width,
            "y": 1.0 - (w_tl[1] / receipt_height),
        }
        top_right = {
            "x": w_tr[0] / receipt_width,
            "y": 1.0 - (w_tr[1] / receipt_height),
        }
        bottom_left = {
            "x": w_bl[0] / receipt_width,
            "y": 1.0 - (w_bl[1] / receipt_height),
        }
        bottom_right = {
            "x": w_br[0] / receipt_width,
            "y": 1.0 - (w_br[1] / receipt_height),
        }

        min_x = min(top_left["x"], top_right["x"], bottom_left["x"], bottom_right["x"])
        max_x = max(top_left["x"], top_right["x"], bottom_left["x"], bottom_right["x"])
        min_y = min(top_left["y"], top_right["y"], bottom_left["y"], bottom_right["y"])
        max_y = max(top_left["y"], top_right["y"], bottom_left["y"], bottom_right["y"])
        bounding_box = {
            "x": min_x,
            "y": min_y,
            "width": (max_x - min_x),
            "height": (max_y - min_y),
        }

        # Create a ReceiptLetter that references “cluster_id” as the receipt_id
        receipt_letter = ReceiptLetter(
            receipt_id=int(cluster_id),
            image_id=image_id,
            line_id=lt.line_id,
            word_id=lt.word_id,
            id=lt.id,  # or reassign new IDs if you prefer
            text=lt.text,
            bounding_box=bounding_box,
            top_right=top_right,
            top_left=top_left,
            bottom_right=bottom_right,
            bottom_left=bottom_left,
            angle_degrees=angle_degrees,
            angle_radians=angle_radians,
            confidence=lt.confidence,
        )
        receipt_letters.append(receipt_letter)

    dynamo_client.addReceiptLines(receipt_lines)
    dynamo_client.addReceiptWords(receipt_words)
    dynamo_client.addReceiptLetters(receipt_letters)

    print(f"Added {len(receipt_lines)} receipt lines, "
          f"{len(receipt_words)} words, and "
          f"{len(receipt_letters)} letters for receipt {cluster_id}.")

def order_points(pts_4):
    # pts_4: shape (4,2)
    # Returns: array of shape (4,2) in order: TL, TR, BR, BL
    rect = np.zeros((4, 2), dtype="float32")

    # sort by y first, then x
    s = sorted(pts_4, key=lambda x: (x[1], x[0]))
    top_two = s[0:2]
    bottom_two = s[2:4]

    # top_two: left is TL, right is TR
    # bottom_two: left is BL, right is BR
    if top_two[0][0] < top_two[1][0]:
        rect[0], rect[1] = top_two[0], top_two[1]
    else:
        rect[0], rect[1] = top_two[1], top_two[0]

    if bottom_two[0][0] < bottom_two[1][0]:
        rect[3], rect[2] = bottom_two[0], bottom_two[1]
    else:
        rect[3], rect[2] = bottom_two[1], bottom_two[0]
    return rect

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
    # 1) Gather all line corners in absolute pixel space
    points_abs = []
    for ln in cluster_lines:
        for corner in [ln.top_left, ln.top_right, ln.bottom_left, ln.bottom_right]:
            # corner is e.g. {"x": 0.22, "y": 0.77}
            x_abs = corner["x"] * image_obj.width
            y_abs = (1 - corner["y"]) * image_obj.height  # note 1 - y if top=0
            points_abs.append((x_abs, y_abs))
    
    if not points_abs:
        logger.warning("No corners for cluster %d, skipping warp.", cluster_id)
        return

    pts = np.array(points_abs, dtype=np.float32)

    # 2) Compute min-area bounding rect
    rect = cv2.minAreaRect(pts)     # (center, (w, h), angle)
    box = cv2.boxPoints(rect)       # 4 corner points
    box = np.array(box, dtype=np.float32)

    # 3) Order points, warp to upright rectangle
    box_ordered = order_points(box)  # see function above
    w = int(rect[1][0])
    h = int(rect[1][1])
    # You might want to check if w < h and possibly swap them
    # so that the receipt is "portrait" rather than "landscape."
    
    dst_pts = np.array([
        [0,     0],
        [w - 1, 0],
        [w - 1, h - 1],
        [0,     h - 1]
    ], dtype="float32")

    M = cv2.getPerspectiveTransform(box_ordered, dst_pts)
    warped = cv2.warpPerspective(image_cv, M, (w, h))

    store_cluster_entities(
        cluster_id=cluster_id,
        image_id=image_obj.id,
        lines=cluster_lines,
        words=cluster_words,
        letters=cluster_letters,
        M=M,
        receipt_width=w,
        receipt_height=h,
        table_name=DYNAMO_DB_TABLE,
        image_obj=image_obj,
    )

    # 4) Save to temp, upload to S3, compute sha256
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp:
        cv2.imwrite(temp.name, warped)
        s3_client = boto3.client("s3")
        receipt_key = image_obj.cdn_s3_key.replace(
            ".png", f"_cluster_{cluster_id:05d}.png"
        )
        try:
            s3_client.upload_file(temp.name, CDN_S3_BUCKET, receipt_key)
        except ClientError as exc:
            logger.error("Failed to upload cluster image: %s", exc)
            raise
        # compute sha
        receipt_sha256 = calculate_sha256(temp.name)

    # 5) Store as a Receipt item in DynamoDB
    receipt_width, receipt_height = warped.shape[1], warped.shape[0]
    receipt = Receipt(
        image_id = image_obj.id,
        id = int(cluster_id),
        width = int(receipt_width),
        height = int(receipt_height),
        timestamp_added = datetime.now(timezone.utc).isoformat(),
        raw_s3_bucket = image_obj.raw_s3_bucket,
        raw_s3_key = image_obj.raw_s3_key,
        # For reference, store the corners in normalized coords if you want
        top_left     = { "x": float(box_ordered[0][0] / image_obj.width),
                         "y": 1 - float(box_ordered[0][1] / image_obj.height) },
        top_right    = { "x": float(box_ordered[1][0] / image_obj.width),
                         "y": 1 - float(box_ordered[1][1] / image_obj.height) },
        bottom_right = { "x": float(box_ordered[2][0] / image_obj.width),
                         "y": 1 - float(box_ordered[2][1] / image_obj.height) },
        bottom_left  = { "x": float(box_ordered[3][0] / image_obj.width),
                         "y": 1 - float(box_ordered[3][1] / image_obj.height) },
        sha256 = receipt_sha256,
        cdn_s3_bucket = CDN_S3_BUCKET,
        cdn_s3_key = receipt_key,
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
