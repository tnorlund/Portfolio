import base64
import hashlib
from typing import Tuple
import cv2
from dynamo import DynamoClient, Line, Word, Letter


def encode_image_below_size(
    image, max_size_kb=380, min_quality=5, step=5
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
    images, lek = client.listImages()
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
            boundingBox=line_data["boundingBox"],
            topRight=line_data["topRight"],
            topLeft=line_data["topLeft"],
            bottomRight=line_data["bottomRight"],
            bottomLeft=line_data["bottomLeft"],
            angleDegrees=line_data["angleDegrees"],
            angleRadians=line_data["angleRadians"],
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
                boundingBox=word_data["boundingBox"],
                topRight=word_data["topRight"],
                topLeft=word_data["topLeft"],
                bottomRight=word_data["bottomRight"],
                bottomLeft=word_data["bottomLeft"],
                angleDegrees=word_data["angleDegrees"],
                angleRadians=word_data["angleRadians"],
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
                    boundingBox=letter_data["boundingBox"],
                    topRight=letter_data["topRight"],
                    topLeft=letter_data["topLeft"],
                    bottomRight=letter_data["bottomRight"],
                    bottomLeft=letter_data["bottomLeft"],
                    angleDegrees=letter_data["angleDegrees"],
                    angleRadians=letter_data["angleRadians"],
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
