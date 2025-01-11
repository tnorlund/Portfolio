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
    # Gather all x/y coordinates from the corners of the lines in this cluster
    all_x = []
    all_y = []
    for line in cluster_lines:
        all_x.extend([
            line.topLeft["x"],
            line.topRight["x"],
            line.bottomLeft["x"],
            line.bottomRight["x"]
        ])
        all_y.extend([
            line.topLeft["y"],
            line.topRight["y"],
            line.bottomLeft["y"],
            line.bottomRight["y"]
        ])

    # Compute the bounding box that encloses all lines
    min_x = min(all_x)
    max_x = max(all_x)
    min_y = min(all_y)
    max_y = max(all_y)
    print(f"min_y = {min_y}, max_y = {max_y}")

    # Define the cluster's bounding box corners
    bbox_topLeft = {"x": min_x, "y": min_y}
    bbox_topRight = {"x": max_x, "y": min_y}
    bbox_bottomLeft = {"x": min_x, "y": max_y}
    bbox_bottomRight = {"x": max_x, "y": max_y}

    # Calculate the angle of the bounding box
    dx = bbox_topRight["x"] * image.width - bbox_topLeft["x"] * image.width  # > 0
    dy = bbox_topRight["y"] * image.height - bbox_topLeft["y"] * image.height  # = 0 for an axis-aligned top edge

    print(f"dx = {dx}, dy = {dy}")
    angle_radians = math.atan2(dy, dx)           # 0.0
    angle_degrees = math.degrees(angle_radians)  # 0.0

    # print the average angle of the lines in this cluster
    avg_angle_lines_in_cluster_degrees = np.mean([line.angleDegrees for line in cluster_lines])
    avg_angle_lines_in_cluster_radians = np.mean([line.angleRadians for line in cluster_lines])
    print(f"Cluster {cluster_id} average line angle: {avg_angle_lines_in_cluster_degrees} degrees")

    print(f"Cluster {cluster_id} bounding box corners:")
    print(f"  topLeft = {bbox_topLeft}")
    print(f"  topRight = {bbox_topRight}")
    print(f"  bottomLeft = {bbox_bottomLeft}")
    print(f"  bottomRight = {bbox_bottomRight}")
    print(f"  angleDegrees = {angle_degrees}")
    print(f"  angleRadians = {angle_radians}")
    print()
    

# You could now do further processing, e.g.:
# - Crop each receipt from the image
# - Store the cluster ID back to Dynamo
# - Generate a new output image or JSON, etc.