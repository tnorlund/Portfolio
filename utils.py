import base64
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
        print(f"Trying quality={quality}, size={size_kb:.2f} KB")

        if size_kb <= max_size_kb:
            print(f"Success with quality={quality}, final size={size_kb:.2f} KB")
            return encoded_str, quality  # Return the Base64 string at this quality

        # Decrease quality and try again
        quality -= step

    # If we exit the loop, we couldn't get below max_size_kb at min_quality.
    print(
        "Warning: Could not reduce image below the desired size with the given constraints."
    )
    return -1  # Return -1 if we couldn't get below the desired size

def get_max_index_in_images(client: DynamoClient) -> int:
    """
    Get the maximum index in the list of images.
    """
    images = client.listImages()
    if not images:
        return 0
    return max([image.id for image in images])

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