import os
import subprocess
import json
from time import sleep
import cv2
from datetime import datetime
from dotenv import load_dotenv
from sklearn.cluster import DBSCAN
import numpy as np
from collections import defaultdict
import tempfile
import math


def rotate_point(point, center, angle_rad):
    """
    Rotate 'point' around 'center' by 'angle_rad' (in radians).
    point, center = (x, y).
    Returns (x_rot, y_rot).
    """
    px, py = point
    cx, cy = center
    s = math.sin(angle_rad)
    c = math.cos(angle_rad)

    # Translate point back to origin:
    px -= cx
    py -= cy

    # Rotate
    xnew = px * c - py * s
    ynew = px * s + py * c

    # Translate forward
    xrot = xnew + cx
    yrot = ynew + cy
    return (xrot, yrot)

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
image, lines, words, letters, scaled_images = dynamo_client.getImageDetails(9)

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
    # 1) Gather all corner points
    all_points = []
    for ln in cluster_lines:
        all_points.append((ln.topLeft["x"],    ln.topLeft["y"]))
        all_points.append((ln.topRight["x"],   ln.topRight["y"]))
        all_points.append((ln.bottomLeft["x"], ln.bottomLeft["y"]))
        all_points.append((ln.bottomRight["x"],ln.bottomRight["y"]))

    # 2) Compute the average angle in degrees
    avg_angle_deg = np.mean([ln.angleDegrees for ln in cluster_lines])
    avg_angle_rad = math.radians(avg_angle_deg)

    # 3) Choose a pivot (the "cluster center" or just (0,0))
    #    For a "cluster center", we might do the average of all x, y
    if all_points:
        mean_x = np.mean([p[0] for p in all_points])
        mean_y = np.mean([p[1] for p in all_points])
        cluster_center = (mean_x, mean_y)
    else:
        cluster_center = (0, 0)

    # 4) Rotate all points by -avg_angle (to align them horizontally)
    rotated_points = [
        rotate_point(p, cluster_center, -avg_angle_rad) for p in all_points
    ]

    # 5) Compute axis-aligned bbox in the rotated space
    min_x, max_x, min_y, max_y = get_axis_aligned_bbox(rotated_points)

    # 6) Reconstruct the four corners in rotated space
    #    top-left, top-right, bottom-left, bottom-right
    tl_rot = (min_x, min_y)
    tr_rot = (max_x, min_y)
    br_rot = (max_x, max_y)
    bl_rot = (min_x, max_y)

    # 7) Rotate those corners back by +avg_angle
    tl = rotate_point(tl_rot, cluster_center, avg_angle_rad)
    tr = rotate_point(tr_rot, cluster_center, avg_angle_rad)
    br = rotate_point(br_rot, cluster_center, avg_angle_rad)
    bl = rotate_point(bl_rot, cluster_center, avg_angle_rad)

    # 8) Now we have a bounding box that aligns with the average text angle
    print(f"Cluster {cluster_id} average line angle = {avg_angle_deg:.2f} deg")
    print("  Oriented bounding box corners:")
    print(f"    topLeft     = {tl}")
    print(f"    topRight    = {tr}")
    print(f"    bottomRight = {br}")
    print(f"    bottomLeft  = {bl}")
    print()
