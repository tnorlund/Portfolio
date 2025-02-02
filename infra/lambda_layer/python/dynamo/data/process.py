import hashlib
from io import BytesIO
import math
from PIL import Image as PIL_Image, UnidentifiedImageError
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
import copy


def process(
    table_name: str,
    raw_bucket_name: str,
    raw_prefix: str,
    uuid: str,
    cdn_bucket_name: str,
    cdn_prefix: str = "assets/",
) -> None:
    """
    Processes the OCR results by adding the entities to DynamoDB and uploading
    the original and transformed images to S3.
    """
    s3 = boto3.client("s3")
    if raw_prefix.endswith("/"):
        raw_prefix = raw_prefix[:-1]
    if cdn_prefix.endswith("/"):
        cdn_prefix = cdn_prefix[:-1]
    # Check that both the JSON and PNG files exist in the raw bucket.
    try:
        s3.head_object(Bucket=raw_bucket_name, Key=f"{raw_prefix}/{uuid}.json")
        s3.head_object(Bucket=raw_bucket_name, Key=f"{raw_prefix}/{uuid}.png")
    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        if error_code == "NoSuchBucket":
            raise ValueError(f"Bucket {raw_bucket_name} not found") from e
        elif error_code in ("NoSuchKey", "404"):
            raise ValueError(
                f"UUID {uuid} not found s3://{raw_bucket_name}/{raw_prefix}/{uuid}*"
            ) from e
        elif error_code == "AccessDenied":
            raise ValueError(f"Access denied to s3://{raw_bucket_name}/{raw_prefix}/*")
        else:
            raise

    # Read and decode the OCR JSON.
    ocr_results = s3.get_object(
        Bucket=raw_bucket_name, Key=f"{raw_prefix}/{uuid}.json"
    )["Body"]
    ocr_results = ocr_results.read().decode("utf-8")
    try:
        ocr_results = json.loads(ocr_results)
    except json.JSONDecodeError as e:
        raise ValueError(f"Error decoding OCR results: {e}")

    # Read the image (and verify it isn’t corrupted).
    try:
        image_bytes = s3.get_object(
            Bucket=raw_bucket_name, Key=f"{raw_prefix}/{uuid}.png"
        )["Body"].read()
        image = PIL_Image.open(BytesIO(image_bytes))
        image.verify()  # force Pillow to fully parse the image
    except UnidentifiedImageError as e:
        raise ValueError(
            f"Corrupted or invalid PNG file at s3://{raw_bucket_name}/{raw_prefix}/{uuid}.png"
        ) from e
    image = PIL_Image.open(BytesIO(image_bytes))  # reopen after verify

    # Upload the original image to the CDN bucket.
    try:
        s3.put_object(
            Bucket=cdn_bucket_name,
            Key=f"{cdn_prefix}/{uuid}.png",
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
        cdn_s3_key=f"{cdn_prefix}/{uuid}.png",
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
        try:
            r_image, r, r_lines, r_words, r_letters = transform_cluster(
                cluster_id,
                cluster_lines,
                cluster_words,
                cluster_letters,
                image,
                image_obj,
            )
            buffer = BytesIO()
            r_image.save(buffer, format="PNG")
            buffer.seek(0)
            png_data = buffer.getvalue()
        except Exception as e:
            raise ValueError(f"Error processing cluster {cluster_id}: {e}") from e

        try:
            s3.put_object(
                Bucket=cdn_bucket_name,
                Key=f"{image_obj.cdn_s3_key.replace('.png', f'_RECEIPT_{cluster_id:05d}.png')}",
                Body=png_data,
                ContentType="image/png",
            )
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "NoSuchBucket":
                raise ValueError(f"Bucket {cdn_bucket_name} not found")
            elif error_code == "AccessDenied":
                raise ValueError(
                    f"Access denied to s3://{cdn_bucket_name}/{cdn_prefix}"
                )
            else:
                raise

        try:
            s3.put_object(
                Bucket=raw_bucket_name,
                Key=f"{raw_prefix}/{uuid}_RECEIPT_{cluster_id:05d}.png",
                Body=png_data,
                ContentType="image/png",
            )
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "NoSuchBucket":
                raise ValueError(f"Bucket {raw_bucket_name} not found")
            elif error_code == "AccessDenied":
                raise ValueError(
                    f"Access denied to s3://{raw_bucket_name}/{raw_prefix}"
                )
            else:
                raise

        DynamoClient(table_name).addReceipt(r)
        DynamoClient(table_name).addReceiptLines(r_lines)
        DynamoClient(table_name).addReceiptWords(r_words)
        DynamoClient(table_name).addReceiptLetters(r_letters)

    # Finally, add all entities to DynamoDB.
    DynamoClient(table_name).addImage(image_obj)
    DynamoClient(table_name).addLines(lines)
    DynamoClient(table_name).addWords(words)
    DynamoClient(table_name).addLetters(letters)


def calculate_sha256_from_bytes(data: bytes) -> str:
    sha256_hash = hashlib.sha256(data)
    return sha256_hash.hexdigest()


def process_ocr_dict(
    ocr_data: Dict[str, Any], image_id: str
) -> Tuple[List[Line], List[Word], List[Letter]]:
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


def invert_affine(a, b, c, d, e, f):
    """
    Inverts the 2x3 affine transform:
    
        [ a  b  c ]
        [ d  e  f ]
        [ 0  0  1 ]
    
    Returns the 6-tuple (a_inv, b_inv, c_inv, d_inv, e_inv, f_inv)
    for the inverse transform, provided the determinant is not zero.
    """
    det = a * e - b * d
    if abs(det) < 1e-14:
        raise ValueError("Singular transform cannot be inverted.")
    a_inv =  e / det
    b_inv = -b / det
    c_inv = ( b * f - c * e) / det
    d_inv = -d / det
    e_inv =  a / det
    f_inv = ( c * d - a * f) / det
    return (a_inv, b_inv, c_inv, d_inv, e_inv, f_inv)

def cluster_receipts(
    lines: List[Line], eps: float = 0.08, min_samples: int = 2
) -> Dict[int, List[Line]]:
    if not lines:
        return {}

    # Compute an x–centroid for each line and sort.
    lines_with_x = [(line, line.calculate_centroid()[0]) for line in lines]
    lines_with_x.sort(key=lambda pair: pair[1])
    current_cluster_id = 0
    clusters = [[]]
    clusters[0].append(lines_with_x[0])
    for i in range(1, len(lines_with_x)):
        current_line, current_x = lines_with_x[i]
        _, prev_x = lines_with_x[i - 1]
        if abs(current_x - prev_x) <= eps:
            clusters[current_cluster_id].append((current_line, current_x))
        else:
            current_cluster_id += 1
            clusters.append([(current_line, current_x)])

    # Mark clusters that have too few lines as noise (cluster_id = -1).
    cluster_id_counter = 1
    for cluster in clusters:
        if len(cluster) < min_samples:
            for line_obj, _ in cluster:
                line_obj.cluster_id = -1
        else:
            for line_obj, _ in cluster:
                line_obj.cluster_id = cluster_id_counter
            cluster_id_counter += 1

    cluster_dict: Dict[int, List[Line]] = {}
    for line_obj, _ in lines_with_x:
        if line_obj.cluster_id == -1:
            continue
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
    PIL_Image.Image, Receipt, List[ReceiptLine], List[ReceiptWord], List[ReceiptLetter]
]:
    """
    Given a cluster of lines, compute an affine transformation to crop and
    warp the corresponding region from the original image.
    """
    # 1) Gather all the corner points (in absolute pixel space).
    points_abs = []
    for ln in cluster_lines:
        for corner in [ln.top_left, ln.top_right, ln.bottom_left, ln.bottom_right]:
            x_abs = corner["x"] * image_obj.width
            y_abs = (1 - corner["y"]) * image_obj.height  # flip y–axis
            points_abs.append((x_abs, y_abs))
    if not points_abs:
        raise ValueError("No points found for cluster transformation.")

    # 2) Compute a minimal–area bounding rectangle around the points.
    (cx, cy), (rw, rh), angle_deg = min_area_rect(points_abs)
    box_4 = box_points((cx, cy), (rw, rh), angle_deg)
    # Reorder the four points into a consistent order: top–left, top–right, bottom–right, bottom–left.
    box_4_ordered = reorder_box_points(box_4)

    # 3) Determine the destination image size.
    w, h = int(round(rw)), int(round(rh))
    if w > h:
        w, h = h, w  # swap if needed (e.g. to enforce a “portrait” orientation)

    # 4) Compute the affine transform.
    # For an affine transform we need three corresponding points.
    # Here we choose:
    #   - Source points: top–left, top–right, and bottom–left from the detected box.
    #   - Destination points: (0,0), (w–1, 0) and (0, h–1) respectively.
    src_tl = box_4_ordered[0]
    src_tr = box_4_ordered[1]
    src_bl = box_4_ordered[3]
    if (w - 1) == 0 or (h - 1) == 0:
        raise ValueError("Invalid destination dimensions for affine transform.")

    # Compute the transform coefficients.
    # For destination (0,0): x = c, y = f  =>  c = src_tl[0], f = src_tl[1]
    # For destination (w-1, 0): a*(w-1) + c = src_tr[0]  =>  a = (src_tr[0] - src_tl[0])/(w-1)
    #                          d*(w-1) + f = src_tr[1]  =>  d = (src_tr[1] - src_tl[1])/(w-1)
    # For destination (0, h-1): b*(h-1) + c = src_bl[0]  =>  b = (src_bl[0] - src_tl[0])/(h-1)
    #                          e*(h-1) + f = src_bl[1]  =>  e = (src_bl[1] - src_tl[1])/(h-1)
    a_f = (src_tr[0] - src_tl[0]) / (w - 1)
    d_f = (src_tr[1] - src_tl[1]) / (w - 1)
    b_f = (src_bl[0] - src_tl[0]) / (h - 1)
    e_f = (src_bl[1] - src_tl[1]) / (h - 1)
    c_f = src_tl[0]
    f_f = src_tl[1]

    a_i, b_i, c_i, d_i, e_i, f_i = invert_affine(a_f, b_f, c_f, d_f, e_f, f_f)

    # 5) Apply the affine transform.
    affine_img = pil_image.transform(
        (w, h),
        PIL_Image.AFFINE,
        (a_f, b_f, c_f, d_f, e_f, f_f),
        resample=PIL_Image.BICUBIC,
    )

    r = Receipt(
        id=cluster_id,
        image_id=image_obj.id,
        width=w,
        height=h,
        timestamp_added=datetime.now(timezone.utc),
        raw_s3_bucket=image_obj.raw_s3_bucket,
        raw_s3_key=image_obj.raw_s3_key.replace(
            ".png", f"_RECEIPT_{cluster_id:05d}.png"
        ),
        top_left={
            "x": box_4_ordered[0][0] / image_obj.width,
            "y": 1 - box_4_ordered[0][1] / image_obj.height,
        },
        top_right={
            "x": box_4_ordered[1][0] / image_obj.width,
            "y": 1 - box_4_ordered[1][1] / image_obj.height,
        },
        bottom_right={
            "x": box_4_ordered[2][0] / image_obj.width,
            "y": 1 - box_4_ordered[2][1] / image_obj.height,
        },
        bottom_left={
            "x": box_4_ordered[3][0] / image_obj.width,
            "y": 1 - box_4_ordered[3][1] / image_obj.height,
        },
        sha256=calculate_sha256_from_bytes(affine_img.tobytes()),
        cdn_s3_bucket=image_obj.cdn_s3_bucket,
        cdn_s3_key=image_obj.cdn_s3_key.replace(
            ".png", f"_RECEIPT_{cluster_id:05d}.png"
        ),
    )
    receipt_lines = []
    for line in cluster_lines:
        line_copy = copy.deepcopy(line)
        line_copy.warp_affine_normalized_forward(
            a_i, b_i, c_i, d_i, e_i, f_i,
            orig_width=image_obj.width,
            orig_height=image_obj.height,
            new_width=w,
            new_height=h,
            flip_y=True,
        )
        receipt_lines.append(ReceiptLine(**dict(line_copy), receipt_id=cluster_id))

    receipt_words = []
    for word in cluster_words:
        word_copy = copy.deepcopy(word)
        word_copy.warp_affine_normalized_forward(
            a_i, b_i, c_i, d_i, e_i, f_i,
            orig_width=image_obj.width,
            orig_height=image_obj.height,
            new_width=w,
            new_height=h,
            flip_y=True,
        )
        receipt_words.append(ReceiptWord(**dict(word_copy), receipt_id=cluster_id))

    receipt_letters = []
    for letter in cluster_letters:
        letter_copy = copy.deepcopy(letter)
        letter_copy.warp_affine_normalized_forward(
            a_i, b_i, c_i, d_i, e_i, f_i,
            orig_width=image_obj.width,
            orig_height=image_obj.height,
            new_width=w,
            new_height=h,
            flip_y=True,
        )
        receipt_letters.append(
            ReceiptLetter(**dict(letter_copy), receipt_id=cluster_id)
        )

    return affine_img, r, receipt_lines, receipt_words, receipt_letters


def reorder_box_points(pts: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """
    Given four points in any order, return them in a consistent order:
    top-left, top-right, bottom-right, bottom-left.
    """
    pts_sorted = sorted(pts, key=lambda p: (p[1], p[0]))
    top1, top2 = pts_sorted[0], pts_sorted[1]
    bottom1, bottom2 = pts_sorted[2], pts_sorted[3]
    if top1[0] < top2[0]:
        tl, tr = top1, top2
    else:
        tl, tr = top2, top1
    if bottom1[0] < bottom2[0]:
        bl, br = bottom1, bottom2
    else:
        bl, br = bottom2, bottom1
    return [tl, tr, br, bl]


def convex_hull(points: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """
    Compute the convex hull of a set of 2D points (in CCW order) using the
    monotone chain algorithm.
    """
    points = sorted(set(points))
    if len(points) <= 1:
        return points

    lower = []
    for p in points:
        while (
            len(lower) >= 2
            and (
                (lower[-1][0] - lower[-2][0]) * (p[1] - lower[-2][1])
                - (lower[-1][1] - lower[-2][1]) * (p[0] - lower[-2][0])
            )
            <= 0
        ):
            lower.pop()
        lower.append(p)

    upper = []
    for p in reversed(points):
        while (
            len(upper) >= 2
            and (
                (upper[-1][0] - upper[-2][0]) * (p[1] - upper[-2][1])
                - (upper[-1][1] - upper[-2][1]) * (p[0] - upper[-2][0])
            )
            <= 0
        ):
            upper.pop()
        upper.append(p)

    return lower[:-1] + upper[:-1]


def min_area_rect(
    points: List[Tuple[float, float]]
) -> Tuple[Tuple[float, float], Tuple[float, float], float]:
    """
    Compute the minimum–area bounding rectangle of a set of 2D points.
    Returns a tuple of:
      - center (cx, cy)
      - (width, height)
      - angle (in degrees) of rotation such that rotating back by that angle
        yields an axis–aligned rectangle.
    """
    if not points:
        return ((0, 0), (0, 0), 0)
    if len(points) == 1:
        return (points[0], (0, 0), 0)

    hull = convex_hull(points)
    if len(hull) < 3:
        xs = [p[0] for p in hull]
        ys = [p[1] for p in hull]
        minx, maxx = min(xs), max(xs)
        miny, maxy = min(ys), max(ys)
        width, height = (maxx - minx), (maxy - miny)
        cx, cy = (minx + width / 2.0), (miny + height / 2.0)
        return ((cx, cy), (width, height), 0.0)

    n = len(hull)
    min_area = float("inf")
    best_rect = ((0, 0), (0, 0), 0)

    def edge_angle(p1, p2):
        return math.atan2(p2[1] - p1[1], p2[0] - p1[0])

    for i in range(n):
        p1 = hull[i]
        p2 = hull[(i + 1) % n]
        theta = -edge_angle(p1, p2)
        cos_t = math.cos(theta)
        sin_t = math.sin(theta)
        xs = [cos_t * px - sin_t * py for (px, py) in hull]
        ys = [sin_t * px + cos_t * py for (px, py) in hull]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        width = max_x - min_x
        height = max_y - min_y
        area = width * height
        if area < min_area:
            min_area = area
            cx_r = min_x + width / 2.0
            cy_r = min_y + height / 2.0
            cx = cos_t * cx_r + sin_t * cy_r
            cy = -sin_t * cx_r + cos_t * cy_r
            best_rect = ((cx, cy), (width, height), -math.degrees(theta))
    return best_rect


def box_points(
    center: Tuple[float, float], size: Tuple[float, float], angle_deg: float
) -> List[Tuple[float, float]]:
    """
    Given a rectangle defined by center, size, and rotation angle (in degrees),
    compute its 4 corner coordinates (in order).
    """
    cx, cy = center
    w, h = size
    angle = math.radians(angle_deg)
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    hw = w / 2.0
    hh = h / 2.0
    # Corners in local space (before rotation).
    corners_local = [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)]
    corners_world = []
    for lx, ly in corners_local:
        rx = cos_a * lx - sin_a * ly
        ry = sin_a * lx + cos_a * ly
        corners_world.append((cx + rx, cy + ry))

    return corners_world
