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
    is_noise_text,
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
        chromadb_bucket: str = "",
    ):
        self.table_name = table_name
        self.raw_bucket = raw_bucket
        self.site_bucket = site_bucket
        self.ocr_job_queue_url = ocr_job_queue_url
        self.ocr_results_queue_url = ocr_results_queue_url
        self.chromadb_bucket = chromadb_bucket
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
            if ocr_job.job_type == OCRJobType.REGIONAL_REOCR.value:
                return self._process_regional_reocr_job(
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

    @staticmethod
    def _clamp01(value: float) -> float:
        return max(0.0, min(1.0, value))

    def _map_point_to_region(
        self, point: dict[str, float], region: dict[str, float]
    ) -> dict[str, float]:
        return {
            "x": self._clamp01(
                region["x"] + float(point.get("x", 0.0)) * region["width"]
            ),
            "y": self._clamp01(
                region["y"] + float(point.get("y", 0.0)) * region["height"]
            ),
        }

    def _map_bbox_to_region(
        self, bbox: dict[str, float], region: dict[str, float]
    ) -> dict[str, float]:
        return {
            "x": self._clamp01(
                region["x"] + float(bbox.get("x", 0.0)) * region["width"]
            ),
            "y": self._clamp01(
                region["y"] + float(bbox.get("y", 0.0)) * region["height"]
            ),
            "width": max(
                0.0, min(1.0, float(bbox.get("width", 0.0)) * region["width"])
            ),
            "height": max(
                0.0,
                min(1.0, float(bbox.get("height", 0.0)) * region["height"]),
            ),
        }

    @staticmethod
    def _image_to_receipt(
        ix: float, iy: float, corners: dict
    ) -> tuple[float, float]:
        """Inverse bilinear: full-image Vision (ix, iy) -> receipt-relative.

        Uses Newton's method (converges in 3-5 iterations for typical
        near-rectangular receipts).
        """
        bl = corners["bottom_left"]
        br = corners["bottom_right"]
        tl = corners["top_left"]
        tr = corners["top_right"]
        u, v = 0.5, 0.5
        for _ in range(10):
            fx = (
                (1 - u) * (1 - v) * bl["x"]
                + u * (1 - v) * br["x"]
                + (1 - u) * v * tl["x"]
                + u * v * tr["x"]
            )
            fy = (
                (1 - u) * (1 - v) * bl["y"]
                + u * (1 - v) * br["y"]
                + (1 - u) * v * tl["y"]
                + u * v * tr["y"]
            )
            ex, ey = ix - fx, iy - fy
            if abs(ex) < 1e-8 and abs(ey) < 1e-8:
                break
            dfx_du = -(1 - v) * bl["x"] + (1 - v) * br["x"] - v * tl["x"] + v * tr["x"]
            dfx_dv = -(1 - u) * bl["x"] - u * br["x"] + (1 - u) * tl["x"] + u * tr["x"]
            dfy_du = -(1 - v) * bl["y"] + (1 - v) * br["y"] - v * tl["y"] + v * tr["y"]
            dfy_dv = -(1 - u) * bl["y"] - u * br["y"] + (1 - u) * tl["y"] + u * tr["y"]
            det = dfx_du * dfy_dv - dfx_dv * dfy_du
            if abs(det) < 1e-12:
                break
            u += (dfy_dv * ex - dfx_dv * ey) / det
            v += (-dfy_du * ex + dfx_du * ey) / det
        return max(0.0, min(1.0, u)), max(0.0, min(1.0, v))

    def _image_point_to_receipt(
        self, point: dict[str, float], corners: dict
    ) -> dict[str, float]:
        rx, ry = self._image_to_receipt(
            float(point.get("x", 0.0)),
            float(point.get("y", 0.0)),
            corners,
        )
        return {"x": rx, "y": ry}

    def _image_bbox_to_receipt(
        self, bbox: dict[str, float], corners: dict
    ) -> dict[str, float]:
        x = float(bbox.get("x", 0.0))
        y = float(bbox.get("y", 0.0))
        w = float(bbox.get("width", 0.0))
        h = float(bbox.get("height", 0.0))
        rx, ry = self._image_to_receipt(x, y, corners)
        rx2, ry2 = self._image_to_receipt(x + w, y + h, corners)
        return {
            "x": min(rx, rx2),
            "y": min(ry, ry2),
            "width": abs(rx2 - rx),
            "height": abs(ry2 - ry),
        }

    def _apply_receipt_inverse_mapping(
        self,
        receipt_lines: list[ReceiptLine],
        receipt_words: list[ReceiptWord],
        receipt_letters: list[ReceiptLetter],
        corners: dict,
    ) -> None:
        """Map full-image Vision coords back to receipt-relative space."""
        for entity in receipt_lines + receipt_words + receipt_letters:
            entity.bounding_box = self._image_bbox_to_receipt(
                entity.bounding_box, corners
            )
            entity.top_left = self._image_point_to_receipt(entity.top_left, corners)
            entity.top_right = self._image_point_to_receipt(entity.top_right, corners)
            entity.bottom_left = self._image_point_to_receipt(
                entity.bottom_left, corners
            )
            entity.bottom_right = self._image_point_to_receipt(
                entity.bottom_right, corners
            )

    def _apply_region_mapping(
        self,
        receipt_lines: list[ReceiptLine],
        receipt_words: list[ReceiptWord],
        receipt_letters: list[ReceiptLetter],
        region: dict[str, float],
    ) -> None:
        """Map crop-space OCR geometry back to full-image Vision space."""

        for entity in receipt_lines + receipt_words + receipt_letters:
            entity.bounding_box = self._map_bbox_to_region(
                entity.bounding_box, region
            )
            entity.top_left = self._map_point_to_region(entity.top_left, region)
            entity.top_right = self._map_point_to_region(entity.top_right, region)
            entity.bottom_left = self._map_point_to_region(
                entity.bottom_left, region
            )
            entity.bottom_right = self._map_point_to_region(
                entity.bottom_right, region
            )

    @staticmethod
    def _bbox_center_x(bbox: dict[str, float]) -> float:
        return float(bbox.get("x", 0.0)) + (float(bbox.get("width", 0.0)) / 2.0)

    @staticmethod
    def _bbox_center_y(bbox: dict[str, float]) -> float:
        return float(bbox.get("y", 0.0)) + (float(bbox.get("height", 0.0)) / 2.0)

    @staticmethod
    def _y_overlap_ratio(
        a_bbox: dict[str, float], b_bbox: dict[str, float]
    ) -> float:
        a_y1 = float(a_bbox.get("y", 0.0))
        a_y2 = a_y1 + float(a_bbox.get("height", 0.0))
        b_y1 = float(b_bbox.get("y", 0.0))
        b_y2 = b_y1 + float(b_bbox.get("height", 0.0))
        overlap = max(0.0, min(a_y2, b_y2) - max(a_y1, b_y1))
        denom = max(
            min(
                float(a_bbox.get("height", 0.0)),
                float(b_bbox.get("height", 0.0)),
            ),
            1e-6,
        )
        return overlap / denom

    def _match_regional_words(
        self,
        new_words: list[ReceiptWord],
        candidate_words: list[ReceiptWord],
    ) -> list[tuple[ReceiptWord, ReceiptWord]]:
        """Greedy y-overlap matching between re-OCR words and existing words."""
        remaining = list(candidate_words)
        matches: list[tuple[ReceiptWord, ReceiptWord]] = []

        for new_word in sorted(
            new_words,
            key=lambda w: (
                float(w.bounding_box.get("y", 0.0)),
                float(w.bounding_box.get("x", 0.0)),
            ),
        ):
            best_idx: int | None = None
            best_score = -1.0
            for idx, old_word in enumerate(remaining):
                overlap = self._y_overlap_ratio(
                    new_word.bounding_box, old_word.bounding_box
                )
                if overlap < 0.15:
                    continue
                x_distance = abs(
                    self._bbox_center_x(new_word.bounding_box)
                    - self._bbox_center_x(old_word.bounding_box)
                )
                score = overlap - (0.5 * x_distance)
                if score > best_score:
                    best_score = score
                    best_idx = idx

            if best_idx is None:
                continue

            old_word = remaining.pop(best_idx)
            matches.append((new_word, old_word))

        return matches

    def _process_regional_reocr_job(
        self, ocr_job: Any, ocr_routing_decision: Any
    ) -> Dict[str, Any]:
        """Overlay regional re-OCR words onto existing receipt words."""
        logger.info("Regional re-OCR overlay for receipt %s", ocr_job.image_id)

        if ocr_job.receipt_id is None:
            self._update_routing_decision_with_error(ocr_routing_decision)
            return {"success": False, "error": "Receipt ID is None"}
        if not ocr_job.reocr_region:
            self._update_routing_decision_with_error(ocr_routing_decision)
            return {"success": False, "error": "reocr_region is missing"}

        # Guard 1: Skip if this routing decision has already been completed
        if ocr_routing_decision.status == OCRStatus.COMPLETED.value:
            logger.info(
                "Regional re-OCR already completed for %s#%s, skipping",
                ocr_job.image_id,
                ocr_job.receipt_id,
            )
            return {
                "success": True,
                "skipped": True,
                "reason": "already_completed",
                "image_id": ocr_job.image_id,
                "receipt_id": ocr_job.receipt_id,
            }

        region = {
            "x": float(ocr_job.reocr_region.get("x", 0.70)),
            "y": float(ocr_job.reocr_region.get("y", 0.0)),
            "width": float(ocr_job.reocr_region.get("width", 0.30)),
            "height": float(ocr_job.reocr_region.get("height", 1.0)),
        }

        ocr_json_path = download_file_from_s3(
            ocr_routing_decision.s3_bucket,
            ocr_routing_decision.s3_key,
            Path("/tmp"),
        )
        with open(ocr_json_path, "r", encoding="utf-8") as f:
            ocr_json = json.load(f)

        lines_data = ocr_json.get("lines", [])
        if not lines_data and ocr_json.get("receipts"):
            first_receipt = ocr_json["receipts"][0]
            lines_data = first_receipt.get("lines", [])

        ocr_lines, ocr_words, ocr_letters = process_ocr_dict_as_image(
            {"lines": lines_data}, ocr_job.image_id
        )
        receipt_lines, receipt_words, receipt_letters = image_ocr_to_receipt_ocr(
            lines=ocr_lines,
            words=ocr_words,
            letters=ocr_letters,
            receipt_id=ocr_job.receipt_id,
        )
        # Step 1: crop-relative → full-image Vision coords
        self._apply_region_mapping(
            receipt_lines, receipt_words, receipt_letters, region
        )

        # Step 2: full-image Vision → receipt-relative coords.
        # Existing receipt words are stored in receipt-relative space
        # (normalised to the warped receipt paper), so the new words
        # must be in the same space for matching and storage.
        receipt = self.dynamo.get_receipt(
            ocr_job.image_id, ocr_job.receipt_id
        )
        corners = {
            "top_left": receipt.top_left,
            "top_right": receipt.top_right,
            "bottom_left": receipt.bottom_left,
            "bottom_right": receipt.bottom_right,
        }
        self._apply_receipt_inverse_mapping(
            receipt_lines, receipt_words, receipt_letters, corners
        )

        # Compute receipt-relative region bounds for candidate filtering
        r_bl = self._image_to_receipt(
            region["x"], region["y"], corners
        )
        r_tr = self._image_to_receipt(
            region["x"] + region["width"],
            region["y"] + region["height"],
            corners,
        )
        region_x1 = min(r_bl[0], r_tr[0])
        region_y1 = min(r_bl[1], r_tr[1])
        region_x2 = max(r_bl[0], r_tr[0])
        region_y2 = max(r_bl[1], r_tr[1])

        existing_words = self.dynamo.list_receipt_words_from_receipt(
            ocr_job.image_id, ocr_job.receipt_id
        )
        if not existing_words:
            self._update_routing_decision_with_error(ocr_routing_decision)
            return {
                "success": False,
                "error": "No existing receipt words found for overlay",
            }

        candidate_words = [
            word
            for word in existing_words
            if region_x1
            <= self._bbox_center_x(word.bounding_box)
            <= region_x2
            and region_y1
            <= self._bbox_center_y(word.bounding_box)
            <= region_y2
        ]

        matches = self._match_regional_words(receipt_words, candidate_words)
        if not matches:
            logger.warning(
                "Regional re-OCR produced no overlay matches for %s#%s",
                ocr_job.image_id,
                ocr_job.receipt_id,
            )

        # Identify new OCR words that didn't match any existing word.
        matched_new_keys = {(nw.line_id, nw.word_id) for nw, _ in matches}
        unmatched_new_words = [
            w
            for w in receipt_words
            if (w.line_id, w.word_id) not in matched_new_keys
        ]

        new_letters_by_new_key: dict[tuple[int, int], list[ReceiptLetter]] = {}
        for letter in receipt_letters:
            new_letters_by_new_key.setdefault(
                (letter.line_id, letter.word_id), []
            ).append(letter)

        words_to_update: list[ReceiptWord] = []
        letters_to_delete: list[ReceiptLetter] = []
        letters_to_add: list[ReceiptLetter] = []
        words_rejected = 0

        for new_word, existing_word in matches:
            # Guard 3: Confidence-based quality check.
            # If the text is identical and the new confidence is lower,
            # reject this overlay to avoid degrading existing data.
            if (
                new_word.text == existing_word.text
                and new_word.confidence < existing_word.confidence
            ):
                logger.info(
                    "Rejecting overlay for word %s#%s line=%d word=%d: "
                    "text unchanged and confidence dropped %.4f -> %.4f",
                    ocr_job.image_id,
                    ocr_job.receipt_id,
                    existing_word.line_id,
                    existing_word.word_id,
                    existing_word.confidence,
                    new_word.confidence,
                )
                words_rejected += 1
                continue

            existing_word.text = new_word.text
            existing_word.bounding_box = new_word.bounding_box
            existing_word.top_left = new_word.top_left
            existing_word.top_right = new_word.top_right
            existing_word.bottom_left = new_word.bottom_left
            existing_word.bottom_right = new_word.bottom_right
            existing_word.angle_degrees = new_word.angle_degrees
            existing_word.angle_radians = new_word.angle_radians
            existing_word.confidence = new_word.confidence
            existing_word.extracted_data = new_word.extracted_data
            existing_word.is_noise = is_noise_text(new_word.text)
            words_to_update.append(existing_word)

            old_letters = self.dynamo.list_receipt_letters_from_word(
                image_id=ocr_job.image_id,
                receipt_id=ocr_job.receipt_id,
                line_id=existing_word.line_id,
                word_id=existing_word.word_id,
            )
            letters_to_delete.extend(old_letters)

            replacement_letters = new_letters_by_new_key.get(
                (new_word.line_id, new_word.word_id), []
            )
            for letter_idx, letter in enumerate(replacement_letters, start=1):
                letters_to_add.append(
                    ReceiptLetter(
                        image_id=ocr_job.image_id,
                        receipt_id=ocr_job.receipt_id,
                        line_id=existing_word.line_id,
                        word_id=existing_word.word_id,
                        letter_id=letter_idx,
                        text=letter.text,
                        bounding_box=letter.bounding_box,
                        top_left=letter.top_left,
                        top_right=letter.top_right,
                        bottom_left=letter.bottom_left,
                        bottom_right=letter.bottom_right,
                        angle_degrees=letter.angle_degrees,
                        angle_radians=letter.angle_radians,
                        confidence=letter.confidence,
                    )
                )

        # --- Add unmatched new words to the receipt ---
        words_to_add: list[ReceiptWord] = []
        letters_to_add_for_new: list[ReceiptLetter] = []

        if unmatched_new_words:
            # Compute Y-range for each existing receipt line.
            line_y_ranges: dict[int, tuple[float, float]] = {}
            for w in existing_words:
                wy = float(w.bounding_box.get("y", 0))
                wh = float(w.bounding_box.get("height", 0))
                cur = line_y_ranges.get(w.line_id)
                if cur is None:
                    line_y_ranges[w.line_id] = (wy, wy + wh)
                else:
                    line_y_ranges[w.line_id] = (
                        min(cur[0], wy),
                        max(cur[1], wy + wh),
                    )

            # Track max word_id per line so new words get unique IDs.
            max_word_id: dict[int, int] = {}
            for w in existing_words:
                if w.line_id not in max_word_id or w.word_id > max_word_id[w.line_id]:
                    max_word_id[w.line_id] = w.word_id

            for new_word in unmatched_new_words:
                nw_y = float(new_word.bounding_box.get("y", 0))
                nw_h = float(new_word.bounding_box.get("height", 0))

                # Find existing line with best Y-overlap.
                best_line_id: int | None = None
                best_overlap = 0.0
                for lid, (ly_min, ly_max) in line_y_ranges.items():
                    overlap = max(0.0, min(nw_y + nw_h, ly_max) - max(nw_y, ly_min))
                    if overlap > best_overlap:
                        best_overlap = overlap
                        best_line_id = lid

                if best_line_id is None:
                    logger.info(
                        "Skipping unmatched re-OCR word '%s' — no line overlap",
                        new_word.text,
                    )
                    continue

                next_wid = max_word_id.get(best_line_id, 0) + 1
                max_word_id[best_line_id] = next_wid

                words_to_add.append(
                    ReceiptWord(
                        image_id=ocr_job.image_id,
                        receipt_id=ocr_job.receipt_id,
                        line_id=best_line_id,
                        word_id=next_wid,
                        text=new_word.text,
                        bounding_box=new_word.bounding_box,
                        top_left=new_word.top_left,
                        top_right=new_word.top_right,
                        bottom_left=new_word.bottom_left,
                        bottom_right=new_word.bottom_right,
                        angle_degrees=new_word.angle_degrees,
                        angle_radians=new_word.angle_radians,
                        confidence=new_word.confidence,
                        extracted_data=new_word.extracted_data,
                        is_noise=is_noise_text(new_word.text),
                    )
                )

                for letter_idx, letter in enumerate(
                    new_letters_by_new_key.get(
                        (new_word.line_id, new_word.word_id), []
                    ),
                    start=1,
                ):
                    letters_to_add_for_new.append(
                        ReceiptLetter(
                            image_id=ocr_job.image_id,
                            receipt_id=ocr_job.receipt_id,
                            line_id=best_line_id,
                            word_id=next_wid,
                            letter_id=letter_idx,
                            text=letter.text,
                            bounding_box=letter.bounding_box,
                            top_left=letter.top_left,
                            top_right=letter.top_right,
                            bottom_left=letter.bottom_left,
                            bottom_right=letter.bottom_right,
                            angle_degrees=letter.angle_degrees,
                            angle_radians=letter.angle_radians,
                            confidence=letter.confidence,
                        )
                    )

                logger.info(
                    "Adding unmatched re-OCR word '%s' to line %d as word %d",
                    new_word.text,
                    best_line_id,
                    next_wid,
                )

        if words_to_update:
            self.dynamo.update_receipt_words(words_to_update)
        if words_to_add:
            self.dynamo.add_receipt_words(words_to_add)
        # Delete old letters BEFORE adding replacements. The old letters
        # and new letters can share the same (line_id, word_id, letter_id)
        # keys, so adding first then deleting would clobber the new data.
        if letters_to_delete:
            self.dynamo.remove_receipt_letters(letters_to_delete)
        if letters_to_add:
            self.dynamo.put_receipt_letters(letters_to_add)
        if letters_to_add_for_new:
            self.dynamo.put_receipt_letters(letters_to_add_for_new)

        # Rebuild ReceiptLine.text for lines with overlaid or added words so
        # downstream consumers (Chroma embeddings, merchant resolution,
        # agents, cache generators) see the corrected text.
        lines_to_update: list[ReceiptLine] = []
        affected_line_ids = {w.line_id for w in words_to_update} | {
            w.line_id for w in words_to_add
        }
        if affected_line_ids:
            existing_lines = self.dynamo.list_receipt_lines_from_receipt(
                ocr_job.image_id, ocr_job.receipt_id
            )
            # Build a word lookup with updated words taking precedence.
            updated_lookup = {
                (w.line_id, w.word_id): w for w in words_to_update
            }
            added_by_line: dict[int, list[ReceiptWord]] = {}
            for w in words_to_add:
                added_by_line.setdefault(w.line_id, []).append(w)
            for line in existing_lines:
                if line.line_id not in affected_line_ids:
                    continue
                line_words = [
                    updated_lookup.get((w.line_id, w.word_id), w)
                    for w in existing_words
                    if w.line_id == line.line_id
                ]
                line_words.extend(added_by_line.get(line.line_id, []))
                line_words.sort(key=lambda w: w.word_id)
                line.text = " ".join(w.text for w in line_words)
                lines_to_update.append(line)
            if lines_to_update:
                self.dynamo.update_receipt_lines(lines_to_update)

        ocr_routing_decision.status = OCRStatus.COMPLETED.value
        ocr_routing_decision.receipt_count = 1
        ocr_routing_decision.updated_at = datetime.now(timezone.utc)
        self.dynamo.update_ocr_routing_decision(ocr_routing_decision)

        # Re-embed the full receipt so ChromaDB reflects corrected text.
        # Same pattern as the merge receipt lambda: generate embeddings,
        # upload deltas to S3, create CompactionRun (DynamoDB stream
        # triggers the enhanced compactor asynchronously).
        compaction_run_id = None
        if self.chromadb_bucket:
            try:
                from receipt_chroma.embedding.orchestration import (
                    EmbeddingConfig,
                    create_embeddings_and_compaction_run,
                )

                all_lines = self.dynamo.list_receipt_lines_from_receipt(
                    ocr_job.image_id, ocr_job.receipt_id
                )
                all_words = self.dynamo.list_receipt_words_from_receipt(
                    ocr_job.image_id, ocr_job.receipt_id
                )
                non_noise_words = [
                    w for w in all_words if not getattr(w, "is_noise", False)
                ] or all_words

                embedding_config = EmbeddingConfig(
                    image_id=ocr_job.image_id,
                    receipt_id=ocr_job.receipt_id,
                    chromadb_bucket=self.chromadb_bucket,
                    dynamo_client=self.dynamo,
                    receipt_word_labels=labels if labels else None,
                )
                embedding_result = create_embeddings_and_compaction_run(
                    receipt_lines=all_lines,
                    receipt_words=non_noise_words,
                    config=embedding_config,
                )
                compaction_run_id = embedding_result.compaction_run.run_id
                embedding_result.close()
                logger.info(
                    "Re-OCR embeddings created, compaction_run=%s",
                    compaction_run_id,
                )
            except Exception:
                logger.exception(
                    "Failed to create embeddings after re-OCR for %s#%s",
                    ocr_job.image_id,
                    ocr_job.receipt_id,
                )

        return {
            "success": True,
            "image_id": ocr_job.image_id,
            "receipt_id": ocr_job.receipt_id,
            "image_type": "REGIONAL_REOCR",
            "line_count": len(receipt_lines),
            "word_count": len(words_to_update) + len(words_to_add),
            "words_replaced": len(words_to_update),
            "words_added": len(words_to_add),
            "words_rejected": words_rejected,
            "lines_rebuilt": len(lines_to_update),
            "compaction_run_id": compaction_run_id,
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
        # Track lines/words per receipt for individual merchant resolution
        per_receipt_data: Dict[int, Dict[str, Any]] = {}
        successful_receipts = 0

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
            except (
                Exception
            ) as cdn_exc:  # pylint: disable=broad-exception-caught
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

            successful_receipts += 1

            # Store per-receipt data for individual merchant resolution
            per_receipt_data[receipt_id] = {
                "lines": receipt_lines,
                "words": receipt_words,
            }

        # Update routing decision
        ocr_routing_decision.status = OCRStatus.COMPLETED.value
        ocr_routing_decision.receipt_count = successful_receipts
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
                receipt_count=successful_receipts,
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
                image_entity.cdn_thumbnail_s3_key = cdn_keys.get(
                    "jpeg_thumbnail"
                )
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
                image_entity.cdn_medium_webp_s3_key = cdn_keys.get(
                    "webp_medium"
                )
                image_entity.cdn_medium_avif_s3_key = cdn_keys.get(
                    "avif_medium"
                )

                logger.info(
                    "Processed original image for CDN: %s -> %s",
                    image_id,
                    cdn_base_key,
                )
            except (
                Exception
            ) as cdn_exc:  # pylint: disable=broad-exception-caught
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

        # Return only successfully persisted receipt IDs
        all_receipt_ids = sorted(per_receipt_data.keys())

        return {
            "success": True,
            "image_id": image_id,
            "image_type": image_type_str,
            "receipt_id": all_receipt_ids[0] if all_receipt_ids else None,
            "receipt_ids": all_receipt_ids,
            "per_receipt_data": per_receipt_data,
            "receipt_count": successful_receipts,
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

        def _has_valid_geometry(data: dict[str, Any]) -> bool:
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

        def _has_valid_geometry(data: dict[str, Any]) -> bool:
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
                if (
                    not word_text
                    or word_confidence <= 0
                    or not _has_valid_geometry(word_data)
                ):
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

            logger.error("Unknown image type: %s", image_type)
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
