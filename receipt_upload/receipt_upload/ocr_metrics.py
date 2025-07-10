"""OCR processing with integrated noise detection metrics."""

import time
from typing import List, Tuple

from receipt_dynamo.entities import Receipt, ReceiptWord

from receipt_label.utils.noise_detection import is_noise_word
from receipt_label.utils.noise_metrics import (
    NoiseMetricsCollector,
    log_noise_detection_event,
)


def process_ocr_with_metrics(
    ocr_response: dict,
    image_id: str,
    merchant_type: str = None,
) -> Tuple[Receipt, List[ReceiptWord], dict]:
    """
    Process OCR response with noise detection and metrics collection.

    Args:
        ocr_response: OCR response dictionary
        image_id: Image identifier
        merchant_type: Optional merchant type for metrics categorization

    Returns:
        Tuple of (Receipt, List[ReceiptWord], metrics_dict)
    """
    from receipt_upload.ocr import process_ocr_dict_as_receipt

    # Start timing
    start_time = time.perf_counter()

    # Process OCR as normal
    receipt_id = ocr_response.get("receipt_id", 1)
    receipt, words = process_ocr_dict_as_receipt(
        ocr_response, image_id, receipt_id
    )

    # Calculate processing time
    processing_time_ms = (time.perf_counter() - start_time) * 1000

    # Collect metrics
    collector = NoiseMetricsCollector()
    metrics = collector.collect_receipt_metrics(
        words=words,
        processing_time_ms=processing_time_ms,
        receipt_id=receipt.receipt_id,
        image_id=image_id,
        merchant_type=merchant_type,
    )

    # Log event
    log_noise_detection_event(
        event_type="ocr_processing_complete",
        details={
            "total_words": metrics.total_words,
            "noise_words": metrics.noise_words,
            "noise_percentage": metrics.noise_percentage,
            "processing_time_ms": metrics.processing_time_ms,
        },
        receipt_id=receipt.receipt_id,
        image_id=image_id,
    )

    return receipt, words, metrics.to_dict()


def analyze_noise_patterns_by_merchant(
    receipts_by_merchant: dict,
) -> dict:
    """
    Analyze noise patterns across different merchant types.

    Args:
        receipts_by_merchant: Dict mapping merchant_type to list of (receipt, words) tuples

    Returns:
        Analysis results by merchant type
    """
    results = {}

    for merchant_type, receipt_data in receipts_by_merchant.items():
        collector = NoiseMetricsCollector()

        for receipt, words in receipt_data:
            # Simulate processing time
            processing_time = len(words) * 0.01  # 0.01ms per word estimate

            collector.collect_receipt_metrics(
                words=words,
                processing_time_ms=processing_time,
                receipt_id=receipt.receipt_id,
                image_id=receipt.image_id,
                merchant_type=merchant_type,
            )

        # Get summary for this merchant type
        summary = collector.get_batch_summary()
        results[merchant_type] = {
            "summary": summary,
            "average_noise_percentage": summary["average_noise_percentage"],
            "total_receipts": summary["total_receipts"],
            "cost_saved": summary["total_cost_saved"],
        }

    return results
