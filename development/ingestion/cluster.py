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

# Load environment variables
S3_BUCKET = os.getenv("S3_BUCKET")
DYNAMO_DB_TABLE = os.getenv("DYNAMO_DB_TABLE")
IMAGE_ID = os.getenv("IMAGE_ID")
if not S3_BUCKET or not DYNAMO_DB_TABLE or not IMAGE_ID:
    raise ValueError("Missing environment variables")


def euclidean_dist(a, b):
    return dist(a, b)


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
    cluster_words = [word for word in words if word.line_id in [ln.id for ln in cluster_lines]]
    for word in cluster_words:
        # 1) Translate so min_x/min_y become 0,0
        word.translate(-min_x, -min_y)
        # 2) Scale so the bounding box fits in [0, 1] in both dimensions
        word.scale(scale_x, scale_y)
        # TODO: Turn these into ReceiptWord entities for DynamoDB
        word.to_receipt_word()
    cluster_letters = [letter for letter in letters if letter.word_id in [word.id for word in cluster_words]]
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
    return {
        "statusCode": 200,
        "body": json.dumps("Hello from Lambda!"),
    }
