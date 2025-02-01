import hashlib
from io import BytesIO
import math
from PIL import Image as PIL_Image, UnidentifiedImageError, ImageDraw
import json
from typing import Any, Dict, List, Tuple
import boto3
from botocore.exceptions import ClientError
from dynamo.data.dynamo_client import DynamoClient
from dynamo.entities import (
    Image,
    Line,
    Word,
    Letter,
    Receipt,
    ReceiptLine,
    ReceiptWord,
    ReceiptLetter,
)
from datetime import datetime, timezone
import numpy as np


def process(
    table_name: str,
    raw_bucket_name: str,
    raw_prefix: str,
    uuid: str,
    cdn_bucket_name: str,
    cdn_prefix: str = "assets/",
) -> None:
    """Processes the OCR results by adding the entities to DynamoDB and resulting files to S3.

    Args:
        table_name (str): The name of the DynamoDB table
        raw_bucket_name (str): The name of the S3 bucket that holds the ".png" file and the ".json" OCR results
        raw_prefix (str): The prefix to the S3 objects in the raw bucket
        uuid (str): The uuid that identifies the ".json" and ".png" files
        cdn_bucket_name (str): The name of the S3 bucket that will hold the processed files
        cdn_prefix (str, optional): The prefix to the S3 objects in the cdn bucket. Defaults to "assets/".
    """
    # Check to see that the UUID ".json" and ".png" files exist in the raw bucket
    s3 = boto3.client("s3")
    try:
        s3.head_object(Bucket=raw_bucket_name, Key=f"{raw_prefix}/{uuid}.json")
        s3.head_object(Bucket=raw_bucket_name, Key=f"{raw_prefix}/{uuid}.png")
    except ClientError as e:
        error_code = e.response["Error"]["Code"]

        # Bucket not found
        if error_code == "NoSuchBucket":
            raise ValueError(f"Bucket {raw_bucket_name} not found") from e

        # Key not found
        elif error_code in ("NoSuchKey", "404"):
            raise ValueError(
                f"UUID {uuid} not found in raw bucket {raw_bucket_name}"
            ) from e

        # Access denied
        elif error_code == "AccessDenied":
            raise ValueError(f"Access denied to s3://{raw_bucket_name}/{raw_prefix}/*")

        # Anything else, re-raise
        else:
            raise

    # Read the OCR results from the ".json" file
    ocr_results = (
        s3.get_object(Bucket=raw_bucket_name, Key=f"{raw_prefix}/{uuid}.json")["Body"]
        .read()
        .decode("utf-8")
    )
    try:
        ocr_results = json.loads(ocr_results)
    except json.JSONDecodeError as e:
        raise ValueError(f"Error decoding OCR results: {e}")

    # Read the image file
    try:
        image_bytes = s3.get_object(
            Bucket=raw_bucket_name, Key=f"{raw_prefix}/{uuid}.png"
        )["Body"].read()
        image = PIL_Image.open(BytesIO(image_bytes))
        # Force Pillow to parse the file fully so corrupted data is caught
        image.verify()
    except UnidentifiedImageError as e:
        raise ValueError(
            f"Corrupted or invalid PNG file at s3://{raw_bucket_name}/{raw_prefix}/{uuid}.png"
        ) from e
    image = PIL_Image.open(BytesIO(image_bytes))
    # pix_src = image.load()

    # Store the image in the CDN bucket
    try:
        s3.put_object(
            Bucket=cdn_bucket_name,
            Key=f"{cdn_prefix}{uuid}.png",
            Body=image_bytes,
            ContentType="image/png",
        )
    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        if error_code == "NoSuchBucket":
            raise ValueError(f"Bucket {cdn_bucket_name} not found")
        elif error_code == "AccessDenied":
            raise ValueError(f"Access denied to s3://{cdn_bucket_name}/{cdn_prefix}")
        else:
            raise

    image_obj = Image(
        id=uuid,
        width=image.size[0],
        height=image.size[1],
        timestamp_added=datetime.now(timezone.utc),
        raw_s3_bucket=raw_bucket_name,
        raw_s3_key=f"{raw_prefix}/{uuid}.png",
        cdn_s3_bucket=cdn_bucket_name,
        cdn_s3_key=f"{cdn_prefix}{uuid}.png",
        sha256=calculate_sha256_from_bytes(image_bytes),
    )
    lines, words, letters = process_ocr_dict(ocr_results, uuid)

    cluster_dict = cluster_receipts(lines)

    for cluster_id, cluster_lines in cluster_dict.items():
        if cluster_id == -1:
            continue
        line_ids = [ln.id for ln in cluster_lines]
        cluster_words = [w for w in words if w.line_id in line_ids]
        cluster_letters = [lt for lt in letters if lt.line_id in line_ids]
        transform_cluster(
            cluster_id, cluster_lines, cluster_words, cluster_letters, image, image_obj
        )

    # Finally, add the entities to DynamoDB
    DynamoClient(table_name).addImage(image_obj)
    DynamoClient(table_name).addLines(lines)
    DynamoClient(table_name).addWords(words)
    DynamoClient(table_name).addLetters(letters)


def calculate_sha256_from_bytes(data: bytes) -> str:
    """
    Calculate the SHA-256 hash of data in memory.

    Args:
        data (bytes): The file data in memory.

    Returns:
        str: The SHA-256 hash in hexadecimal format.
    """
    sha256_hash = hashlib.sha256(data)
    return sha256_hash.hexdigest()


def process_ocr_dict(
    ocr_data: Dict[str, Any], image_id: str
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


def cluster_receipts(
    lines: List[Line], eps: float = 0.08, min_samples: int = 2
) -> Dict[int, List[Line]]:
    """
    Cluster lines of text based on their x-centroids using a simple threshold-based approach.

    - Sort lines by x-centroid.
    - Group consecutive lines if their x-centroids are within `eps` of each other.
    - Mark clusters with fewer than `min_samples` lines as noise (cluster_id = -1).

    Returns:
        A dictionary mapping cluster_id -> list_of_lines.
    """
    if not lines:
        return {}

    # Sort by x-centroid
    lines_with_x = [(line, line.calculate_centroid()[0]) for line in lines]
    lines_with_x.sort(key=lambda pair: pair[1])  # sort by x value

    current_cluster_id = 0
    clusters = [[]]  # list of clusters; each cluster is a list of (Line, x)
    clusters[0].append(lines_with_x[0])  # start with the first line

    # Walk through lines, group if within eps
    for i in range(1, len(lines_with_x)):
        current_line, current_x = lines_with_x[i]
        prev_line, prev_x = lines_with_x[i - 1]

        if abs(current_x - prev_x) <= eps:
            # same cluster
            clusters[current_cluster_id].append((current_line, current_x))
        else:
            # start a new cluster
            current_cluster_id += 1
            clusters.append([(current_line, current_x)])

    # Mark cluster IDs; small clusters become noise (cluster_id = -1)
    cluster_id_counter = 1
    for cluster in clusters:
        if len(cluster) < min_samples:
            # mark noise
            for line_obj, _ in cluster:
                line_obj.cluster_id = -1
        else:
            # assign a valid cluster ID
            for line_obj, _ in cluster:
                line_obj.cluster_id = cluster_id_counter
            cluster_id_counter += 1

    # Build the final dictionary of cluster_id -> lines
    cluster_dict: Dict[int, List[Line]] = {}
    for line_obj, _ in lines_with_x:
        if line_obj.cluster_id == -1:
            continue  # skip noise
        cluster_dict.setdefault(line_obj.cluster_id, []).append(line_obj)

    return cluster_dict


def transform_cluster(
    cluster_id: int,
    cluster_lines: List[Line],
    cluster_words: List[Word],
    cluster_letters: List[Letter],
    pil_image: PIL_Image.Image,
    image_obj: Image,
) -> Tuple[
    PIL_Image.Image, Receipt, list[ReceiptLine], list[ReceiptWord], list[ReceiptLetter]
]:
    """
    Given a cluster of lines, create a bounding box transform that crops and
    warps the corresponding region from the original image (similar to
    cv2.minAreaRect + cv2.warpPerspective). Then you can upload or store as needed.
    """
    # 1) Gather all line corners in absolute pixel space
    points_abs = []
    for ln in cluster_lines:
        for corner in [ln.top_left, ln.top_right, ln.bottom_left, ln.bottom_right]:
            x_abs = corner["x"] * image_obj.width
            y_abs = (1 - corner["y"]) * image_obj.height  # 1 - y to flip y-axis
            points_abs.append((x_abs, y_abs))

    if not points_abs:
        print(f"[WARN] No corners for cluster {cluster_id}, skipping warp.")
        return

    # 2) Compute minimal-area bounding rect
    (cx, cy), (rw, rh), angle_deg = min_area_rect(points_abs)
    print(f"(cx, cy), (rw, rh), angle_deg: {(cx, cy), (rw, rh), angle_deg}")
    # cv2.boxPoints equivalent:
    box_4 = box_points((cx, cy), (rw, rh), angle_deg)
    print(f"box_4: {box_4}")
    debug_img = pil_image.copy()
    draw_box_pil(debug_img, box_4, color="red", width=3)
    # draw a circle on the top left
    draw = ImageDraw.Draw(debug_img)
    draw.ellipse(
        (
            reorder_box_points(box_4)[0][0] - 5,
            reorder_box_points(box_4)[0][1] - 5,
            reorder_box_points(box_4)[0][0] + 5,
            reorder_box_points(box_4)[0][1] + 5,
        ),
        fill="blue",
    )

    debug_img.save("debug_box_4_pil.png")

    # Convert (width, height) to integer for the output.
    # Some logic: if the detected rect is "rotated", we want w < h or h < w?
    # For consistency with your original code, do something like:
    w, h = int(round(rw)), int(round(rh))
    if w > h:
        print("w > h, swapping")
        w, h = h, w  # swap so that the final is "portrait" if you prefer

    print(f"w, h: {w}, {h}")

    # 3) Build your destination 4 corners:
    dst_pts = [(0, 0), (w - 1, 0), (w - 1, h - 1), (0, h - 1)]
    box_4_ordered = reorder_box_points(box_4)
    print(f"box_4_ordered: {box_4_ordered}")

    M_forward = compute_perspective_transform(box_4_ordered, dst_pts)
    M_inv = invert_3x3(M_forward)
    print(f"M_forward: {M_forward}")
    print(f"M_inv: {M_inv}")
    product = matmul_3x3(M_forward, M_inv)

    # Print or check product—it should be very close to the identity matrix.
    print("Product of M and M_inv:")
    for row in product:
        print(row)

    a_, b_, c_ = M_forward[0]
    d_, e_, f_ = M_forward[1]
    g_, h_, _ = M_forward[2]

    M = (
        (9.97730471e-01, 1.29210324e-02, -7.60432200e+02),
        (-1.29420519e-02, 9.99334899e-01, -3.36652518e+02),
        (-1.12553496e-11, -2.91572008e-13, 1.00000000e+00),
    )

    img_cv = np.array(pil_image)
    M_np = np.array(M)
    warped = cv2.warpPerspective(
        img_cv,   # the source image (NumPy array)
        M_np,     # your hardcoded 3x3 matrix
        (w, h),  # (width, height)
        flags=cv2.INTER_LINEAR,  # or cv2.INTER_CUBIC, etc.
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(255, 0, 255)  # fill with magenta, for example
    )
    # save warped image
    cv2.imwrite(f"cluster_{cluster_id}_warped_cv.png", warped)
    M = invert_3x3(M)

    a_, b_, c_ = M[0]
    d_, e_, f_ = M[1]
    g_, h_, _  = M[2]

    perspective_coeffs = (a_, b_, c_, d_, e_, f_, g_, h_)

    print(f"perspective_coeffs: {perspective_coeffs}")
    warped_img = pil_image.transform(
        (w, h),
        PIL_Image.PERSPECTIVE,
        perspective_coeffs,
        resample=PIL_Image.BICUBIC,
        fillcolor=(255, 0, 255, 255),  # bright magenta for debugging
    )
    

    warped_img.save(f"cluster_{cluster_id}_warped.png")

    affine_img = pil_image.transform(
        (w, h),
        PIL_Image.AFFINE,
        (a_, b_, c_, d_, e_, f_),
        resample=PIL_Image.BICUBIC,
        fillcolor=(255, 0, 255, 255),  # bright magenta for debugging
    )
    affine_img.save(f"cluster_{cluster_id}_affine.png")

    print(f"Cluster {cluster_id} warped image saved as cluster_{cluster_id}_warped.png")

    r = Receipt(
        id=cluster_id,
        image_id=image_obj.id,
        width=w,
        height=h,
        timestamp_added=datetime.now(timezone.utc),
        raw_s3_bucket=image_obj.raw_s3_bucket,
        raw_s3_key=image_obj.raw_s3_key,
        top_left={
            "x": box_4[3][0] / image_obj.width,
            "y": 1 - box_4[3][1] / image_obj.height,
        },
        top_right={
            "x": box_4[0][0] / image_obj.width,
            "y": 1 - box_4[0][1] / image_obj.height,
        },
        bottom_right={
            "x": box_4[1][0] / image_obj.width,
            "y": 1 - box_4[1][1] / image_obj.height,
        },
        bottom_left={
            "x": box_4[2][0] / image_obj.width,
            "y": 1 - box_4[2][1] / image_obj.height,
        },
        sha256=calculate_sha256_from_bytes(warped_img.tobytes()),
        cdn_s3_bucket=image_obj.cdn_s3_bucket,
        cdn_s3_key=image_obj.cdn_s3_key.replace(
            ".png", f"_cluster_{cluster_id:05d}.png"
        ),
    )

    # You would also do any code to store a “Receipt” object, e.g.:
    #   - compute a new SHA-256
    #   - upload to S3
    #   - store metadata in Dynamo
    #   - etc.
    #
    # Pseudocode:
    #
    #   receipt_sha256 = calculate_sha256(f"cluster_{cluster_id}.png")
    #   s3_client.upload_file(...)
    #   ...
    #
    # or anything else your pipeline needs.

    print(f"Done transforming cluster {cluster_id} into bounding box.")

import numpy as np

import cv2
import numpy as np

def compute_perspective_transform_cv(src_pts, dst_pts):
    src = np.array(src_pts, dtype=np.float32)
    dst = np.array(dst_pts, dtype=np.float32)
    return cv2.getPerspectiveTransform(src, dst)


def matmul_3x3(A, B):
    """Multiply two 3x3 matrices A*B, return a 3x3 result."""
    return [
        [A[r][0] * B[0][c] + A[r][1] * B[1][c] + A[r][2] * B[2][c] for c in range(3)]
        for r in range(3)
    ]


def signed_area_of_polygon(pts):
    """
    Shoelace formula for signed area.
    If area > 0 => points are in CCW order.
    If area < 0 => points are in CW order.
    """
    area = 0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        area += x1 * y2 - x2 * y1
    return area / 2.0


def reorder_box_points(pts):
    # pts is a list of 4 corners: [(x1, y1), (x2, y2), (x3, y3), (x4, y4)]
    # We'll return them in [top-left, top-right, bottom-right, bottom-left].

    # 1) Sort by y, then by x
    pts_sorted = sorted(pts, key=lambda p: (p[1], p[0]))
    # now the first two are the "top" corners, the last two are "bottom" corners

    top1, top2 = pts_sorted[0], pts_sorted[1]
    bottom1, bottom2 = pts_sorted[2], pts_sorted[3]

    # 2) Among the top corners, we want "left" first, "right" second
    if top1[0] < top2[0]:
        tl, tr = top1, top2
    else:
        tl, tr = top2, top1

    # 3) Among the bottom corners, we want "left" first, "right" second
    if bottom1[0] < bottom2[0]:
        bl, br = bottom1, bottom2
    else:
        bl, br = bottom2, bottom1

    return [tl, tr, br, bl]


def draw_box_pil(pil_image, corners, color="red", width=2):
    """
    Draw lines connecting the four corners in 'corners' onto a Pillow Image.
    """
    draw = ImageDraw.Draw(pil_image)
    num_pts = len(corners)
    for i in range(num_pts):
        x1, y1 = corners[i]
        x2, y2 = corners[(i + 1) % num_pts]
        draw.line([(x1, y1), (x2, y2)], fill=color, width=width)


def polygon_area(points: List[Tuple[float, float]]) -> float:
    """
    Return the area of a polygon given by points[] using the Shoelace formula.
    """
    area = 0.0
    n = len(points)
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0


def convex_hull(points: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """
    Return the convex hull of a set of 2D points as a list of vertices in CCW order.
    This uses Graham Scan or Andrew’s monotone chain algorithm.
    """
    # Remove duplicates
    points = sorted(set(points))

    if len(points) <= 1:
        return points

    # Build lower hull
    lower = []
    for p in points:
        while len(lower) >= 2:
            cross = (lower[-1][0] - lower[-2][0]) * (p[1] - lower[-2][1]) - (
                lower[-1][1] - lower[-2][1]
            ) * (p[0] - lower[-2][0])
            if cross <= 0:
                lower.pop()
            else:
                break
        lower.append(p)

    # Build upper hull
    upper = []
    for p in reversed(points):
        while len(upper) >= 2:
            cross = (upper[-1][0] - upper[-2][0]) * (p[1] - upper[-2][1]) - (
                upper[-1][1] - upper[-2][1]
            ) * (p[0] - upper[-2][0])
            if cross <= 0:
                upper.pop()
            else:
                break
        upper.append(p)

    # Concatenate lower + upper hull to get full hull
    # The last point of each list is the same as the first point of the other list
    return lower[:-1] + upper[:-1]


def min_area_rect(
    points: List[Tuple[float, float]]
) -> Tuple[Tuple[float, float], Tuple[float, float], float]:
    """
    Compute the minimum-area bounding rectangle of a set of 2D points.

    Returns: (center, (width, height), angle_degrees)
      - center = (cx, cy)
      - (width, height)
      - angle_degrees  (the rotation of the rectangle in degrees)

    The rectangle is returned such that if you rotate it back by 'angle_degrees',
    you get an axis-aligned w x h.

    This implements the 'rotating calipers' approach on the convex hull.
    """
    # Special cases
    if not points:
        return ((0, 0), (0, 0), 0)
    if len(points) == 1:
        return (points[0], (0, 0), 0)

    hull = convex_hull(points)
    if len(hull) < 3:
        # If it's effectively a line or a point, handle trivially:
        # compute bounding box the naive way
        xs = [p[0] for p in hull]
        ys = [p[1] for p in hull]
        minx, maxx = min(xs), max(xs)
        miny, maxy = min(ys), max(ys)
        width, height = (maxx - minx), (maxy - miny)
        cx, cy = (minx + width / 2.0), (miny + height / 2.0)
        return ((cx, cy), (width, height), 0.0)

    # Rotating calipers:
    # For each edge on the hull, consider that edge to be the "base".
    # Rotate all points so that this edge is horizontal, compute the bounding box, track min area.
    # This is more advanced geometry, so see references for details.
    n = len(hull)
    min_area = float("inf")
    best_rect = ((0, 0), (0, 0), 0)

    def edge_angle(p1, p2):
        # angle that the edge p1->p2 makes with the x-axis
        return math.atan2(p2[1] - p1[1], p2[0] - p1[0])

    for i in range(n):
        p1 = hull[i]
        p2 = hull[(i + 1) % n]
        theta = -edge_angle(p1, p2)  # we'll rotate points by -theta

        cos_t = math.cos(theta)
        sin_t = math.sin(theta)

        # Rotate all hull points by -theta to get an axis-aligned bounding box
        xs = []
        ys = []
        for px, py in hull:
            rx = cos_t * px - sin_t * py
            ry = sin_t * px + cos_t * py
            xs.append(rx)
            ys.append(ry)

        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        width = max_x - min_x
        height = max_y - min_y
        area = width * height
        if area < min_area:
            min_area = area
            # We'll store the rectangle’s rotation as angle_degrees
            # The rectangle center in *unrotated* space is the inverse rotation
            # of the center in rotated space. So rotate the midpoint of the bounding box.
            cx_r = min_x + width / 2.0
            cy_r = min_y + height / 2.0
            # rotate back by +theta to get the center in original coords
            cx = cos_t * cx_r + sin_t * cy_r
            cy = -sin_t * cx_r + cos_t * cy_r
            # store
            best_rect = ((cx, cy), (width, height), -math.degrees(theta))

    return best_rect


def box_points(
    center: Tuple[float, float], size: Tuple[float, float], angle_deg: float
) -> List[Tuple[float, float]]:
    """
    Given a rectangle defined by (center=(cx,cy), (width, height), angle_degrees),
    compute its 4 corner coordinates in (x, y) order, going roughly:
       top-left, top-right, bottom-right, bottom-left
    (Or a similar consistent ordering.)
    """
    cx, cy = center
    w, h = size
    angle = math.radians(angle_deg)
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)

    # Half-sizes
    hw = w / 2.0
    hh = h / 2.0

    # corners in local space before rotation:
    #   top-left:     (-hw, -hh)
    #   top-right:    (+hw, -hh)
    #   bottom-right: (+hw, +hh)
    #   bottom-left:  (-hw, +hh)
    corners_local = [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)]
    # rotate each corner by angle, then translate by center
    corners_world = []
    for lx, ly in corners_local:
        rx = cos_a * lx - sin_a * ly
        ry = sin_a * lx + cos_a * ly
        corners_world.append((cx + rx, cy + ry))
    return corners_world


def compute_perspective_transform(
    src_pts: List[Tuple[float, float]], dst_pts: List[Tuple[float, float]]
) -> List[List[float]]:
    """
    Given four source points and four destination points (each in clockwise or
    counter-clockwise order), compute the 3x3 perspective transform matrix M.
    We'll solve for M in:
        [x'_i, y'_i, w'_i]^T = M * [x_i, y_i, 1]^T
    for i in {0..3}.
    """
    # We'll set up a linear system of 8 equations (since each point pair gives two).
    # M has 8 unknowns (the 9th is 1 for scale). We'll do a standard solve for a, b, c, d, e, f, g, h.
    # If we cannot use NumPy, we can do a basic matrix solve with e.g. Cramer's rule or similar.

    # For convenience name the matrix as:
    #  M = [ a  b  c ]
    #      [ d  e  f ]
    #      [ g  h  1 ]

    # Then for each src->dst point pair:
    #  x'_i = (a x_i + b y_i + c) / (g x_i + h y_i + 1)
    #  y'_i = (d x_i + e y_i + f) / (g x_i + h y_i + 1)
    #
    # We rearrange to get a linear system in a..h.

    # Build the system: A * params = B,  where params = [a, b, c, d, e, f, g, h].
    A = []
    B = []

    for i in range(4):
        x, y = src_pts[i]
        X, Y = dst_pts[i]
        # x' = X, y' = Y

        # X = (a x + b y + c) / (g x + h y + 1)
        # => X (g x + h y + 1) = a x + b y + c
        # => X*g*x + X*h*y + X = a*x + b*y + c
        # => x*a + y*b + c - X*x*g - X*y*h = X  (rearranged)
        # => x*a + y*b + 1*c + (-x*X)*g + (-y*X)*h = X
        row1 = [x, y, 1.0, 0, 0, 0, -x * X, -y * X]
        A.append(row1)
        B.append(X)

        # Similarly for Y = ...
        # Y = (d x + e y + f) / (g x + h y + 1)
        # => Y (g x + h y + 1) = d x + e y + f
        row2 = [0, 0, 0, x, y, 1.0, -x * Y, -y * Y]
        A.append(row2)
        B.append(Y)

    # Solve the 8x8 system. We’ll implement a simple function for that.
    params = solve_linear_system_8x8(A, B)
    a, b, c, d, e, f, g, h = params
    return [[a, b, c], [d, e, f], [g, h, 1.0]]


def solve_linear_system_8x8(A: List[List[float]], B: List[float]) -> List[float]:
    """
    Solve the 8x8 system A*x = B for x, using a naive Gauss-Jordan elimination.
    A is 8x8, B is length 8. Return solution as length-8 list.
    """
    # Make a local copy so as not to mutate caller data
    mat = [row[:] for row in A]  # copy of A
    vec = B[:]  # copy of B
    n = 8

    # Gauss-Jordan elimination
    for i in range(n):
        # Find pivot
        pivot = i
        pivot_val = abs(mat[i][i])
        for r in range(i + 1, n):
            if abs(mat[r][i]) > pivot_val:
                pivot_val = abs(mat[r][i])
                pivot = r
        if pivot != i:
            mat[i], mat[pivot] = mat[pivot], mat[i]
            vec[i], vec[pivot] = vec[pivot], vec[i]

        # Eliminate below
        diag = mat[i][i]
        if abs(diag) < 1e-14:
            raise ValueError("Matrix is singular or near-singular.")
        for c in range(i, n):
            mat[i][c] /= diag
        vec[i] /= diag

        for r in range(i + 1, n):
            factor = mat[r][i]
            for c in range(i, n):
                mat[r][c] -= factor * mat[i][c]
            vec[r] -= factor * vec[i]

    # Back-substitute
    for i in reversed(range(n)):
        for r in range(i):
            factor = mat[r][i]
            mat[r][i] = 0
            vec[r] -= factor * vec[i]

    return vec


def warp_perspective(
    image: PIL_Image.Image, M: List[List[float]], dst_width: int, dst_height: int
) -> PIL_Image.Image:
    """
    Manually apply a 3x3 perspective transform M to produce a new image
    of size (dst_width, dst_height). We'll do nearest-neighbor sampling.
    """
    # Inversion of M if we want: for each (x', y') in the output image,
    # we find the corresponding (x, y) in the source. That is M^-1 * (x', y', 1).
    Minv = invert_3x3(M)

    # Create a new blank image
    warped_img = PIL_Image.new("RGB", (dst_width, dst_height))
    # image = image.convert("RGB")

    pix_src = image.load()
    pix_dst = warped_img.load()

    # For each pixel in the destination, find where it maps in the source:
    for y_dst in range(dst_height):
        for x_dst in range(dst_width):
            # homogeneous coords
            xh = x_dst
            yh = y_dst
            wh = 1.0
            # transform by Minv
            X = Minv[0][0] * xh + Minv[0][1] * yh + Minv[0][2] * wh
            Y = Minv[1][0] * xh + Minv[1][1] * yh + Minv[1][2] * wh
            W = Minv[2][0] * xh + Minv[2][1] * yh + Minv[2][2] * wh

            if abs(W) < 1e-14:
                # outside or near-infinite
                continue
            sx = X / W
            sy = Y / W

            # nearest-neighbor
            sx_int = int(round(sx))
            sy_int = int(round(sy))
            if 0 <= sx_int < image.width and 0 <= sy_int < image.height:
                pix_dst[x_dst, y_dst] = pix_src[sx_int, sy_int]

    return warped_img


def invert_3x3(M: List[List[float]]) -> List[List[float]]:
    a, b, c = M[0]
    d, e, f = M[1]
    g, h, i = M[2]

    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)

    if abs(det) < 1e-14:
        raise ValueError("Matrix is singular and can't be inverted.")
    inv_det = 1.0 / det

    return [
        [
            (e * i - f * h) * inv_det,
            (c * h - b * i) * inv_det,
            (b * f - c * e) * inv_det,
        ],
        [
            (f * g - d * i) * inv_det,
            (a * i - c * g) * inv_det,
            (c * d - a * f) * inv_det,
        ],
        [
            (d * h - e * g) * inv_det,
            (g * b - d * c) * inv_det,  # watch carefully the minor
            (a * e - b * d) * inv_det,
        ],
    ]
