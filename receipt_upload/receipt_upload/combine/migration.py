"""Preserve semantic sections and barcode geometry through a receipt merge."""

from collections import defaultdict
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from math import atan2, degrees

from receipt_dynamo import DynamoClient
from receipt_dynamo.entities.receipt_barcode import ReceiptBarcode
from receipt_dynamo.entities.receipt_section import ReceiptSection

from receipt_upload.combine.geometry_utils import (
    transform_point_to_warped_space,
)
from receipt_upload.combine.records_builder import (
    _get_receipt_to_image_transform,
)
from receipt_upload.geometry.transformations import invert_warp

CORNERS = ("top_left", "top_right", "bottom_left", "bottom_right")


def migrate_receipt_sections(
    client: DynamoClient,
    image_id: str,
    receipt_ids: list[int],
    new_receipt_id: int,
    line_id_map: dict[tuple[int, int], set[int]],
) -> list[ReceiptSection]:
    """Union source sections by type using retained and deduplicated line IDs.

    Warping invalidates row references and verification; new usable sections
    are PENDING. INVALID inputs never expand a usable section of that type.
    Missing mappings fail before source deletion instead of losing metadata.
    """
    groups = defaultdict(list)
    for rid in receipt_ids:
        for section in client.get_receipt_sections_from_receipt(image_id, rid):
            mapped = set()
            for line_id in section.line_ids:
                targets = line_id_map.get((line_id, rid))
                if not targets:
                    raise ValueError(
                        f"Cannot preserve section line {rid}:{line_id} in merge"
                    )
                mapped.update(targets)
            groups[section.section_type].append((section, mapped))

    result = []
    for section_type, sources in sorted(groups.items()):
        active = [s for s in sources if s[0].validation_status != "INVALID"]
        chosen = active or sources
        confidence = [
            s.confidence for s, _ in chosen if s.confidence is not None
        ]
        result.append(
            ReceiptSection(
                image_id=image_id,
                receipt_id=new_receipt_id,
                section_type=section_type,
                line_ids=sorted(set().union(*(ids for _, ids in chosen))),
                created_at=datetime.now(timezone.utc),
                confidence=min(confidence) if confidence else None,
                model_source="merge-remap-v1",
                validation_status="PENDING" if active else "INVALID",
            )
        )
    return result


def receipt_barcodes_in_image_space(
    client: DynamoClient,
    image_id: str,
    receipt_ids: list[int],
    image_width: int,
    image_height: int,
) -> list[ReceiptBarcode]:
    """Read barcodes consistently and map source OCR coordinates to the image.

    The same transform as words is used. Any read or geometry failure aborts
    the merge before it can delete a source barcode.
    """
    result = []
    for rid in receipt_ids:
        source = client.get_receipt(image_id, rid)
        coeffs, width, height = _get_receipt_to_image_transform(
            source, image_width, image_height
        )
        for barcode in client.list_receipt_barcodes_from_receipt_consistent(
            image_id, rid
        ):
            transformed = deepcopy(barcode)
            transformed.warp_transform(
                *invert_warp(*coeffs),
                src_width=image_width,
                src_height=image_height,
                dst_width=width,
                dst_height=height,
                flip_y=True,
            )
            result.append(transformed)
    return result


def migrate_receipt_barcodes(
    barcodes: list[ReceiptBarcode],
    new_receipt_id: int,
    image_width: int,
    image_height: int,
    src_corners: list[tuple[float, float]],
    warped_width: int,
    warped_height: int,
) -> list[ReceiptBarcode]:
    """Transform image-space barcodes into the new crop, preserving payloads."""
    result = []
    for index, barcode in enumerate(barcodes):
        corners = {}
        for name in CORNERS:
            point = getattr(barcode, name)
            x, y = transform_point_to_warped_space(
                point["x"] * image_width,
                (1.0 - point["y"]) * image_height,
                src_corners,
                warped_width,
                warped_height,
            )
            corners[name] = {"x": x / warped_width, "y": 1 - y / warped_height}
        xs = [p["x"] for p in corners.values()]
        ys = [p["y"] for p in corners.values()]
        angle = atan2(
            (corners["top_right"]["y"] - corners["top_left"]["y"])
            * warped_height,
            (corners["top_right"]["x"] - corners["top_left"]["x"])
            * warped_width,
        )
        result.append(
            replace(
                barcode,
                receipt_id=new_receipt_id,
                barcode_id=index,
                **corners,
                bounding_box={
                    "x": min(xs),
                    "y": min(ys),
                    "width": max(xs) - min(xs),
                    "height": max(ys) - min(ys),
                },
                angle_degrees=degrees(angle),
                angle_radians=angle,
            )
        )
    return result
