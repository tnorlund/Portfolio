"""Unit tests for the CDN re-warp tool (pure parts only).

No AWS, no network: aspect grading, the OCR box projection, the
text-alignment gate that guards every republish, and the CDN key
conflict check.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError
from PIL import Image as PIL_Image
from PIL import ImageDraw

from scripts import rewarp_receipt_cdn_images as rw

IMG = "0e2f8a2c-1111-4222-8333-944445555666"


def make_receipt(width: int, height: int, **overrides) -> SimpleNamespace:
    """Duck-typed receipt occupying the middle of a portrait photo."""
    fields = {
        "image_id": IMG,
        "receipt_id": 1,
        "width": width,
        "height": height,
        "top_left": {"x": 0.25, "y": 0.75},
        "top_right": {"x": 0.75, "y": 0.75},
        "bottom_right": {"x": 0.75, "y": 0.25},
        "bottom_left": {"x": 0.25, "y": 0.25},
        "cdn_s3_bucket": "site-bucket",
        "cdn_s3_key": f"assets/{IMG}/1.jpg",
        "cdn_thumbnail_s3_key": f"assets/{IMG}/1_thumbnail.jpg",
        "timestamp_added": "2026-01-01T00:00:00+00:00",
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def make_word(x: float, y: float, width: float, height: float, text: str):
    return SimpleNamespace(
        text=text,
        bounding_box={"x": x, "y": y, "width": width, "height": height},
    )


# --------------------------------------------------------------------- #
# Aspect grading                                                        #
# --------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "cdn_size,expected",
    [
        ((866, 2688), "ok"),
        ((96, 300), "ok"),  # thumbnail, integer-rounded
        ((2688, 866), "rotated_90"),
        ((300, 96), "rotated_90"),
        ((848, 2420), "aspect_mismatch"),  # stale crop, still portrait
    ],
)
def test_variant_verdicts(monkeypatch, cdn_size, expected):
    monkeypatch.setattr(
        rw, "_probe_cdn_size", lambda *a, **k: (cdn_size, None, None)
    )
    probe = rw._probe_variant(
        None, "bucket", "full", "key.jpg", rw._aspect(866, 2688)
    )
    assert probe.verdict == expected


def test_scan_status_is_worst_variant(monkeypatch):
    """A receipt is only 'ok' when every published variant agrees."""
    sizes = {
        "assets/x/1.jpg": (866, 2688),
        "assets/x/1_thumbnail.jpg": (2688, 866),
    }
    monkeypatch.setattr(
        rw,
        "_probe_cdn_size",
        lambda client, bucket, key: (sizes[key], None, None),
    )
    receipt = make_receipt(
        866,
        2688,
        cdn_s3_key="assets/x/1.jpg",
        cdn_thumbnail_s3_key="assets/x/1_thumbnail.jpg",
    )
    result = rw.scan_receipt(receipt, None, None)
    assert result.status == "rotated_90"
    assert result.is_mismatch
    assert {v.name for v in result.bad_variants} == {"thumbnail"}


def test_quad_aspect_flags_inconsistent_entity():
    """Corners spanning a 3:4 photo cannot describe a 1:4 receipt."""
    receipt = make_receipt(200, 800)
    # Corners cover the full 600x800 image -> quad aspect 0.75, while the
    # declared dimensions claim 0.25.
    receipt.top_left = {"x": 0.0, "y": 1.0}
    receipt.top_right = {"x": 1.0, "y": 1.0}
    receipt.bottom_right = {"x": 1.0, "y": 0.0}
    receipt.bottom_left = {"x": 0.0, "y": 0.0}
    quad = rw._quad_aspect(receipt, 600, 800)
    assert quad == pytest.approx(0.75)
    assert not rw._close(quad, rw._aspect(200, 800), rw.QUAD_ASPECT_TOLERANCE)


# --------------------------------------------------------------------- #
# OCR box projection                                                    #
# --------------------------------------------------------------------- #


def test_word_boxes_flip_y_up_to_pil_rows():
    """Receipt-relative y is bottom-up; PIL rows count downward."""
    word = make_word(0.1, 0.8, 0.5, 0.1, "TOTAL")
    (box,) = rw._word_boxes_in_pixels([word], 100, 1000)
    left, top, right, bottom = box
    assert (left, right) == (10, 60)
    # y=0.8 is the bottom edge, y+h=0.9 the top -> rows 100..200.
    assert (top, bottom) == (100, 200)


# --------------------------------------------------------------------- #
# Text-alignment gate                                                   #
# --------------------------------------------------------------------- #


def _receipt_canvas(width: int, height: int, words) -> PIL_Image.Image:
    """White canvas with a dark bar drawn exactly where each word sits."""
    canvas = PIL_Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(canvas)
    for box in rw._word_boxes_in_pixels(words, width, height):
        draw.rectangle(box, fill=(20, 20, 20))
    return canvas


def test_alignment_gate_accepts_matching_text():
    words = [
        make_word(0.1, 0.05 + 0.09 * i, 0.6, 0.04, f"ITEM{i}")
        for i in range(10)
    ]
    canvas = _receipt_canvas(400, 1200, words)
    gap, measured = rw.text_alignment_score(canvas, words)
    assert measured > 0
    assert gap > rw.DEFAULT_DARKNESS_MARGIN


def test_alignment_gate_rejects_shifted_text():
    """The gate is what stops a wrong warp from being published."""
    words = [
        make_word(0.1, 0.05 + 0.09 * i, 0.6, 0.04, f"ITEM{i}")
        for i in range(10)
    ]
    # Render the text half a line lower than the OCR boxes claim: the
    # boxes now straddle blank paper and their own gaps.
    shifted = [
        make_word(0.1, 0.05 + 0.09 * i + 0.045, 0.6, 0.04, f"ITEM{i}")
        for i in range(10)
    ]
    canvas = _receipt_canvas(400, 1200, shifted)
    gap, _ = rw.text_alignment_score(canvas, words)
    assert gap < rw.DEFAULT_DARKNESS_MARGIN


def test_alignment_gate_rejects_blank_canvas():
    words = [
        make_word(0.1, 0.05 + 0.09 * i, 0.6, 0.04, f"ITEM{i}")
        for i in range(10)
    ]
    canvas = PIL_Image.new("RGB", (400, 1200), "white")
    gap, _ = rw.text_alignment_score(canvas, words)
    assert gap == pytest.approx(0.0, abs=0.01)


def test_alignment_score_reports_no_boxes_when_words_missing():
    canvas = PIL_Image.new("RGB", (400, 1200), "white")
    assert rw.text_alignment_score(canvas, []) == (None, 0)


def test_probe_boxes_spread_down_the_receipt():
    """Sampling must span the receipt, not cluster at one edge."""
    words = [
        make_word(0.1, 0.01 + 0.0098 * i, 0.6, 0.005, f"ITEM{i}")
        for i in range(100)
    ]
    boxes = rw._select_probe_boxes(words, 400, 4000, sample=5)
    assert len(boxes) == 5
    tops = [box[1] for box in boxes]
    assert max(tops) - min(tops) > 2000


# --------------------------------------------------------------------- #
# CDN key safety                                                        #
# --------------------------------------------------------------------- #


def test_no_conflicts_for_conventional_keys():
    receipt = make_receipt(866, 2688)
    base = rw._derive_base_key(receipt)
    assert base == f"assets/{IMG}/1"
    assert rw._expected_key_conflicts(receipt, base) == []


def test_conflicts_reported_for_legacy_key_layout():
    """Legacy ``{image_id}_RECEIPT_00003`` keys must block a republish."""
    receipt = make_receipt(
        942,
        1480,
        cdn_s3_key=f"assets/{IMG}_RECEIPT_00003.jpg",
        cdn_thumbnail_s3_key=f"assets/{IMG}_RECEIPT_00003_thumb.jpg",
    )
    base = rw._derive_base_key(receipt)
    conflicts = rw._expected_key_conflicts(receipt, base)
    assert [name for name, _, _ in conflicts] == ["cdn_thumbnail_s3_key"]


# --------------------------------------------------------------------- #
# Staleness signal                                                      #
# --------------------------------------------------------------------- #


def _stub_probe(monkeypatch, size, mtime):
    monkeypatch.setattr(
        rw,
        "_probe_cdn_size",
        lambda *a, **k: (size, None, mtime),
    )


def test_stale_when_asset_predates_receipt_row(monkeypatch):
    """The case aspect grading cannot see: right shape, wrong crop.

    Mirrors prod 04b16930: the asset was written 2026-01-16 but the
    receipt row was rewritten 2026-07-11, so the crop cannot depict the
    geometry the row now carries even though its ratio still matches.
    """
    _stub_probe(
        monkeypatch,
        (846, 2462),
        datetime(2026, 1, 16, 19, 12, 47, tzinfo=timezone.utc),
    )
    receipt = make_receipt(
        846, 2462, timestamp_added="2026-07-11T23:27:29.682716+00:00"
    )
    result = rw.scan_receipt(receipt, None, None)
    assert result.status == "stale_timestamp"
    assert result.is_mismatch
    assert "predates receipt row" in " ".join(result.notes)


def test_not_stale_when_asset_newer_than_row(monkeypatch):
    _stub_probe(
        monkeypatch,
        (846, 2462),
        datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    receipt = make_receipt(
        846, 2462, timestamp_added="2026-07-11T23:27:29.682716+00:00"
    )
    assert rw.scan_receipt(receipt, None, None).status == "ok"


def test_aspect_mismatch_outranks_staleness(monkeypatch):
    """A wrong-shape asset keeps the more specific verdict."""
    _stub_probe(
        monkeypatch,
        (2462, 846),
        datetime(2026, 1, 16, tzinfo=timezone.utc),
    )
    receipt = make_receipt(
        846, 2462, timestamp_added="2026-07-11T23:27:29.682716+00:00"
    )
    result = rw.scan_receipt(receipt, None, None)
    assert result.status == "rotated_90"
    # The staleness evidence is still recorded, just not the headline.
    assert "predates receipt row" in " ".join(result.notes)


def test_naive_entity_timestamp_treated_as_utc():
    receipt = make_receipt(846, 2462, timestamp_added="2026-07-11T23:27:29")
    parsed = rw._entity_timestamp(receipt)
    assert parsed is not None and parsed.tzinfo is not None


@pytest.mark.parametrize("bad", [None, "", "not-a-date", 12345])
def test_unparseable_entity_timestamp_is_not_fatal(bad):
    receipt = make_receipt(846, 2462, timestamp_added=bad)
    assert rw._entity_timestamp(receipt) is None


def test_stale_verdict_ranks_below_aspect_mismatch():
    assert (
        rw.VERDICT_SEVERITY["stale_timestamp"]
        < rw.VERDICT_SEVERITY["aspect_mismatch"]
    )
    assert "stale_timestamp" in rw.MISMATCH_VERDICTS


# --------------------------------------------------------------------- #
# Backup before overwrite                                               #
# --------------------------------------------------------------------- #


class FakeS3:
    """Records copy_object calls; optionally fails one key."""

    def __init__(self, missing=(), fail_key=None):
        self.copies: list[tuple[str, str]] = []
        self.missing = set(missing)
        self.fail_key = fail_key

    def copy_object(self, Bucket, Key, CopySource):  # noqa: N803
        source = CopySource["Key"]
        if source in self.missing:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "CopyObject")
        if source == self.fail_key:
            raise ClientError(
                {"Error": {"Code": "AccessDenied"}}, "CopyObject"
            )
        self.copies.append(((CopySource["Bucket"], source), (Bucket, Key)))


def test_backup_lands_in_a_different_bucket():
    """Backups must leave the site bucket entirely.

    The prod deploy runs `aws s3 sync --delete --exclude "assets/*"`, so
    a backup prefix in the site bucket is deleted by the next deploy --
    which is how a real backup run was lost.
    """
    receipt = make_receipt(866, 2688)
    s3 = FakeS3()
    copied, error = rw._backup_existing_variants(
        s3, "site-bucket", "raw-bucket", receipt, "rewarp-backups/run/"
    )
    assert error is None
    assert copied == [f"assets/{IMG}/1.jpg", f"assets/{IMG}/1_thumbnail.jpg"]
    assert s3.copies == [
        (
            ("site-bucket", f"assets/{IMG}/1.jpg"),
            ("raw-bucket", f"rewarp-backups/run/assets/{IMG}/1.jpg"),
        ),
        (
            ("site-bucket", f"assets/{IMG}/1_thumbnail.jpg"),
            (
                "raw-bucket",
                f"rewarp-backups/run/assets/{IMG}/1_thumbnail.jpg",
            ),
        ),
    ]


def test_backup_tolerates_missing_objects():
    """A key with nothing published has nothing to lose."""
    receipt = make_receipt(866, 2688)
    s3 = FakeS3(missing={f"assets/{IMG}/1.jpg"})
    copied, error = rw._backup_existing_variants(
        s3, "site-bucket", "raw-bucket", receipt, "rewarp-backups/run/"
    )
    assert error is None
    assert copied == [f"assets/{IMG}/1_thumbnail.jpg"]


def test_backup_failure_is_reported_so_caller_can_abort():
    receipt = make_receipt(866, 2688)
    s3 = FakeS3(fail_key=f"assets/{IMG}/1.jpg")
    _copied, error = rw._backup_existing_variants(
        s3, "site-bucket", "raw-bucket", receipt, "rewarp-backups/run/"
    )
    assert error is not None
    assert "AccessDenied" in error


def _rewarp_with_stubs(monkeypatch, uploaded, **kwargs):
    """Drive rewarp_receipt past everything except the backup step."""
    monkeypatch.setattr(
        rw, "upload_all_cdn_formats", lambda *a, **k: uploaded.append(a)
    )
    monkeypatch.setattr(
        rw,
        "_load_original",
        lambda *a, **k: (PIL_Image.new("RGB", (100, 200)), []),
    )
    monkeypatch.setattr(
        rw, "warp_receipt_crop", lambda *a, **k: PIL_Image.new("RGB", (8, 8))
    )
    monkeypatch.setattr(rw, "text_alignment_score", lambda *a, **k: (99.0, 5))
    dynamo = SimpleNamespace(
        list_receipt_words_from_receipt=lambda *a, **k: []
    )
    image_entity = SimpleNamespace(
        width=100,
        height=200,
        raw_s3_bucket="raw-bucket",
        raw_s3_key="raw.png",
    )
    return rw.rewarp_receipt(
        dynamo,
        FakeS3(),
        make_receipt(866, 2688),
        image_entity,
        darkness_margin=6.0,
        dry_run=False,
        backup_prefix="rewarp-backups/run/",
        **kwargs,
    )


def test_backup_defaults_to_the_raw_photo_bucket(monkeypatch):
    uploaded: list = []
    record = _rewarp_with_stubs(monkeypatch, uploaded)
    assert record["status"] == "applied"
    assert record["backup_bucket"] == "raw-bucket"
    assert uploaded


def test_backup_into_the_site_bucket_is_refused(monkeypatch):
    """Same-bucket backup is the exact mistake that lost the last run."""
    uploaded: list = []
    record = _rewarp_with_stubs(
        monkeypatch, uploaded, backup_bucket="site-bucket"
    )
    assert record["status"] == "skipped_backup_same_bucket"
    assert uploaded == []
    assert "sync --delete" in " ".join(record["notes"])


def test_same_bucket_backup_allowed_with_explicit_opt_in(monkeypatch):
    uploaded: list = []
    record = _rewarp_with_stubs(
        monkeypatch,
        uploaded,
        backup_bucket="site-bucket",
        allow_same_bucket=True,
    )
    assert record["status"] == "applied"
    assert record["backup_bucket"] == "site-bucket"
    assert uploaded


def test_rewarp_aborts_when_backup_fails(monkeypatch):
    """A failed backup must stop the overwrite, not just warn."""
    uploaded = []
    monkeypatch.setattr(
        rw,
        "upload_all_cdn_formats",
        lambda *a, **k: uploaded.append(a),
    )
    monkeypatch.setattr(
        rw,
        "_backup_existing_variants",
        lambda *a, **k: ([], "backup_failed:boom"),
    )
    # Same aspect as the image entity below, so the run reaches the
    # backup step rather than stopping at the aspect guard.
    monkeypatch.setattr(
        rw,
        "_load_original",
        lambda *a, **k: (PIL_Image.new("RGB", (100, 200)), []),
    )
    monkeypatch.setattr(
        rw, "warp_receipt_crop", lambda *a, **k: PIL_Image.new("RGB", (8, 8))
    )
    monkeypatch.setattr(rw, "text_alignment_score", lambda *a, **k: (99.0, 5))

    dynamo = SimpleNamespace(
        list_receipt_words_from_receipt=lambda *a, **k: []
    )
    image_entity = SimpleNamespace(
        width=100, height=200, raw_s3_bucket="raw", raw_s3_key="raw.png"
    )
    record = rw.rewarp_receipt(
        dynamo,
        None,
        make_receipt(866, 2688),
        image_entity,
        darkness_margin=6.0,
        dry_run=False,
        backup_prefix="backups/run/",
    )
    assert record["status"] == "skipped_backup_failed"
    assert uploaded == []


# --------------------------------------------------------------------- #
# Exclusions                                                            #
# --------------------------------------------------------------------- #


def test_parse_excludes():
    assert rw._parse_excludes([f"{IMG}:3", f"{IMG}:11"]) == {
        (IMG, 3),
        (IMG, 11),
    }


@pytest.mark.parametrize("bad", ["no-colon", f"{IMG}:", f"{IMG}:abc", ":3"])
def test_parse_excludes_rejects_malformed(bad):
    with pytest.raises(ValueError):
        rw._parse_excludes([bad])


# --------------------------------------------------------------------- #
# --force guardrails                                                    #
# --------------------------------------------------------------------- #


def test_force_with_apply_all_is_refused():
    """Unfiltered + unfiltered is the one combination with no brakes."""
    assert (
        rw.main(
            [
                "--table",
                "t",
                "--apply",
                "--apply-all",
                "--force",
            ]
        )
        == 2
    )


def test_force_without_image_filter_is_refused():
    assert rw.main(["--table", "t", "--apply", "--force"]) == 2


def test_apply_without_filter_or_apply_all_is_refused():
    assert rw.main(["--table", "t", "--apply"]) == 2


def test_force_parses_with_an_image_filter(monkeypatch):
    """--force + a named image is the supported shape."""
    args = rw.parse_args(
        ["--table", "t", "--apply", "--force", "--image-id", IMG]
    )
    assert args.force and args.image_ids == [IMG]


def test_close_is_symmetric():
    assert rw._close(0.32, 0.33, 0.05) == rw._close(0.33, 0.32, 0.05)
    assert not rw._close(0.32, 1.0 / 0.32, 0.05)
    assert math.isclose(rw._aspect(866, 2688), 866 / 2688)
