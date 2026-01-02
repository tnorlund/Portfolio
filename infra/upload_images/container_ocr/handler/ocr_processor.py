"""
OCR processing logic extracted from process_ocr_results.py.

Handles:
- Downloading OCR JSON and images from S3
- Parsing OCR data into LINE/WORD/LETTER entities
- Classifying image type (NATIVE/PHOTO/SCAN)
- Processing receipts based on type
- Storing entities in DynamoDB
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from PIL import Image as PIL_Image
from receipt_upload.ocr import process_ocr_dict_as_image
from receipt_upload.receipt_processing.native import process_native
from receipt_upload.receipt_processing.photo import process_photo
from receipt_upload.receipt_processing.scan import process_scan
from receipt_upload.route_images import classify_image_layout
from receipt_upload.utils import (
    download_file_from_s3,
    download_image_from_s3,
    get_ocr_job,
    get_ocr_routing_decision,
    image_ocr_to_receipt_ocr,
)

from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import ImageType, OCRJobType, OCRStatus
from receipt_dynamo.entities import (
    Letter,
    Line,
    Receipt,
    ReceiptLetter,
    ReceiptLine,
    ReceiptWord,
    Word,
)

logger = logging.getLogger(__name__)


@dataclass
class OCRData:
    """Container for OCR processing results."""

    lines: list[Line]
    words: list[Word]
    letters: list[Letter]


class OCRProcessor:
    """Handles OCR parsing and storage."""

    def __init__(
        self,
        table_name: str,
        raw_bucket: str,
        site_bucket: str,
        ocr_job_queue_url: str,
        ocr_results_queue_url: str,
    ):
        self.table_name = table_name
        self.raw_bucket = raw_bucket
        self.site_bucket = site_bucket
        self.ocr_job_queue_url = ocr_job_queue_url
        self.ocr_results_queue_url = ocr_results_queue_url
        self.dynamo = DynamoClient(table_name)

    def process_ocr_job(self, image_id: str, job_id: str) -> Dict[str, Any]:
        """
        Process an OCR job: download, parse, classify, and store.

        Returns:
            Dict with success status, image_type, and receipt_id
        """
        try:
            # Debug: Log which table we're using
            print(
                f"[DEBUG] Processing job: image_id={image_id}, job_id={job_id}, "
                f"table={self.table_name}",
                flush=True
            )

            # Get job and routing decision
            ocr_job = get_ocr_job(self.table_name, image_id, job_id)
            ocr_routing_decision = get_ocr_routing_decision(
                self.table_name, image_id, job_id
            )

            print(
                f"[DEBUG] Got OCR job type: {ocr_job.job_type}, "
                f"routing s3_key={ocr_routing_decision.s3_key}",
                flush=True
            )

            # Handle refinement jobs differently
            if ocr_job.job_type == OCRJobType.REFINEMENT.value:
                return self._process_refinement_job(
                    ocr_job, ocr_routing_decision
                )

            # Download and parse OCR JSON
            print(
                f"[DEBUG] Downloading OCR JSON: bucket={ocr_routing_decision.s3_bucket}, "
                f"key={ocr_routing_decision.s3_key}",
                flush=True
            )
            ocr_json_path = download_file_from_s3(
                ocr_routing_decision.s3_bucket,
                ocr_routing_decision.s3_key,
                Path("/tmp"),
            )

            with open(ocr_json_path, "r", encoding="utf-8") as f:
                ocr_json = json.load(f)

            # Debug logging for Swift single-pass detection
            json_keys = list(ocr_json.keys())
            has_receipts = bool(ocr_json.get("receipts"))
            has_classification = bool(ocr_json.get("classification"))
            receipts_count = len(ocr_json.get("receipts", []))
            print(
                f"[DEBUG] OCR JSON keys: {json_keys}, "
                f"has_receipts={has_receipts} (count={receipts_count}), "
                f"has_classification={has_classification}",
                flush=True
            )

            # Check if this is a Swift single-pass result (has receipts with OCR)
            if ocr_json.get("receipts") and ocr_json.get("classification"):
                logger.info(
                    f"Detected Swift single-pass OCR for image {image_id}"
                )
                return self._process_swift_single_pass(
                    ocr_json, ocr_job, ocr_routing_decision
                )

            # Legacy multi-pass flow: parse OCR and process with geometry
            ocr_lines, ocr_words, ocr_letters = process_ocr_dict_as_image(
                ocr_json, image_id
            )

            ocr_data = OCRData(
                lines=ocr_lines, words=ocr_words, letters=ocr_letters
            )

            # Download image
            raw_image_path = download_image_from_s3(
                ocr_job.s3_bucket, ocr_job.s3_key, image_id
            )
            image = PIL_Image.open(raw_image_path)

            # Process first-pass job
            return self._process_first_pass_job(
                image, ocr_data, ocr_job, ocr_routing_decision
            )

        except Exception as e:
            logger.error(f"OCR processing failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
            }

    def _process_refinement_job(
        self, ocr_job, ocr_routing_decision
    ) -> Dict[str, Any]:
        """Process a refinement OCR job."""
        logger.info(f"Refining receipt {ocr_job.image_id}")

        if ocr_job.receipt_id is None:
            logger.error(
                f"Receipt ID is None for refinement job {ocr_job.job_id}"
            )
            return {"success": False, "error": "Receipt ID is None"}

        # Download and parse OCR JSON
        ocr_json_path = download_file_from_s3(
            ocr_routing_decision.s3_bucket,
            ocr_routing_decision.s3_key,
            Path("/tmp"),
        )

        with open(ocr_json_path, "r", encoding="utf-8") as f:
            ocr_json = json.load(f)

        ocr_lines, ocr_words, ocr_letters = process_ocr_dict_as_image(
            ocr_json, ocr_job.image_id
        )

        receipt_lines, receipt_words, receipt_letters = (
            image_ocr_to_receipt_ocr(
                lines=ocr_lines,
                words=ocr_words,
                letters=ocr_letters,
                receipt_id=ocr_job.receipt_id,
            )
        )

        from receipt_upload.receipt_processing.receipt import refine_receipt

        refine_receipt(
            dynamo_table_name=self.table_name,
            receipt_lines=receipt_lines,
            receipt_words=receipt_words,
            receipt_letters=receipt_letters,
            ocr_routing_decision=ocr_routing_decision,
        )

        return {
            "success": True,
            "image_id": ocr_job.image_id,
            "receipt_id": ocr_job.receipt_id,
            "image_type": "REFINEMENT",
            "line_count": len(receipt_lines),
            "word_count": len(receipt_words),
        }

    def _process_swift_single_pass(
        self, ocr_json: Dict[str, Any], ocr_job, ocr_routing_decision
    ) -> Dict[str, Any]:
        """
        Process Swift single-pass OCR results.

        Swift has already done:
        - OCR on original image
        - Classification (NATIVE/PHOTO/SCAN)
        - Clustering
        - Perspective/affine transforms
        - OCR on warped receipts (REFINEMENT)
        - Upload of warped images to S3

        This method just creates DynamoDB entities from the pre-processed data.
        """
        image_id = ocr_job.image_id
        current_time = datetime.now(timezone.utc)

        # Get and validate image type from classification
        classification = ocr_json.get("classification", {})
        image_type_str = classification.get("image_type", "NATIVE").upper()
        try:
            image_type = ImageType[image_type_str]
        except KeyError:
            image_type = ImageType.NATIVE
        # Use normalized enum name for consistency
        image_type_str = image_type.name

        receipts = ocr_json.get("receipts", [])
        receipt_count = len(receipts)

        logger.info(
            f"Processing Swift single-pass: image_id={image_id}, "
            f"image_type={image_type_str}, receipt_count={receipt_count}"
        )

        all_receipt_lines = []
        all_receipt_words = []

        for receipt_data in receipts:
            receipt_id = receipt_data["cluster_id"]
            bounds = receipt_data["bounds"]

            # Create Receipt entity
            # Note: warped images already uploaded by Swift OCRWorker
            raw_s3_key = f"receipts/{image_id}/{receipt_data['s3_key']}"

            receipt = Receipt(
                image_id=image_id,
                receipt_id=receipt_id,
                width=receipt_data["warped_width"],
                height=receipt_data["warped_height"],
                timestamp_added=current_time,
                raw_s3_bucket=ocr_job.s3_bucket,
                raw_s3_key=raw_s3_key,
                top_left=bounds["top_left"],
                top_right=bounds["top_right"],
                bottom_left=bounds["bottom_left"],
                bottom_right=bounds["bottom_right"],
            )
            self.dynamo.add_receipt(receipt)

            # Process OCR lines from warped image (already refined by Swift)
            lines_data = receipt_data.get("lines", [])
            receipt_lines, receipt_words, receipt_letters = (
                self._parse_receipt_ocr_from_swift(
                    image_id, receipt_id, lines_data
                )
            )

            # Store receipt OCR entities
            if receipt_lines:
                self.dynamo.add_receipt_lines(receipt_lines)
                all_receipt_lines.extend(receipt_lines)
            if receipt_words:
                self.dynamo.add_receipt_words(receipt_words)
                all_receipt_words.extend(receipt_words)
            if receipt_letters:
                self.dynamo.add_receipt_letters(receipt_letters)

            logger.info(
                f"Created receipt {receipt_id}: {len(receipt_lines)} lines, "
                f"{len(receipt_words)} words, {len(receipt_letters)} letters"
            )

        # Update routing decision
        ocr_routing_decision.status = OCRStatus.COMPLETED.value
        ocr_routing_decision.receipt_count = receipt_count
        ocr_routing_decision.updated_at = current_time
        self.dynamo.update_ocr_routing_decision(ocr_routing_decision)

        return {
            "success": True,
            "image_id": image_id,
            "image_type": image_type_str,
            "receipt_count": receipt_count,
            "line_count": len(all_receipt_lines),
            "word_count": len(all_receipt_words),
        }

    def _parse_receipt_ocr_from_swift(
        self,
        image_id: str,
        receipt_id: int,
        lines_data: list,
    ) -> tuple:
        """
        Parse Swift OCR output into ReceiptLine/ReceiptWord/ReceiptLetter entities.

        The Swift JSON structure matches the standard OCR format with nested
        lines -> words -> letters.

        Entries with invalid data are skipped with a warning:
        - Lines with empty text are skipped
        - Words with empty text or confidence <= 0 are skipped
        - Letters with text != 1 char or confidence <= 0 are skipped
        """
        receipt_lines = []
        receipt_words = []
        receipt_letters = []

        for line_idx, line_data in enumerate(lines_data, start=1):
            line_text = line_data.get("text", "")
            if not line_text:
                logger.warning(
                    f"Skipping line {line_idx} with empty text for receipt {receipt_id}"
                )
                continue

            receipt_line = ReceiptLine(
                image_id=image_id,
                receipt_id=receipt_id,
                line_id=line_idx,
                text=line_text,
                bounding_box=line_data.get("bounding_box", {}),
                top_left=line_data.get("top_left", {}),
                top_right=line_data.get("top_right", {}),
                bottom_left=line_data.get("bottom_left", {}),
                bottom_right=line_data.get("bottom_right", {}),
                angle_degrees=line_data.get("angle_degrees", 0.0),
                angle_radians=line_data.get("angle_radians", 0.0),
                confidence=line_data.get("confidence", 1.0),
            )
            receipt_lines.append(receipt_line)

            for word_idx, word_data in enumerate(
                line_data.get("words", []), start=1
            ):
                word_text = word_data.get("text", "")
                word_confidence = word_data.get("confidence", 0.0)

                if not word_text or word_confidence <= 0.0:
                    logger.warning(
                        f"Skipping word {word_idx} in line {line_idx} "
                        f"(empty text or confidence <= 0) for receipt {receipt_id}"
                    )
                    continue

                receipt_word = ReceiptWord(
                    image_id=image_id,
                    receipt_id=receipt_id,
                    line_id=line_idx,
                    word_id=word_idx,
                    text=word_text,
                    bounding_box=word_data.get("bounding_box", {}),
                    top_left=word_data.get("top_left", {}),
                    top_right=word_data.get("top_right", {}),
                    bottom_left=word_data.get("bottom_left", {}),
                    bottom_right=word_data.get("bottom_right", {}),
                    angle_degrees=word_data.get("angle_degrees", 0.0),
                    angle_radians=word_data.get("angle_radians", 0.0),
                    confidence=word_confidence,
                    extracted_data=word_data.get("extracted_data"),
                )
                receipt_words.append(receipt_word)

                for letter_idx, letter_data in enumerate(
                    word_data.get("letters", []), start=1
                ):
                    letter_text = letter_data.get("text", "")
                    letter_confidence = letter_data.get("confidence", 0.0)

                    # ReceiptLetter requires exactly 1 character and confidence > 0
                    if len(letter_text) != 1 or letter_confidence <= 0.0:
                        continue

                    receipt_letter = ReceiptLetter(
                        image_id=image_id,
                        receipt_id=receipt_id,
                        line_id=line_idx,
                        word_id=word_idx,
                        letter_id=letter_idx,
                        text=letter_text,
                        bounding_box=letter_data.get("bounding_box", {}),
                        top_left=letter_data.get("top_left", {}),
                        top_right=letter_data.get("top_right", {}),
                        bottom_left=letter_data.get("bottom_left", {}),
                        bottom_right=letter_data.get("bottom_right", {}),
                        angle_degrees=letter_data.get("angle_degrees", 0.0),
                        angle_radians=letter_data.get("angle_radians", 0.0),
                        confidence=letter_confidence,
                    )
                    receipt_letters.append(receipt_letter)

        return receipt_lines, receipt_words, receipt_letters

    def _process_first_pass_job(
        self, image, ocr_data, ocr_job, ocr_routing_decision
    ) -> Dict[str, Any]:
        """Process a first-pass OCR job."""
        # Classify image type
        image_type = classify_image_layout(
            lines=ocr_data.lines,
            image_height=image.height,
            image_width=image.width,
        )

        logger.info(
            f"Image {ocr_job.image_id} classified as {image_type} "
            f"(dimensions: {image.width}x{image.height})"
        )

        try:
            if image_type == ImageType.NATIVE:
                logger.info(f"Processing native receipt {ocr_job.image_id}")

                # Convert image OCR to receipt OCR
                from receipt_upload.utils import image_ocr_to_receipt_ocr

                receipt_lines, receipt_words, _ = image_ocr_to_receipt_ocr(
                    lines=ocr_data.lines,
                    words=ocr_data.words,
                    letters=ocr_data.letters,
                    receipt_id=1,
                )

                process_native(
                    raw_bucket=self.raw_bucket,
                    site_bucket=self.site_bucket,
                    dynamo_table_name=self.table_name,
                    ocr_job_queue_url=self.ocr_job_queue_url,
                    image=image,
                    lines=ocr_data.lines,
                    words=ocr_data.words,
                    letters=ocr_data.letters,
                    ocr_routing_decision=ocr_routing_decision,
                    ocr_job=ocr_job,
                )
                return {
                    "success": True,
                    "image_id": ocr_job.image_id,
                    "image_type": "NATIVE",
                    "receipt_id": 1,  # NATIVE always produces receipt_id=1
                    "line_count": len(receipt_lines),
                    "word_count": len(receipt_words),
                }

            elif image_type == ImageType.PHOTO:
                logger.info(f"Processing photo {ocr_job.image_id}")
                process_photo(
                    raw_bucket=self.raw_bucket,
                    site_bucket=self.site_bucket,
                    dynamo_table_name=self.table_name,
                    ocr_job_queue_url=self.ocr_job_queue_url,
                    ocr_routing_decision=ocr_routing_decision,
                    ocr_job=ocr_job,
                    image=image,
                )
                return {
                    "success": True,
                    "image_id": ocr_job.image_id,
                    "image_type": "PHOTO",
                    "receipt_id": None,  # Multiple receipts
                }

            elif image_type == ImageType.SCAN:
                logger.info(f"Processing scan {ocr_job.image_id}")
                process_scan(
                    raw_bucket=self.raw_bucket,
                    site_bucket=self.site_bucket,
                    dynamo_table_name=self.table_name,
                    ocr_job_queue_url=self.ocr_job_queue_url,
                    ocr_routing_decision=ocr_routing_decision,
                    ocr_job=ocr_job,
                    image=image,
                )
                return {
                    "success": True,
                    "image_id": ocr_job.image_id,
                    "image_type": "SCAN",
                    "receipt_id": None,  # Multiple receipts
                }

            else:
                logger.error(f"Unknown image type: {image_type}")
                self._update_routing_decision_with_error(ocr_routing_decision)
                return {
                    "success": False,
                    "error": f"Unknown image type: {image_type}",
                }

        except ValueError as e:
            logger.error(
                f"Geometry error in processing for image {ocr_job.image_id}: {e}",
                exc_info=True,
            )
            self._update_routing_decision_with_error(ocr_routing_decision)
            return {
                "success": False,
                "error": f"Geometry error: {e}",
            }

    def _update_routing_decision_with_error(self, ocr_routing_decision):
        """Updates the OCR routing decision with an error status."""
        ocr_routing_decision.status = OCRStatus.FAILED.value
        ocr_routing_decision.receipt_count = 0
        ocr_routing_decision.updated_at = datetime.now(timezone.utc)
        self.dynamo.update_ocr_routing_decision(ocr_routing_decision)
