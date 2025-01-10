import base64
import hashlib
from typing import Tuple
import cv2
from dynamo import DynamoClient, Line, Word, Letter


def encode_image_below_size(
    image, max_size_kb=380, min_quality=10, step=10
) -> Tuple[str, int]:
    """
    Compress the image (JPEG) to ensure the final Base64 string is <= max_size_kb.
    Gradually lowers the JPEG quality in steps until the size requirement is met or
    the minimum quality is reached.
    """
    quality = 90  # start from a high quality
    while quality >= min_quality:
        retval, buffer = cv2.imencode(
            ".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality]
        )

        # Base64-encode the compressed image
        encoded_str = base64.b64encode(buffer).decode("utf-8")

        # Check the size
        size_kb = len(encoded_str) / 1024

        if size_kb <= max_size_kb:
            return encoded_str, quality  # Return the Base64 string at this quality

        # Decrease quality and try again
        quality -= step

    # If we exit the loop, we couldn't get below max_size_kb at min_quality.
    return -1  # Return -1 if we couldn't get below the desired size


def get_max_index_in_images(client: DynamoClient) -> int:
    """
    Get the maximum index in the list of images.
    """
    images = client.listImages()
    if not images:
        return 0
    image_indexes = [image.id for image in images]
    image_indexes.sort()
    # Find where the indexes are not consecutive
    for i, index in enumerate(image_indexes):
        if i + 1 != index:
            return i + 1
    return len(image_indexes) + 1


def process_ocr_dict(ocr_data: dict, image_id: int) -> Tuple[list, list, list]:
    """
    Process the OCR data and return lists of lines, words, and letters.
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
            x=line_data["boundingBox"]["x"],
            y=line_data["boundingBox"]["y"],
            width=line_data["boundingBox"]["width"],
            height=line_data["boundingBox"]["height"],
            angle=line_data["angle"],
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
                x=word_data["boundingBox"]["x"],
                y=word_data["boundingBox"]["y"],
                width=word_data["boundingBox"]["width"],
                height=word_data["boundingBox"]["height"],
                angle=word_data["angle"],
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
                    x=letter_data["boundingBox"]["x"],
                    y=letter_data["boundingBox"]["y"],
                    width=letter_data["boundingBox"]["width"],
                    height=letter_data["boundingBox"]["height"],
                    angle=letter_data["angle"],
                    confidence=letter_data["confidence"],
                )
                letters.append(letter_obj)
    return lines, words, letters


import hashlib


def calculate_sha256(file_path):
    """
    Calculate the SHA-256 hash of a file.

    Example
    -------
    png_file_path = "example.png"  # Replace with your PNG file path
    hash_value = calculate_sha256(png_file_path)
    print(f"SHA-256: {hash_value}")
    """
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


{
    "S3Key": {"S": "raw/accb5c7c-381d-4f1e-b18a-f9d28abb625d.png"},
    "TimestampAdded": {"S": "2025-01-05T18:53:06.294508"},
    "Width": {"N": "2480"},
    "S3Bucket": {"S": "raw-image-bucket-c779c32"},
    "SK": {"S": "IMAGE"},
    "Height": {"N": "3508"},
    "PK": {"S": "IMAGE#00042"},
    "Type": {"S": "IMAGE"},
}
