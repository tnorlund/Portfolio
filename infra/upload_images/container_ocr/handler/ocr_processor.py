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
from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import ImageType, OCRJobType, OCRStatus
from receipt_dynamo.entities import (
    Image,
    Letter,
    Line,
    Receipt,
    ReceiptLetter,
    ReceiptLine,
    ReceiptWord,
    Word,
)
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
    upload_all_cdn_formats,
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

    def __init__(  # pylint: disable=too-many-positional-arguments
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
            # Get job and routing decision
            ocr_job = get_ocr_job(self.table_name, image_id, job_id)
            ocr_routing_decision = get_ocr_routing_decision(
                self.table_name, image_id, job_id
            )

            # Handle refinement jobs differently
            if ocr_job.job_type == OCRJobType.REFINEMENT.value:
                return self._process_refinement_job(
                    ocr_job, ocr_routing_decision
                )

            # Download and parse OCR JSON
            ocr_json_path = download_file_from_s3(
                ocr_routing_decision.s3_bucket,
                ocr_routing_decision.s3_key,
                Path("/tmp"),
            )

            with open(ocr_json_path, "r", encoding="utf-8") as f:
                ocr_json = json.load(f)

            # Check if this is a Swift single-pass result (has receipts with
            # OCR). Swift uploads JSON with 'receipts' array and
            # 'classification' dict.
            if ocr_json.get("receipts") and ocr_json.get("classification"):
                logger.info(
                    "Detected Swift single-pass OCR for image %s",
                    image_id,
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

        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("OCR processing failed: %s", exc, exc_info=True)
            return {
                "success": False,
                "error": str(exc),
            }

    def _process_refinement_job(
        self, ocr_job: Any, ocr_routing_decision: Any
    ) -> Dict[str, Any]:
        """Process a refinement OCR job."""
        logger.info("Refining receipt %s", ocr_job.image_id)

        if ocr_job.receipt_id is None:
            logger.error(
                "Receipt ID is None for refinement job %s",
                ocr_job.job_id,
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

        from receipt_upload.receipt_processing.receipt import (  # pylint: disable=import-outside-toplevel
            refine_receipt,
        )

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
        self,
        ocr_json: Dict[str, Any],
        ocr_job: Any,
        ocr_routing_decision: Any,
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
            "Processing Swift single-pass: image_id=%s, image_type=%s, "
            "receipt_count=%s",
            image_id,
            image_type_str,
            receipt_count,
        )

        # Create Line/Word/Letter entities from first-pass OCR (original image)
        original_lines = ocr_json.get("lines", [])
        if original_lines:
            lines, words, letters = self._parse_original_ocr_from_swift(
                image_id, original_lines
            )
            if lines:
                self.dynamo.add_lines(lines)
            if words:
                self.dynamo.add_words(words)
            if letters:
                self.dynamo.add_letters(letters)
            logger.info(
                "Created first-pass entities: %s lines, %s words, %s letters",
                len(lines),
                len(words),
                len(letters),
            )

        all_receipt_lines = []
        all_receipt_words = []

        for receipt_idx, receipt_data in enumerate(receipts):
            try:
                # Validate required fields exist
                receipt_id = receipt_data["cluster_id"]
                bounds = receipt_data["bounds"]
                s3_key = receipt_data["s3_key"]
                warped_width = receipt_data["warped_width"]
                warped_height = receipt_data["warped_height"]

                # Validate bounds structure has all required corners
                required_corners = [
                    "top_left",
                    "top_right",
                    "bottom_left",
                    "bottom_right",
                ]
                if not all(corner in bounds for corner in required_corners):
                    raise ValueError(
                        "Missing required bounds corners for receipt "
                        f"{receipt_id}"
                    )

            except (KeyError, ValueError) as exc:
                logger.error(
                    "Skipping malformed receipt %s in image %s: %s",
                    receipt_idx,
                    image_id,
                    exc,
                )
                continue

            # Create Receipt entity
            # Note: warped images already uploaded by Swift OCRWorker
            raw_s3_key = f"receipts/{image_id}/{s3_key}"

            receipt = Receipt(
                image_id=image_id,
                receipt_id=receipt_id,
                width=warped_width,
                height=warped_height,
                timestamp_added=current_time,
                raw_s3_bucket=ocr_job.s3_bucket,
                raw_s3_key=raw_s3_key,
                top_left=bounds["top_left"],
                top_right=bounds["top_right"],
                bottom_left=bounds["bottom_left"],
                bottom_right=bounds["bottom_right"],
            )

            # Process warped receipt image for CDN (multiple sizes and formats)
            try:
                warped_image_path = download_image_from_s3(
                    ocr_job.s3_bucket,
                    raw_s3_key,
                    image_id,
                    unique_suffix=f"receipt_{receipt_id}",
                )
                warped_image = PIL_Image.open(warped_image_path)

                # Upload to CDN with all sizes and formats
                # Receipt CDN key: assets/{image_id}/{receipt_id}.jpg etc.
                receipt_cdn_base_key = f"assets/{image_id}/{receipt_id}"
                receipt_cdn_keys = upload_all_cdn_formats(
                    warped_image,
                    self.site_bucket,
                    receipt_cdn_base_key,
                    generate_thumbnails=True,
                )

                # Update Receipt entity with CDN keys
                receipt.cdn_s3_key = receipt_cdn_keys.get("jpeg_full")
                receipt.cdn_webp_s3_key = receipt_cdn_keys.get("webp_full")
                receipt.cdn_avif_s3_key = receipt_cdn_keys.get("avif_full")
                receipt.cdn_thumbnail_s3_key = receipt_cdn_keys.get(
                    "jpeg_thumbnail"
                )
                receipt.cdn_thumbnail_webp_s3_key = receipt_cdn_keys.get(
                    "webp_thumbnail"
                )
                receipt.cdn_thumbnail_avif_s3_key = receipt_cdn_keys.get(
                    "avif_thumbnail"
                )
                receipt.cdn_small_s3_key = receipt_cdn_keys.get("jpeg_small")
                receipt.cdn_small_webp_s3_key = receipt_cdn_keys.get(
                    "webp_small"
                )
                receipt.cdn_small_avif_s3_key = receipt_cdn_keys.get(
                    "avif_small"
                )
                receipt.cdn_medium_s3_key = receipt_cdn_keys.get("jpeg_medium")
                receipt.cdn_medium_webp_s3_key = receipt_cdn_keys.get(
                    "webp_medium"
                )
                receipt.cdn_medium_avif_s3_key = receipt_cdn_keys.get(
                    "avif_medium"
                )

                logger.info(
                    "Processed receipt %s for CDN: %s -> %s",
                    receipt_id,
                    raw_s3_key,
                    receipt_cdn_base_key,
                )
            except Exception as cdn_exc:  # pylint: disable=broad-exception-caught
                logger.warning(
                    "Failed to process receipt %s for CDN: %s - %s",
                    receipt_id,
                    raw_s3_key,
                    cdn_exc,
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
                "Created receipt %s: %s lines, %s words, %s letters",
                receipt_id,
                len(receipt_lines),
                len(receipt_words),
                len(receipt_letters),
            )

        # Update routing decision
        ocr_routing_decision.status = OCRStatus.COMPLETED.value
        ocr_routing_decision.receipt_count = receipt_count
        ocr_routing_decision.updated_at = current_time
        self.dynamo.update_ocr_routing_decision(ocr_routing_decision)

        # Create Image entity (Swift provides dimensions in classification)
        image_width = classification.get("image_width", 0)
        image_height = classification.get("image_height", 0)
        if image_width > 0 and image_height > 0:
            image_entity = Image(
                image_id=image_id,
                width=image_width,
                height=image_height,
                timestamp_added=current_time,
                raw_s3_bucket=ocr_job.s3_bucket,
                raw_s3_key=ocr_job.s3_key,
                image_type=image_type,
            )

            # Process original image for CDN (multiple sizes and formats)
            try:
                original_image_path = download_image_from_s3(
                    ocr_job.s3_bucket,
                    ocr_job.s3_key,
                    image_id,
                    unique_suffix="original",
                )
                original_image = PIL_Image.open(original_image_path)

                # Upload to CDN with all sizes and formats
                # Use 'original' to avoid collision with receipt CDN keys
                cdn_base_key = f"assets/{image_id}/original"
                cdn_keys = upload_all_cdn_formats(
                    original_image,
                    self.site_bucket,
                    cdn_base_key,
                    generate_thumbnails=True,
                )

                # Update Image entity with CDN keys
                image_entity.cdn_s3_key = cdn_keys.get("jpeg_full")
                image_entity.cdn_webp_s3_key = cdn_keys.get("webp_full")
                image_entity.cdn_avif_s3_key = cdn_keys.get("avif_full")
                image_entity.cdn_thumbnail_s3_key = cdn_keys.get("jpeg_thumbnail")
                image_entity.cdn_thumbnail_webp_s3_key = cdn_keys.get(
                    "webp_thumbnail"
                )
                image_entity.cdn_thumbnail_avif_s3_key = cdn_keys.get(
                    "avif_thumbnail"
                )
                image_entity.cdn_small_s3_key = cdn_keys.get("jpeg_small")
                image_entity.cdn_small_webp_s3_key = cdn_keys.get("webp_small")
                image_entity.cdn_small_avif_s3_key = cdn_keys.get("avif_small")
                image_entity.cdn_medium_s3_key = cdn_keys.get("jpeg_medium")
                image_entity.cdn_medium_webp_s3_key = cdn_keys.get("webp_medium")
                image_entity.cdn_medium_avif_s3_key = cdn_keys.get("avif_medium")

                logger.info(
                    "Processed original image for CDN: %s -> %s",
                    image_id,
                    cdn_base_key,
                )
            except Exception as cdn_exc:  # pylint: disable=broad-exception-caught
                logger.warning(
                    "Failed to process original image for CDN: %s - %s",
                    image_id,
                    cdn_exc,
                )

            self.dynamo.add_image(image_entity)
            logger.info(
                "Created Image entity: %s (%dx%d, type=%s)",
                image_id,
                image_width,
                image_height,
                image_type_str,
            )
        else:
            logger.warning(
                "Skipping Image entity creation - missing dimensions for %s",
                image_id,
            )

        # For Swift single-pass, return first receipt_id for embedding
        # processing (most uploads are single-receipt; multi-receipt will
        # process first only).
        first_receipt_id = receipts[0]["cluster_id"] if receipts else None

        return {
            "success": True,
            "image_id": image_id,
            "image_type": image_type_str,
            "receipt_id": first_receipt_id,
            "receipt_count": receipt_count,
            "receipt_lines": all_receipt_lines,
            "receipt_words": all_receipt_words,
            "line_count": len(all_receipt_lines),
            "word_count": len(all_receipt_words),
            "swift_single_pass": True,  # Flag for handler to enable embeddings
        }

    def _parse_receipt_ocr_from_swift(
        self,
        image_id: str,
        receipt_id: int,
        lines_data: list[Dict[str, Any]],
    ) -> tuple[list[ReceiptLine], list[ReceiptWord], list[ReceiptLetter]]:
        """
        Parse Swift OCR output into ReceiptLine/ReceiptWord/ReceiptLetter
        entities.

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

        def _has_valid_geometry(data: dict) -> bool:
            """Check if geometry fields have required keys."""
            bbox = data.get("bounding_box", {})
            if not all(k in bbox for k in ("x", "y", "width", "height")):
                return False
            for corner in (
                "top_left",
                "top_right",
                "bottom_left",
                "bottom_right",
            ):
                point = data.get(corner, {})
                if not all(k in point for k in ("x", "y")):
                    return False
            return True

        for line_idx, line_data in enumerate(lines_data, start=1):
            line_text = line_data.get("text", "")
            if not line_text:
                logger.warning(
                    "Skipping line %s with empty text for receipt %s",
                    line_idx,
                    receipt_id,
                )
                continue

            if not _has_valid_geometry(line_data):
                logger.warning(
                    "Skipping line %s with missing geometry for receipt %s",
                    line_idx,
                    receipt_id,
                )
                continue

            receipt_line = ReceiptLine(
                image_id=image_id,
                receipt_id=receipt_id,
                line_id=line_idx,
                text=line_text,
                bounding_box=line_data["bounding_box"],
                top_left=line_data["top_left"],
                top_right=line_data["top_right"],
                bottom_left=line_data["bottom_left"],
                bottom_right=line_data["bottom_right"],
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
                        "Skipping word %s in line %s (empty text or "
                        "confidence <= 0) for receipt %s",
                        word_idx,
                        line_idx,
                        receipt_id,
                    )
                    continue

                if not _has_valid_geometry(word_data):
                    logger.warning(
                        "Skipping word %s in line %s with missing geometry "
                        "for receipt %s",
                        word_idx,
                        line_idx,
                        receipt_id,
                    )
                    continue

                receipt_word = ReceiptWord(
                    image_id=image_id,
                    receipt_id=receipt_id,
                    line_id=line_idx,
                    word_id=word_idx,
                    text=word_text,
                    bounding_box=word_data["bounding_box"],
                    top_left=word_data["top_left"],
                    top_right=word_data["top_right"],
                    bottom_left=word_data["bottom_left"],
                    bottom_right=word_data["bottom_right"],
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

                    # ReceiptLetter requires exactly 1 character and
                    # confidence > 0.
                    if len(letter_text) != 1 or letter_confidence <= 0.0:
                        continue

                    # Skip letters with missing geometry
                    if not _has_valid_geometry(letter_data):
                        continue

                    receipt_letter = ReceiptLetter(
                        image_id=image_id,
                        receipt_id=receipt_id,
                        line_id=line_idx,
                        word_id=word_idx,
                        letter_id=letter_idx,
                        text=letter_text,
                        bounding_box=letter_data["bounding_box"],
                        top_left=letter_data["top_left"],
                        top_right=letter_data["top_right"],
                        bottom_left=letter_data["bottom_left"],
                        bottom_right=letter_data["bottom_right"],
                        angle_degrees=letter_data.get("angle_degrees", 0.0),
                        angle_radians=letter_data.get("angle_radians", 0.0),
                        confidence=letter_confidence,
                    )
                    receipt_letters.append(receipt_letter)

        return receipt_lines, receipt_words, receipt_letters

    def _parse_original_ocr_from_swift(
        self,
        image_id: str,
        lines_data: list[Dict[str, Any]],
    ) -> tuple[list[Line], list[Word], list[Letter]]:
        """
        Parse Swift first-pass OCR output into Line/Word/Letter entities.

        These are the original image OCR results used for classification and
        clustering, before perspective/affine transforms are applied.
        """
        lines = []
        words = []
        letters = []

        def _has_valid_geometry(data: dict) -> bool:
            """Check if geometry fields have required keys."""
            bbox = data.get("bounding_box", {})
            if not all(k in bbox for k in ("x", "y", "width", "height")):
                return False
            for corner in ("top_left", "top_right", "bottom_left", "bottom_right"):
                point = data.get(corner, {})
                if not all(k in point for k in ("x", "y")):
                    return False
            return True

        for line_idx, line_data in enumerate(lines_data, start=1):
            line_text = line_data.get("text", "")
            if not line_text or not _has_valid_geometry(line_data):
                continue

            line = Line(
                image_id=image_id,
                line_id=line_idx,
                text=line_text,
                bounding_box=line_data["bounding_box"],
                top_left=line_data["top_left"],
                top_right=line_data["top_right"],
                bottom_left=line_data["bottom_left"],
                bottom_right=line_data["bottom_right"],
                angle_degrees=line_data.get("angle_degrees", 0.0),
                angle_radians=line_data.get("angle_radians", 0.0),
                confidence=line_data.get("confidence", 1.0),
            )
            lines.append(line)

            for word_idx, word_data in enumerate(
                line_data.get("words", []), start=1
            ):
                word_text = word_data.get("text", "")
                word_confidence = word_data.get("confidence", 0.0)
                if not word_text or word_confidence <= 0 or not _has_valid_geometry(word_data):
                    continue

                word = Word(
                    image_id=image_id,
                    line_id=line_idx,
                    word_id=word_idx,
                    text=word_text,
                    bounding_box=word_data["bounding_box"],
                    top_left=word_data["top_left"],
                    top_right=word_data["top_right"],
                    bottom_left=word_data["bottom_left"],
                    bottom_right=word_data["bottom_right"],
                    angle_degrees=word_data.get("angle_degrees", 0.0),
                    angle_radians=word_data.get("angle_radians", 0.0),
                    confidence=word_confidence,
                )
                words.append(word)

                for letter_idx, letter_data in enumerate(
                    word_data.get("letters", []), start=1
                ):
                    letter_text = letter_data.get("text", "")
                    letter_confidence = letter_data.get("confidence", 0.0)
                    if len(letter_text) != 1 or letter_confidence <= 0:
                        continue
                    if not _has_valid_geometry(letter_data):
                        continue

                    letter = Letter(
                        image_id=image_id,
                        line_id=line_idx,
                        word_id=word_idx,
                        letter_id=letter_idx,
                        text=letter_text,
                        bounding_box=letter_data["bounding_box"],
                        top_left=letter_data["top_left"],
                        top_right=letter_data["top_right"],
                        bottom_left=letter_data["bottom_left"],
                        bottom_right=letter_data["bottom_right"],
                        angle_degrees=letter_data.get("angle_degrees", 0.0),
                        angle_radians=letter_data.get("angle_radians", 0.0),
                        confidence=letter_confidence,
                    )
                    letters.append(letter)

        return lines, words, letters

    def _process_first_pass_job(
        self,
        image: Any,
        ocr_data: OCRData,
        ocr_job: Any,
        ocr_routing_decision: Any,
    ) -> Dict[str, Any]:
        """Process a first-pass OCR job."""
        # Classify image type
        image_type = classify_image_layout(
            lines=ocr_data.lines,
            image_height=image.height,
            image_width=image.width,
        )

        logger.info(
            "Image %s classified as %s (dimensions: %sx%s)",
            ocr_job.image_id,
            image_type,
            image.width,
            image.height,
        )

        try:
            if image_type == ImageType.NATIVE:
                logger.info("Processing native receipt %s", ocr_job.image_id)

                # Convert image OCR to receipt OCR
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

            if image_type == ImageType.PHOTO:
                logger.info("Processing photo %s", ocr_job.image_id)
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

            if image_type == ImageType.SCAN:
                logger.info("Processing scan %s", ocr_job.image_id)
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

            logger.error("Unknown image type: %s", image_type)  # type: ignore[unreachable]
            self._update_routing_decision_with_error(ocr_routing_decision)
            return {
                "success": False,
                "error": f"Unknown image type: {image_type}",
            }

        except ValueError as exc:
            logger.error(
                "Geometry error in processing for image %s: %s",
                ocr_job.image_id,
                exc,
                exc_info=True,
            )
            self._update_routing_decision_with_error(ocr_routing_decision)
            return {
                "success": False,
                "error": f"Geometry error: {exc}",
            }

    def _update_routing_decision_with_error(
        self, ocr_routing_decision: Any
    ) -> None:
        """Updates the OCR routing decision with an error status."""
        ocr_routing_decision.status = OCRStatus.FAILED.value
        ocr_routing_decision.receipt_count = 0
        ocr_routing_decision.updated_at = datetime.now(timezone.utc)
        self.dynamo.update_ocr_routing_decision(ocr_routing_decision)
