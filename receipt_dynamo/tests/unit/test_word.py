import math

import pytest

from receipt_dynamo import Word, item_to_word


@pytest.fixture
def example_word():
    return Word(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=2,
        word_id=3,
        text="test_string",
        bounding_box={
            "x": 10.0,
            "y": 20.0,
            "width": 5.0,
            "height": 2.0,
        },
        top_right={"x": 15.0, "y": 20.0},
        top_left={"x": 10.0, "y": 20.0},
        bottom_right={"x": 15.0, "y": 22.0},
        bottom_left={"x": 10.0, "y": 22.0},
        angle_degrees=1.0,
        angle_radians=5.0,
        confidence=0.90,
    )


def create_test_word() -> Word:
    """
    A helper function that returns a Word object
    with easily verifiable points for testing.
    """
    return Word(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        word_id=1,
        text="Hello",
        line_id=1,
        bounding_box={"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0},
        top_right={"x": 15.0, "y": 20.0},
        top_left={"x": 10.0, "y": 20.0},
        bottom_right={"x": 15.0, "y": 22.0},
        bottom_left={"x": 10.0, "y": 22.0},
        angle_degrees=0.0,
        angle_radians=0.0,
        confidence=1.0,
    )


@pytest.mark.unit
def test_word_init_valid(example_word):
    assert example_word.image_id == ("3f52804b-2fad-4e00-92c8-b593da3a8ed3")
    assert example_word.line_id == 2
    assert example_word.word_id == 3
    assert example_word.text == "test_string"
    assert example_word.bounding_box == {
        "x": 10.0,
        "y": 20.0,
        "width": 5.0,
        "height": 2.0,
    }
    assert example_word.top_right == {"x": 15.0, "y": 20.0}
    assert example_word.top_left == {"x": 10.0, "y": 20.0}
    assert example_word.bottom_right == {"x": 15.0, "y": 22.0}
    assert example_word.bottom_left == {"x": 10.0, "y": 22.0}
    assert example_word.angle_degrees == 1
    assert example_word.angle_radians == 5
    assert example_word.confidence == 0.90


@pytest.mark.unit
def test_word_init_invalid_uuid():
    """Test that Word raises a ValueError if the image_id is not a string"""
    with pytest.raises(ValueError, match="uuid must be a string"):
        Word(
            1,
            2,
            3,
            "test_string",
            {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0},
            {"x": 15.0, "y": 20.0},
            {"x": 10.0, "y": 20.0},
            {"x": 15.0, "y": 22.0},
            {"x": 10.0, "y": 22.0},
            1.0,
            5.0,
            0.90,
        )
    with pytest.raises(ValueError, match="uuid must be a valid UUID"):
        Word(
            "bad-uuid",
            2,
            3,
            "test_string",
            {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0},
            {"x": 15.0, "y": 20.0},
            {"x": 10.0, "y": 20.0},
            {"x": 15.0, "y": 22.0},
            {"x": 10.0, "y": 22.0},
            1.0,
            5.0,
            0.90,
        )


@pytest.mark.unit
def test_word_init_invalid_line_id():
    """Test that Word raises a ValueError if the line_id is not an integer"""
    with pytest.raises(ValueError, match="line_id must be int, got"):
        Word(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "bad-line-id",
            3,
            "test_string",
            {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0},
            {"x": 15.0, "y": 20.0},
            {"x": 10.0, "y": 20.0},
            {"x": 15.0, "y": 22.0},
            {"x": 10.0, "y": 22.0},
            1.0,
            5.0,
            0.90,
        )
    with pytest.raises(ValueError, match="line_id must be positive"):
        Word(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            -1,
            3,
            "test_string",
            {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0},
            {"x": 15.0, "y": 20.0},
            {"x": 10.0, "y": 20.0},
            {"x": 15.0, "y": 22.0},
            {"x": 10.0, "y": 22.0},
            1.0,
            5.0,
            0.90,
        )


@pytest.mark.unit
def test_word_init_invalid_id():
    """Test that Word raises a ValueError if the id is not an integer"""
    with pytest.raises(ValueError, match="word_id must be int, got"):
        Word(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            2,
            "bad-id",
            "test_string",
            {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0},
            {"x": 15.0, "y": 20.0},
            {"x": 10.0, "y": 20.0},
            {"x": 15.0, "y": 22.0},
            {"x": 10.0, "y": 22.0},
            1.0,
            5.0,
            0.90,
        )
    with pytest.raises(ValueError, match="id must be positive"):
        Word(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            2,
            -1,
            "test_string",
            {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0},
            {"x": 15.0, "y": 20.0},
            {"x": 10.0, "y": 20.0},
            {"x": 15.0, "y": 22.0},
            {"x": 10.0, "y": 22.0},
            1.0,
            5.0,
            0.90,
        )


@pytest.mark.unit
def test_word_init_invalid_text():
    """Test that Word raises a ValueError if the text is not a string"""
    with pytest.raises(ValueError, match="text must be str, got"):
        Word(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            2,
            3,
            1,
            {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0},
            {"x": 15.0, "y": 20.0},
            {"x": 10.0, "y": 20.0},
            {"x": 15.0, "y": 22.0},
            {"x": 10.0, "y": 22.0},
            1.0,
            5.0,
            0.90,
        )


@pytest.mark.unit
def test_word_init_invalid_bounding_box():
    """Test that Word raises a ValueError if the bounding_box is not a dict"""
    with pytest.raises(ValueError, match="bounding_box must be a dict"):
        Word(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            2,
            3,
            "test_string",
            1,
            {"x": 15.0, "y": 20.0},
            {"x": 10.0, "y": 20.0},
            {"x": 15.0, "y": 22.0},
            {"x": 10.0, "y": 22.0},
            1.0,
            5.0,
            0.90,
        )
    with pytest.raises(
        ValueError, match="bounding_box must contain the key 'x'"
    ):
        Word(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            2,
            3,
            "test_string",
            {"bad": 1},
            {"x": 15.0, "y": 20.0},
            {"x": 10.0, "y": 20.0},
            {"x": 15.0, "y": 22.0},
            {"x": 10.0, "y": 22.0},
            1.0,
            5.0,
            0.90,
        )


@pytest.mark.unit
def test_word_init_invalid_corners():
    """Test that Word raises a ValueError if the corners are not dicts"""
    with pytest.raises(ValueError, match="point must be a dictionary"):
        Word(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            2,
            3,
            "test_string",
            {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0},
            1,
            {"x": 10.0, "y": 20.0},
            {"x": 15.0, "y": 22.0},
            {"x": 10.0, "y": 22.0},
            1.0,
            5.0,
            0.90,
        )
    with pytest.raises(ValueError, match="point must contain the key 'x'"):
        Word(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            2,
            3,
            "test_string",
            {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0},
            {"bad": 1},
            {"x": 10.0, "y": 20.0},
            {"x": 15.0, "y": 22.0},
            {"x": 10.0, "y": 22.0},
            1.0,
            5.0,
            0.90,
        )


@pytest.mark.unit
def test_word_init_invalid_angle():
    """Test that Word raises a ValueError if the angle is not a float"""
    with pytest.raises(
        ValueError, match="angle_degrees must be float, int, got"
    ):
        Word(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            2,
            3,
            "test_string",
            {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0},
            {"x": 15.0, "y": 20.0},
            {"x": 10.0, "y": 20.0},
            {"x": 15.0, "y": 22.0},
            {"x": 10.0, "y": 22.0},
            "bad",
            5.0,
            0.90,
        )
    with pytest.raises(
        ValueError, match="angle_radians must be float, int, got"
    ):
        Word(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            2,
            3,
            "test_string",
            {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0},
            {"x": 15.0, "y": 20.0},
            {"x": 10.0, "y": 20.0},
            {"x": 15.0, "y": 22.0},
            {"x": 10.0, "y": 22.0},
            1.0,
            "bad",
            0.90,
        )


@pytest.mark.unit
def test_word_init_invalid_confidence():
    """Test that Word raises a ValueError if the confidence is not a float"""
    with pytest.raises(ValueError, match="confidence must be float, got"):
        Word(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            2,
            3,
            "test_string",
            {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0},
            {"x": 15.0, "y": 20.0},
            {"x": 10.0, "y": 20.0},
            {"x": 15.0, "y": 22.0},
            {"x": 10.0, "y": 22.0},
            1.0,
            5.0,
            "bad",
        )
    word = Word(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        2,
        3,
        "test_string",
        {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0},
        {"x": 15.0, "y": 20.0},
        {"x": 10.0, "y": 20.0},
        {"x": 15.0, "y": 22.0},
        {"x": 10.0, "y": 22.0},
        1.0,
        5.0,
        1,
    )
    assert word.confidence == 1.0
    with pytest.raises(ValueError, match="confidence must be between 0 and 1"):
        Word(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            2,
            3,
            "test_string",
            {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0},
            {"x": 15.0, "y": 20.0},
            {"x": 10.0, "y": 20.0},
            {"x": 15.0, "y": 22.0},
            {"x": 10.0, "y": 22.0},
            1.0,
            5.0,
            1.1,
        )


@pytest.mark.unit
def test_word_init_invalid_extracted_data():
    """Test that Word raises a ValueError if the extracted_data is not a dict"""
    with pytest.raises(ValueError, match="extracted_data must be dict, got"):
        Word(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=2,
            word_id=3,
            text="test_string",
            bounding_box={"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0},
            top_right={"x": 15.0, "y": 20.0},
            top_left={"x": 10.0, "y": 20.0},
            bottom_right={"x": 15.0, "y": 22.0},
            bottom_left={"x": 10.0, "y": 22.0},
            angle_degrees=1.0,
            angle_radians=5.0,
            confidence=0.90,
            extracted_data=1,
        )
    word = Word(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=2,
        word_id=3,
        text="test_string",
        bounding_box={"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0},
        top_right={"x": 15.0, "y": 20.0},
        top_left={"x": 10.0, "y": 20.0},
        bottom_right={"x": 15.0, "y": 22.0},
        bottom_left={"x": 10.0, "y": 22.0},
        angle_degrees=1.0,
        angle_radians=5.0,
        confidence=0.90,
        extracted_data={"type": "test_type", "value": "test_value"},
    )
    assert word.extracted_data == {"type": "test_type", "value": "test_value"}


@pytest.mark.unit
def test_word_key(example_word):
    """Test the Word key method"""
    assert example_word.key == {
        "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "LINE#00002#WORD#00003"},
    }


@pytest.mark.unit
def test_word_to_item(example_word):
    """Test the Word to_item method"""
    item = example_word.to_item()
    assert item["PK"] == {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"}
    assert item["SK"] == {"S": "LINE#00002#WORD#00003"}
    assert item["TYPE"] == {"S": "WORD"}
    assert item["text"] == {"S": "test_string"}
    assert item["bounding_box"] == {
        "M": {
            "height": {"N": "2.00000000000000000000"},
            "width": {"N": "5.00000000000000000000"},
            "x": {"N": "10.00000000000000000000"},
            "y": {"N": "20.00000000000000000000"},
        }
    }
    assert item["top_right"] == {
        "M": {
            "x": {"N": "15.00000000000000000000"},
            "y": {"N": "20.00000000000000000000"},
        }
    }
    assert item["top_left"] == {
        "M": {
            "x": {"N": "10.00000000000000000000"},
            "y": {"N": "20.00000000000000000000"},
        }
    }
    assert item["bottom_right"] == {
        "M": {
            "x": {"N": "15.00000000000000000000"},
            "y": {"N": "22.00000000000000000000"},
        }
    }
    assert item["bottom_left"] == {
        "M": {
            "x": {"N": "10.00000000000000000000"},
            "y": {"N": "22.00000000000000000000"},
        }
    }
    assert item["angle_degrees"] == {"N": "1.000000000000000000"}
    assert item["angle_radians"] == {"N": "5.000000000000000000"}
    assert item["confidence"] == {"N": "0.90"}


@pytest.mark.unit
def test_word_calculate_centroid(example_word):
    """Test the Word calculate_centroid method"""
    assert example_word.calculate_centroid() == (12.5, 21.0)


@pytest.mark.unit
@pytest.mark.parametrize(
    "dx, dy",
    [
        (5, -2),
        (0, 0),
        (-3, 10),
    ],
)
def test_word_translate(dx, dy):
    """
    Test that Word.translate(dx, dy) shifts the corner points and updates the
    bounding_box accordingly, while leaving the angles unchanged.
    """
    word = create_test_word()

    orig_top_right = word.top_right.copy()
    orig_top_left = word.top_left.copy()
    orig_bottom_right = word.bottom_right.copy()
    orig_bottom_left = word.bottom_left.copy()
    orig_bb = word.bounding_box.copy()

    # Apply translation
    word.translate(dx, dy)

    # Check that the corners are translated correctly.
    assert word.top_right["x"] == pytest.approx(orig_top_right["x"] + dx)
    assert word.top_right["y"] == pytest.approx(orig_top_right["y"] + dy)
    assert word.top_left["x"] == pytest.approx(orig_top_left["x"] + dx)
    assert word.top_left["y"] == pytest.approx(orig_top_left["y"] + dy)
    assert word.bottom_right["x"] == pytest.approx(orig_bottom_right["x"] + dx)
    assert word.bottom_right["y"] == pytest.approx(orig_bottom_right["y"] + dy)
    assert word.bottom_left["x"] == pytest.approx(orig_bottom_left["x"] + dx)
    assert word.bottom_left["y"] == pytest.approx(orig_bottom_left["y"] + dy)

    # Now expect that the bounding_box is updated as well.
    expected_bb = {
        "x": orig_bb["x"] + dx,
        "y": orig_bb["y"] + dy,
        "width": orig_bb["width"],
        "height": orig_bb["height"],
    }
    assert word.bounding_box == expected_bb

    # Angles should remain unchanged.
    assert word.angle_degrees == 0.0
    assert word.angle_radians == 0.0


@pytest.mark.unit
@pytest.mark.parametrize(
    "sx, sy",
    [
        (2, 3),
        (1, 1),
        (0.5, 2),
    ],
)
def test_word_scale(sx, sy):
    """
    Test that Word.scale(sx, sy) scales corner points
    and bounding_box, but does NOT modify angles.
    """
    word = create_test_word()

    orig_top_right = word.top_right.copy()
    orig_top_left = word.top_left.copy()
    orig_bottom_right = word.bottom_right.copy()
    orig_bottom_left = word.bottom_left.copy()
    orig_bb = word.bounding_box.copy()

    word.scale(sx, sy)

    # Check corners
    assert word.top_right["x"] == pytest.approx(orig_top_right["x"] * sx)
    assert word.top_right["y"] == pytest.approx(orig_top_right["y"] * sy)
    assert word.top_left["x"] == pytest.approx(orig_top_left["x"] * sx)
    assert word.top_left["y"] == pytest.approx(orig_top_left["y"] * sy)
    assert word.bottom_right["x"] == pytest.approx(orig_bottom_right["x"] * sx)
    assert word.bottom_right["y"] == pytest.approx(orig_bottom_right["y"] * sy)
    assert word.bottom_left["x"] == pytest.approx(orig_bottom_left["x"] * sx)
    assert word.bottom_left["y"] == pytest.approx(orig_bottom_left["y"] * sy)

    # Check bounding_box
    assert word.bounding_box["x"] == pytest.approx(orig_bb["x"] * sx)
    assert word.bounding_box["y"] == pytest.approx(orig_bb["y"] * sy)
    assert word.bounding_box["width"] == pytest.approx(orig_bb["width"] * sx)
    assert word.bounding_box["height"] == pytest.approx(orig_bb["height"] * sy)

    assert word.angle_degrees == 0.0
    assert word.angle_radians == 0.0


@pytest.mark.unit
@pytest.mark.parametrize(
    "angle, use_radians, should_raise",
    [
        (90, False, False),
        (-90, False, False),
        (45, False, False),
        (0, False, False),
        (91, False, True),
        (-91, False, True),
        (180, False, True),
        (math.pi / 2, True, False),
        (-math.pi / 2, True, False),
        (0, True, False),
        (0.5, True, False),
        (math.pi / 2 + 0.01, True, True),
        (-math.pi / 2 - 0.01, True, True),
        (math.pi, True, True),
    ],
)
def test_word_rotate_limited_range(angle, use_radians, should_raise):
    """
    Test that Word.rotate(angle, origin_x, origin_y, use_radians) rotates the
    word's corners, recalculates the axis-aligned bounding box from the
    rotated corners, and updates the angle values accordingly. If the angle is
    outside the allowed range, a ValueError is raised and no changes are made.
    """
    word = create_test_word()
    orig_corners = {
        "top_right": word.top_right.copy(),
        "top_left": word.top_left.copy(),
        "bottom_right": word.bottom_right.copy(),
        "bottom_left": word.bottom_left.copy(),
    }
    orig_angle_degrees = word.angle_degrees
    orig_angle_radians = word.angle_radians

    if should_raise:
        with pytest.raises(ValueError):
            word.rotate(angle, 0, 0, use_radians=use_radians)
        assert word.top_right == orig_corners["top_right"]
        assert word.top_left == orig_corners["top_left"]
        assert word.bottom_right == orig_corners["bottom_right"]
        assert word.bottom_left == orig_corners["bottom_left"]
        assert word.angle_degrees == orig_angle_degrees
        assert word.angle_radians == orig_angle_radians
    else:
        theta = angle if use_radians else math.radians(angle)

        def rotate_point(px, py, ox, oy, theta):
            tx, ty = px - ox, py - oy
            rx = tx * math.cos(theta) - ty * math.sin(theta)
            ry = tx * math.sin(theta) + ty * math.cos(theta)
            return rx + ox, ry + oy

        expected_top_right = {}
        expected_top_left = {}
        expected_bottom_right = {}
        expected_bottom_left = {}

        expected_top_right["x"], expected_top_right["y"] = rotate_point(
            orig_corners["top_right"]["x"],
            orig_corners["top_right"]["y"],
            0,
            0,
            theta,
        )
        expected_top_left["x"], expected_top_left["y"] = rotate_point(
            orig_corners["top_left"]["x"],
            orig_corners["top_left"]["y"],
            0,
            0,
            theta,
        )
        expected_bottom_right["x"], expected_bottom_right["y"] = rotate_point(
            orig_corners["bottom_right"]["x"],
            orig_corners["bottom_right"]["y"],
            0,
            0,
            theta,
        )
        expected_bottom_left["x"], expected_bottom_left["y"] = rotate_point(
            orig_corners["bottom_left"]["x"],
            orig_corners["bottom_left"]["y"],
            0,
            0,
            theta,
        )

        word.rotate(angle, 0, 0, use_radians=use_radians)

        assert word.top_right["x"] == pytest.approx(
            expected_top_right["x"], rel=1e-6
        )
        assert word.top_right["y"] == pytest.approx(
            expected_top_right["y"], rel=1e-6
        )
        assert word.top_left["x"] == pytest.approx(
            expected_top_left["x"], rel=1e-6
        )
        assert word.top_left["y"] == pytest.approx(
            expected_top_left["y"], rel=1e-6
        )
        assert word.bottom_right["x"] == pytest.approx(
            expected_bottom_right["x"], rel=1e-6
        )
        assert word.bottom_right["y"] == pytest.approx(
            expected_bottom_right["y"], rel=1e-6
        )
        assert word.bottom_left["x"] == pytest.approx(
            expected_bottom_left["x"], rel=1e-6
        )
        assert word.bottom_left["y"] == pytest.approx(
            expected_bottom_left["y"], rel=1e-6
        )

        xs = [
            expected_top_right["x"],
            expected_top_left["x"],
            expected_bottom_right["x"],
            expected_bottom_left["x"],
        ]
        ys = [
            expected_top_right["y"],
            expected_top_left["y"],
            expected_bottom_right["y"],
            expected_bottom_left["y"],
        ]
        expected_bb = {
            "x": min(xs),
            "y": min(ys),
            "width": max(xs) - min(xs),
            "height": max(ys) - min(ys),
        }

        assert word.bounding_box["x"] == pytest.approx(
            expected_bb["x"], rel=1e-6
        )
        assert word.bounding_box["y"] == pytest.approx(
            expected_bb["y"], rel=1e-6
        )
        assert word.bounding_box["width"] == pytest.approx(
            expected_bb["width"], rel=1e-6
        )
        assert word.bounding_box["height"] == pytest.approx(
            expected_bb["height"], rel=1e-6
        )

        if use_radians:
            expected_angle_radians = orig_angle_radians + angle
            expected_angle_degrees = orig_angle_degrees + (
                angle * 180.0 / math.pi
            )
        else:
            expected_angle_degrees = orig_angle_degrees + angle
            expected_angle_radians = orig_angle_radians + math.radians(angle)
        assert word.angle_radians == pytest.approx(
            expected_angle_radians, abs=1e-9
        )
        assert word.angle_degrees == pytest.approx(
            expected_angle_degrees, abs=1e-9
        )


@pytest.mark.parametrize(
    "shx, shy, pivot_x, pivot_y, expected_corners",
    [
        (
            0.2,
            0.0,
            10.0,
            20.0,
            {
                "top_right": {"x": 15.0 + 0.2 * (20.0 - 20.0), "y": 20.0},
                "top_left": {"x": 10.0 + 0.2 * (20.0 - 20.0), "y": 20.0},
                "bottom_right": {"x": 15.0 + 0.2 * (22.0 - 20.0), "y": 22.0},
                "bottom_left": {"x": 10.0 + 0.2 * (22.0 - 20.0), "y": 22.0},
            },
        ),
        (
            0.0,
            0.2,
            10.0,
            20.0,
            {
                "top_right": {
                    "x": 15.0,
                    "y": 20.0 + 0.2 * (15.0 - 10.0),
                },
                "top_left": {"x": 10.0, "y": 20.0 + 0.2 * (10.0 - 10.0)},
                "bottom_right": {
                    "x": 15.0,
                    "y": 22.0 + 0.2 * (15.0 - 10.0),
                },
                "bottom_left": {
                    "x": 10.0,
                    "y": 22.0 + 0.2 * (10.0 - 10.0),
                },
            },
        ),
        (
            0.1,
            0.1,
            12.0,
            21.0,
            {
                "top_right": {
                    "x": 15.0 + 0.1 * (20.0 - 21.0),
                    "y": 20.0 + 0.1 * (15.0 - 12.0),
                },
                "top_left": {
                    "x": 10.0 + 0.1 * (20.0 - 21.0),
                    "y": 20.0 + 0.1 * (10.0 - 12.0),
                },
                "bottom_right": {
                    "x": 15.0 + 0.1 * (22.0 - 21.0),
                    "y": 22.0 + 0.1 * (15.0 - 12.0),
                },
                "bottom_left": {
                    "x": 10.0 + 0.1 * (22.0 - 21.0),
                    "y": 22.0 + 0.1 * (10.0 - 12.0),
                },
            },
        ),
    ],
)
def test_word_shear(shx, shy, pivot_x, pivot_y, expected_corners):
    """
    Test that Word.shear(shx, shy, pivot_x, pivot_y) correctly shears the
    word's corner points, updates the bounding box accordingly, and leaves
    the angles unchanged.
    """
    word = create_test_word()

    word.shear(shx, shy, pivot_x, pivot_y)

    for corner_name in [
        "top_right",
        "top_left",
        "bottom_right",
        "bottom_left",
    ]:
        for coord in ["x", "y"]:
            expected_value = expected_corners[corner_name][coord]
            actual_value = word.__dict__[corner_name][coord]
            assert actual_value == pytest.approx(
                expected_value
            ), f"{corner_name} {coord} expected {expected_value},"
            f" got {actual_value}"

    xs = [
        word.top_right["x"],
        word.top_left["x"],
        word.bottom_right["x"],
        word.bottom_left["x"],
    ]
    ys = [
        word.top_right["y"],
        word.top_left["y"],
        word.bottom_right["y"],
        word.bottom_left["y"],
    ]
    expected_bb = {
        "x": min(xs),
        "y": min(ys),
        "width": max(xs) - min(xs),
        "height": max(ys) - min(ys),
    }
    assert word.bounding_box["x"] == pytest.approx(expected_bb["x"])
    assert word.bounding_box["y"] == pytest.approx(expected_bb["y"])
    assert word.bounding_box["width"] == pytest.approx(expected_bb["width"])
    assert word.bounding_box["height"] == pytest.approx(expected_bb["height"])


@pytest.mark.unit
def test_word_warp_affine_normalized_forward():
    """
    Test that warp_affine_normalized_forward(a, b, c, d, e, f) applies a
    normalized affine transform to all corners of the Word by operating on
    normalized coordinates (relative to the original bounding box),
    recalculates the bounding box correctly, and leaves the angle unchanged.
    """
    word = create_test_word()
    a, b, c = 1.0, 0.0, 0.1
    d, e, f = 0.0, 1.0, 0.2

    expected_top_left = {"x": 10.02, "y": 20.1}
    expected_top_right = {"x": 15.02, "y": 20.1}
    expected_bottom_left = {"x": 10.02, "y": 22.1}
    expected_bottom_right = {"x": 15.02, "y": 22.1}
    expected_bb = {"x": 10.02, "y": 20.1, "width": 5.0, "height": 2.0}

    word.warp_affine_normalized_forward(a, b, c, d, e, f, 5.0, 2.0, 5.0, 2.0)

    assert word.top_left["x"] == pytest.approx(expected_top_left["x"])
    assert word.top_left["y"] == pytest.approx(expected_top_left["y"])
    assert word.top_right["x"] == pytest.approx(expected_top_right["x"])
    assert word.top_right["y"] == pytest.approx(expected_top_right["y"])
    assert word.bottom_left["x"] == pytest.approx(expected_bottom_left["x"])
    assert word.bottom_left["y"] == pytest.approx(expected_bottom_left["y"])
    assert word.bottom_right["x"] == pytest.approx(expected_bottom_right["x"])
    assert word.bottom_right["y"] == pytest.approx(expected_bottom_right["y"])

    assert word.bounding_box["x"] == pytest.approx(expected_bb["x"])
    assert word.bounding_box["y"] == pytest.approx(expected_bb["y"])
    assert word.bounding_box["width"] == pytest.approx(expected_bb["width"])
    assert word.bounding_box["height"] == pytest.approx(expected_bb["height"])

    assert word.angle_degrees == pytest.approx(0.0)
    assert word.angle_radians == pytest.approx(0.0)


def test_word_rotate_90_ccw_in_place():
    """
    Test the rotate_90_ccw_in_place method of the Word class.
    """
    word = create_test_word()
    word.angle_degrees = 0.0
    word.angle_radians = 0.0
    old_w = 100
    old_h = 200
    word.rotate_90_ccw_in_place(old_w, old_h)
    expected_top_left = {"x": 20.0, "y": -9.0}
    expected_top_right = {"x": 20.0, "y": -14.0}
    expected_bottom_right = {"x": 22.0, "y": -14.0}
    expected_bottom_left = {"x": 22.0, "y": -9.0}
    expected_bb = {"x": 20.0, "y": -14.0, "width": 2.0, "height": 5.0}
    expected_angle_degrees = 90.0
    expected_angle_radians = math.pi / 2

    assert word.top_left["x"] == pytest.approx(expected_top_left["x"])
    assert word.top_left["y"] == pytest.approx(expected_top_left["y"])
    assert word.top_right["x"] == pytest.approx(expected_top_right["x"])
    assert word.top_right["y"] == pytest.approx(expected_top_right["y"])
    assert word.bottom_right["x"] == pytest.approx(expected_bottom_right["x"])
    assert word.bottom_right["y"] == pytest.approx(expected_bottom_right["y"])
    assert word.bottom_left["x"] == pytest.approx(expected_bottom_left["x"])
    assert word.bottom_left["y"] == pytest.approx(expected_bottom_left["y"])

    assert word.bounding_box["x"] == pytest.approx(expected_bb["x"])
    assert word.bounding_box["y"] == pytest.approx(expected_bb["y"])
    assert word.bounding_box["width"] == pytest.approx(expected_bb["width"])
    assert word.bounding_box["height"] == pytest.approx(expected_bb["height"])

    assert word.angle_degrees == pytest.approx(expected_angle_degrees)
    assert word.angle_radians == pytest.approx(expected_angle_radians)


@pytest.mark.unit
def test_word_warp_affine():
    """
    Test that warp_affine(a, b, c, d, e, f) applies the affine transform
    x' = a*x + b*y + c, y' = d*x + e*y + f to all corners, recomputes the
    bounding box, and updates the angle accordingly.
    """
    word = create_test_word()
    a, b, c = 2.0, 0.0, 3.0
    d, e, f = 0.0, 2.0, 4.0

    expected_top_left = {"x": 2 * 10.0 + 3, "y": 2 * 20.0 + 4}
    expected_top_right = {"x": 2 * 15.0 + 3, "y": 2 * 20.0 + 4}
    expected_bottom_left = {"x": 2 * 10.0 + 3, "y": 2 * 22.0 + 4}
    expected_bottom_right = {"x": 2 * 15.0 + 3, "y": 2 * 22.0 + 4}

    xs = [
        expected_top_left["x"],
        expected_top_right["x"],
        expected_bottom_left["x"],
        expected_bottom_right["x"],
    ]
    ys = [
        expected_top_left["y"],
        expected_top_right["y"],
        expected_bottom_left["y"],
        expected_bottom_right["y"],
    ]
    expected_bb = {
        "x": min(xs),
        "y": min(ys),
        "width": max(xs) - min(xs),
        "height": max(ys) - min(ys),
    }
    word.warp_affine(a, b, c, d, e, f)
    assert word.top_left["x"] == pytest.approx(expected_top_left["x"])
    assert word.top_left["y"] == pytest.approx(expected_top_left["y"])
    assert word.top_right["x"] == pytest.approx(expected_top_right["x"])
    assert word.top_right["y"] == pytest.approx(expected_top_right["y"])
    assert word.bottom_left["x"] == pytest.approx(expected_bottom_left["x"])
    assert word.bottom_left["y"] == pytest.approx(expected_bottom_left["y"])
    assert word.bottom_right["x"] == pytest.approx(expected_bottom_right["x"])
    assert word.bottom_right["y"] == pytest.approx(expected_bottom_right["y"])
    assert word.bounding_box["x"] == pytest.approx(expected_bb["x"])
    assert word.bounding_box["y"] == pytest.approx(expected_bb["y"])
    assert word.bounding_box["width"] == pytest.approx(expected_bb["width"])
    assert word.bounding_box["height"] == pytest.approx(expected_bb["height"])
    assert word.angle_radians == pytest.approx(0.0)
    assert word.angle_degrees == pytest.approx(0.0)


@pytest.mark.unit
def test_word_repr(example_word):
    """Test the Word __repr__ method"""
    assert (
        repr(example_word)
        == "Word(word_id=3, text='test_string', bounding_box={'x': 10.0, "
        "'y': 20.0, 'width': 5.0, 'height': 2.0}, top_right={'x': 15.0, "
        "'y': 20.0}, top_left={'x': 10.0, 'y': 20.0}, bottom_right={'x': "
        "15.0, 'y': 22.0}, bottom_left={'x': 10.0, 'y': 22.0}, angle_degrees="
        "1.0, angle_radians=5.0, confidence=0.9)"
    )


@pytest.mark.unit
def test_word_iter(example_word):
    """Test the Word __iter__ method"""
    word_dict = dict(example_word)
    expected_keys = {
        "image_id",
        "line_id",
        "word_id",
        "text",
        "bounding_box",
        "top_right",
        "top_left",
        "bottom_right",
        "bottom_left",
        "angle_degrees",
        "angle_radians",
        "confidence",
        "extracted_data",
    }
    assert set(word_dict.keys()) == expected_keys
    assert word_dict["image_id"] == ("3f52804b-2fad-4e00-92c8-b593da3a8ed3")
    assert word_dict["line_id"] == 2
    assert word_dict["word_id"] == 3
    assert word_dict["text"] == "test_string"
    assert word_dict["bounding_box"] == {
        "x": 10.0,
        "y": 20.0,
        "width": 5.0,
        "height": 2.0,
    }
    assert word_dict["top_right"] == {"x": 15.0, "y": 20.0}
    assert word_dict["top_left"] == {"x": 10.0, "y": 20.0}
    assert word_dict["bottom_right"] == {"x": 15.0, "y": 22.0}
    assert word_dict["bottom_left"] == {"x": 10.0, "y": 22.0}
    assert word_dict["angle_degrees"] == 1
    assert word_dict["angle_radians"] == 5
    assert word_dict["confidence"] == 0.90
    assert Word(**dict(example_word)) == example_word


@pytest.mark.unit
def test_word_eq():
    """Test the Word __eq__ method"""
    w1 = Word(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=2,
        word_id=3,
        text="test_string",
        bounding_box={"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0},
        top_right={"x": 15.0, "y": 20.0},
        top_left={"x": 10.0, "y": 20.0},
        bottom_right={"x": 15.0, "y": 22.0},
        bottom_left={"x": 10.0, "y": 22.0},
        angle_degrees=1.0,
        angle_radians=5.0,
        confidence=0.90,
    )
    w2 = Word(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=2,
        word_id=3,
        text="test_string",
        bounding_box={"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0},
        top_right={"x": 15.0, "y": 20.0},
        top_left={"x": 10.0, "y": 20.0},
        bottom_right={"x": 15.0, "y": 22.0},
        bottom_left={"x": 10.0, "y": 22.0},
        angle_degrees=1.0,
        angle_radians=5.0,
        confidence=0.90,
    )
    w3 = Word(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed4",
        line_id=2,
        word_id=3,
        text="test_string",
        bounding_box={"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0},
        top_right={"x": 15.0, "y": 20.0},
        top_left={"x": 10.0, "y": 20.0},
        bottom_right={"x": 15.0, "y": 22.0},
        bottom_left={"x": 10.0, "y": 22.0},
        angle_degrees=1.0,
        angle_radians=5.0,
        confidence=0.90,
    )
    w4 = Word(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=3,
        word_id=3,
        text="test_string",
        bounding_box={"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0},
        top_right={"x": 15.0, "y": 20.0},
        top_left={"x": 10.0, "y": 20.0},
        bottom_right={"x": 15.0, "y": 22.0},
        bottom_left={"x": 10.0, "y": 22.0},
        angle_degrees=1.0,
        angle_radians=5.0,
        confidence=0.90,
    )
    w5 = Word(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=2,
        word_id=4,  # Different Word ID
        text="test_string",
        bounding_box={"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0},
        top_right={"x": 15.0, "y": 20.0},
        top_left={"x": 10.0, "y": 20.0},
        bottom_right={"x": 15.0, "y": 22.0},
        bottom_left={"x": 10.0, "y": 22.0},
        angle_degrees=1.0,
        angle_radians=5.0,
        confidence=0.90,
    )
    w6 = Word(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=2,
        word_id=3,
        text="Test_string",  # Different Text
        bounding_box={"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0},
        top_right={"x": 15.0, "y": 20.0},
        top_left={"x": 10.0, "y": 20.0},
        bottom_right={"x": 15.0, "y": 22.0},
        bottom_left={"x": 10.0, "y": 22.0},
        angle_degrees=1.0,
        angle_radians=5.0,
        confidence=0.90,
    )
    w7 = Word(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=2,
        word_id=3,
        text="test_string",
        bounding_box={
            "x": 20.0,
            "y": 20.0,
            "width": 5.0,
            "height": 2.0,
        },  # Different Bounding Box
        top_right={"x": 15.0, "y": 20.0},
        top_left={"x": 10.0, "y": 20.0},
        bottom_right={"x": 15.0, "y": 22.0},
        bottom_left={"x": 10.0, "y": 22.0},
        angle_degrees=1.0,
        angle_radians=5.0,
        confidence=0.90,
    )
    w8 = Word(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=2,
        word_id=3,
        text="test_string",
        bounding_box={"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0},
        top_right={"x": 20.0, "y": 20.0},  # Different Top Right
        top_left={"x": 10.0, "y": 20.0},
        bottom_right={"x": 15.0, "y": 22.0},
        bottom_left={"x": 10.0, "y": 22.0},
        angle_degrees=1.0,
        angle_radians=5.0,
        confidence=0.90,
    )
    w9 = Word(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=2,
        word_id=3,
        text="test_string",
        bounding_box={"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0},
        top_right={"x": 15.0, "y": 20.0},
        top_left={"x": 20.0, "y": 20.0},  # Different Top Left
        bottom_right={"x": 15.0, "y": 22.0},
        bottom_left={"x": 10.0, "y": 22.0},
        angle_degrees=1.0,
        angle_radians=5.0,
        confidence=0.90,
    )
    w10 = Word(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=2,
        word_id=3,
        text="test_string",
        bounding_box={"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0},
        top_right={"x": 15.0, "y": 20.0},
        top_left={"x": 10.0, "y": 20.0},
        bottom_right={"x": 20.0, "y": 22.0},  # Different Bottom Right
        bottom_left={"x": 10.0, "y": 22.0},
        angle_degrees=1.0,
        angle_radians=5.0,
        confidence=0.90,
    )
    w11 = Word(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=2,
        word_id=3,
        text="test_string",
        bounding_box={"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0},
        top_right={"x": 15.0, "y": 20.0},
        top_left={"x": 10.0, "y": 20.0},
        bottom_right={"x": 15.0, "y": 22.0},
        bottom_left={"x": 20.0, "y": 22.0},  # Different Bottom Left
        angle_degrees=1.0,
        angle_radians=5.0,
        confidence=0.90,
    )
    w12 = Word(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=2,
        word_id=3,
        text="test_string",
        bounding_box={"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0},
        top_right={"x": 15.0, "y": 20.0},
        top_left={"x": 10.0, "y": 20.0},
        bottom_right={"x": 15.0, "y": 22.0},
        bottom_left={"x": 10.0, "y": 22.0},
        angle_degrees=2.0,  # Different Angle Degrees
        angle_radians=5.0,
        confidence=0.90,
    )
    w13 = Word(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=2,
        word_id=3,
        text="test_string",
        bounding_box={"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0},
        top_right={"x": 15.0, "y": 20.0},
        top_left={"x": 10.0, "y": 20.0},
        bottom_right={"x": 15.0, "y": 22.0},
        bottom_left={"x": 10.0, "y": 22.0},
        angle_degrees=1.0,
        angle_radians=6.0,  # Different Angle Radians
        confidence=0.90,
    )
    w14 = Word(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=2,
        word_id=3,
        text="test_string",
        bounding_box={"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0},
        top_right={"x": 15.0, "y": 20.0},
        top_left={"x": 10.0, "y": 20.0},
        bottom_right={"x": 15.0, "y": 22.0},
        bottom_left={"x": 10.0, "y": 22.0},
        angle_degrees=1.0,
        angle_radians=5.0,
        confidence=0.91,  # Different Confidence
    )

    assert w1 == w2
    assert w1 != w3
    assert w1 != w4
    assert w1 != w5
    assert w1 != w6
    assert w1 != w7
    assert w1 != w8
    assert w1 != w9
    assert w1 != w10
    assert w1 != w11
    assert w1 != w12
    assert w1 != w13
    assert w1 != w14
    assert w1 != "test_string"


@pytest.mark.unit
def test_word_hash(example_word):
    """Test the Word __hash__ method and set notation."""
    duplicate_word = item_to_word(example_word.to_item())
    assert hash(example_word) == hash(duplicate_word)
    word_set = {example_word, duplicate_word}
    assert len(word_set) == 1
    different_word = Word(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        2,
        4,
        "test_string",
        {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0},
        {"x": 15.0, "y": 20.0},
        {"x": 10.0, "y": 20.0},
        {"x": 15.0, "y": 22.0},
        {"x": 10.0, "y": 22.0},
        1.0,
        5.0,
        0.90,
    )
    word_set = {example_word, duplicate_word, different_word}
    assert len(word_set) == 2


@pytest.mark.unit
def test_item_to_word(example_word):
    """Test the item_to_word function"""
    item_to_word(example_word.to_item()) == example_word
    with pytest.raises(ValueError, match="^Item is missing required keys: "):
        item_to_word({})
    with pytest.raises(ValueError, match="^Error converting item to Word: "):
        item_to_word(
            {
                "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
                "SK": {"S": "LINE#00002#WORD#00003"},
                "TYPE": {"S": "LINE"},
                "text": {"N": "100"},
                "bounding_box": {
                    "M": {
                        "height": {"N": "2.000000000000000000"},
                        "width": {"N": "5.000000000000000000"},
                        "x": {"N": "10.000000000000000000"},
                        "y": {"N": "20.000000000000000000"},
                    }
                },
                "top_right": {
                    "M": {
                        "x": {"N": "15.000000000000000000"},
                        "y": {"N": "20.000000000000000000"},
                    }
                },
                "top_left": {
                    "M": {
                        "x": {"N": "10.000000000000000000"},
                        "y": {"N": "20.000000000000000000"},
                    }
                },
                "bottom_right": {
                    "M": {
                        "x": {"N": "15.000000000000000000"},
                        "y": {"N": "22.000000000000000000"},
                    }
                },
                "bottom_left": {
                    "M": {
                        "x": {"N": "10.000000000000000000"},
                        "y": {"N": "22.000000000000000000"},
                    }
                },
                "angle_degrees": {"N": "1.0000000000"},
                "angle_radians": {"N": "5.0000000000"},
                "confidence": {"N": "0.90"},
            }
        )
