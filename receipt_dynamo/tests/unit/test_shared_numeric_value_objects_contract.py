"""Numeric-boundary and round-trip tests for shared entity value objects."""

from math import inf, nan, pi

import pytest

from receipt_dynamo.entities.util import (
    assert_type,
    assert_valid_bounding_box,
    assert_valid_point,
    validate_confidence_range,
    validate_non_negative_int,
    validate_positive_dimensions,
    validate_positive_int,
)
from receipt_dynamo.entities.value_objects import (
    Angle,
    BoundingBox,
    CDNVariants,
    Corners,
    Point,
    S3Location,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "validator",
    [
        lambda: assert_type("count", True, int),
        lambda: assert_type("score", False, (int, float)),
        lambda: validate_positive_int("receipt_id", True),
        lambda: validate_non_negative_int("line_id", False),
        lambda: validate_positive_dimensions(True, 10),
        lambda: validate_positive_dimensions(10, True),
        lambda: validate_confidence_range("confidence", True),
        lambda: assert_valid_point({"x": True, "y": 0}),
        lambda: assert_valid_bounding_box(
            {"x": 0, "y": 0, "width": True, "height": 1}
        ),
    ],
)
def test_numeric_validators_reject_bool_values(validator):
    with pytest.raises((TypeError, ValueError)):
        validator()


@pytest.mark.parametrize(
    "factory",
    [
        lambda: Point(True, 0),
        lambda: Point(0, False),
        lambda: Point.from_dict({"x": True, "y": 0}),
        lambda: BoundingBox(True, 0, 1, 1),
        lambda: BoundingBox(0, 0, False, 1),
        lambda: BoundingBox.from_dict(
            {"x": 0, "y": 0, "width": True, "height": 1}
        ),
        lambda: Angle(True, 0),
        lambda: Angle(0, False),
        lambda: Angle.from_degrees(True),
        lambda: Angle.from_radians(False),
    ],
)
def test_numeric_value_objects_reject_bool_values(factory):
    with pytest.raises(ValueError, match="must be numeric"):
        factory()


@pytest.mark.parametrize(
    "validator",
    [
        lambda: assert_valid_point({"x": inf, "y": 0}),
        lambda: assert_valid_bounding_box(
            {"x": 0, "y": 0, "width": nan, "height": 1}
        ),
        lambda: validate_confidence_range("confidence", nan),
        lambda: validate_confidence_range("confidence", inf),
    ],
)
def test_numeric_validators_reject_non_finite_values(validator):
    with pytest.raises(ValueError):
        validator()


@pytest.mark.parametrize(
    "factory",
    [
        lambda: Point(inf, 0),
        lambda: BoundingBox(0, 0, nan, 1),
        lambda: Angle(inf, 0),
        lambda: Angle(0, nan),
    ],
)
def test_numeric_value_objects_reject_non_finite_values(factory):
    with pytest.raises(ValueError, match="must be numeric"):
        factory()


@pytest.mark.parametrize(
    "point",
    [Point(0, 0), Point(-1.25, 2.5), Point(1, 2)],
)
def test_point_exact_round_trips(point):
    assert Point.from_dict(point.to_dict()) == point
    assert Point.from_dynamodb(point.to_dynamodb()) == point


@pytest.mark.parametrize(
    "box",
    [
        BoundingBox(0, 0, 1, 1),
        BoundingBox(-1.25, 2.5, 3.75, 4.125),
    ],
)
def test_bounding_box_exact_round_trips(box):
    assert BoundingBox.from_dict(box.to_dict()) == box
    assert BoundingBox.from_dynamodb(box.to_dynamodb()) == box


def test_bounding_box_derived_geometry():
    box = BoundingBox(1, 2, 4, 6)

    assert box.right == 5
    assert box.bottom == 8
    assert box.center == Point(3, 5)
    assert box.area == 24
    assert box.contains_point(3, 5)
    assert not box.contains_point(6, 5)


def test_corners_exact_round_trip_and_geometry():
    corners = Corners(
        top_left=Point(1, 2),
        top_right=Point(5, 2),
        bottom_left=Point(1, 8),
        bottom_right=Point(5, 8),
    )

    assert Corners.from_dynamodb(corners.to_dynamodb()) == corners
    assert Corners.from_dicts(**corners.to_dict()) == corners
    assert corners.centroid == Point(3, 5)
    assert corners.to_bounding_box() == BoundingBox(1, 2, 4, 6)


def test_angle_factories_and_normalization_keep_units_in_sync():
    angle = Angle.from_degrees(540).normalized()

    assert angle.degrees == pytest.approx(180)
    assert angle.radians == pytest.approx(pi)
    assert Angle.from_radians(pi).degrees == pytest.approx(180)
    assert Angle.from_degrees(-540).normalized().degrees == pytest.approx(-180)


def test_s3_location_exact_round_trip():
    location = S3Location(bucket="receipt-bucket", key="path/to/image.png")

    assert S3Location.from_dynamodb(location.to_dynamodb()) == location
    assert location.uri == "s3://receipt-bucket/path/to/image.png"


@pytest.mark.parametrize(
    "factory",
    [lambda: S3Location(1, "key"), lambda: S3Location("bucket", 1)],
)
def test_s3_location_rejects_non_string_fields(factory):
    with pytest.raises(ValueError, match="must be string"):
        factory()


def test_cdn_variants_exact_round_trip():
    variants = CDNVariants(
        original="original.png",
        webp="original.webp",
        thumbnail_avif="thumbnail.avif",
        medium="medium.png",
    )

    restored = CDNVariants.from_dynamodb(variants.to_dynamodb())

    assert restored == variants
    assert restored.has_any_variant()
    assert restored.get_best_variant("original") == "original.webp"
    assert restored.get_best_variant("thumbnail") == "thumbnail.avif"
    assert restored.get_best_variant("small") is None
    assert restored.get_best_variant("medium") == "medium.png"
    assert restored.get_best_variant("unsupported") is None


def test_cdn_variants_reject_non_string_fields():
    with pytest.raises(ValueError, match="original must be string or None"):
        CDNVariants(original=1)
