import base64
from typing import Tuple
import cv2
from dynamo import Image


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

def get_max_index_in_images(images: list[Image]) -> int:
    """
    Get the maximum index in the list of images.
    """
    return max([image.id for image in images])