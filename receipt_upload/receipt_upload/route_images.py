import math
from dataclasses import dataclass
from pathlib import Path

from PIL import Image as PIL_Image

from receipt_dynamo.constants import ImageType
from receipt_dynamo.entities import Letter, Line, ReceiptLine, Word

# Known reference formats
SCANNER_FORMAT = (3508, 2480)  # Scanner dimensions
PHONE_FORMAT = (4032, 3024)  # Phone camera dimensions


def _dimension_distance(
    width: int, height: int, reference_width: int, reference_height: int
) -> float:
    """
    Calculate Euclidean distance between given dimensions and a reference
    format. Handles both normal and rotated (90°) orientations.

    Args:
        width: Image width to check
        height: Image height to check
        reference_width: Reference format width
        reference_height: Reference format height

    Returns:
        float: The smaller of the normalized distances between normal and
               rotated orientations
    """
    # Calculate distance for normal orientation
    dx1 = (width - reference_width) / reference_width
    dy1 = (height - reference_height) / reference_height
    normal_distance = math.sqrt(dx1 * dx1 + dy1 * dy1)

    # Calculate distance for rotated orientation (swap width/height)
    dx2 = (width - reference_height) / reference_height
    dy2 = (height - reference_width) / reference_width
    rotated_distance = math.sqrt(dx2 * dx2 + dy2 * dy2)

    # Return the smaller distance (better match)
    return min(normal_distance, rotated_distance)


def classify_image_layout(
    lines: list[Line], image_height: int, image_width: int
) -> ImageType:
    """
    Classifies image based on margins and dimension matching.
    Handles both normal and rotated orientations.

    Args:
        lines: List of OCR lines
        image_height: Height of the image in pixels
        image_width: Width of the image in pixels

    Returns:
        ImageType: Classification of the image (RECEIPT, SCAN, or PHOTO)
    """
    # First check margins to identify individual receipts
    margins = find_margins(lines)
    if all(margin < 0.01 for margin in margins.values()):
        return ImageType.NATIVE

    # Calculate distances to known formats
    scan_distance = _dimension_distance(
        width=image_width,
        height=image_height,
        reference_width=SCANNER_FORMAT[0],
        reference_height=SCANNER_FORMAT[1],
    )
    photo_distance = _dimension_distance(
        width=image_width,
        height=image_height,
        reference_width=PHONE_FORMAT[0],
        reference_height=PHONE_FORMAT[1],
    )

    # Classify based on which format is closer
    return ImageType.SCAN if scan_distance < photo_distance else ImageType.PHOTO


def _open_image(image_path: Path) -> PIL_Image.Image:
    """Open an image and return a PIL Image object."""
    if not image_path.exists():
        raise FileNotFoundError(f"Image path {image_path} does not exist.")
    return PIL_Image.open(image_path)


@dataclass
class ImageOCRInfo:
    """Information about an image's layout and properties."""

    image_path: Path
    image_id: str
    lines: list[Line]
    words: list[Word]
    letters: list[Letter]


def find_margins(lines: list[Line] | list[ReceiptLine]) -> dict[str, float]:
    """Find the margins between text boundaries and image edges.

    Args:
        lines: List of OCR lines

    Returns:
        Dictionary with keys 'left', 'right', 'top', 'bottom' containing the
        distance from the text boundaries to the image edges. All values are
        normalized (0..1). For example:
        - left: distance from x=0 to leftmost text
        - right: distance from rightmost text to x=1
        - top: distance from topmost text to y=1
        - bottom: distance from y=0 to bottommost text
    """
    if not lines:
        return {
            "left": 1.0,  # Full margin when no text
            "right": 1.0,
            "top": 1.0,
            "bottom": 1.0,
        }

    # Initialize with worst case values
    left_margin = 1.0  # Rightmost possible left edge
    right_margin = 1.0  # Leftmost possible right edge
    top_margin = 1.0  # Lowest possible top edge
    bottom_margin = 1.0  # Highest possible bottom edge

    # Find the extremes of all text boxes
    for line in lines:
        # Left margin - distance from x=0 to leftmost text
        left_margin = min(left_margin, line.top_left["x"])

        # Right margin - distance from rightmost text to x=1
        right_margin = min(right_margin, 1.0 - line.top_right["x"])

        # Bottom margin - distance from y=0 to bottommost text
        bottom_margin = min(bottom_margin, line.bottom_left["y"])

        # Top margin - distance from topmost text to y=1
        top_margin = min(top_margin, 1.0 - line.top_left["y"])

    return {
        "left": left_margin,
        "right": right_margin,
        "top": top_margin,
        "bottom": bottom_margin,
    }
