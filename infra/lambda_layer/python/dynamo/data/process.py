from dataclasses import dataclass
from datetime import datetime, timezone
from copy import deepcopy
import hashlib
from io import BytesIO
from PIL import Image as PIL_Image, UnidentifiedImageError, ImageOps
import json
from typing import Any, Dict, List, Tuple
import boto3
from botocore.exceptions import ClientError
from dynamo.data._gpt import gpt_request
from dynamo.data.dynamo_client import DynamoClient
from dynamo.data._geometry import (
    invert_affine,
    min_area_rect,
    box_points,
)
from dynamo.entities import (
    Image,
    Line,
    Word,
    Letter,
    Receipt,
    ReceiptLine,
    ReceiptWord,
    ReceiptLetter,
    GPTInitialTagging,
)
from dynamo.entities.receipt_word_tag import ReceiptWordTag
from dynamo.entities.word_tag import WordTag


@dataclass
class Tag:
    tag: str
    word_id: int
    line_id: int


def process(
    table_name: str,
    raw_bucket_name: str,
    raw_prefix: str,
    uuid: str,
    cdn_bucket_name: str,
    cdn_prefix: str = "assets/",
    ocr_dict: Dict[str, Any] = None,
    png_data: bytes = None,
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
                raise ValueError(
                    f"UUID {uuid} not found s3://{raw_bucket_name}/{raw_prefix}/{uuid}*"
                ) from e
            elif error_code == "AccessDenied":
                raise ValueError(
                    f"Access denied to s3://{raw_bucket_name}/{raw_prefix}/*"
                )
            else:
                raise

    if ocr_dict is not None:
        # The user provided OCR data. Upload it to the raw location:
        s3.put_object(
            Bucket=raw_bucket_name,
            Key=f"{raw_prefix}/{uuid}.json",
            Body=json.dumps(ocr_dict).encode("utf-8"),
            ContentType="application/json",
        )
        ocr_results = ocr_dict
    else:
        # Fetch from S3 as before
        ocr_data_obj = s3.get_object(
            Bucket=raw_bucket_name, Key=f"{raw_prefix}/{uuid}.json"
        )["Body"]
        ocr_data_str = ocr_data_obj.read().decode("utf-8")
        try:
            ocr_results = json.loads(ocr_data_str)
        except json.JSONDecodeError as e:
            raise ValueError(f"Error decoding OCR results: {e}")

    if png_data is not None:
        # The user provided the PNG bytes. Upload them to the raw location:
        s3.put_object(
            Bucket=raw_bucket_name,
            Key=f"{raw_prefix}/{uuid}.png",
            Body=png_data,
            ContentType="image/png",
        )
        image_bytes = png_data
    else:
        # Read from S3 as before
        image_bytes = s3.get_object(
            Bucket=raw_bucket_name, Key=f"{raw_prefix}/{uuid}.png"
        )["Body"].read()

    # Verify the image
    try:
        test_img = PIL_Image.open(BytesIO(image_bytes))
        test_img.verify()  # force Pillow to fully parse the image
    except UnidentifiedImageError as e:
        raise ValueError(
            f"Corrupted or invalid PNG at s3://{raw_bucket_name}/{raw_prefix}/{uuid}.png"
        ) from e

    # Reopen (Pillow cannot reuse the same file handle after verify())
    image = PIL_Image.open(BytesIO(image_bytes))
    image = ImageOps.exif_transpose(image)  # Correct orientation

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

    # Build our top-level Image object
    image_obj = Image(
        image_id=uuid,
        width=image.size[0],
        height=image.size[1],
        timestamp_added=datetime.now(timezone.utc),
        raw_s3_bucket=raw_bucket_name,
        raw_s3_key=f"{raw_prefix}/{uuid}.png",
        cdn_s3_bucket=cdn_bucket_name,
        cdn_s3_key=f"{cdn_prefix}/{uuid}.png",
        sha256=calculate_sha256_from_bytes(image_bytes),
    )

    # Parse the OCR into lines, words, letters
    lines, words, letters = process_ocr_dict(ocr_results, uuid)

    # Cluster logic
    cluster_dict = cluster_receipts(lines)

    word_tags = []
    # Receipt extraction, transformation, saving
    for cluster_id, cluster_lines in cluster_dict.items():
        if cluster_id == -1:
            continue
        line_ids = [ln.line_id for ln in cluster_lines]
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

        # Upload the receipt image to the CDN
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

        # Upload the receipt image to the raw bucket
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

        gpt_response, query, response = gpt_request(r, r_words)

        # Save the GPT response to the raw bucket
        try:
            s3.put_object(
                Bucket=raw_bucket_name,
                Key=f"{raw_prefix}/{uuid}_RECEIPT_{cluster_id:05d}_GPT.json",
                Body=json.dumps(gpt_response).encode("utf-8"),
                ContentType="application/json",
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

        initial_tagging = GPTInitialTagging(
            image_id=image_obj.image_id,
            receipt_id=cluster_id,
            query=query,
            response=response,
            timestamp_added=datetime.now(timezone.utc),
        )

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
                    r_word_tags.append(
                        ReceiptWordTag(
                            image_id=uuid,
                            receipt_id=cluster_id,
                            line_id=tag.line_id,
                            word_id=tag.word_id,
                            tag=tag.tag,
                            timestamp_added=datetime.now(timezone.utc),
                        )
                    )

        # Deduplicate the ReceiptWordTags
        r_word_tags = deduplicate_receipt_word_tags(r_word_tags)

        # Create the WordTag objects
        for tag in tags:
            for word in words:
                if word.word_id == tag.word_id and word.line_id == tag.line_id:
                    word_tags.append(
                        WordTag(
                            image_id=uuid,
                            line_id=tag.line_id,
                            word_id=tag.word_id,
                            tag=tag.tag,
                            timestamp_added=datetime.now(timezone.utc),
                        )
                    )

        # Deduplicate the WordTags
        word_tags = deduplicate_word_tags(word_tags)

        # Update each word with the tags
        update_words_with_tags(r_words, tags)
        update_words_with_tags(words, tags)

        # Insert the receipt + its lines/words/letters in Dynamo
        DynamoClient(table_name).addReceipt(r)
        DynamoClient(table_name).addReceiptLines(r_lines)
        DynamoClient(table_name).addReceiptWords(r_words)
        DynamoClient(table_name).addReceiptWordTags(r_word_tags)
        DynamoClient(table_name).addReceiptLetters(r_letters)
        DynamoClient(table_name).addGPTInitialTagging(initial_tagging)

    # Finally, add all entities to DynamoDB.
    DynamoClient(table_name).addImage(image_obj)
    DynamoClient(table_name).addLines(lines)
    DynamoClient(table_name).addWords(words)
    DynamoClient(table_name).addWordTags(word_tags)
    DynamoClient(table_name).addLetters(letters)


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

    # For each tag, update the matching Word object if the tag is not already present.
    for tag in tags:
        for word in receipt_words:
            if word.line_id == tag.line_id and word.word_id == tag.word_id:
                if tag.tag not in word.tags:
                    word.tags.append(tag.tag)

    # Deduplicate each Word object's tags list (preserving order)
    for word in receipt_words:
        # Using dict.fromkeys preserves the order while removing duplicates.
        word.tags = list(dict.fromkeys(word.tags))


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
            line_id=line_idx,
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
                word_id=word_idx,
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
                    letter_id=letter_idx,
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



def deduplicate_receipt_word_tags(tags: List[ReceiptWordTag]) -> List[ReceiptWordTag]:
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
        # Build a unique key; we use the same fields that are used in key() for DynamoDB.
        dedup_key = (
            tag.image_id,
            tag.receipt_id,
            tag.line_id,
            tag.word_id,
            tag.tag.strip(),  # ensure no leading/trailing spaces
        )
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
        dedup_key = (
            tag.image_id,
            tag.line_id,
            tag.word_id,
            tag.tag.strip(),  # Normalize the tag by stripping extra whitespace
        )
        if dedup_key not in unique:
            unique[dedup_key] = tag
    return list(unique.values())


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
):
    """
    1) Compute min-area-rect
    2) If needed, rotate bounding box by 90° so that final w <= h
    3) Affine warp that rectangle to (w,h)
    4) Warp the line/word/letter coords so they match the final orientation
    5) Return the receipt record plus the warped image and OCR objects
    """
    import copy
    from datetime import datetime, timezone

    # 1) Collect cluster points in absolute image coordinates
    points_abs = []
    for ln in cluster_lines:
        for corner in [ln.top_left, ln.top_right, ln.bottom_left, ln.bottom_right]:
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

    # 4) Warp the image using the “inverse” (dst->src) matrix
    affine_img = pil_image.transform(
        (w, h),
        PIL_Image.AFFINE,
        (a_i, b_i, c_i, d_i, e_i, f_i),
        resample=PIL_Image.BICUBIC,
    )

    # ------------------------------------------------------
    # 5) Create the Receipt record with final size
    # ------------------------------------------------------
    final_w, final_h = affine_img.size
    r = Receipt(
        receipt_id=cluster_id,
        image_id=image_obj.image_id,
        width=final_w,
        height=final_h,
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

    # ------------------------------------------------------
    # 6) Warp the line/word/letter coords “forward”
    #    so they match the final orientation
    # ------------------------------------------------------
    receipt_lines = []
    for ln in cluster_lines:
        ln_copy = deepcopy(ln)
        ln_copy.warp_affine_normalized_forward(
            a_f,
            b_f,
            c_f,
            d_f,
            e_f,
            f_f,
            orig_width=image_obj.width,
            orig_height=image_obj.height,
            new_width=w,
            new_height=h,
            flip_y=True,
        )
        receipt_lines.append(ReceiptLine(**dict(ln_copy), receipt_id=cluster_id))

    receipt_words = []
    for wd in cluster_words:
        wd_copy = deepcopy(wd)
        wd_copy.warp_affine_normalized_forward(
            a_f,
            b_f,
            c_f,
            d_f,
            e_f,
            f_f,
            orig_width=image_obj.width,
            orig_height=image_obj.height,
            new_width=w,
            new_height=h,
            flip_y=True,
        )
        receipt_words.append(ReceiptWord(**dict(wd_copy), receipt_id=cluster_id))

    receipt_letters = []
    for lt in cluster_letters:
        lt_copy = deepcopy(lt)
        lt_copy.warp_affine_normalized_forward(
            a_f,
            b_f,
            c_f,
            d_f,
            e_f,
            f_f,
            orig_width=image_obj.width,
            orig_height=image_obj.height,
            new_width=w,
            new_height=h,
            flip_y=True,
        )
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
