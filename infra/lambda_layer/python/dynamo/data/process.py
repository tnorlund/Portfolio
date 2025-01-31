import hashlib
from io import BytesIO
from PIL import Image as PIL_Image, UnidentifiedImageError
import json
from typing import Any, Dict, List, Tuple
import boto3
from botocore.exceptions import ClientError
from dynamo.data.dynamo_client import DynamoClient
from dynamo.entities import Image, Line, Word, Letter
from datetime import datetime, timezone


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
            raise ValueError(f"UUID {uuid} not found in raw bucket {raw_bucket_name}") from e
        
        # Access denied
        elif error_code == "AccessDenied":
            raise ValueError(f"Access denied to s3://{raw_bucket_name}/{raw_prefix}/*")

        # Anything else, re-raise
        else:
            raise
    
    # Read the OCR results from the ".json" file
    ocr_results = s3.get_object(Bucket=raw_bucket_name, Key=f"{raw_prefix}/{uuid}.json")["Body"].read().decode("utf-8")
    try:
        ocr_results = json.loads(ocr_results)
    except json.JSONDecodeError as e:
        raise ValueError(f"Error decoding OCR results: {e}")
    
    # Read the image file
    try:
        image_bytes = s3.get_object(Bucket=raw_bucket_name, Key=f"{raw_prefix}/{uuid}.png")["Body"].read()
        image = PIL_Image.open(BytesIO(image_bytes))
        # Force Pillow to parse the file fully so corrupted data is caught
        image.verify()
    except UnidentifiedImageError as e:
        raise ValueError(
            f"Corrupted or invalid PNG file at s3://{raw_bucket_name}/{raw_prefix}/{uuid}.png"
        ) from e
    
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
    
    lines, words, letters = process_ocr_dict(ocr_results, uuid)

    # Finally, add the entities to DynamoDB
    DynamoClient(table_name).addImage(Image(
        id=uuid,
        width=image.size[0],
        height=image.size[1],
        timestamp_added=datetime.now(timezone.utc),
        raw_s3_bucket=raw_bucket_name,
        raw_s3_key=f"{raw_prefix}/{uuid}.png",
        cdn_s3_bucket=cdn_bucket_name,
        cdn_s3_key=f"{cdn_prefix}{uuid}.png",
        sha256=calculate_sha256_from_bytes(image_bytes),
    ))


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