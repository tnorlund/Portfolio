"""
Record building utilities for receipt combination.

This module contains functions for combining words and letters from multiple
receipts and creating DynamoDB entities for the combined receipt.
"""

import copy
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from geometry_utils import transform_point_to_warped_space

from receipt_dynamo import DynamoClient
from receipt_dynamo.entities import (
    Receipt,
    ReceiptLetter,
    ReceiptLine,
    ReceiptWord,
)


def combine_receipt_words_to_image_coords(
    client: DynamoClient,
    image_id: str,
    receipt_ids: List[int],
    image_width: int,
    image_height: int,
) -> List[Dict[str, Any]]:
    """
    Combine words from multiple receipts and transform to image coordinates.

    Args:
        client: DynamoDB client
        image_id: Image ID containing the receipts
        receipt_ids: List of receipt IDs to combine
        image_width: Width of the original image
        image_height: Height of the original image

    Returns:
        List of word dictionaries with transformed coordinates
    """
    all_words = []
    for receipt_id in receipt_ids:
        try:
            receipt = client.get_receipt(image_id, receipt_id)
            receipt_words = client.list_receipt_words_from_receipt(
                image_id, receipt_id
            )
            for word in receipt_words:
                try:
                    # Use the new Receipt entity method
                    transform_coeffs, receipt_width, receipt_height = (
                        receipt.get_transform_to_image(
                            image_width, image_height
                        )
                    )
                    word_copy = copy.deepcopy(word)
                    from receipt_upload.geometry.transformations import (
                        invert_warp,
                    )

                    forward_coeffs = invert_warp(*transform_coeffs)
                    # ReceiptWord coordinates are in OCR space (y=0 at bottom),
                    # normalized 0-1. The transform destination is in PIL space
                    # (y=0 at top). So we need flip_y=True to convert from OCR
                    # space to PIL space during transform
                    word_copy.warp_transform(
                        *forward_coeffs,
                        src_width=image_width,
                        src_height=image_height,
                        dst_width=receipt_width,
                        dst_height=receipt_height,
                        flip_y=True,  # Receipt coords are in OCR space
                    )
                    centroid = word_copy.calculate_centroid()
                    # After warp_transform, word_copy coordinates are always
                    # normalized (0-1) in image space. We always need to
                    # multiply by image_width/height to get pixel coordinates.
                    # The centroid check tells us if the word is within bounds
                    # (centroid <= 1.0) or outside (centroid > 1.0). But
                    # regardless, we need to convert normalized coords to pixel
                    # coords
                    centroid_x = centroid[0] * image_width
                    centroid_y = centroid[1] * image_height
                    bounding_box = {
                        "x": word_copy.bounding_box["x"] * image_width,
                        "y": word_copy.bounding_box["y"] * image_height,
                        "width": word_copy.bounding_box["width"] * image_width,
                        "height": word_copy.bounding_box["height"]
                        * image_height,
                    }
                    top_left = {
                        "x": word_copy.top_left["x"] * image_width,
                        "y": word_copy.top_left["y"] * image_height,
                    }
                    top_right = {
                        "x": word_copy.top_right["x"] * image_width,
                        "y": word_copy.top_right["y"] * image_height,
                    }
                    bottom_left = {
                        "x": word_copy.bottom_left["x"] * image_width,
                        "y": word_copy.bottom_left["y"] * image_height,
                    }
                    bottom_right = {
                        "x": word_copy.bottom_right["x"] * image_width,
                        "y": word_copy.bottom_right["y"] * image_height,
                    }

                    all_words.append(
                        {
                            "receipt_id": receipt_id,
                            "word_id": word.word_id,
                            "line_id": word.line_id,
                            "text": word.text,
                            "centroid_x": centroid_x,
                            "centroid_y": centroid_y,
                            "bounding_box": bounding_box,
                            "top_left": top_left,
                            "top_right": top_right,
                            "bottom_left": bottom_left,
                            "bottom_right": bottom_right,
                            "angle_degrees": word_copy.angle_degrees,
                            "confidence": word_copy.confidence,
                            "is_noise": getattr(word, "is_noise", False),
                        }
                    )
                except Exception:  # pylint: disable=broad-except
                    continue
        except Exception:  # pylint: disable=broad-except
            continue

    all_words.sort(key=lambda w: (-w["centroid_y"], w["centroid_x"]))

    # Deduplicate words with identical coordinates (OCR duplicates)
    # This handles cases where the same word was detected on multiple lines
    # with identical bounding box coordinates
    deduplicated_words = []
    seen_coords = set()
    for word in all_words:
        # Create a coordinate signature for deduplication
        # Use a small tolerance for floating point comparison (0.1 pixels)
        coord_key = (
            round(word["top_left"]["x"], 1),
            round(word["top_left"]["y"], 1),
            round(word["top_right"]["x"], 1),
            round(word["top_right"]["y"], 1),
            round(word["bottom_left"]["x"], 1),
            round(word["bottom_left"]["y"], 1),
            round(word["bottom_right"]["x"], 1),
            round(word["bottom_right"]["y"], 1),
            word["text"],  # Also include text to avoid deduplicating
            # different words at same location
        )

        if coord_key not in seen_coords:
            seen_coords.add(coord_key)
            deduplicated_words.append(word)
        # Skip duplicate - log if needed for debugging
        # Note: We keep the first occurrence (already sorted by reading order)

    return deduplicated_words


def combine_receipt_letters_to_image_coords(
    client: DynamoClient,
    image_id: str,
    receipt_ids: List[int],
    image_width: int,
    image_height: int,
    word_id_map: Dict[Tuple[int, int, int], int],
    line_id_map: Dict[Tuple[int, int], int],
) -> List[Dict[str, Any]]:
    """
    Combine letters from multiple receipts and transform to image coordinates.

    Args:
        client: DynamoDB client
        image_id: Image ID containing the receipts
        receipt_ids: List of receipt IDs to combine
        image_width: Width of the original image
        image_height: Height of the original image
        word_id_map: Mapping from (word_id, line_id, receipt_id) to new word_id
        line_id_map: Mapping from (line_id, receipt_id) to new line_id

    Returns:
        List of letter dictionaries with transformed coordinates
    """
    all_letters = []
    for receipt_id in receipt_ids:
        try:
            receipt = client.get_receipt(image_id, receipt_id)
            receipt_words = client.list_receipt_words_from_receipt(
                image_id, receipt_id
            )
            for word in receipt_words:
                try:
                    receipt_letters = client.list_receipt_letters_from_word(
                        receipt_id, image_id, word.line_id, word.word_id
                    )
                    for letter in receipt_letters:
                        try:
                            # Use the new Receipt entity method
                            transform_coeffs, receipt_width, receipt_height = (
                                receipt.get_transform_to_image(
                                    image_width, image_height
                                )
                            )
                            letter_copy = copy.deepcopy(letter)
                            from receipt_upload.geometry.transformations import (
                                invert_warp,
                            )

                            forward_coeffs = invert_warp(*transform_coeffs)
                            # ReceiptLetter coordinates are in OCR space
                            # (y=0 at bottom), normalized 0-1. The transform
                            # destination is in PIL space (y=0 at top). So we
                            # need flip_y=True to convert from OCR space to
                            # PIL space during transform
                            letter_copy.warp_transform(
                                *forward_coeffs,
                                src_width=image_width,
                                src_height=image_height,
                                dst_width=receipt_width,
                                dst_height=receipt_height,
                                flip_y=True,  # Receipt coords are in OCR space
                            )
                            original_key = (
                                word.word_id,
                                word.line_id,
                                receipt_id,
                            )
                            new_word_id = word_id_map.get(original_key)
                            new_line_id = line_id_map.get(
                                (word.line_id, receipt_id)
                            )
                            if new_word_id is None or new_line_id is None:
                                continue

                            centroid = letter_copy.calculate_centroid()
                            # After warp_transform, letter_copy coordinates are
                            # always normalized (0-1) in image space. We always
                            # need to multiply by image_width/height to get pixel
                            # coordinates. The centroid check tells us if the
                            # letter is within bounds (centroid <= 1.0) or outside
                            # (centroid > 1.0). But regardless, we need to convert
                            # normalized coords to pixel coords
                            centroid_x = centroid[0] * image_width
                            centroid_y = centroid[1] * image_height
                            bounding_box = {
                                "x": letter_copy.bounding_box["x"]
                                * image_width,
                                "y": letter_copy.bounding_box["y"]
                                * image_height,
                                "width": letter_copy.bounding_box["width"]
                                * image_width,
                                "height": letter_copy.bounding_box["height"]
                                * image_height,
                            }
                            top_left = {
                                "x": letter_copy.top_left["x"] * image_width,
                                "y": letter_copy.top_left["y"] * image_height,
                            }
                            top_right = {
                                "x": letter_copy.top_right["x"] * image_width,
                                "y": letter_copy.top_right["y"] * image_height,
                            }
                            bottom_left = {
                                "x": letter_copy.bottom_left["x"]
                                * image_width,
                                "y": letter_copy.bottom_left["y"]
                                * image_height,
                            }
                            bottom_right = {
                                "x": letter_copy.bottom_right["x"]
                                * image_width,
                                "y": letter_copy.bottom_right["y"]
                                * image_height,
                            }

                            all_letters.append(
                                {
                                    "receipt_id": receipt_id,
                                    "letter_id": letter.letter_id,
                                    "word_id": word.word_id,
                                    "line_id": word.line_id,
                                    "new_word_id": new_word_id,
                                    "new_line_id": new_line_id,
                                    "text": letter.text,
                                    "centroid_x": centroid_x,
                                    "centroid_y": centroid_y,
                                    "bounding_box": bounding_box,
                                    "top_left": top_left,
                                    "top_right": top_right,
                                    "bottom_left": bottom_left,
                                    "bottom_right": bottom_right,
                                    "angle_degrees": letter_copy.angle_degrees,
                                    "confidence": letter_copy.confidence,
                                }
                            )
                        except Exception:  # pylint: disable=broad-except
                            continue
                except Exception:  # pylint: disable=broad-except
                    continue
        except Exception:  # pylint: disable=broad-except
            continue

    return all_letters


def create_combined_receipt_records(
    image_id: str,
    new_receipt_id: int,
    combined_words: List[Dict[str, Any]],
    bounds: Dict[str, Any],
    raw_bucket: str,
    site_bucket: str,
    image_width: int,
    image_height: int,
    warped_width: int,
    warped_height: int,
    src_corners: List[Tuple[float, float]],
) -> Dict[str, Any]:
    """
    Create all DynamoDB entities for the combined receipt with coordinates in warped space.

    Args:
        image_id: Image ID containing the receipts
        new_receipt_id: ID for the new combined receipt
        combined_words: List of word dictionaries with image coordinates
        bounds: Bounds dictionary with corner coordinates
        raw_bucket: S3 bucket for raw images
        site_bucket: S3 bucket for CDN images
        image_width: Width of the original image
        image_height: Height of the original image
        warped_width: Width of the warped image
        warped_height: Height of the warped image
        src_corners: Source corners in PIL image space for warping

    Returns:
        Dict with receipt, receipt_lines, receipt_words, receipt_letters,
        line_id_map, word_id_map, and letter_id_map
    """
    # Use warped dimensions for receipt
    receipt_width = warped_width
    receipt_height = warped_height

    # Transform receipt corners from original image space to warped space
    # Convert OCR space bounds to PIL space for transformation
    top_left_ocr = bounds["top_left"]
    top_right_ocr = bounds["top_right"]
    bottom_left_ocr = bounds["bottom_left"]
    bottom_right_ocr = bounds["bottom_right"]

    # Convert to PIL space (y=0 at top)
    top_left_pil = (top_left_ocr["x"], image_height - top_left_ocr["y"])
    top_right_pil = (top_right_ocr["x"], image_height - top_right_ocr["y"])
    bottom_left_pil = (
        bottom_left_ocr["x"],
        image_height - bottom_left_ocr["y"],
    )
    bottom_right_pil = (
        bottom_right_ocr["x"],
        image_height - bottom_right_ocr["y"],
    )

    # Transform to warped space
    top_left_warped = transform_point_to_warped_space(
        top_left_pil[0],
        top_left_pil[1],
        src_corners,
        warped_width,
        warped_height,
    )
    top_right_warped = transform_point_to_warped_space(
        top_right_pil[0],
        top_right_pil[1],
        src_corners,
        warped_width,
        warped_height,
    )
    bottom_left_warped = transform_point_to_warped_space(
        bottom_left_pil[0],
        bottom_left_pil[1],
        src_corners,
        warped_width,
        warped_height,
    )
    bottom_right_warped = transform_point_to_warped_space(
        bottom_right_pil[0],
        bottom_right_pil[1],
        src_corners,
        warped_width,
        warped_height,
    )

    # Create Receipt entity with corners in warped space, normalized (0-1)
    # Receipt coordinates use OCR space (y=0 at bottom), so convert back
    receipt = Receipt(
        image_id=image_id,
        receipt_id=new_receipt_id,
        width=receipt_width,
        height=receipt_height,
        timestamp_added=datetime.now(timezone.utc),
        raw_s3_bucket=raw_bucket,
        raw_s3_key=f"raw/{image_id}_RECEIPT_{new_receipt_id:05d}.png",
        # Receipt corners in normalized warped space (0-1),
        # OCR coordinate system (y=0 at bottom)
        top_left={
            "x": (
                top_left_warped[0] / warped_width if warped_width > 0 else 0.0
            ),
            "y": (
                (warped_height - top_left_warped[1]) / warped_height
                if warped_height > 0
                else 0.0
            ),  # Convert to OCR space
        },
        top_right={
            "x": (
                top_right_warped[0] / warped_width if warped_width > 0 else 1.0
            ),
            "y": (
                (warped_height - top_right_warped[1]) / warped_height
                if warped_height > 0
                else 0.0
            ),
        },
        bottom_left={
            "x": (
                bottom_left_warped[0] / warped_width
                if warped_width > 0
                else 0.0
            ),
            "y": (
                (warped_height - bottom_left_warped[1]) / warped_height
                if warped_height > 0
                else 1.0
            ),
        },
        bottom_right={
            "x": (
                bottom_right_warped[0] / warped_width
                if warped_width > 0
                else 1.0
            ),
            "y": (
                (warped_height - bottom_right_warped[1]) / warped_height
                if warped_height > 0
                else 1.0
            ),
        },
        sha256=None,  # Will be calculated and set when image is uploaded
        cdn_s3_bucket=site_bucket,
    )

    # Group words by line
    words_by_line = defaultdict(list)
    for word in combined_words:
        line_key = (word["line_id"], word["receipt_id"])
        words_by_line[line_key].append(word)

    # Create ReceiptLine entities
    receipt_lines = []
    line_id_map = {}
    new_line_id = 1

    for (original_line_id, original_receipt_id), line_words in sorted(
        words_by_line.items()
    ):
        # Sort words in line by x coordinate
        line_words_sorted = sorted(line_words, key=lambda w: w["centroid_x"])

        # Collect all corners for this line and transform to warped space
        line_corners_ocr = []
        for word in line_words_sorted:
            # Convert OCR space to PIL space, transform, then back to OCR space
            for corner_name in [
                "top_left",
                "top_right",
                "bottom_left",
                "bottom_right",
            ]:
                corner = word.get(corner_name, {})
                x_ocr = corner.get("x", 0)
                y_ocr = corner.get("y", 0)
                # Convert to PIL space
                y_pil = image_height - y_ocr
                # Transform to warped space
                x_warped, y_warped = transform_point_to_warped_space(
                    x_ocr, y_pil, src_corners, warped_width, warped_height
                )
                # Convert back to OCR space in warped image
                y_ocr_warped = warped_height - y_warped
                line_corners_ocr.append((x_warped, y_ocr_warped))

        # Calculate line bounding box in warped space
        line_min_x = min(c[0] for c in line_corners_ocr)
        line_max_x = max(c[0] for c in line_corners_ocr)
        line_min_y_ocr = min(
            c[1] for c in line_corners_ocr
        )  # Bottom of line in OCR space
        line_max_y_ocr = max(
            c[1] for c in line_corners_ocr
        )  # Top of line in OCR space

        # Create line text
        line_text = " ".join(w["text"] for w in line_words_sorted)

        # Create ReceiptLine with coordinates in warped OCR space,
        # normalized (0-1)
        receipt_line = ReceiptLine(
            receipt_id=new_receipt_id,
            image_id=image_id,
            line_id=new_line_id,
            text=line_text,
            bounding_box={
                "x": line_min_x,  # Already in warped space, absolute pixels
                "y": line_min_y_ocr,
                "width": line_max_x - line_min_x,
                "height": line_max_y_ocr - line_min_y_ocr,
            },
            top_left={
                "x": line_min_x / receipt_width if receipt_width > 0 else 0.0,
                "y": (
                    line_max_y_ocr / receipt_height
                    if receipt_height > 0
                    else 0.0
                ),  # Normalize relative to bottom (OCR space)
            },
            top_right={
                "x": line_max_x / receipt_width if receipt_width > 0 else 1.0,
                "y": (
                    line_max_y_ocr / receipt_height
                    if receipt_height > 0
                    else 0.0
                ),
            },
            bottom_left={
                "x": line_min_x / receipt_width if receipt_width > 0 else 0.0,
                "y": (
                    line_min_y_ocr / receipt_height
                    if receipt_height > 0
                    else 1.0
                ),
            },
            bottom_right={
                "x": line_max_x / receipt_width if receipt_width > 0 else 1.0,
                "y": (
                    line_min_y_ocr / receipt_height
                    if receipt_height > 0
                    else 1.0
                ),
            },
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=1.0,
        )

        receipt_lines.append(receipt_line)
        line_id_map[(original_line_id, original_receipt_id)] = new_line_id
        new_line_id += 1

    # Create ReceiptWord entities
    # IMPORTANT: We assign word IDs sequentially to ALL words (including noise)
    # to maintain proper structure (words belong to lines, letters belong to words).
    # However, we need to ensure word IDs match between DynamoDB and ChromaDB.
    # Since noise words are filtered out during embedding, we assign IDs to all
    # words but only non-noise words will be embedded with those IDs.
    receipt_words = []
    word_id_map = {}
    new_word_id = 1

    for word in combined_words:
        original_key = (word["word_id"], word["line_id"], word["receipt_id"])
        new_line_id = line_id_map.get((word["line_id"], word["receipt_id"]), 1)

        # Transform word corners from original image space to warped space
        word_corners_ocr_warped = {}
        for corner_name in [
            "top_left",
            "top_right",
            "bottom_left",
            "bottom_right",
        ]:
            corner = word.get(corner_name, {})
            x_ocr = corner.get("x", 0)
            y_ocr = corner.get("y", 0)
            # Convert to PIL space
            y_pil = image_height - y_ocr
            # Transform to warped space
            x_warped, y_warped = transform_point_to_warped_space(
                x_ocr, y_pil, src_corners, warped_width, warped_height
            )
            # Convert back to OCR space in warped image
            y_ocr_warped = warped_height - y_warped
            word_corners_ocr_warped[corner_name] = (x_warped, y_ocr_warped)

        # Calculate bounding box in warped space
        word_min_x = min(c[0] for c in word_corners_ocr_warped.values())
        word_max_x = max(c[0] for c in word_corners_ocr_warped.values())
        word_min_y_ocr = min(c[1] for c in word_corners_ocr_warped.values())
        word_max_y_ocr = max(c[1] for c in word_corners_ocr_warped.values())

        # Check if word is noise (preserve from original word)
        is_noise = word.get("is_noise", False)

        # Create ReceiptWord with coordinates in warped OCR space,
        # normalized (0-1)
        # NOTE: We include ALL words (including noise) in DynamoDB to maintain
        # proper structure (words belong to lines, letters belong to words).
        # Noise words will be filtered out during embedding, so they won't appear
        # in ChromaDB, but they must exist in DynamoDB for structural integrity.
        # Word IDs are assigned sequentially to ALL words to ensure consistency.
        receipt_word = ReceiptWord(
            receipt_id=new_receipt_id,
            image_id=image_id,
            line_id=new_line_id,
            word_id=new_word_id,
            text=word["text"],
            bounding_box={
                "x": word_min_x,  # Already in warped space, absolute pixels
                "y": word_min_y_ocr,
                "width": word_max_x - word_min_x,
                "height": word_max_y_ocr - word_min_y_ocr,
            },
            top_left={
                "x": (
                    word_corners_ocr_warped["top_left"][0] / receipt_width
                    if receipt_width > 0
                    else 0.0
                ),
                "y": (
                    word_corners_ocr_warped["top_left"][1] / receipt_height
                    if receipt_height > 0
                    else 0.0
                ),  # Normalize relative to bottom (OCR space)
            },
            top_right={
                "x": (
                    word_corners_ocr_warped["top_right"][0] / receipt_width
                    if receipt_width > 0
                    else 1.0
                ),
                "y": (
                    word_corners_ocr_warped["top_right"][1] / receipt_height
                    if receipt_height > 0
                    else 0.0
                ),
            },
            bottom_left={
                "x": (
                    word_corners_ocr_warped["bottom_left"][0] / receipt_width
                    if receipt_width > 0
                    else 0.0
                ),
                "y": (
                    word_corners_ocr_warped["bottom_left"][1] / receipt_height
                    if receipt_height > 0
                    else 1.0
                ),
            },
            bottom_right={
                "x": (
                    word_corners_ocr_warped["bottom_right"][0] / receipt_width
                    if receipt_width > 0
                    else 1.0
                ),
                "y": (
                    word_corners_ocr_warped["bottom_right"][1] / receipt_height
                    if receipt_height > 0
                    else 1.0
                ),
            },
            angle_degrees=word.get("angle_degrees", 0.0),
            angle_radians=word.get("angle_degrees", 0.0)
            * 3.141592653589793
            / 180.0,
            confidence=word.get("confidence", 1.0),
            is_noise=is_noise,  # Preserve noise flag from original word
        )

        receipt_words.append(receipt_word)
        # Map ALL words (including noise) in word_id_map for letter mapping
        # Letters need to map to their words even if the word is noise
        word_id_map[original_key] = new_word_id
        new_word_id += 1

    return {
        "receipt": receipt,
        "receipt_lines": receipt_lines,
        "receipt_words": receipt_words,
        "receipt_letters": [],
        "line_id_map": line_id_map,
        "word_id_map": word_id_map,
        "letter_id_map": {},
    }


def create_receipt_letters_from_combined(
    combined_letters: List[Dict[str, Any]],
    new_receipt_id: int,
    image_id: str,
    receipt_width: int,
    receipt_height: int,
    image_height: int,
    warped_height: int,
    src_corners: List[Tuple[float, float]],
    warped_width: int,
) -> List[ReceiptLetter]:
    """
    Create ReceiptLetter entities from combined letter data.

    Args:
        combined_letters: List of letter dictionaries with image coordinates
        new_receipt_id: ID for the new combined receipt
        image_id: Image ID containing the receipts
        receipt_width: Width of the receipt (warped)
        receipt_height: Height of the receipt (warped)
        image_height: Height of the original image
        warped_height: Height of the warped image
        src_corners: Source corners in PIL image space for warping
        warped_width: Width of the warped image

    Returns:
        List of ReceiptLetter entities
    """
    receipt_letters = []
    new_letter_id = 1
    for letter_data in combined_letters:
        # Transform letter corners from original image space to warped space
        letter_corners_ocr_warped = {}
        for corner_name in [
            "top_left",
            "top_right",
            "bottom_left",
            "bottom_right",
        ]:
            corner = letter_data.get(corner_name, {})
            x_ocr = corner.get("x", 0)
            y_ocr = corner.get("y", 0)
            # Convert to PIL space
            y_pil = image_height - y_ocr
            # Transform to warped space
            x_warped, y_warped = transform_point_to_warped_space(
                x_ocr, y_pil, src_corners, warped_width, warped_height
            )
            # Convert back to OCR space in warped image
            y_ocr_warped = warped_height - y_warped
            letter_corners_ocr_warped[corner_name] = (
                x_warped,
                y_ocr_warped,
            )

        # Calculate bounding box in warped space
        letter_min_x = min(c[0] for c in letter_corners_ocr_warped.values())
        letter_max_x = max(c[0] for c in letter_corners_ocr_warped.values())
        letter_min_y_ocr = min(
            c[1] for c in letter_corners_ocr_warped.values()
        )
        letter_max_y_ocr = max(
            c[1] for c in letter_corners_ocr_warped.values()
        )

        # Create ReceiptLetter with coordinates in warped OCR space,
        # normalized (0-1)
        receipt_letter = ReceiptLetter(
            receipt_id=new_receipt_id,
            image_id=image_id,
            line_id=letter_data["new_line_id"],
            word_id=letter_data["new_word_id"],
            letter_id=new_letter_id,
            text=letter_data["text"],
            bounding_box={
                "x": letter_min_x,  # Already in warped space, absolute pixels
                "y": letter_min_y_ocr,
                "width": letter_max_x - letter_min_x,
                "height": letter_max_y_ocr - letter_min_y_ocr,
            },
            top_left={
                "x": (
                    letter_corners_ocr_warped["top_left"][0] / receipt_width
                    if receipt_width > 0
                    else 0.0
                ),
                "y": (
                    letter_corners_ocr_warped["top_left"][1] / receipt_height
                    if receipt_height > 0
                    else 0.0
                ),  # Normalize relative to bottom (OCR space)
            },
            top_right={
                "x": (
                    letter_corners_ocr_warped["top_right"][0] / receipt_width
                    if receipt_width > 0
                    else 1.0
                ),
                "y": (
                    letter_corners_ocr_warped["top_right"][1] / receipt_height
                    if receipt_height > 0
                    else 0.0
                ),
            },
            bottom_left={
                "x": (
                    letter_corners_ocr_warped["bottom_left"][0] / receipt_width
                    if receipt_width > 0
                    else 0.0
                ),
                "y": (
                    letter_corners_ocr_warped["bottom_left"][1]
                    / receipt_height
                    if receipt_height > 0
                    else 1.0
                ),
            },
            bottom_right={
                "x": (
                    letter_corners_ocr_warped["bottom_right"][0]
                    / receipt_width
                    if receipt_width > 0
                    else 1.0
                ),
                "y": (
                    letter_corners_ocr_warped["bottom_right"][1]
                    / receipt_height
                    if receipt_height > 0
                    else 1.0
                ),
            },
            angle_degrees=letter_data.get("angle_degrees", 0.0),
            angle_radians=letter_data.get("angle_degrees", 0.0)
            * 3.141592653589793
            / 180.0,
            confidence=letter_data.get("confidence", 1.0),
        )
        receipt_letters.append(receipt_letter)
        new_letter_id += 1

    return receipt_letters
