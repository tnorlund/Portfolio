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
from datetime import datetime

# Load environment variables
DYNAMO_DB_TABLE = os.getenv("DYNAMO_DB_TABLE")
S3_BUCKET = os.getenv("S3_BUCKET")
CDN_S3_BUCKET = os.getenv("CDN_S3_BUCKET")

IMAGE_ID = os.getenv("IMAGE_ID")
JSON_KEY = os.getenv("JSON_KEY")
PNG_KEY = os.getenv("PNG_KEY")
CDN_S3_KEY = os.getenv("CDN_S3_KEY")

if (
    not S3_BUCKET
    or not DYNAMO_DB_TABLE
    or not IMAGE_ID
    or not JSON_KEY
    or not PNG_KEY
):
    missing_vars = [
        var_name
        for var_name, var_value in [
            ("DYNAMO_DB_TABLE", DYNAMO_DB_TABLE),
            ("IMAGE_ID", IMAGE_ID),
            ("JSON_KEY", JSON_KEY),
            ("S3_BUCKET", S3_BUCKET),
            ("PNG_KEY", PNG_KEY),
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


def add_initial_image() -> Tuple[Image, list[Line], list[Word], list[Letter]]:
    """
    Add the initial image to the database.

    Returns:
    Image: The image object.
    """
    dynamo_client = DynamoClient(DYNAMO_DB_TABLE)
    # Download the PNG from S3 to calculate the SHA256
    s3_client = boto3.client("s3")
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp:
        s3_client.download_file(S3_BUCKET, PNG_KEY, temp.name)
        sha256 = calculate_sha256(temp.name)
        img_cv = cv2.imread(temp.name)
        height, width, _ = img_cv.shape
    image = dynamo_client.addImage(
        Image(
            id=IMAGE_ID,
            width=width,
            height=height,
            timestamp_added=datetime.now(datetime.timezone.utc).isoformat(),
            s3_bucket=S3_BUCKET,
            s3_key=PNG_KEY,
            sha256=sha256,
            cdn_s3_bucket=CDN_S3_BUCKET,
            cdn_s3_key=CDN_S3_KEY,
        )
    )
    # Read JSON file from S3
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as temp:
        s3_client.download_file(S3_BUCKET, JSON_KEY, temp.name)
        with open(temp.name, "r") as f:
            ocr_data = json.load(f)
    lines, words, letters = process_ocr_dict(ocr_data, IMAGE_ID)
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
    for i, line in enumerate(lines):
        line.cluster_id = int(labels[i])
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
    cluster_id: int, cluster_lines: list[Line], image_cv: np.ndarray, image: Image
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
    avg_angle_radians = sum(ln.angleRadians for ln in cluster_lines) / len(
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
                (ln.topLeft["x"], ln.topLeft["y"]),
                (ln.topRight["x"], ln.topRight["y"]),
                (ln.bottomLeft["x"], ln.bottomLeft["y"]),
                (ln.bottomRight["x"], ln.bottomRight["y"]),
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

    # TODO use these to create ReceiptLine, ReceiptWord, and ReceiptLetter entities

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

    warped = cv2.warpPerspective(image_cv, M, (dst_width, dst_height))
    height, width, _ = warped.shape

    # 7) Add the cropped image to the CDN S3
    s3_client = boto3.client("s3")
    this_s3_key = f"{CDN_S3_KEY}_{int(cluster_id):05d}.png"
    with tempfile.NamedTemporaryFile(suffix=".png", delete=True) as temp:
        cv2.imwrite(temp.name, warped)
        s3_client.upload_file(temp.name, CDN_S3_BUCKET, this_s3_key)
        sha256 = calculate_sha256(temp.name)
    
    # 8) Add the receipt to DynamoDB
    receipt = Receipt(
        id=cluster_id,
        width=width,
        height=height,
        timestamp_added=datetime.utcnow().isoformat(),
        s3_bucket=image.s3_bucket,
        s3_key=image.s3_key,
        top_left=pt_tl,
        top_right=pt_tr,
        bottom_left=pt_bl,
        bottom_right=pt_br,
        sha256=sha256,
        cdn_s3_bucket=CDN_S3_BUCKET,
        cdn_s3_key=this_s3_key,
    )
    dynamo_client = DynamoClient(DYNAMO_DB_TABLE)
    dynamo_client.addReceipt(receipt)


# 3) Initialize DynamoClient and get image details
dynamo_client = DynamoClient(DYNAMO_DB_TABLE)
image, lines, words, letters, scaled_images = dynamo_client.getImageDetails(IMAGE_ID)

# 4) Assemble X coordinates for DBSCAN: we take the centroid's x-value
#    (line.calculate_centroid()[0]) for each line
X = np.array([line.calculate_centroid()[0] for line in lines]).reshape(-1, 1)

# 5) Run DBSCAN
db = DBSCAN(eps=0.08, min_samples=2)  # <-- tune these params based on your data
db.fit(X)
labels = db.labels_

# 6) Assign a cluster_id to each line
for i, line in enumerate(lines):
    line.cluster_id = int(labels[i])  # -1 means outlier

# 7) Group lines by cluster (receipt)
receipt_dict = defaultdict(list)
for line in lines:
    if line.cluster_id == -1:
        # you can decide whether to skip outliers or handle them differently
        continue
    receipt_dict[line.cluster_id].append(line)

# print the number of clusters
print(f"Found {len(receipt_dict)} receipts")

# Download the image from S3
s3_client = boto3.client("s3")
with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp:
    s3_client.download_file(image.s3_bucket, image.s3_key, temp.name)
    local_image_path = temp.name
img_cv = cv2.imread(local_image_path)


# 8) Find the bounding boxes for each receipt
for cluster_id, cluster_lines in receipt_dict.items():
    # 1) Compute the cluster’s centroid based on the average of each line’s centroid.
    sum_x = 0.0
    sum_y = 0.0
    for ln in cluster_lines:
        cx, cy = ln.calculate_centroid()  # e.g. (mean_x_of_corners, mean_y_of_corners)
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
    avg_angle_radians = sum(ln.angleRadians for ln in cluster_lines) / len(
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
                (ln.topLeft["x"], ln.topLeft["y"]),
                (ln.topRight["x"], ln.topRight["y"]),
                (ln.bottomLeft["x"], ln.bottomLeft["y"]),
                (ln.bottomRight["x"], ln.bottomRight["y"]),
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

    for ln in cluster_lines:
        # 1) Translate so min_x/min_y become 0,0
        ln.translate(-min_x, -min_y)
        # 2) Scale so the bounding box fits in [0, 1] in both dimensions
        ln.scale(scale_x, scale_y)
        # TODO: Turn these into ReceiptLine entities for DynamoDB
        ln.to_receipt_line()
    cluster_words = [
        word for word in words if word.line_id in [ln.id for ln in cluster_lines]
    ]
    for word in cluster_words:
        # 1) Translate so min_x/min_y become 0,0
        word.translate(-min_x, -min_y)
        # 2) Scale so the bounding box fits in [0, 1] in both dimensions
        word.scale(scale_x, scale_y)
        # TODO: Turn these into ReceiptWord entities for DynamoDB
        word.to_receipt_word()
    cluster_letters = [
        letter
        for letter in letters
        if letter.word_id in [word.id for word in cluster_words]
    ]
    for letter in cluster_letters:
        # 1) Translate so min_x/min_y become 0,0
        letter.translate(-min_x, -min_y)
        # 2) Scale so the bounding box fits in [0, 1] in both dimensions
        letter.scale(scale_x, scale_y)
        # TODO: Turn these into ReceiptLetter entities for DynamoDB
        letter.to_receipt_letter()

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

    warped = cv2.warpPerspective(img_cv, M, (dst_width, dst_height))

    # TODO: Remove drawing the circles to the image
    # Draw the centroids of the cluster_lines
    for ln in cluster_lines:
        corrected_x = int(ln.calculate_centroid()[0] * dst_width)
        corrected_y = int((1 - ln.calculate_centroid()[1]) * dst_height)
        cv2.circle(
            warped,
            (corrected_x, corrected_y),
            20,
            (255, 0, 0),
            -1,
        )
        ln_centroid = ln.calculate_centroid()
        print(f"{ln.text}: {corrected_x}, {corrected_y}")

    output_path = (
        f"{image.s3_key.split('/')[-1].replace('.png', f'_cluster_{cluster_id}.png')}"
    )
    cv2.imwrite(output_path, warped)

    # Draw circles on the original image for the bounding box
    for corner in [orig_top_left, orig_top_right, orig_bottom_left, orig_bottom_right]:
        cv2.circle(
            img_cv,
            (
                int(corner[0] * image.width),
                int((1 - corner[1]) * image.height),
            ),
            20,
            (255, 0, 0),
            -1,
        )

output_path = f"{image.s3_key.split('/')[-1].replace('.png', f'_bounding_box.png')}"
cv2.imwrite(output_path, img_cv)
print(f"Saved receipt image to {output_path}")


def lambda_handler(event, context):
    """Lambda handler function."""
    image, lines, words, letters = add_initial_image()
    cluster_dict = cluster_image(lines)
    # Read the image from S3
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp:
        s3_client.download_file(image.s3_bucket, image.cdn_s3_key, temp.name)
        img_cv = cv2.imread(temp.name)
    for cluster_id, cluster_lines in cluster_dict.items():
        transform_cluster(cluster_id, cluster_lines, img_cv)

    return {
        "statusCode": 200,
        "body": json.dumps("Hello from Lambda!"),
    }
