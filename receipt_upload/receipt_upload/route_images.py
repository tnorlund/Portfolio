import json
from uuid import uuid4
from dataclasses import dataclass
from PIL import Image as PIL_Image
from pathlib import Path
from tempfile import TemporaryDirectory
from receipt_dynamo.constants import ImageType
from receipt_dynamo.entities import Line, Word, Letter
from receipt_upload.ocr import apple_vision_ocr_job, process_ocr_dict


def classify_image_layout(
    fill_ratio: float, density: float, area_mean: float, area_stddev: float
) -> str:
    """Classifies image based on fill ratio, density, mean line area, and stddev."""
    if fill_ratio > 0.35 and density < 100 and area_mean > 0.004:
        return ImageType.NATIVE
    elif density > 150 and fill_ratio < 0.3 and area_mean < 0.003:
        return ImageType.SCAN
    else:
        return ImageType.PHOTO


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


def get_ocr_info(
    image_path: Path,
) -> ImageOCRInfo:
    """Get the OCR information for an image."""
    image_id = str(uuid4())
    image = _open_image(image_path)
    with TemporaryDirectory() as temp_dir:
        ocr_json_files = apple_vision_ocr_job([image_path], Path(temp_dir))
        ocr_dict = json.load(open(ocr_json_files[0]))
        lines, words, letters = process_ocr_dict(ocr_dict, image_id)
    return ImageOCRInfo(image_path, image_id, lines, words, letters)
