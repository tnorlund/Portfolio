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
from typing import Dict, Any

from PIL import Image as PIL_Image

from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import ImageType, OCRJobType, OCRStatus
from receipt_dynamo.entities import Letter, Line, Word
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
            # Get job and routing decision
            ocr_job = get_ocr_job(self.table_name, image_id, job_id)
            ocr_routing_decision = get_ocr_routing_decision(
                self.table_name, image_id, job_id
            )
            
            logger.info(f"Got OCR job type: {ocr_job.job_type}")
            
            # Handle refinement jobs differently
            if ocr_job.job_type == OCRJobType.REFINEMENT.value:
                return self._process_refinement_job(ocr_job, ocr_routing_decision)
            
            # Download and parse OCR JSON
            ocr_json_path = download_file_from_s3(
                ocr_routing_decision.s3_bucket,
                ocr_routing_decision.s3_key,
                Path("/tmp"),
            )
            
            with open(ocr_json_path, "r", encoding="utf-8") as f:
                ocr_json = json.load(f)
            
            ocr_lines, ocr_words, ocr_letters = process_ocr_dict_as_image(
                ocr_json, image_id
            )
            
            ocr_data = OCRData(
                lines=ocr_lines,
                words=ocr_words,
                letters=ocr_letters
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
        
        receipt_lines, receipt_words, receipt_letters = image_ocr_to_receipt_ocr(
            lines=ocr_lines,
            words=ocr_words,
            letters=ocr_letters,
            receipt_id=ocr_job.receipt_id,
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
        }
    
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
                exc_info=True
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

