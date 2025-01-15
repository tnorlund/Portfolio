#!/usr/bin/env python3
"""Clusters and groups lines of text into receipts then uploads to Dynamodb.

This script starts with a ".png" and ".json" file in an S3 bucket. The ".json" has the OCR results from the SwiftOCR model. The image, lines, words, and letters entities are first added to Dynamodb. The lines are then clustered using DBSCAN. The lines are grouped into receipts based on the cluster_id. The receipts are then transformed to fit within a 1x1 box. The bounding box is then scaled to fit within a 1x1 box. The bounding box is then rotated to be axis-aligned. The bounding box is then scaled back to the original size. The bounding box is then drawn on the image and saved to a new S3 bucket.

"""
import os
import json
from sklearn.cluster import DBSCAN
import numpy as np
from collections import defaultdict
import boto3
import tempfile
import cv2
from math import sin, cos, dist
from dynamo import DynamoClient
from typing import Tuple
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
import hashlib
from datetime import datetime, timezone

# Load environment variables
DYNAMO_DB_TABLE = os.getenv("DYNAMO_DB_TABLE")
S3_BUCKET = os.getenv("S3_BUCKET")
CDN_S3_BUCKET = os.getenv("CDN_S3_BUCKET")

if not S3_BUCKET or not DYNAMO_DB_TABLE or not CDN_S3_BUCKET:
    missing_vars = [
        var_name
        for var_name, var_value in [
            ("DYNAMO_DB_TABLE", DYNAMO_DB_TABLE),
            ("S3_BUCKET", S3_BUCKET),
            ("CDN_S3_BUCKET", CDN_S3_BUCKET),
        ]
        if not var_value
    ]
    raise ValueError(f"Missing environment variables: {', '.join(missing_vars)}")


def process_ocr_dict(ocr_data: dict, image_id: int) -> Tuple[list, list, list]:
    """
    Process the OCR data and return lists of lines, words, and letters.

    Args:
    ocr_data (dict): The OCR data from the SwiftOCR model.
    image_id (int): The ID of the image.

    Returns:
    Tuple[list, list, list]: A tuple of lists containing Line, Word, and Letter objects.
    """
    lines = []
    words = []
    letters = []
    for line_id, line_data in enumerate(ocr_data["lines"]):
        line_id = line_id + 1
        line_obj = Line(
            image_id=image_id,
            id=line_id,
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
        for word_id, word_data in enumerate(line_data["words"]):
            word_id = word_id + 1
            word_obj = Word(
                image_id=image_id,
                line_id=line_id,
                id=word_id,
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
            for letter_id, letter_data in enumerate(word_data["letters"]):
                letter_id = letter_id + 1
                letter_obj = Letter(
                    image_id=image_id,
                    line_id=line_id,
                    word_id=word_id,
                    id=letter_id,
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


def euclidean_dist(a: float, b: float) -> float:
    """
    Calculate the Euclidean distance between two points.

    Args:
    a (float): The first point.
    b (float): The second point.

    Returns:
    float: The Euclidean distance between the two points.
    """
    return dist(a, b)


def rotate_point(
    x: float, y: float, cx: float, cy: float, theta: float
) -> Tuple[float, float]:
    """
    Rotate a point around a center by a given angle.

    Args:
    x (float): The x-coordinate of the point.
    y (float): The y-coordinate of the point.
    cx (float): The x-coordinate of the center.
    cy (float): The y-coordinate of the center.
    theta (float): The angle in radians to rotate by.

    Returns:
    Tuple[float, float]: The rotated point.
    """
    # Translate to origin
    x_trans = x - cx
    y_trans = y - cy

    # Rotate
    x_rot = x_trans * cos(theta) - y_trans * sin(theta)
    y_rot = x_trans * sin(theta) + y_trans * cos(theta)

    # Translate back
    return (x_rot + cx, y_rot + cy)


def get_axis_aligned_bbox(
    points: Tuple[Tuple[float, float]]
) -> Tuple[float, float, float, float]:
    """
    Get the axis-aligned bounding box for a set of points.

    Args:
    points (Tuple[Tuple[float, float]]): The points to get the bounding box for.

    Returns:
    Tuple[float, float, float, float]: The axis-aligned bounding box.
    """
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), max(xs), min(ys), max(ys)


def calculate_sha256(file_path: str):
    """
    Calculate the SHA-256 hash of a file.

    Args:
    file_path (str): The path to the file to hash.

    Returns:
    str: The SHA-256 hash of the file.
    """
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


def add_initial_image(
    s3_path: str, uuid: str, image_id: int, cdn_path: str
) -> Tuple[Image, list[Line], list[Word], list[Letter]]:
    """
    Add the initial image to the database.

    Returns:
    Image: The image object.
    """
    dynamo_client = DynamoClient(DYNAMO_DB_TABLE)
    # Download the PNG from S3 to calculate the SHA256
    s3_client = boto3.client("s3")
    png_key = f"{s3_path}{uuid}.png"
    print(f"Downloading {png_key} from S3...")
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp:
        s3_client.download_file(S3_BUCKET, png_key, temp.name)
        sha256 = calculate_sha256(temp.name)
        img_cv = cv2.imread(temp.name)
        height, width, _ = img_cv.shape
    image = Image(
        id=image_id,
        width=width,
        height=height,
        timestamp_added=datetime.now(timezone.utc).isoformat(),
        s3_bucket=S3_BUCKET,
        s3_key=png_key,
        sha256=sha256,
        cdn_s3_bucket=CDN_S3_BUCKET,
        cdn_s3_key=cdn_path,
    )
    dynamo_client.addImage(image)
    # Read JSON file from S3
    json_key = f"{s3_path}{uuid}.json"
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as temp:
        s3_client.download_file(S3_BUCKET, json_key, temp.name)
        with open(temp.name, "r") as f:
            ocr_data = json.load(f)
    lines, words, letters = process_ocr_dict(ocr_data, image_id)
    print(
        f"Adding {len(lines)} lines, {len(words)} words, {len(letters)} letters, and image to DynamoDB"
    )
    dynamo_client.addLines(lines)
    dynamo_client.addWords(words)
    dynamo_client.addLetters(letters)
    return image, lines, words, letters


def cluster_image(lines: list[Line]) -> dict[int, list[Line]]:
    """
    Cluster the lines of text into receipts.

    Args:
    lines (list[Line]): The lines of text to cluster.

    Returns:
    dict[int, list[Line]]: A dictionary of cluster_id to list of lines.
    """
    # 1) Assembly X coordinates for DBSCAN: we take the centroid's x-value
    X = np.array([line.calculate_centroid()[0] for line in lines]).reshape(-1, 1)
    # 2) Run DBSCAN
    db = DBSCAN(eps=0.08, min_samples=2)
    db.fit(X)
    labels = db.labels_
    # 3) Assign a cluster_id to each line
    offset_labels = []
    for label in labels:
        if label == -1:
            offset_labels.append(-1)
        else:
            offset_labels.append(label + 1)
    for i, line in enumerate(lines):
        line.cluster_id = offset_labels[i]
    # 4) Group lines by cluster
    cluster_dict = {}
    for line in lines:
        if line.cluster_id == -1:
            continue
        if line.cluster_id not in cluster_dict:
            cluster_dict[line.cluster_id] = []
        cluster_dict[line.cluster_id].append(line)
    print(f"Found {len(cluster_dict)} receipts")
    return cluster_dict


def transform_cluster(
    cluster_id: int,
    cluster_lines: list[Line],
    cluster_words: list[Word],
    cluster_letters: list[Letter],
    image_cv: np.ndarray,
    image: Image,
):
    """Transform the cluster of lines into a bounding box."""
    # 1) Compute the cluster’s centroid based on the average of each line’s centroid.
    sum_x = 0.0
    sum_y = 0.0
    for ln in cluster_lines:
        cx, cy = ln.calculate_centroid()
        sum_x += cx
        sum_y += cy
    cluster_center_x = sum_x / len(cluster_lines)
    cluster_center_y = sum_y / len(cluster_lines)

    # 2) Translate all lines so the cluster centroid moves to (0.5, 0.5).
    dx = 0.5 - cluster_center_x
    dy = 0.5 - cluster_center_y
    for ln in cluster_lines:
        ln.translate(dx, dy)

    # 3) Rotate the cluster around (0.5, 0.5) by -avg_angle (in radians).
    avg_angle_radians = sum(ln.angle_radians for ln in cluster_lines) / len(
        cluster_lines
    )
    for ln in cluster_lines:
        # Negative average angle => rotate(-avg_angle_radians, 0.5, 0.5)
        ln.rotate(-avg_angle_radians, 0.5, 0.5, use_radians=True)

    # 4) Compute a bounding box from the now-transformed corners.
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
    top_left = (min_x, min_y)
    top_right = (max_x, min_y)
    bottom_left = (min_x, max_y)
    bottom_right = (max_x, max_y)

    # 5) Scale the lines to fit within a 1x1 box.
    range_x = max_x - min_x
    range_y = max_y - min_y
    scale_x = 1.0 / range_x
    scale_y = 1.0 / range_y

    print(f"Before original_entities_to_receipt_entities {cluster_id}")
    original_entities_to_receipt_entities(
        (cluster_lines, cluster_words, cluster_letters),
        min_x,
        min_y,
        scale_x,
        scale_y,
        cluster_id,
    )

    # 6) Reverse the transformations to get the original bounding box
    original_corners = []
    for corner in [top_left, top_right, bottom_left, bottom_right]:
        # 1) Rotate back by +avg_angle_radians around (0.5, 0.5)
        x_rot, y_rot = rotate_point(
            corner[0],
            corner[1],
            0.5,
            0.5,
            +avg_angle_radians,  # opposite sign of the earlier -avg_angle_radians
        )
        # 2) Translate back by (-dx, -dy)
        x_orig = x_rot - dx
        y_orig = y_rot - dy

        original_corners.append((x_orig, y_orig))

    orig_top_left, orig_top_right, orig_bottom_left, orig_bottom_right = (
        original_corners
    )
    pt_bl = (orig_top_left[0] * image.width, (1 - orig_top_left[1]) * image.height)
    pt_br = (orig_top_right[0] * image.width, (1 - orig_top_right[1]) * image.height)
    pt_tl = (
        orig_bottom_left[0] * image.width,
        (1 - orig_bottom_left[1]) * image.height,
    )
    pt_tr = (
        orig_bottom_right[0] * image.width,
        (1 - orig_bottom_right[1]) * image.height,
    )
    width_top = euclidean_dist(pt_tl, pt_tr)
    width_bot = euclidean_dist(pt_bl, pt_br)
    height_left = euclidean_dist(pt_tl, pt_bl)
    height_right = euclidean_dist(pt_tr, pt_br)

    dst_width = int((width_top + width_bot) / 2.0)
    dst_height = int((height_left + height_right) / 2.0)

    # Destination corners (x,y) for the resulting image
    dst_tl = (0, 0)
    dst_tr = (dst_width - 1, 0)
    dst_bl = (0, dst_height - 1)
    dst_br = (dst_width - 1, dst_height - 1)

    src_pts = np.float32([pt_tl, pt_tr, pt_bl, pt_br])  # original image corners
    dst_pts = np.float32([dst_tl, dst_tr, dst_bl, dst_br])  # new upright rectangle

    M = cv2.getPerspectiveTransform(src_pts, dst_pts)

    print(f"image_cv.shape: {image_cv.shape}")
    print(f"M shape: {M.shape}")
    print(f"dst_width: {dst_width}, dst_height: {dst_height}")
    warped = cv2.warpPerspective(image_cv, M, (dst_width, dst_height))
    height, width, _ = warped.shape

    # 7) Add the cropped image to the CDN S3
    s3_client = boto3.client("s3")

    cdn_s3_key = image.cdn_s3_key.replace(".png", f"_cluster_{int(cluster_id):05d}.png")
    with tempfile.NamedTemporaryFile(suffix=".png", delete=True) as temp:
        cv2.imwrite(temp.name, warped)
        s3_client.upload_file(temp.name, CDN_S3_BUCKET, cdn_s3_key)
        sha256 = calculate_sha256(temp.name)

    # 8) Add the receipt to DynamoDB
    receipt = Receipt(
        id=int(cluster_id),
        width=width,
        height=height,
        image_id=image.id,
        timestamp_added=datetime.now(timezone.utc).isoformat(),
        s3_bucket=image.s3_bucket,
        s3_key=image.s3_key,
        top_left={"x": pt_tl[0], "y": pt_tl[1]},
        top_right={"x": pt_tr[0], "y": pt_tr[1]},
        bottom_left={"x": pt_bl[0], "y": pt_bl[1]},
        bottom_right={"x": pt_br[0], "y": pt_br[1]},
        sha256=sha256,
        cdn_s3_bucket=CDN_S3_BUCKET,
        cdn_s3_key=cdn_s3_key,
    )
    dynamo_client = DynamoClient(DYNAMO_DB_TABLE)
    dynamo_client.addReceipt(receipt)


def original_entities_to_receipt_entities(
    cluster_entities: Tuple[list[Line], list[Word], list[Letter]],
    min_x: float,
    min_y: float,
    scale_x: float,
    scale_y: float,
    cluster_id: int,
):
    """Transform the original entities to receipt entities."""
    print(f"""Transforming cluster {cluster_id} to receipt entities""")
    cluster_lines, cluster_words, cluster_letters = cluster_entities
    receipt_lines = []
    receipt_words = []
    receipt_letters = []
    for ln in cluster_lines:
        # 1) Translate so min_x/min_y become 0,0
        ln.translate(-min_x, -min_y)
        # 2) Scale so the bounding box fits in [0, 1] in both dimensions
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
    for word in cluster_words:
        # 1) Translate so min_x/min_y become 0,0
        word.translate(-min_x, -min_y)
        # 2) Scale so the bounding box fits in [0, 1] in both dimensions
        word.scale(scale_x, scale_y)
        receipt_words.append(
            ReceiptWord(
                receipt_id=int(cluster_id),
                image_id=word.image_id,
                line_id=word.line_id,
                id=word.id,
                text=word.text,
                bounding_box=word.bounding_box,
                top_right=word.top_right,
                top_left=word.top_left,
                bottom_right=word.bottom_right,
                bottom_left=word.bottom_left,
                angle_degrees=word.angle_degrees,
                angle_radians=word.angle_radians,
                confidence=word.confidence,
                tags=word.tags,
            )
        )
    for letter in cluster_letters:
        # 1) Translate so min_x/min_y become 0,0
        letter.translate(-min_x, -min_y)
        # 2) Scale so the bounding box fits in [0, 1] in both dimensions
        letter.scale(scale_x, scale_y)
        receipt_letters.append(
            ReceiptLetter(
                receipt_id=int(cluster_id),
                image_id=letter.image_id,
                line_id=letter.line_id,
                word_id=letter.word_id,
                id=letter.id,
                text=letter.text,
                bounding_box=letter.bounding_box,
                top_right=letter.top_right,
                top_left=letter.top_left,
                bottom_right=letter.bottom_right,
                bottom_left=letter.bottom_left,
                angle_degrees=letter.angle_degrees,
                angle_radians=letter.angle_radians,
                confidence=letter.confidence,
            )
        )
    print(
        f"Adding {len(receipt_lines)} receipt lines, {len(receipt_words)} receipt words, and {len(receipt_letters)} receipt letters to DynamoDB"
    )
    dynamo_client = DynamoClient(DYNAMO_DB_TABLE)
    dynamo_client.addReceiptLines(receipt_lines)
    dynamo_client.addReceiptWords(receipt_words)
    dynamo_client.addReceiptLetters(receipt_letters)


def lambda_handler(event, _):
    """Lambda handler function."""
    uuid = event.get("uuid")  # or event["uuid"]
    s3_path = event.get("s3_path")  # or event["s3_path"]
    image_id = event.get("image_id")  # or event["image_id"]
    # Ensure the event has the required keys
    if not all([uuid, s3_path, image_id]):
        raise ValueError("Missing required keys in event")

    image, lines, words, letters = add_initial_image(s3_path, uuid, image_id, "test/")
    cluster_dict = cluster_image(lines)
    # Read the image from S3
    s3_client = boto3.client("s3")
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp:
        s3_client.download_file(image.s3_bucket, image.s3_key, temp.name)
        img_cv = cv2.imread(temp.name)
        # Check to see that img_cv is an image
        if img_cv is None:
            raise ValueError("Image is not a valid image")
    for cluster_id, cluster_lines in cluster_dict.items():
        if cluster_id == -1:
            continue
        cluster_line_ids = [ln.id for ln in cluster_lines]
        # Get all the words and letters in the cluster
        cluster_words = [word for word in words if word.line_id in cluster_line_ids]
        cluster_letters = [
            letter for letter in letters if letter.line_id in cluster_line_ids
        ]
        print(f"Adding cluster {cluster_id} to DynamoDB")
        transform_cluster(
            cluster_id,
            cluster_lines,
            cluster_words,
            cluster_letters,
            img_cv,
            image,
        )

    return {
        "statusCode": 200,
        "body": json.dumps("Hello from Lambda!"),
    }
