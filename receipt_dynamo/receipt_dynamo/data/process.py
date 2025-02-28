import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from typing import Any, Dict, List, Tuple

import boto3
from botocore.exceptions import ClientError
from PIL import Image as PIL_Image
from PIL import ImageOps, UnidentifiedImageError

from receipt_dynamo.data._geometry import (box_points,
    invert_affine,
    min_area_rect, )
from receipt_dynamo.data._gpt import gpt_request_initial_tagging
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities import (GPTInitialTagging,
    Image,
    Letter,
    Line,
    Receipt,
    ReceiptLetter,
    ReceiptLine,
    ReceiptWord,
    Word, )
from receipt_dynamo.entities.receipt_word_tag import ReceiptWordTag
from receipt_dynamo.entities.word_tag import WordTag


@dataclass
class Tag:
    tag: str
    word_id: int
    line_id: int


def process(table_name: str,
    raw_bucket_name: str,
    raw_prefix: str,
    uuid: str,
    cdn_bucket_name: str,
    cdn_prefix: str = "assets/",
    ocr_dict: Dict[str, Any] = None,
    png_data: bytes = None, ) -> None:
    """
    Processes the OCR results by adding the entities to DynamoDB and uploading
    the original and transformed images to S3.
    """
    s3 = boto3.client("s3")
    if raw_prefix.endswith("/"):
        raw_prefix = raw_prefix[:-1]
    if cdn_prefix.endswith("/"):
        cdn_prefix = cdn_prefix[:-1]

    # If the user does NOT pass ocr_dict/png_data, ensure the raw objects exist in S3
    # as originally done.
    if ocr_dict is None or png_data is None:  # [MODIFIED CODE]
        try:
            # We only check for the object if it's not passed in
            if ocr_dict is None:
                s3.head_object(Bucket=raw_bucket_name, Key=f"{raw_prefix}/{uuid}.json")
            if png_data is None:
                s3.head_object(Bucket=raw_bucket_name, Key=f"{raw_prefix}/{uuid}.png")
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "NoSuchBucket":
                raise ValueError(f"Bucket {raw_bucket_name} not found") from e
            elif error_code in ("NoSuchKey", "404"):
                raise ValueError(f"UUID {uuid} not found s3://{raw_bucket_name}/{raw_prefix}/{uuid}*") from e
            elif error_code == "AccessDenied":
                raise ValueError(f"Access denied to s3://{raw_bucket_name}/{raw_prefix}/*")
            else:
                raise

    if ocr_dict is not None:
        # The user provided OCR data. Upload it to the raw location:
        s3.put_object(Bucket=raw_bucket_name,
            Key=f"{raw_prefix}/{uuid}.json",
            Body=json.dumps(ocr_dict).encode("utf-8"),
            ContentType="application/json", )
        ocr_results = ocr_dict
    else:
        # Fetch from S3 as before
        ocr_data_obj = s3.get_object(Bucket=raw_bucket_name, Key=f"{raw_prefix}/{uuid}.json")["Body"]
        ocr_data_str = ocr_data_obj.read().decode("utf-8")
        try:
            ocr_results = json.loads(ocr_data_str)
        except json.JSONDecodeError as e:
            raise ValueError(f"Error decoding OCR results: {e}")

    if png_data is not None:
        # The user provided the PNG bytes. Upload them to the raw location:
        s3.put_object(Bucket=raw_bucket_name,
            Key=f"{raw_prefix}/{uuid}.png",
            Body=png_data,
            ContentType="image/png", )
        image_bytes = png_data
    else:
        # Read from S3 as before
        image_bytes = s3.get_object(Bucket=raw_bucket_name, Key=f"{raw_prefix}/{uuid}.png")["Body"].read()

    # Verify the image
    try:
        test_img = PIL_Image.open(BytesIO(image_bytes))
        test_img.verify()  # force Pillow to fully parse the image
    except UnidentifiedImageError as e:
        raise ValueError(f"Corrupted or invalid PNG at s3://{raw_bucket_name}/{raw_prefix}/{uuid}.png") from e

    # Reopen (Pillow cannot reuse the same file handle after verify())
    image = PIL_Image.open(BytesIO(image_bytes))
    image = ImageOps.exif_transpose(image)  # Correct orientation

    # Upload the original image to the CDN bucket as JPEG.
    try:
        image_converted = image.convert("RGB")
        buffer = BytesIO()
        image_converted.save(buffer, format="JPEG", quality=85)
        buffer.seek(0)
        jpeg_data = buffer.getvalue()
        s3.put_object(Bucket=cdn_bucket_name,
            Key=f"{cdn_prefix}/{uuid}.jpg",  # change file extension to .jpg
            Body=jpeg_data,
            ContentType="image/jpeg", )
    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        if error_code == "NoSuchBucket":
            raise ValueError(f"Bucket {cdn_bucket_name} not found")
        elif error_code == "AccessDenied":
            raise ValueError(f"Access denied to s3://{cdn_bucket_name}/{cdn_prefix}")
        else:
            raise

    # Build our top-level Image object
    image_obj = Image(image_id=uuid,
        width=image.size[0],
        height=image.size[1],
        timestamp_added=datetime.now(timezone.utc),
        raw_s3_bucket=raw_bucket_name,
        raw_s3_key=f"{raw_prefix}/{uuid}.png",
        cdn_s3_bucket=cdn_bucket_name,
        cdn_s3_key=f"{cdn_prefix}/{uuid}.jpg",
        sha256=calculate_sha256_from_bytes(image_bytes), )

    # Parse the OCR into lines, words, letters
    lines, words, letters = process_ocr_dict(ocr_results, uuid)

    # Cluster logic
    cluster_dict = cluster_receipts(lines)
    cluster_dict = join_overlapping_clusters(cluster_dict, image.width, image.height, iou_threshold=0.01)

    # Instead of writing to Dynamo inside the loop, we will
    # accumulate everything in lists and do one final insert at the end.
    all_receipts = []
    all_receipt_lines = []
    all_receipt_words = []
    all_receipt_letters = []
    all_gpt_initial_taggings = []
    # We already have a "word_tags" list in the code, but let's rename it
    # to keep track of everything clearly:
    all_word_tags = []
    all_receipt_word_tags = []
    word_tags = []
    # Receipt extraction, transformation, saving
    for cluster_id, cluster_lines in cluster_dict.items():
        if cluster_id == -1:
            continue
        line_ids = [ln.line_id for ln in cluster_lines]
        cluster_words = [w for w in words if w.line_id in line_ids]
        cluster_letters = [lt for lt in letters if lt.line_id in line_ids]
        try:
            r_image, r, r_lines, r_words, r_letters = transform_cluster(cluster_id,
                cluster_lines,
                cluster_words,
                cluster_letters,
                image,
                image_obj, )
            buffer = BytesIO()
            r_image.save(buffer, format="PNG")
            buffer.seek(0)
            png_data = buffer.getvalue()
        except Exception as e:
            raise ValueError(f"Error processing cluster {cluster_id}: {e}") from e

        # Convert the receipt image to JPEG and upload to the CDN bucket.
        try:
            # Convert the transformed image (r_image) to JPEG.
            r_image_converted = r_image.convert("RGB")
            buffer = BytesIO()
            r_image_converted.save(buffer, format="JPEG", quality=85)
            buffer.seek(0)
            jpeg_receipt_data = buffer.getvalue()
            s3.put_object(Bucket=cdn_bucket_name,
                Key=image_obj.cdn_s3_key.replace(".jpg", f"_RECEIPT_{cluster_id:05d}.jpg"),
                Body=jpeg_receipt_data,
                ContentType="image/jpeg", )
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "NoSuchBucket":
                raise ValueError(f"Bucket {cdn_bucket_name} not found")
            elif error_code == "AccessDenied":
                raise ValueError(f"Access denied to s3://{cdn_bucket_name}/{cdn_prefix}")
            else:
                raise

        # Upload the receipt image to the raw bucket
        try:
            s3.put_object(Bucket=raw_bucket_name,
                Key=f"{raw_prefix}/{uuid}_RECEIPT_{cluster_id:05d}.png",
                Body=png_data,
                ContentType="image/png", )
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "NoSuchBucket":
                raise ValueError(f"Bucket {raw_bucket_name} not found")
            elif error_code == "AccessDenied":
                raise ValueError(f"Access denied to s3://{raw_bucket_name}/{raw_prefix}")
            else:
                raise

        gpt_response, query, response = gpt_request_initial_tagging(r, r_words)

        # Save the GPT response to the raw bucket
        try:
            s3.put_object(Bucket=raw_bucket_name,
                Key=f"{raw_prefix}/{uuid}_RECEIPT_{cluster_id:05d}_GPT.json",
                Body=json.dumps(gpt_response).encode("utf-8"),
                ContentType="application/json", )
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "NoSuchBucket":
                raise ValueError(f"Bucket {raw_bucket_name} not found")
            elif error_code == "AccessDenied":
                raise ValueError(f"Access denied to s3://{raw_bucket_name}/{raw_prefix}")
            else:
                raise

        initial_tagging = GPTInitialTagging(image_id=image_obj.image_id,
            receipt_id=cluster_id,
            query=query,
            response=response,
            timestamp_added=datetime.now(timezone.utc), )

        # Remove all tags without words
        tags = []
        for key, value in gpt_response.items():
            if value and isinstance(value, list) and len(value) > 0:
                for tag_obj in value:
                    tags.append(Tag(key, tag_obj["w"], tag_obj["l"]))

        # Create the ReceiptWordTag objects
        r_word_tags = []
        for tag in tags:
            for word in r_words:
                if word.word_id == tag.word_id and word.line_id == tag.line_id:
                    r_word_tags.append(ReceiptWordTag(image_id=uuid,
                            receipt_id=cluster_id,
                            line_id=tag.line_id,
                            word_id=tag.word_id,
                            tag=tag.tag,
                            timestamp_added=datetime.now(timezone.utc), ))

        # Deduplicate the ReceiptWordTags
        r_word_tags = deduplicate_receipt_word_tags(r_word_tags)

        # Create the WordTag objects
        for tag in tags:
            for word in words:
                if word.word_id == tag.word_id and word.line_id == tag.line_id:
                    word_tags.append(WordTag(image_id=uuid,
                            line_id=tag.line_id,
                            word_id=tag.word_id,
                            tag=tag.tag,
                            timestamp_added=datetime.now(timezone.utc), ))

        # Deduplicate the WordTags
        word_tags = deduplicate_word_tags(word_tags)

        # Update each word with the tags
        update_words_with_tags(r_words, tags)
        update_words_with_tags(words, tags)

        # Add the receipt + its lines/words/letters to the lists
        all_receipts.append(r)
        all_receipt_lines.extend(r_lines)
        all_receipt_words.extend(r_words)
        all_receipt_word_tags.extend(r_word_tags)
        all_receipt_letters.extend(r_letters)
        all_gpt_initial_taggings.append(initial_tagging)
        all_word_tags.extend(word_tags)

    all_word_tags = deduplicate_word_tags(all_word_tags)
    # Finally, add all entities to DynamoDB.
    DynamoClient(table_name).addImage(image_obj)
    DynamoClient(table_name).addLines(lines)
    DynamoClient(table_name).addWords(words)
    DynamoClient(table_name).addWordTags(all_word_tags)
    DynamoClient(table_name).addLetters(letters)
    DynamoClient(table_name).addReceipts(all_receipts)
    DynamoClient(table_name).addReceiptLines(all_receipt_lines)
    DynamoClient(table_name).addReceiptWords(all_receipt_words)
    DynamoClient(table_name).addReceiptWordTags(all_receipt_word_tags)
    DynamoClient(table_name).addReceiptLetters(all_receipt_letters)
    DynamoClient(table_name).addGPTInitialTaggings(all_gpt_initial_taggings)


def update_words_with_tags(receipt_words: list, tags: list[Tag]) -> None:
    """
    Updates each Word object's tag attribute with the tag names from the GPT response.

    For every Tag in the list, if the Word object's `line_id` and `word_id`
    match the Tag's values, then append the tag's `tag` to the Word's tag list,
    ensuring no duplicates.

    Args:
        receipt_words (list): List of Word objects.
        tags (list[Tag]): List of Tag objects produced from the GPT response.
    """
    # Ensure every Word has a tags list
    for word in receipt_words:
        if not hasattr(word, "tags") or word.tags is None:
            word.tags = []

    # For each tag, update the matching Word object if the tag is not already
    # present.
    for tag in tags:
        for word in receipt_words:
            if word.line_id == tag.line_id and word.word_id == tag.word_id:
                if tag.tag not in word.tags:
                    word.tags.append(tag.tag)

    # Deduplicate each Word object's tags list (preserving order)
    for word in receipt_words:
        # Using dict.fromkeys preserves the order while removing duplicates.
        word.tags = list(dict.fromkeys(word.tags))


def join_overlapping_clusters(cluster_dict: Dict[int, List[Line]],
    image_width: int,
    image_height: int,
    iou_threshold: float = 0.01, ) -> Dict[int, List[Line]]:
    """
    Merge clusters whose bounding boxes overlap above the given iou_threshold.
    This returns a new dictionary of cluster_id -> List[Line] with no overlaps.

    We ignore cluster_id == -1 (noise).
    """
    # Step 1: Collect valid cluster_ids (excluding -1)
    valid_cluster_ids = [cid for cid in cluster_dict.keys() if cid != -1]
    if not valid_cluster_ids:
        return {}

    # Step 2: Compute bounding boxes (as polygon points) for each cluster
    #         We'll reuse your min_area_rect + reorder_box_points approach
    cluster_bboxes = {}
    for cid in valid_cluster_ids:
        lines_in_cluster = cluster_dict[cid]
        # Collect absolute coords for all corners in the cluster
        pts_abs = []
        for ln in lines_in_cluster:
            for corner in [ln.top_left,
                ln.top_right,
                ln.bottom_left,
                ln.bottom_right, ]:
                x_abs = corner["x"] * image_width
                # flip Y to absolute coords
                y_abs = (1.0 - corner["y"]) * image_height
                pts_abs.append((x_abs, y_abs))

        # If no points, skip (should not happen, but just in case)
        if not pts_abs:
            continue

        # Compute the min-area rect with your existing geometry
        (cx, cy), (rw, rh), angle_deg = min_area_rect(pts_abs)
        # Generate 4 corners for that bounding box
        box_4 = box_points((cx, cy), (rw, rh), angle_deg)
        # Order them top-left, top-right, bottom-right, bottom-left
        box_4_ordered = reorder_box_points(box_4)

        # Store this polygon in a dict (matching what dev.py's IoU code
        # expects)
        cluster_bboxes[cid] = {"box_points": box_4_ordered}

    # Step 3: Use a Union-Find structure to merge clusters that overlap
    # each cluster is its own parent
    parent = {cid: cid for cid in valid_cluster_ids}

    def find(x):
        # Path compression
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]

    def union(a, b):
        rootA = find(a)
        rootB = find(b)
        if rootA != rootB:
            parent[rootB] = rootA

    def cross(a, b):
        """Return the 2D cross product of vectors a and b."""
        return a[0] * b[1] - a[1] * b[0]

    def subtract(a, b):
        """Subtract vector b from vector a."""
        return (a[0] - b[0], a[1] - b[1])

    def polygon_area(polygon):
        """
        Compute the area of a polygon given as a list of (x, y) tuples
        using the Shoelace formula.
        """
        area = 0.0
        n = len(polygon)
        for i in range(n):
            j = (i + 1) % n
            area += (polygon[i][0] * polygon[j][1] - polygon[j][0] * polygon[i][1])
        return abs(area) / 2.0

    def is_inside(p, cp1, cp2):
        """
        Check if point p is inside the half-plane defined by the edge from cp1 to cp2.
        Assumes the clipping polygon is ordered counterclockwise.
        """
        # For a counterclockwise clip polygon, a point is inside if it lies to the left
        # of the edge (or on the edge), i.e., if the cross product >= 0.
        return cross(subtract(cp2, cp1), subtract(p, cp1)) >= 0

    def compute_intersection(s, e, cp1, cp2):
        """
        Compute the intersection point of the line segment from s to e with the line
        defined by cp1 -> cp2. Returns the intersection as an (x, y) tuple.
        """
        # Represent the segment as s + t*(e - s)
        # and the clip edge as cp1 + u*(cp2 - cp1)
        r = subtract(e, s)
        d = subtract(cp2, cp1)
        denominator = cross(r, d)
        if denominator == 0:
            # Lines are parallel; in our use-case this is unlikely since
            # we're dealing with rotated rectangles. Return None to be safe.
            return None
        t = cross(subtract(cp1, s), d) / denominator
        return (s[0] + t * r[0], s[1] + t * r[1])

    def polygon_clip(subjectPolygon, clipPolygon):
        """
        Clip a polygon with another polygon using the Sutherland-Hodgman algorithm.
        Both polygons are defined as lists of (x, y) tuples. Returns a list of points
        representing the intersection polygon.
        """
        outputList = subjectPolygon[:]
        cp1 = clipPolygon[-1]
        for cp2 in clipPolygon:
            inputList = outputList
            outputList = []
            if not inputList:
                # No intersection.
                break
            s = inputList[-1]
            for e in inputList:
                if is_inside(e, cp1, cp2):
                    if not is_inside(s, cp1, cp2):
                        intersection_pt = compute_intersection(s, e, cp1, cp2)
                        if intersection_pt:
                            outputList.append(intersection_pt)
                    outputList.append(e)
                elif is_inside(s, cp1, cp2):
                    intersection_pt = compute_intersection(s, e, cp1, cp2)
                    if intersection_pt:
                        outputList.append(intersection_pt)
                s = e
            cp1 = cp2
        return outputList

    def compute_iou(box_a, box_b):
        """
        Compute the Intersection over Union (IoU) for two bounding boxes.

        Each box is a dict that must contain the key "box_points" which is a list
        of four (x, y) tuples defining the polygon.
        """
        poly_a = box_a["box_points"]
        poly_b = box_b["box_points"]

        area_a = polygon_area(poly_a)
        area_b = polygon_area(poly_b)

        # Compute the intersection polygon (if any)
        intersection_poly = polygon_clip(poly_a, poly_b)
        if not intersection_poly:
            intersection_area = 0.0
        else:
            intersection_area = polygon_area(intersection_poly)

        union_area = area_a + area_b - intersection_area
        if union_area == 0:
            return 0.0
        return intersection_area / union_area

    def boxes_overlap(box_a, box_b, iou_threshold=0.1):
        """
        Determine whether two bounding boxes overlap by comparing their IoU to a threshold.
        Returns True if IoU > iou_threshold.
        """
        iou = compute_iou(box_a, box_b)
        return iou > iou_threshold

    # Compare every pair of clusters; if boxes_overlap, union them
    all_ids = list(cluster_bboxes.keys())
    for i in range(len(all_ids)):
        cid_a = all_ids[i]
        for j in range(i + 1, len(all_ids)):
            cid_b = all_ids[j]
            if boxes_overlap(cluster_bboxes[cid_a], cluster_bboxes[cid_b], iou_threshold):
                union(cid_a, cid_b)

    # Step 4: Build a mapping from original cluster_id -> final merged root
    merged_map = {}
    for cid in valid_cluster_ids:
        merged_map[cid] = find(cid)

    # Step 5: Rebuild a new cluster dictionary from these merges
    new_cluster_dict: Dict[int, List[Line]] = {}
    # We also want to assign new consecutive cluster IDs (1, 2, 3, ...)
    # but we first gather lines by their root.
    root_to_lines: Dict[int, List[Line]] = {}
    for cid in valid_cluster_ids:
        root = merged_map[cid]
        if root not in root_to_lines:
            root_to_lines[root] = []
        root_to_lines[root].extend(cluster_dict[cid])

    # Now we reassign new cluster IDs in ascending order
    sorted_roots = sorted(root_to_lines.keys())
    new_cid = 1
    for root_cid in sorted_roots:
        lines_merged = root_to_lines[root_cid]
        new_cluster_dict[new_cid] = lines_merged
        new_cid += 1

    return new_cluster_dict


def calculate_sha256_from_bytes(data: bytes) -> str:
    sha256_hash = hashlib.sha256(data)
    return sha256_hash.hexdigest()


def process_ocr_dict(ocr_data: Dict[str, Any], image_id: str) -> Tuple[List[Line], List[Word], List[Letter]]:
    lines, words, letters = [], [], []
    for line_idx, line_data in enumerate(ocr_data.get("lines", []), start=1):
        line_obj = Line(image_id=image_id,
            line_id=line_idx,
            text=line_data["text"],
            bounding_box=line_data["bounding_box"],
            top_right=line_data["top_right"],
            top_left=line_data["top_left"],
            bottom_right=line_data["bottom_right"],
            bottom_left=line_data["bottom_left"],
            angle_degrees=line_data["angle_degrees"],
            angle_radians=line_data["angle_radians"],
            confidence=line_data["confidence"], )
        lines.append(line_obj)

        for word_idx, word_data in enumerate(line_data.get("words", []), start=1):
            word_obj = Word(image_id=image_id,
                line_id=line_idx,
                word_id=word_idx,
                text=word_data["text"],
                bounding_box=word_data["bounding_box"],
                top_right=word_data["top_right"],
                top_left=word_data["top_left"],
                bottom_right=word_data["bottom_right"],
                bottom_left=word_data["bottom_left"],
                angle_degrees=word_data["angle_degrees"],
                angle_radians=word_data["angle_radians"],
                confidence=word_data["confidence"], )
            words.append(word_obj)

            for letter_idx, letter_data in enumerate(word_data.get("letters", []), start=1):
                letter_obj = Letter(image_id=image_id,
                    line_id=line_idx,
                    word_id=word_idx,
                    letter_id=letter_idx,
                    text=letter_data["text"],
                    bounding_box=letter_data["bounding_box"],
                    top_right=letter_data["top_right"],
                    top_left=letter_data["top_left"],
                    bottom_right=letter_data["bottom_right"],
                    bottom_left=letter_data["bottom_left"],
                    angle_degrees=letter_data["angle_degrees"],
                    angle_radians=letter_data["angle_radians"],
                    confidence=letter_data["confidence"], )
                letters.append(letter_obj)

    return lines, words, letters


def deduplicate_receipt_word_tags(tags: List[ReceiptWordTag], ) -> List[ReceiptWordTag]:
    """
    Deduplicate a list of ReceiptWordTag objects by using their identifying attributes.

    Two ReceiptWordTag objects are considered duplicates if they have the same:
      - image_id
      - receipt_id
      - line_id
      - word_id
      - tag (after stripping extra whitespace)

    Returns a list with only one instance of each unique tag.
    """
    unique = {}
    for tag in tags:
        # Build a unique key; we use the same fields that are used in key() for
        # DynamoDB.
        dedup_key = (tag.image_id,
            tag.receipt_id,
            tag.line_id,
            tag.word_id,
            tag.tag.strip())  # ensure no leading/trailing spaces
        if dedup_key not in unique:
            unique[dedup_key] = tag
    return list(unique.values())


def deduplicate_word_tags(tags: List[WordTag]) -> List[WordTag]:
    """
    Deduplicate a list of WordTag objects by using their identifying attributes.

    Two WordTag objects are considered duplicates if they have the same:
      - image_id
      - line_id
      - word_id
      - tag (after stripping extra whitespace)

    Returns a list with only one instance of each unique tag.
    """
    unique = {}
    for tag in tags:
        # Build a unique key using the fields that define the primary key.
        dedup_key = (tag.image_id,
            tag.line_id,
            tag.word_id,
            tag.tag.strip())  # Normalize the tag by stripping extra whitespace
        if dedup_key not in unique:
            unique[dedup_key] = tag
    return list(unique.values())


def cluster_receipts(lines: List[Line], eps: float = 0.08, min_samples: int = 2) -> Dict[int, List[Line]]:
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


def transform_cluster(cluster_id: int,
    cluster_lines: List[Line],
    cluster_words: List[Word],
    cluster_letters: List[Letter],
    pil_image: PIL_Image.Image,
    image_obj: Image, ):
    """
    1) Compute min-area-rect
    2) If needed, rotate bounding box by 90° so that final w <= h
    3) Affine warp that rectangle to (w, h)
    4) Warp the line/word/letter coords so they match the final orientation
    5) Return the receipt record plus the warped image and OCR objects
    """
    from datetime import datetime, timezone

    # 1) Collect cluster points in absolute image coordinates
    points_abs = []
    for ln in cluster_lines:
        for corner in [ln.top_left,
            ln.top_right,
            ln.bottom_left,
            ln.bottom_right, ]:
            x_abs = corner["x"] * image_obj.width
            y_abs = (1 - corner["y"]) * image_obj.height  # flip Y
            points_abs.append((x_abs, y_abs))

    if not points_abs:
        raise ValueError("No points found for cluster transformation.")

    # Use your existing min_area_rect to find the bounding box
    (cx, cy), (rw, rh), angle_deg = min_area_rect(points_abs)
    w = int(round(rw))
    h = int(round(rh))
    if w < 1 or h < 1:
        raise ValueError("Degenerate bounding box for cluster.")

    # ------------------------------------------------------
    # 2) *Option A fix* - Ensure portrait orientation now
    # ------------------------------------------------------
    if w > h:
        # Rotate the bounding box by -90° so the final warp is 'portrait'
        angle_deg -= 90.0
        # Swap the width & height so the final image is portrait
        w, h = h, w
        # Also swap rw, rh so our box_points() call below is correct
        rw, rh = rh, rw

    # Recompute the four corners for the (possibly) adjusted angle & size
    box_4 = box_points((cx, cy), (rw, rh), angle_deg)
    box_4_ordered = reorder_box_points(box_4)

    # For convenience, name the corners we need for the transform
    src_tl = box_4_ordered[0]
    src_tr = box_4_ordered[1]
    src_bl = box_4_ordered[3]

    # ------------------------------------------------------
    # 3) Build the Pillow transform (dst->src) matrix
    # ------------------------------------------------------
    # Given a 2x3 matrix:
    #   (a_i, b_i, c_i)
    #   (d_i, e_i, f_i)
    # We want:  (x_src, y_src) = M * (x_dst, y_dst, 1).
    # Solve so that (0,0)->src_tl, (w-1,0)->src_tr, (0,h-1)->src_bl.
    if w > 1:
        a_i = (src_tr[0] - src_tl[0]) / (w - 1)
        d_i = (src_tr[1] - src_tl[1]) / (w - 1)
    else:
        a_i = d_i = 0.0

    if h > 1:
        b_i = (src_bl[0] - src_tl[0]) / (h - 1)
        e_i = (src_bl[1] - src_tl[1]) / (h - 1)
    else:
        b_i = e_i = 0.0

    c_i = src_tl[0]
    f_i = src_tl[1]

    # Invert it to get the forward transform for lines, words, etc.
    a_f, b_f, c_f, d_f, e_f, f_f = invert_affine(a_i, b_i, c_i, d_i, e_i, f_i)

    # 4) Warp the image using the "inverse" (dst->src) matrix
    affine_img = pil_image.transform((w, h),
        PIL_Image.AFFINE,
        (a_i, b_i, c_i, d_i, e_i, f_i),
        resample=PIL_Image.BICUBIC, )

    # ------------------------------------------------------
    # 5) Create the Receipt record with final size
    # ------------------------------------------------------
    final_w, final_h = affine_img.size
    r = Receipt(receipt_id=cluster_id,
        image_id=image_obj.image_id,
        width=final_w,
        height=final_h,
        timestamp_added=datetime.now(timezone.utc),
        raw_s3_bucket=image_obj.raw_s3_bucket,
        raw_s3_key=image_obj.raw_s3_key.replace(".png", f"_RECEIPT_{cluster_id:05d}.png"),
        top_left={"x": box_4_ordered[0][0] / image_obj.width,
            "y": 1 - box_4_ordered[0][1] / image_obj.height, },
        top_right={"x": box_4_ordered[1][0] / image_obj.width,
            "y": 1 - box_4_ordered[1][1] / image_obj.height, },
        bottom_right={"x": box_4_ordered[2][0] / image_obj.width,
            "y": 1 - box_4_ordered[2][1] / image_obj.height, },
        bottom_left={"x": box_4_ordered[3][0] / image_obj.width,
            "y": 1 - box_4_ordered[3][1] / image_obj.height, },
        sha256=calculate_sha256_from_bytes(affine_img.tobytes()),
        cdn_s3_bucket=image_obj.cdn_s3_bucket,
        cdn_s3_key=image_obj.cdn_s3_key.replace(".jpg", f"_RECEIPT_{cluster_id:05d}.jpg"), )

    # ------------------------------------------------------
    # 6) Warp the line/word/letter coords "forward"
    #    so they match the final orientation
    # ------------------------------------------------------
    receipt_lines = []
    for ln in cluster_lines:
        ln_copy = deepcopy(ln)
        ln_copy.warp_affine_normalized_forward(a_f,
            b_f,
            c_f,
            d_f,
            e_f,
            f_f,
            orig_width=image_obj.width,
            orig_height=image_obj.height,
            new_width=w,
            new_height=h,
            flip_y=True, )
        receipt_lines.append(ReceiptLine(**dict(ln_copy), receipt_id=cluster_id))

    receipt_words = []
    for wd in cluster_words:
        wd_copy = deepcopy(wd)
        wd_copy.warp_affine_normalized_forward(a_f,
            b_f,
            c_f,
            d_f,
            e_f,
            f_f,
            orig_width=image_obj.width,
            orig_height=image_obj.height,
            new_width=w,
            new_height=h,
            flip_y=True, )
        receipt_words.append(ReceiptWord(**dict(wd_copy), receipt_id=cluster_id))

    receipt_letters = []
    for lt in cluster_letters:
        lt_copy = deepcopy(lt)
        lt_copy.warp_affine_normalized_forward(a_f,
            b_f,
            c_f,
            d_f,
            e_f,
            f_f,
            orig_width=image_obj.width,
            orig_height=image_obj.height,
            new_width=w,
            new_height=h,
            flip_y=True, )
        receipt_letters.append(ReceiptLetter(**dict(lt_copy), receipt_id=cluster_id))

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
