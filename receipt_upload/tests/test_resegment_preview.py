"""Visual evidence tests for PHOTO and SCAN re-segmentation."""

from PIL import Image

from receipt_upload.resegment import build_preview_bundle


def _word(line_id: int, word_id: int, x: float, pil_y: float) -> dict:
    height = 600
    width = 70
    box_height = 18
    return {
        "line_id": line_id,
        "word_id": word_id,
        "text": f"w{line_id}-{word_id}",
        "top_left": {"x": x, "y": height - pil_y},
        "top_right": {"x": x + width, "y": height - pil_y},
        "bottom_left": {"x": x, "y": height - pil_y - box_height},
        "bottom_right": {
            "x": x + width,
            "y": height - pil_y - box_height,
        },
        "angle_degrees": 0.0,
        "confidence": 0.95,
    }


def _line(line_id: int) -> dict:
    return {
        "line_id": line_id,
        "text": f"line {line_id}",
        "angle_degrees": 0.0,
        "confidence": 0.95,
    }


def _letter(line_id: int, word_id: int, slope: float = 0.02) -> dict:
    return {
        "line_id": line_id,
        "word_id": word_id,
        "letter_id": 1,
        "text": "x",
        "bottom_left": {"x": 0.1, "y": 0.1},
        "bottom_right": {"x": 0.2, "y": 0.1 + slope},
    }


def _segment(key: str, refs: list[tuple[int, int]], z_index: int) -> dict:
    return {
        "segment_key": key,
        "word_refs": [
            {"line_id": line_id, "word_id": word_id}
            for line_id, word_id in refs
        ],
        "z_index": z_index,
        "visible_regions": [],
    }


def test_photo_preview_preserves_disconnected_underlay_and_inline_sheet():
    words = [
        _word(1, 1, 20, 55),
        _word(2, 1, 24, 90),
        _word(3, 1, 35, 500),
        _word(4, 1, 38, 535),
        _word(10, 1, 190, 210),
        _word(11, 1, 195, 250),
        _word(12, 1, 200, 290),
    ]
    words_by_ref = {(word["line_id"], word["word_id"]): word for word in words}
    card_refs = [(1, 1), (2, 1), (3, 1), (4, 1)]
    guest_refs = [(10, 1), (11, 1), (12, 1)]

    bundle = build_preview_bundle(
        Image.new("RGB", (400, 600), "white"),
        image_type="PHOTO",
        strategy="LAYERED_MULTI_REGION",
        lines=[_line(line_id) for line_id in (1, 2, 3, 4, 10, 11, 12)],
        words_by_ref=words_by_ref,
        segments=[
            {
                **_segment("card", card_refs, 0),
                "occluded_by": ["guest"],
            },
            _segment("guest", guest_refs, 1),
        ],
        discard_refs=set(),
        letters=[_letter(line_id, 1) for line_id in (1, 2, 3, 4, 10, 11, 12)],
        padding_px=6,
    )

    assert bundle["metrics"]["card"]["visible_region_count"] == 2
    assert bundle["metrics"]["guest"]["visible_region_count"] == 1
    assert bundle["metrics"]["card"]["word_centroids_retained"] == 4
    assert bundle["metrics"]["guest"]["word_centroids_retained"] == 3
    assert bundle["metrics"]["card"]["assigned_letter_count"] == 4
    assert bundle["metrics"]["guest"]["assigned_letter_count"] == 3
    assert bundle["metrics"]["card"]["foreign_word_centroids_in_mask"] == 0
    assert bundle["metrics"]["guest"]["foreign_word_centroids_in_mask"] == 0
    assert bundle["images"]["segments"]["card"]["visible_crop"].mode == "RGBA"
    assert bundle["images"]["contact_sheet"].width <= 1400
    assert bundle["evidence"]["lines"][0]["derived_baseline_source"] == (
        "LETTER_CORNERS"
    )
    assert {finding["code"] for finding in bundle["findings"]} >= {
        "LAYERED_APPLY_NOT_SUPPORTED",
        "DISCONNECTED_VISIBLE_REGIONS",
    }


def test_scan_rectangular_preview_blocks_foreign_word_capture():
    words = [_word(1, 1, 30, 100), _word(2, 1, 80, 120)]
    words_by_ref = {(word["line_id"], word["word_id"]): word for word in words}
    overlapping_geometry = {
        "src_corners": [[0, 50], [200, 50], [200, 200], [0, 200]]
    }

    bundle = build_preview_bundle(
        Image.new("RGB", (400, 600), "white"),
        image_type="SCAN",
        strategy="RECTANGULAR",
        lines=[_line(1), _line(2)],
        words_by_ref=words_by_ref,
        segments=[
            {
                **_segment("first", [(1, 1)], 0),
                "geometry": overlapping_geometry,
            },
            {
                **_segment("second", [(2, 1)], 0),
                "geometry": overlapping_geometry,
            },
        ],
        discard_refs=set(),
        padding_px=0,
    )

    blockers = {
        (finding["code"], finding.get("segment_key"))
        for finding in bundle["findings"]
        if finding["severity"] == "BLOCKER"
    }
    assert ("FOREIGN_WORD_CAPTURED", "first") in blockers
    assert ("FOREIGN_WORD_CAPTURED", "second") in blockers


def test_photo_rectangular_preview_blocks_disconnected_regions():
    words = [_word(1, 1, 30, 70), _word(2, 1, 35, 530)]
    words_by_ref = {(word["line_id"], word["word_id"]): word for word in words}

    bundle = build_preview_bundle(
        Image.new("RGB", (400, 600), "white"),
        image_type="PHOTO",
        strategy="RECTANGULAR",
        lines=[_line(1), _line(2)],
        words_by_ref=words_by_ref,
        segments=[_segment("underlay", [(1, 1), (2, 1)], 0)],
        discard_refs=set(),
        padding_px=0,
    )

    finding = next(
        finding
        for finding in bundle["findings"]
        if finding["code"] == "DISCONNECTED_VISIBLE_REGIONS"
    )
    assert finding["severity"] == "BLOCKER"
