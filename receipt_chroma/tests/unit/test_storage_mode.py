"""Unit tests for StorageMode enum."""

import pytest
from receipt_chroma.storage import StorageMode


@pytest.mark.unit
class TestStorageMode:
    """Test StorageMode enum."""

    def test_storage_mode_values_exist(self):
        """Test that all expected StorageMode values exist."""
        assert StorageMode.S3_ONLY is not None
        assert StorageMode.EFS is not None
        assert StorageMode.AUTO is not None

    def test_storage_mode_string_values(self):
        """Test that StorageMode values have correct string representations."""
        assert StorageMode.S3_ONLY.value == "s3"
        assert StorageMode.EFS.value == "efs"
        assert StorageMode.AUTO.value == "auto"

    def test_storage_mode_equality(self):
        """Test that StorageMode enum equality works correctly."""
        assert StorageMode.S3_ONLY == StorageMode.S3_ONLY
        assert StorageMode.EFS == StorageMode.EFS
        assert StorageMode.AUTO == StorageMode.AUTO

        assert StorageMode.S3_ONLY != StorageMode.EFS
        assert StorageMode.S3_ONLY != StorageMode.AUTO
        assert StorageMode.EFS != StorageMode.AUTO

    def test_storage_mode_comparison_in_conditionals(self):
        """Test that StorageMode can be used in conditional logic."""
        mode = StorageMode.S3_ONLY

        if mode == StorageMode.S3_ONLY:
            result = "s3"
        elif mode == StorageMode.EFS:
            result = "efs"
        else:
            result = "auto"

        assert result == "s3"

    def test_storage_mode_membership(self):
        """Test that StorageMode values are members of the enum."""
        all_modes = list(StorageMode)
        assert len(all_modes) == 3
        assert StorageMode.S3_ONLY in all_modes
        assert StorageMode.EFS in all_modes
        assert StorageMode.AUTO in all_modes

    def test_storage_mode_can_be_accessed_by_value(self):
        """Test that StorageMode can be accessed by string value."""
        assert StorageMode("s3") == StorageMode.S3_ONLY
        assert StorageMode("efs") == StorageMode.EFS
        assert StorageMode("auto") == StorageMode.AUTO

    def test_storage_mode_invalid_value_raises_error(self):
        """Test that invalid StorageMode value raises ValueError."""
        with pytest.raises(ValueError):
            StorageMode("invalid")

    def test_storage_mode_string_representation(self):
        """Test string representation of StorageMode."""
        assert str(StorageMode.S3_ONLY) == "StorageMode.S3_ONLY"
        assert str(StorageMode.EFS) == "StorageMode.EFS"
        assert str(StorageMode.AUTO) == "StorageMode.AUTO"

    def test_storage_mode_repr(self):
        """Test repr of StorageMode."""
        assert repr(StorageMode.S3_ONLY) == "<StorageMode.S3_ONLY: 's3'>"
        assert repr(StorageMode.EFS) == "<StorageMode.EFS: 'efs'>"
        assert repr(StorageMode.AUTO) == "<StorageMode.AUTO: 'auto'>"
