"""Example integration of parallel pattern detection with receipt processing.

This module demonstrates how Epic #190 (Parallel Pattern Detection) integrates
with the existing receipt processing pipeline and other epics.
"""

import asyncio
from typing import Dict, List

from receipt_dynamo.entities import ReceiptWord

from receipt_label.pattern_detection import ParallelPatternOrchestrator


async def label_receipt_efficiently(
    receipt_id: str,
    words: List[ReceiptWord],
    merchant_patterns: Dict = None,
) -> Dict:
    """Efficiently label a receipt using parallel pattern detection.

    This function demonstrates the integration of:
    - Epic #188: Noise word filtering (words already filtered by is_noise flag)
    - Epic #189: Merchant pattern system (single Pinecone query)
    - Epic #190: Parallel pattern detection (this implementation)
    - Epic #191: Smart GPT decision logic (based on pattern results)

    Args:
        receipt_id: The receipt identifier
        words: List of receipt words (already filtered for noise from Epic #188)
        merchant_patterns: Merchant-specific patterns from Epic #189

    Returns:
        Dictionary containing labeled words and decision for GPT processing
    """
    # Step 1: Initialize the orchestrator
    orchestrator = ParallelPatternOrchestrator(timeout=0.1)  # 100ms timeout

    # Step 2: Run all pattern detectors in parallel
    print(f"Processing {len(words)} words for receipt {receipt_id}")
    pattern_results = await orchestrator.detect_all_patterns(
        words, merchant_patterns
    )

    # Step 3: Check execution time
    exec_time = pattern_results["_metadata"]["execution_time_ms"]
    print(f"Pattern detection completed in {exec_time:.2f}ms")

    # Step 4: Aggregate results for easier processing
    aggregated = orchestrator.aggregate_patterns(pattern_results)

    # Step 5: Apply pattern-based labels to words
    labeled_words = apply_pattern_labels(words, pattern_results)

    # Step 6: Check essential fields for Epic #191 smart decision
    essential_status = orchestrator.get_essential_fields_status(
        pattern_results
    )

    # Step 7: Make smart GPT decision (Epic #191 logic)
    gpt_decision = make_smart_gpt_decision(
        labeled_words, essential_status, aggregated
    )

    return {
        "receipt_id": receipt_id,
        "labeled_words": labeled_words,
        "pattern_summary": aggregated,
        "essential_fields": essential_status,
        "gpt_decision": gpt_decision,
        "execution_time_ms": exec_time,
    }


def apply_pattern_labels(
    words: List[ReceiptWord], pattern_results: Dict
) -> List[Dict]:
    """Apply detected patterns as labels to words.

    Args:
        words: Original receipt words
        pattern_results: Results from parallel pattern detection

    Returns:
        List of words with applied labels
    """
    # Create a map of word_id to labels
    word_labels = {}

    for detector_name, matches in pattern_results.items():
        if detector_name == "_metadata":
            continue

        for match in matches:
            word_id = match.word.word_id
            if word_id not in word_labels:
                word_labels[word_id] = []

            # Map pattern type to label
            label = pattern_type_to_label(match.pattern_type)
            if label:
                word_labels[word_id].append(
                    {
                        "label": label,
                        "confidence": match.confidence,
                        "source": f"pattern_{detector_name}",
                    }
                )

    # Apply labels to words
    labeled_words = []
    for word in words:
        word_dict = {
            "word_id": word.word_id,
            "text": word.text,
            "labels": word_labels.get(word.word_id, []),
        }
        labeled_words.append(word_dict)

    return labeled_words


def pattern_type_to_label(pattern_type) -> str:
    """Convert pattern type to receipt label."""
    # This maps Epic #190 pattern types to standard receipt labels
    mapping = {
        "GRAND_TOTAL": "GRAND_TOTAL",
        "SUBTOTAL": "SUBTOTAL",
        "TAX": "TAX",
        "DISCOUNT": "DISCOUNT",
        "DATE": "DATE",
        "TIME": "TIME",
        "PHONE_NUMBER": "PHONE_NUMBER",
        "EMAIL": "EMAIL",
        "WEBSITE": "WEBSITE",
        "QUANTITY": "QUANTITY",
        "UNIT_PRICE": "UNIT_PRICE",
        "LINE_TOTAL": "LINE_TOTAL",
    }
    return mapping.get(pattern_type.name)


def make_smart_gpt_decision(
    labeled_words: List[Dict],
    essential_status: Dict,
    aggregated_patterns: Dict,  # pylint: disable=unused-argument
) -> Dict:
    """Make smart decision about GPT processing (Epic #191 logic).

    Decision logic:
    1. SKIP (85%): All essential fields found and <5 meaningful unlabeled words
    2. BATCH (10%): Missing some fields but can wait for batch processing
    3. REQUIRED (5%): Missing critical fields that need immediate GPT

    Args:
        labeled_words: Words with applied pattern labels
        essential_status: Status of essential fields
        aggregated_patterns: Aggregated pattern detection results

    Returns:
        Decision dictionary with action and reasoning
    """
    # Check if all essential fields are found
    all_essential = all(
        [
            essential_status["has_date"],
            essential_status["has_total"],
            essential_status["has_merchant"],
            essential_status["has_product"],
        ]
    )

    # Count unlabeled meaningful words
    unlabeled_count = sum(
        1
        for word in labeled_words
        if not word["labels"] and len(word["text"]) > 2
    )

    # Decision logic
    if all_essential and unlabeled_count < 5:
        return {
            "action": "SKIP",
            "confidence": 0.95,
            "reasoning": "All essential fields found via patterns",
            "unlabeled_words": unlabeled_count,
        }
    if essential_status["has_date"] and essential_status["has_total"]:
        return {
            "action": "BATCH",
            "confidence": 0.8,
            "reasoning": "Basic fields found, queue for batch processing",
            "missing_fields": [
                field for field, found in essential_status.items() if not found
            ],
        }
    else:
        return {
            "action": "REQUIRED",
            "confidence": 0.9,
            "reasoning": "Missing critical fields, immediate GPT needed",
            "missing_fields": [
                field for field, found in essential_status.items() if not found
            ],
        }


async def benchmark_integration():
    """Benchmark the integrated pattern detection system."""
    # Create sample receipt
    sample_words = create_sample_receipt()

    # Simulate merchant patterns from Epic #189
    merchant_patterns = {
        "word_patterns": {
            "mcdonald": "MERCHANT_NAME",
            "big mac": "PRODUCT_NAME",
            "fries": "PRODUCT_NAME",
        },
        "confidence_threshold": 0.85,
    }

    # Run the integrated labeling
    print("Running integrated receipt labeling...")
    result = await label_receipt_efficiently(
        "test-receipt-001", sample_words, merchant_patterns
    )

    # Display results
    print(f"\nExecution time: {result['execution_time_ms']:.2f}ms")
    print(f"GPT Decision: {result['gpt_decision']['action']}")
    print(f"Reasoning: {result['gpt_decision']['reasoning']}")

    print("\nPattern Summary:")
    for pattern_type, info in result["pattern_summary"].items():
        print(f"  {pattern_type}: {info['count']} matches")

    print("\nEssential Fields Status:")
    for field, status in result["essential_fields"].items():
        print(f"  {field}: {'✓' if status else '✗'}")


def create_sample_receipt() -> List[ReceiptWord]:
    """Create a sample receipt for testing."""
    words_data = [
        {"text": "McDonald's", "y": 20},
        {"text": "Restaurant", "y": 20},
        {"text": "01/15/2024", "y": 40},
        {"text": "14:30", "y": 40},
        {"text": "Big", "y": 80},
        {"text": "Mac", "y": 80},
        {"text": "$5.99", "y": 80},
        {"text": "Fries", "y": 100},
        {"text": "$2.49", "y": 100},
        {"text": "TAX", "y": 140},
        {"text": "$0.68", "y": 140},
        {"text": "TOTAL", "y": 160},
        {"text": "$9.16", "y": 160},
    ]

    words = []
    for i, data in enumerate(words_data):
        word = ReceiptWord(
            receipt_id=1,
            image_id="550e8400-e29b-41d4-a716-446655440000",
            line_id=data["y"] // 20,
            word_id=i,
            text=data["text"],
            bounding_box={
                "x": i * 60,
                "y": data["y"],
                "width": 50,
                "height": 20,
            },
            top_left={"x": i * 60, "y": data["y"]},
            top_right={"x": i * 60 + 50, "y": data["y"]},
            bottom_left={"x": i * 60, "y": data["y"] + 20},
            bottom_right={"x": i * 60 + 50, "y": data["y"] + 20},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.95,
        )
        words.append(word)

    return words


if __name__ == "__main__":
    # Run the benchmark
    asyncio.run(benchmark_integration())
