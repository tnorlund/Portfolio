import json
import platform
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple
from uuid import uuid4

from receipt_dynamo.entities import (
    Letter,
    Line,
    Word,
    ReceiptLine,
    ReceiptWord,
    ReceiptLetter,
)


def process_ocr_dict_as_receipt(
    ocr_data: Dict[str, Any], image_id: str, receipt_id: int
) -> tuple[list[ReceiptLine], list[ReceiptWord], list[ReceiptLetter]]:
    lines = []
    words = []
    letters = []
    for line_idx, line_data in enumerate(ocr_data.get("lines", []), start=1):
        line_obj = ReceiptLine(
            image_id=image_id,
            receipt_id=receipt_id,
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

        for word_idx, word_data in enumerate(
            line_data.get("words", []), start=1
        ):
            word_obj = ReceiptWord(
                image_id=image_id,
                receipt_id=receipt_id,
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
                extracted_data=word_data.get("extracted_data", None),
            )
            words.append(word_obj)

            for letter_idx, letter_data in enumerate(
                word_data.get("letters", []), start=1
            ):
                letter_obj = ReceiptLetter(
                    image_id=image_id,
                    receipt_id=receipt_id,
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


def process_ocr_dict_as_image(
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

        for word_idx, word_data in enumerate(
            line_data.get("words", []), start=1
        ):
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
                extracted_data=word_data.get("extracted_data", None),
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


def apple_vision_ocr_job(
    image_paths: list[Path], temp_dir: Path
) -> list[Path]:

    # Check to make sure the files exist
    for image_path in image_paths:
        if not image_path.exists():
            raise FileNotFoundError(f"Image file not found: {image_path}")
    swift_script = Path(__file__).parent / "OCRSwift.swift"
    # Check to see that the swift script exists
    if not swift_script.exists():
        raise FileNotFoundError(f"Swift script not found: {swift_script}")
    # Check to see that this is a Mac
    if not platform.system() == "Darwin":
        raise ValueError("Apple's Vision Framework can only be run on a Mac")

    swift_args = [
        "swift",
        str(swift_script),
        str(temp_dir),
    ] + image_paths
    try:
        subprocess.run(
            swift_args,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError as e:
        raise ValueError(f"Error running Swift script: {e}") from e
    json_files = list(temp_dir.glob("*.json"))
    if len(json_files) != len(image_paths):
        raise ValueError(
            f"Expected {len(image_paths)} JSON files, but got {len(json_files)}"
        )
    return json_files


def _download_image_from_s3(
    image_id: str, s3_bucket: str, s3_key: str
) -> Path:
    s3_client = boto3.client("s3")
    temp_dir = Path(tempfile.mkdtemp())
    image_path = temp_dir / f"{image_id}.jpg"
    s3_client.download_file(s3_bucket, s3_key, str(image_path))
    return image_path


def _upload_json_to_s3(image_path: Path, s3_bucket: str, s3_key: str) -> None:
    s3_client = boto3.client("s3")
    s3_client.upload_file(str(image_path), s3_bucket, s3_key)


def apple_vision_ocr(image_paths: list[str]) -> Dict[str, Any]:
    """Executes a Swift OCR script on the provided image paths."""
    swift_script = Path(__file__).parent / "OCRSwift.swift"
    # Check to see that the swift script exists
    if not swift_script.exists():
        raise FileNotFoundError(f"Swift script not found: {swift_script}")
    # Check to see that this is a Mac
    if not platform.system() == "Darwin":
        raise ValueError("Apple's Vision Framework can only be run on a Mac")
    # Make a temporary directory
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_dir = Path(temp_dir)
        try:
            swift_args = [
                "swift",
                str(swift_script),
                str(temp_dir),
            ] + image_paths
            subprocess.run(
                swift_args,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except subprocess.CalledProcessError as e:
            raise ValueError(f"Error running Swift script: {e}") from e

        ocr_dict = {}
        # Iterate over the JSON files in the output directory
        for json_file in temp_dir.glob("*.json"):
            # Get the image ID from the JSON file name
            image_id = str(uuid4())
            # Read the JSON file
            with open(json_file, "r") as f:
                data = json.load(f)
            # Add the image ID to the return dictionary
            ocr_dict[image_id] = _process_ocr_dict(data, image_id)

        return ocr_dict
