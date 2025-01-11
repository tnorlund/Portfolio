import os
from dotenv import load_dotenv
from sklearn.cluster import DBSCAN
import numpy as np
from collections import defaultdict
import math
import boto3
import tempfile
import cv2
from math import sin, cos

IMAGE_ID = 15

def rotate_point(x, y, cx, cy, theta):
    """
    Rotate (x,y) around center (cx,cy) by 'theta' radians.
    Returns (x_rot, y_rot).
    """
    # Translate to origin
    x_trans = x - cx
    y_trans = y - cy

    # Rotate
    x_rot = x_trans * cos(theta) - y_trans * sin(theta)
    y_rot = x_trans * sin(theta) + y_trans * cos(theta)

    # Translate back
    return (x_rot + cx, y_rot + cy)

def get_axis_aligned_bbox(points):
    """
    Given a list of (x, y) points, return (min_x, max_x, min_y, max_y).
    """
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), max(xs), min(ys), max(ys)

# Dynamo / custom imports
from dynamo import DynamoClient, Image, Line, Word, Letter, ScaledImage, itemToImage
from utils import encode_image_below_size, get_max_index_in_images, process_ocr_dict, calculate_sha256

# 1) Load environment variables from .env
load_dotenv()

# 2) Retrieve environment variables
S3_BUCKET = os.getenv("RAW_IMAGE_BUCKET")
DYNAMO_DB_TABLE = os.getenv("DYNAMO_DB_TABLE")

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
    avg_angle_radians = sum(ln.angleRadians for ln in cluster_lines) / len(cluster_lines)
    for ln in cluster_lines:
        # Negative average angle => rotate(-avg_angle_radians, 0.5, 0.5)
        ln.rotate(-avg_angle_radians, 0.5, 0.5, use_radians=True)
    
    # 4) Compute a bounding box from the now-transformed corners.
    all_points = []
    for ln in cluster_lines:
        all_points.extend([
            (ln.topLeft["x"],    ln.topLeft["y"]),
            (ln.topRight["x"],   ln.topRight["y"]),
            (ln.bottomLeft["x"], ln.bottomLeft["y"]),
            (ln.bottomRight["x"],ln.bottomRight["y"]),
        ])
    
    min_x = min(pt[0] for pt in all_points)
    max_x = max(pt[0] for pt in all_points)
    min_y = min(pt[1] for pt in all_points)
    max_y = max(pt[1] for pt in all_points)

    top_left     = (min_x, min_y)
    top_right    = (max_x, min_y)
    bottom_left  = (min_x, max_y)
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
        # TODO: Turn these into ReceiptWord entities for DynamoDB
    
    # 6) Reverse the transformations to get the original bounding box
    original_corners = []
    for corner in [top_left, top_right, bottom_left, bottom_right]:
        # 1) Rotate back by +avg_angle_radians around (0.5, 0.5)
        x_rot, y_rot = rotate_point(
            corner[0], 
            corner[1],
            0.5,
            0.5,
            +avg_angle_radians  # opposite sign of the earlier -avg_angle_radians
        )
        # 2) Translate back by (-dx, -dy)
        x_orig = x_rot - dx
        y_orig = y_rot - dy
        
        original_corners.append((x_orig, y_orig))

    orig_top_left, orig_top_right, orig_bottom_left, orig_bottom_right = original_corners
    print("Bounding box corners back in original coordinates:")
    print("  top_left     =", orig_top_left)
    print("  top_right    =", orig_top_right)
    print("  bottom_left  =", orig_bottom_left)
    print("  bottom_right =", orig_bottom_right)

    