# infra/lambda_layer/python/test/integration/test__ocr.py
import json
import pytest
import subprocess
from pathlib import Path

# Import your function under test
from receipt_dynamo.data._ocr import apple_vision_ocr


@pytest.fixture
def mock_ocr_json():
    """
    This fixture returns the fake OCR JSON you expect your Swift script
    to have produced, now with dictionary-based bounding boxes.
    """
    return {
        "lines": [
            {
                "text": "Test line",
                "bounding_box": {
                    "width": 0.13322906897812287,
                    "y": 0.7911256041373611,
                    "x": 0.43150158271227484,
                    "height": 0.017722232001168403,
                },
                "top_right": {"x": 0.5647306516903977, "y": 0.7911256041373611},
                "top_left": {"x": 0.43150158271227484, "y": 0.7911256041373611},
                "bottom_right": {"x": 0.5647306516903977, "y": 0.8088478361385295},
                "bottom_left": {"x": 0.43150158271227484, "y": 0.8088478361385295},
                "angle_degrees": 0,
                "angle_radians": 0.0,
                "confidence": 1.0,
                "words": [
                    {
                        "text": "Test",
                        "bounding_box": {
                            "width": 0.05329162759124915,
                            "y": 0.7911256041373611,
                            "x": 0.43150158271227484,
                            "height": 0.017722232001168403,
                        },
                        "top_right": {
                            "x": 0.48479321030352399,
                            "y": 0.7911256041373611,
                        },
                        "top_left": {"x": 0.43150158271227484, "y": 0.7911256041373611},
                        "bottom_right": {
                            "x": 0.48479321030352399,
                            "y": 0.8088478361385295,
                        },
                        "bottom_left": {
                            "x": 0.43150158271227484,
                            "y": 0.8088478361385295,
                        },
                        "angle_degrees": 0,
                        "angle_radians": 0.0,
                        "confidence": 1.0,
                        "letters": [
                            {
                                "text": "T",
                                "bounding_box": {
                                    "width": 0.013322906897812287,
                                    "y": 0.7911256041373611,
                                    "x": 0.43150158271227484,
                                    "height": 0.017722232001168403,
                                },
                                "top_right": {
                                    "x": 0.4448244896100871,
                                    "y": 0.7911256041373611,
                                },
                                "top_left": {
                                    "x": 0.43150158271227484,
                                    "y": 0.7911256041373611,
                                },
                                "bottom_right": {
                                    "x": 0.4448244896100871,
                                    "y": 0.8088478361385295,
                                },
                                "bottom_left": {
                                    "x": 0.43150158271227484,
                                    "y": 0.8088478361385295,
                                },
                                "angle_degrees": 0,
                                "angle_radians": 0.0,
                                "confidence": 1.0,
                            },
                        ],
                    }
                ],
            }
        ]
    }


@pytest.mark.integration
@pytest.mark.usefixtures("mock_ocr_json")
def test_apple_vision_ocr_integration(mocker, mock_ocr_json):
    """
    This test mocks out all external dependencies (Swift script, OS checks, file reading)
    and verifies that apple_vision_ocr returns the expected structure given mock JSON data.
    """
    # 1. Mock 'platform.system' to return 'Darwin'
    mocker.patch("receipt_dynamo.data._ocr.platform.system", return_value="Darwin")
    # 2. Mock 'Path.exists' to return True
    mocker.patch.object(Path, "exists", return_value=True)
    # 3. Mock 'subprocess.run'
    mock_subprocess_run = mocker.patch.object(subprocess, "run")

    # Force Path.glob("*.json") to return a known dummy path
    fake_json_path = Path("dummy.json")
    mocker.patch.object(Path, "glob", return_value=[fake_json_path])

    # Mock the built-in open so that any file read returns our fake JSON
    mock_file = mocker.mock_open(read_data=json.dumps(mock_ocr_json))
    mocker.patch("builtins.open", mock_file)

    # Call the function under test
    result = apple_vision_ocr(["dummy_image.png"])

    # --- Assertions ---
    # The function normally returns a dict of { image_id: (lines, words, letters) }.
    assert isinstance(result, dict), "The OCR result should be a dictionary."
    assert (
        len(result) == 1
    ), "Should have exactly one entry for the single mock JSON file."

    image_id = list(result.keys())[0]
    lines, words, letters = result[image_id]

    assert len(lines) == 1, "There should be exactly one line in the mock JSON."
    assert lines[0].text == "Test line"
    assert len(words) == 1, "There should be exactly one word in that line."
    assert words[0].text == "Test"
    assert len(letters) == 1, "There should be exactly one letter in that word."
    assert letters[0].text == "T"

    # Make sure your Swift script never actually got run
    mock_subprocess_run.assert_called_once()


@pytest.mark.integration
def test_apple_vision_ocr_missing_swift_script(mocker):
    """
    Test that FileNotFoundError is raised if the Swift script does not exist.
    """
    # Mock platform to be 'Darwin' so we don't fail on OS check
    mocker.patch("receipt_dynamo.data._ocr.platform.system", return_value="Darwin")
    # Force Path.exists to return False, simulating a missing Swift script
    mocker.patch.object(Path, "exists", return_value=False)

    with pytest.raises(FileNotFoundError, match="Swift script not found"):
        apple_vision_ocr(["some_image_path.png"])


@pytest.mark.integration
def test_apple_vision_ocr_not_darwin(mocker):
    """
    Test that ValueError is raised if we are not on macOS (Darwin).
    """
    # Mock Path.exists to return True so we pass the "script found" check
    mocker.patch.object(Path, "exists", return_value=True)
    # Mock platform.system to return "Windows" (or anything but Darwin)
    mocker.patch("receipt_dynamo.data._ocr.platform.system", return_value="Windows")

    with pytest.raises(
        ValueError, match="Apple's Vision Framework can only be run on a Mac"
    ):
        apple_vision_ocr(["some_image_path.png"])


@pytest.mark.integration
def test_apple_vision_ocr_subprocess_error(mocker):
    """
    Test that we gracefully return False if the Swift subprocess raises a CalledProcessError.
    """
    # Mock platform to be 'Darwin' and script exists
    mocker.patch.object(Path, "exists", return_value=True)
    mocker.patch("receipt_dynamo.data._ocr.platform.system", return_value="Darwin")

    # Make subprocess.run raise CalledProcessError
    mocker.patch.object(
        subprocess,
        "run",
        side_effect=subprocess.CalledProcessError(1, "cmd"),
    )

    result = apple_vision_ocr(["some_image_path.png"])
    assert result is False, "Should return False when subprocess raises an error."
