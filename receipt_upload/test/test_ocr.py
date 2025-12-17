"""Comprehensive unit tests for the OCR module."""

import json
import platform
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, Mock, call, patch
from uuid import uuid4

import pytest
from receipt_upload.ocr import (
    _download_image_from_s3,
    _upload_json_to_s3,
    apple_vision_ocr,
    apple_vision_ocr_job,
    process_ocr_dict_as_image,
    process_ocr_dict_as_receipt,
)

from receipt_dynamo.entities import (
    Letter,
    Line,
    ReceiptLetter,
    ReceiptLine,
    ReceiptWord,
    Word,
)


@pytest.fixture
def sample_ocr_data():
    """Sample OCR data structure for testing."""
    return {
        "lines": [
            {
                "text": "Hello World",
                "bounding_box": {
                    "x": 10.0,
                    "y": 20.0,
                    "width": 100.0,
                    "height": 20.0,
                },
                "top_right": {"x": 110.0, "y": 20.0},
                "top_left": {"x": 10.0, "y": 20.0},
                "bottom_right": {"x": 110.0, "y": 40.0},
                "bottom_left": {"x": 10.0, "y": 40.0},
                "angle_degrees": 0.0,
                "angle_radians": 0.0,
                "confidence": 0.95,
                "words": [
                    {
                        "text": "Hello",
                        "bounding_box": {
                            "x": 10.0,
                            "y": 20.0,
                            "width": 45.0,
                            "height": 20.0,
                        },
                        "top_right": {"x": 55.0, "y": 20.0},
                        "top_left": {"x": 10.0, "y": 20.0},
                        "bottom_right": {"x": 55.0, "y": 40.0},
                        "bottom_left": {"x": 10.0, "y": 40.0},
                        "angle_degrees": 0.0,
                        "angle_radians": 0.0,
                        "confidence": 0.96,
                        "letters": [
                            {
                                "text": "H",
                                "bounding_box": {
                                    "x": 10.0,
                                    "y": 20.0,
                                    "width": 9.0,
                                    "height": 20.0,
                                },
                                "top_right": {"x": 19.0, "y": 20.0},
                                "top_left": {"x": 10.0, "y": 20.0},
                                "bottom_right": {"x": 19.0, "y": 40.0},
                                "bottom_left": {"x": 10.0, "y": 40.0},
                                "angle_degrees": 0.0,
                                "angle_radians": 0.0,
                                "confidence": 0.97,
                            }
                        ],
                    },
                    {
                        "text": "World",
                        "bounding_box": {
                            "x": 60.0,
                            "y": 20.0,
                            "width": 50.0,
                            "height": 20.0,
                        },
                        "top_right": {"x": 110.0, "y": 20.0},
                        "top_left": {"x": 60.0, "y": 20.0},
                        "bottom_right": {"x": 110.0, "y": 40.0},
                        "bottom_left": {"x": 60.0, "y": 40.0},
                        "angle_degrees": 0.0,
                        "angle_radians": 0.0,
                        "confidence": 0.94,
                        "extracted_data": {"type": "greeting"},
                        "letters": [],
                    },
                ],
            }
        ]
    }


@pytest.fixture
def empty_ocr_data():
    """Empty OCR data structure."""
    return {"lines": []}


class TestProcessOCRDictAsReceipt:
    """Test cases for process_ocr_dict_as_receipt function."""

    @pytest.mark.unit
    def test_process_ocr_dict_as_receipt_basic(self, sample_ocr_data):
        """Test basic OCR processing for receipt."""
        image_id = "550e8400-e29b-41d4-a716-446655440000"
        receipt_id = 42

        lines, words, letters = process_ocr_dict_as_receipt(
            sample_ocr_data, image_id, receipt_id
        )

        # Verify counts
        assert len(lines) == 1
        assert len(words) == 2
        assert len(letters) == 1

        # Verify line data
        line = lines[0]
        assert isinstance(line, ReceiptLine)
        assert line.image_id == image_id
        assert line.receipt_id == receipt_id
        assert line.line_id == 1
        assert line.text == "Hello World"
        assert line.confidence == 0.95

        # Verify word data
        word1 = words[0]
        assert isinstance(word1, ReceiptWord)
        assert word1.text == "Hello"
        assert word1.line_id == 1
        assert word1.word_id == 1
        assert word1.extracted_data is None

        word2 = words[1]
        assert word2.text == "World"
        assert word2.extracted_data == {"type": "greeting"}

        # Verify letter data
        letter = letters[0]
        assert isinstance(letter, ReceiptLetter)
        assert letter.text == "H"
        assert letter.letter_id == 1

    @pytest.mark.unit
    def test_process_ocr_dict_as_receipt_empty(self, empty_ocr_data):
        """Test processing empty OCR data."""
        lines, words, letters = process_ocr_dict_as_receipt(
            empty_ocr_data, "550e8400-e29b-41d4-a716-446655440001", 1
        )

        assert lines == []
        assert words == []
        assert letters == []

    @pytest.mark.unit
    def test_process_ocr_dict_as_receipt_multiple_lines(self):
        """Test processing multiple lines with proper indexing."""
        ocr_data = {
            "lines": [
                {
                    "text": f"Line {i}",
                    "bounding_box": {
                        "x": 10.0,
                        "y": 20.0 * i,
                        "width": 100.0,
                        "height": 20.0,
                    },
                    "top_right": {"x": 110.0, "y": 20.0 * i},
                    "top_left": {"x": 10.0, "y": 20.0 * i},
                    "bottom_right": {"x": 110.0, "y": 20.0 * (i + 1)},
                    "bottom_left": {"x": 10.0, "y": 20.0 * (i + 1)},
                    "angle_degrees": 0.0,
                    "angle_radians": 0.0,
                    "confidence": 0.9,
                    "words": [],
                }
                for i in range(1, 4)
            ]
        }

        lines, _, _ = process_ocr_dict_as_receipt(
            ocr_data, "550e8400-e29b-41d4-a716-446655440002", 1
        )

        assert len(lines) == 3
        assert [line.line_id for line in lines] == [1, 2, 3]
        assert [line.text for line in lines] == ["Line 1", "Line 2", "Line 3"]


class TestProcessOCRDictAsImage:
    """Test cases for process_ocr_dict_as_image function."""

    @pytest.mark.unit
    def test_process_ocr_dict_as_image_basic(self, sample_ocr_data):
        """Test basic OCR processing for image."""
        image_id = "550e8400-e29b-41d4-a716-446655440003"

        lines, words, letters = process_ocr_dict_as_image(
            sample_ocr_data, image_id
        )

        # Verify counts
        assert len(lines) == 1
        assert len(words) == 2
        assert len(letters) == 1

        # Verify line data
        line = lines[0]
        assert isinstance(line, Line)
        assert line.image_id == image_id
        assert line.line_id == 1
        assert line.text == "Hello World"

        # Verify word data
        word1 = words[0]
        assert isinstance(word1, Word)
        assert word1.text == "Hello"
        assert word1.extracted_data is None

        word2 = words[1]
        assert word2.text == "World"
        assert word2.extracted_data == {"type": "greeting"}

        # Verify letter data
        letter = letters[0]
        assert isinstance(letter, Letter)
        assert letter.text == "H"

    @pytest.mark.unit
    def test_process_ocr_dict_as_image_empty(self, empty_ocr_data):
        """Test processing empty OCR data for image."""
        lines, words, letters = process_ocr_dict_as_image(
            empty_ocr_data, "550e8400-e29b-41d4-a716-446655440004"
        )

        assert lines == []
        assert words == []
        assert letters == []

    @pytest.mark.unit
    def test_process_ocr_dict_as_image_no_words_or_letters(self):
        """Test processing OCR data with lines but no words or letters."""
        ocr_data = {
            "lines": [
                {
                    "text": "Just a line",
                    "bounding_box": {
                        "x": 0,
                        "y": 0,
                        "width": 100,
                        "height": 20,
                    },
                    "top_right": {"x": 100, "y": 0},
                    "top_left": {"x": 0, "y": 0},
                    "bottom_right": {"x": 100, "y": 20},
                    "bottom_left": {"x": 0, "y": 20},
                    "angle_degrees": 0.0,
                    "angle_radians": 0.0,
                    "confidence": 0.8,
                    "words": [],
                }
            ]
        }

        lines, words, letters = process_ocr_dict_as_image(
            ocr_data, "550e8400-e29b-41d4-a716-446655440005"
        )

        assert len(lines) == 1
        assert len(words) == 0
        assert len(letters) == 0


class TestAppleVisionOCRJob:
    """Test cases for apple_vision_ocr_job function."""

    @pytest.mark.unit
    @pytest.mark.skip(reason="Complex mocking of Swift integration")
    def test_apple_vision_ocr_job_success(self):
        """Test successful OCR job execution."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Create mock image files
            image_paths = []
            for i in range(2):
                img_path = temp_path / f"image_{i}.jpg"
                img_path.write_text("fake image data")
                image_paths.append(img_path)

            # Create expected JSON output files
            for img_path in image_paths:
                json_path = temp_path / f"{img_path.stem}.json"
                json_path.write_text('{"lines": []}')

            with patch(
                "receipt_upload.ocr.platform.system", return_value="Darwin"
            ):
                with patch("receipt_upload.ocr.subprocess.run") as mock_run:
                    mock_run.return_value = Mock(
                        stdout="OCR completed successfully",
                        stderr="",
                        returncode=0,
                    )

                    # Skip the Swift script check by mocking the Path operations
                    with patch("receipt_upload.ocr.Path.__new__") as mock_path:
                        # Create a mock that handles the swift script check
                        def path_side_effect(cls, path_str):
                            p = Path.__new__(Path, path_str)
                            Path.__init__(p, path_str)
                            if str(path_str).endswith("OCRSwift.swift"):
                                # Mock exists to return True for swift script
                                p.exists = Mock(return_value=True)
                            return p

                        mock_path.side_effect = path_side_effect

                        result = apple_vision_ocr_job(image_paths, temp_path)

                        assert len(result) == 2
                        assert all(path.suffix == ".json" for path in result)
                        assert result[0].stem == "image_0"
                        assert result[1].stem == "image_1"

    @pytest.mark.unit
    def test_apple_vision_ocr_job_file_not_found(self):
        """Test error when image file doesn't exist."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            non_existent = temp_path / "missing.jpg"

            with pytest.raises(
                FileNotFoundError, match="Image file not found"
            ):
                apple_vision_ocr_job([non_existent], temp_path)

    @pytest.mark.unit
    def test_apple_vision_ocr_job_not_darwin(self):
        """Test error when not running on macOS."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            img_path = temp_path / "image.jpg"
            img_path.write_text("fake")

            with patch(
                "receipt_upload.ocr.platform.system", return_value="Linux"
            ):
                with pytest.raises(
                    ValueError,
                    match="Vision Framework can only be run on a Mac",
                ):
                    apple_vision_ocr_job([img_path], temp_path)

    @pytest.mark.unit
    @pytest.mark.skip(reason="Complex mocking of Swift integration")
    def test_apple_vision_ocr_job_swift_script_missing(self):
        """Test error when Swift script is missing."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            img_path = temp_path / "image.jpg"
            img_path.write_text("fake")

            with patch(
                "receipt_upload.ocr.platform.system", return_value="Darwin"
            ):
                original_exists = Path.exists
                with patch.object(Path, "exists") as mock_exists:

                    def exists_side_effect(self):
                        if str(self).endswith("OCRSwift.swift"):
                            return False  # Swift script missing
                        return original_exists(self)

                    mock_exists.side_effect = exists_side_effect

                    with pytest.raises(
                        FileNotFoundError, match="Swift script not found"
                    ):
                        apple_vision_ocr_job([img_path], temp_path)

    @pytest.mark.unit
    @pytest.mark.skip(reason="Complex mocking of Swift integration")
    def test_apple_vision_ocr_job_subprocess_error(self):
        """Test handling of subprocess errors."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            img_path = temp_path / "image.jpg"
            img_path.write_text("fake")

            with patch(
                "receipt_upload.ocr.platform.system", return_value="Darwin"
            ):
                original_exists = Path.exists
                with patch.object(Path, "exists") as mock_exists:

                    def exists_side_effect(self):
                        if str(self).endswith("OCRSwift.swift"):
                            return True
                        return original_exists(self)

                    mock_exists.side_effect = exists_side_effect

                    with patch(
                        "receipt_upload.ocr.subprocess.run"
                    ) as mock_run:
                        mock_run.side_effect = subprocess.CalledProcessError(
                            1, ["swift"], stderr="Swift error"
                        )

                        with pytest.raises(
                            ValueError, match="Error running Swift script"
                        ):
                            apple_vision_ocr_job([img_path], temp_path)

    @pytest.mark.unit
    @pytest.mark.skip(reason="Complex mocking of Swift integration")
    def test_apple_vision_ocr_job_missing_output(self):
        """Test error when expected JSON output is missing."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            img_path = temp_path / "image.jpg"
            img_path.write_text("fake")

            with patch(
                "receipt_upload.ocr.platform.system", return_value="Darwin"
            ):
                original_exists = Path.exists
                with patch.object(Path, "exists") as mock_exists:

                    def exists_side_effect(self):
                        if str(self).endswith("OCRSwift.swift"):
                            return True
                        if str(self).endswith(".jpg"):
                            return original_exists(self)
                        if str(self).endswith(".json"):
                            return False  # JSON files don't exist
                        return original_exists(self)

                    mock_exists.side_effect = exists_side_effect

                    with patch(
                        "receipt_upload.ocr.subprocess.run"
                    ) as mock_run:
                        mock_run.return_value = Mock(
                            stdout="", stderr="", returncode=0
                        )

                        with pytest.raises(
                            FileNotFoundError,
                            match="Expected OCR output file not found",
                        ):
                            apple_vision_ocr_job([img_path], temp_path)


class TestS3Functions:
    """Test cases for S3 helper functions."""

    @pytest.mark.unit
    def test_download_image_from_s3(self):
        """Test downloading image from S3."""
        with patch("boto3.client") as mock_boto:
            mock_s3 = Mock()
            mock_boto.return_value = mock_s3

            # Use generic path for mocking - actual code uses mkdtemp
            with patch(
                "tempfile.mkdtemp", return_value="test-dir"
            ):  # nosec B108
                result = _download_image_from_s3(
                    "image-123", "my-bucket", "images/test.jpg"
                )

                assert result == Path("test-dir/image-123.jpg")
                mock_s3.download_file.assert_called_once_with(
                    "my-bucket", "images/test.jpg", "test-dir/image-123.jpg"
                )

    @pytest.mark.unit
    def test_upload_json_to_s3(self):
        """Test uploading JSON to S3."""
        with patch("boto3.client") as mock_boto:
            mock_s3 = Mock()
            mock_boto.return_value = mock_s3

            # Use generic path for mocking
            test_path = Path("test.json")  # nosec B108
            _upload_json_to_s3(test_path, "my-bucket", "ocr/test.json")

            mock_s3.upload_file.assert_called_once_with(
                "test.json", "my-bucket", "ocr/test.json"
            )


class TestAppleVisionOCR:
    """Test cases for apple_vision_ocr function."""

    @pytest.mark.unit
    def test_apple_vision_ocr_success(self):
        """Test successful OCR processing."""
        with patch("platform.system", return_value="Darwin"):
            with patch("pathlib.Path.exists", return_value=True):
                with patch("receipt_upload.ocr.subprocess.run") as mock_run:
                    mock_run.return_value = Mock(returncode=0)

                    with patch("tempfile.TemporaryDirectory") as mock_temp:
                        mock_temp_path = Mock()
                        # Use generic path for mocking - actual code uses TemporaryDirectory
                        mock_temp_path.__enter__ = Mock(
                            return_value="test-dir"  # nosec B108
                        )
                        mock_temp_path.__exit__ = Mock(return_value=None)
                        mock_temp.return_value = mock_temp_path

                        with patch("pathlib.Path.glob") as mock_glob:
                            # Mock JSON files
                            mock_json1 = Mock()
                            mock_json1.name = "result1.json"
                            mock_json2 = Mock()
                            mock_json2.name = "result2.json"
                            mock_glob.return_value = [mock_json1, mock_json2]

                            with patch(
                                "builtins.open", create=True
                            ) as mock_open:
                                mock_open.return_value.__enter__.return_value.read.return_value = (
                                    '{"lines": []}'
                                )

                                with patch(
                                    "json.load", return_value={"lines": []}
                                ):
                                    with patch(
                                        "receipt_upload.ocr.process_ocr_dict_as_image"
                                    ) as mock_process:
                                        mock_process.return_value = (
                                            [],
                                            [],
                                            [],
                                        )

                                        result = apple_vision_ocr(
                                            [
                                                "/path/img1.jpg",
                                                "/path/img2.jpg",
                                            ]
                                        )

                                        assert len(result) == 2
                                        assert all(
                                            isinstance(k, str)
                                            for k in result.keys()
                                        )

    @pytest.mark.unit
    def test_apple_vision_ocr_not_darwin(self):
        """Test error on non-macOS systems."""
        with patch("platform.system", return_value="Windows"):
            with pytest.raises(
                ValueError, match="Vision Framework can only be run on a Mac"
            ):
                apple_vision_ocr(["/path/image.jpg"])

    @pytest.mark.unit
    def test_apple_vision_ocr_subprocess_error(self):
        """Test handling of subprocess errors."""
        with patch("platform.system", return_value="Darwin"):
            with patch("pathlib.Path.exists", return_value=True):
                with patch("receipt_upload.ocr.subprocess.run") as mock_run:
                    mock_run.side_effect = subprocess.CalledProcessError(
                        1, ["swift"], stderr="Error"
                    )

                    with pytest.raises(
                        ValueError, match="Error running Swift script"
                    ):
                        apple_vision_ocr(["/path/image.jpg"])
