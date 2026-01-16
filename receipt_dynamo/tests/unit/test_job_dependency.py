from datetime import datetime

import pytest

from receipt_dynamo.entities.job_dependency import (
    JobDependency,
    item_to_job_dependency,
)


@pytest.fixture
def example_job_dependency():
    """Provides a sample JobDependency for testing."""
    return JobDependency(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",  # dependent_job_id
        "4f52804b-2fad-4e00-92c8-b593da3a8ed4",  # dependency_job_id
        "COMPLETION",  # type
        "2021-01-01T12:30:45",  # created_at
        "Specific completion condition",  # condition
    )


@pytest.fixture
def example_job_dependency_minimal():
    """Provides a minimal sample JobDependency for testing."""
    return JobDependency(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",  # dependent_job_id
        "4f52804b-2fad-4e00-92c8-b593da3a8ed4",  # dependency_job_id
        "SUCCESS",  # type
        "2021-01-01T12:30:45",  # created_at
    )


@pytest.mark.unit
def test_job_dependency_init_valid(
    example_job_dependency, example_job_dependency_minimal
):
    """Test the JobDependency constructor with valid parameters."""
    # Test full job dependency
    assert (
        example_job_dependency.dependent_job_id
        == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    )
    assert (
        example_job_dependency.dependency_job_id
        == "4f52804b-2fad-4e00-92c8-b593da3a8ed4"
    )
    assert example_job_dependency.type == "COMPLETION"
    assert example_job_dependency.created_at == "2021-01-01T12:30:45"
    assert example_job_dependency.condition == "Specific completion condition"

    # Test minimal job dependency
    assert (
        example_job_dependency_minimal.dependent_job_id
        == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    )
    assert (
        example_job_dependency_minimal.dependency_job_id
        == "4f52804b-2fad-4e00-92c8-b593da3a8ed4"
    )
    assert example_job_dependency_minimal.type == "SUCCESS"
    assert example_job_dependency_minimal.created_at == "2021-01-01T12:30:45"
    assert example_job_dependency_minimal.condition is None


@pytest.mark.unit
def test_job_dependency_init_with_datetime():
    """Test the JobDependency constructor with datetime for created_at."""
    job_dependency = JobDependency(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "4f52804b-2fad-4e00-92c8-b593da3a8ed4",
        "SUCCESS",
        datetime(2021, 1, 1, 12, 30, 45),
    )
    assert job_dependency.created_at == "2021-01-01T12:30:45"


@pytest.mark.unit
def test_job_dependency_init_invalid_dependent_id():
    """Test the JobDependency constructor with invalid dependent_job_id."""
    with pytest.raises(ValueError, match="uuid must be a string"):
        JobDependency(
            1,  # Invalid: should be a string
            "4f52804b-2fad-4e00-92c8-b593da3a8ed4",
            "SUCCESS",
            "2021-01-01T12:30:45",
        )

    with pytest.raises(ValueError, match="uuid must be a valid UUID"):
        JobDependency(
            "not-a-uuid",  # Invalid: not a valid UUID format
            "4f52804b-2fad-4e00-92c8-b593da3a8ed4",
            "SUCCESS",
            "2021-01-01T12:30:45",
        )


@pytest.mark.unit
def test_job_dependency_init_invalid_dependency_id():
    """Test the JobDependency constructor with invalid dependency_job_id."""
    with pytest.raises(ValueError, match="uuid must be a string"):
        JobDependency(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            1,  # Invalid: should be a string
            "SUCCESS",
            "2021-01-01T12:30:45",
        )

    with pytest.raises(ValueError, match="uuid must be a valid UUID"):
        JobDependency(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "not-a-uuid",  # Invalid: not a valid UUID format
            "SUCCESS",
            "2021-01-01T12:30:45",
        )


@pytest.mark.unit
def test_job_dependency_init_self_dependency():
    """JobDependency constructor with same IDs for dependent and dependency."""
    same_id = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    with pytest.raises(ValueError, match="A job cannot depend on itself"):
        JobDependency(
            same_id,
            same_id,  # Invalid: same as dependent_job_id
            "SUCCESS",
            "2021-01-01T12:30:45",
        )


@pytest.mark.unit
def test_job_dependency_init_invalid_type():
    """Test the JobDependency constructor with invalid type."""
    with pytest.raises(ValueError, match="type must be one of"):
        JobDependency(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "4f52804b-2fad-4e00-92c8-b593da3a8ed4",
            "INVALID_TYPE",  # Invalid: not a valid type
            "2021-01-01T12:30:45",
        )

    with pytest.raises(ValueError, match="type must be one of"):
        JobDependency(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "4f52804b-2fad-4e00-92c8-b593da3a8ed4",
            123,  # Invalid: not a string
            "2021-01-01T12:30:45",
        )


@pytest.mark.unit
def test_job_dependency_init_invalid_created_at():
    """Test the JobDependency constructor with invalid created_at."""
    with pytest.raises(
        ValueError, match="created_at must be a datetime object or a string"
    ):
        JobDependency(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "4f52804b-2fad-4e00-92c8-b593da3a8ed4",
            "SUCCESS",
            123,  # Invalid: not a datetime or string
        )


@pytest.mark.unit
def test_job_dependency_init_invalid_condition():
    """Test the JobDependency constructor with invalid condition."""
    with pytest.raises(ValueError, match="condition must be a string"):
        JobDependency(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "4f52804b-2fad-4e00-92c8-b593da3a8ed4",
            "SUCCESS",
            "2021-01-01T12:30:45",
            123,  # Invalid: not a string
        )


@pytest.mark.unit
def test_job_dependency_key(example_job_dependency):
    """Test the JobDependency.key method."""
    assert example_job_dependency.key == {
        "PK": {"S": "JOB#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "DEPENDS_ON#4f52804b-2fad-4e00-92c8-b593da3a8ed4"},
    }


@pytest.mark.unit
def test_job_dependency_gsi1_key(example_job_dependency):
    """Test the JobDependency.gsi1_key() method."""
    assert example_job_dependency.gsi1_key() == {
        "GSI1PK": {"S": "DEPENDENCY"},
        "GSI1SK": {
            "S": f"DEPENDENT#"
            f"{example_job_dependency.dependent_job_id}#"
            f"DEPENDENCY#{example_job_dependency.dependency_job_id}"
        },
    }


@pytest.mark.unit
def test_job_dependency_gsi2_key(example_job_dependency):
    """Test the JobDependency.gsi2_key() method."""
    assert example_job_dependency.gsi2_key() == {
        "GSI2PK": {"S": "DEPENDENCY"},
        "GSI2SK": {
            "S": f"DEPENDED_BY#{example_job_dependency.dependency_job_id}#"
            f"DEPENDENT#{example_job_dependency.dependent_job_id}"
        },
    }


@pytest.mark.unit
def test_job_dependency_to_item(
    example_job_dependency, example_job_dependency_minimal
):
    """Test the JobDependency.to_item() method."""
    # Test with full job dependency
    item = example_job_dependency.to_item()
    assert item["PK"] == {"S": "JOB#3f52804b-2fad-4e00-92c8-b593da3a8ed3"}
    assert item["SK"] == {
        "S": "DEPENDS_ON#4f52804b-2fad-4e00-92c8-b593da3a8ed4"
    }
    assert item["GSI1PK"] == {"S": "DEPENDENCY"}
    assert item["GSI1SK"] == {
        "S": f"DEPENDENT#{example_job_dependency.dependent_job_id}#"
        f"DEPENDENCY#{example_job_dependency.dependency_job_id}"
    }
    assert item["GSI2PK"] == {"S": "DEPENDENCY"}
    assert item["GSI2SK"] == {
        "S": f"DEPENDED_BY#{example_job_dependency.dependency_job_id}#"
        f"DEPENDENT#{example_job_dependency.dependent_job_id}"
    }
    assert item["TYPE"] == {"S": "JOB_DEPENDENCY"}
    assert item["dependent_job_id"] == {
        "S": "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    }
    assert item["dependency_job_id"] == {
        "S": "4f52804b-2fad-4e00-92c8-b593da3a8ed4"
    }
    assert item["type"] == {"S": "COMPLETION"}
    assert item["created_at"] == {"S": "2021-01-01T12:30:45"}
    assert item["condition"] == {"S": "Specific completion condition"}

    # Test with minimal job dependency
    item = example_job_dependency_minimal.to_item()
    assert item["dependent_job_id"] == {
        "S": "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    }
    assert item["dependency_job_id"] == {
        "S": "4f52804b-2fad-4e00-92c8-b593da3a8ed4"
    }
    assert item["type"] == {"S": "SUCCESS"}
    assert item["created_at"] == {"S": "2021-01-01T12:30:45"}
    assert "condition" not in item


@pytest.mark.unit
def test_job_dependency_case_insensitive_type():
    """Test that type is converted to uppercase."""
    job_dependency = JobDependency(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "4f52804b-2fad-4e00-92c8-b593da3a8ed4",
        "success",  # lowercase
        "2021-01-01T12:30:45",
    )
    assert job_dependency.type == "SUCCESS"  # Should be converted to uppercase


@pytest.mark.unit
def test_job_dependency_repr(example_job_dependency):
    """Test the JobDependency.__repr__() method."""
    repr_str = repr(example_job_dependency)
    assert (
        "dependent_job_id='3f52804b-2fad-4e00-92c8-b593da3a8ed3'" in repr_str
    )
    assert (
        "dependency_job_id='4f52804b-2fad-4e00-92c8-b593da3a8ed4'" in repr_str
    )
    assert "type='COMPLETION'" in repr_str
    assert "created_at='2021-01-01T12:30:45'" in repr_str
    assert "condition='Specific completion condition'" in repr_str


@pytest.mark.unit
def test_job_dependency_iter(example_job_dependency):
    """Test the JobDependency.__iter__() method."""
    job_dependency_dict = dict(example_job_dependency)
    assert (
        job_dependency_dict["dependent_job_id"]
        == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    )
    assert (
        job_dependency_dict["dependency_job_id"]
        == "4f52804b-2fad-4e00-92c8-b593da3a8ed4"
    )
    assert job_dependency_dict["type"] == "COMPLETION"
    assert job_dependency_dict["created_at"] == "2021-01-01T12:30:45"
    assert job_dependency_dict["condition"] == "Specific completion condition"


@pytest.mark.unit
def test_job_dependency_eq():
    """Test the JobDependency.__eq__() method."""
    # Same job dependencies
    job_dep1 = JobDependency(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "4f52804b-2fad-4e00-92c8-b593da3a8ed4",
        "SUCCESS",
        "2021-01-01T12:30:45",
        "condition",
    )
    job_dep2 = JobDependency(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "4f52804b-2fad-4e00-92c8-b593da3a8ed4",
        "SUCCESS",
        "2021-01-01T12:30:45",
        "condition",
    )
    assert job_dep1 == job_dep2, "Should be equal"

    # Different dependent_job_id
    job_dep3 = JobDependency(
        "5f52804b-2fad-4e00-92c8-b593da3a8ed5",
        "4f52804b-2fad-4e00-92c8-b593da3a8ed4",
        "SUCCESS",
        "2021-01-01T12:30:45",
        "condition",
    )
    assert job_dep1 != job_dep3, "Different dependent_job_id"

    # Different dependency_job_id
    job_dep4 = JobDependency(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "5f52804b-2fad-4e00-92c8-b593da3a8ed5",
        "SUCCESS",
        "2021-01-01T12:30:45",
        "condition",
    )
    assert job_dep1 != job_dep4, "Different dependency_job_id"

    # Different type
    job_dep5 = JobDependency(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "4f52804b-2fad-4e00-92c8-b593da3a8ed4",
        "ARTIFACT",
        "2021-01-01T12:30:45",
        "condition",
    )
    assert job_dep1 != job_dep5, "Different type"

    # Different created_at
    job_dep6 = JobDependency(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "4f52804b-2fad-4e00-92c8-b593da3a8ed4",
        "SUCCESS",
        "2021-01-02T12:30:45",
        "condition",
    )
    assert job_dep1 != job_dep6, "Different created_at"

    # Different condition
    job_dep7 = JobDependency(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "4f52804b-2fad-4e00-92c8-b593da3a8ed4",
        "SUCCESS",
        "2021-01-01T12:30:45",
        "different condition",
    )
    assert job_dep1 != job_dep7, "Different condition"

    # Compare with non-JobDependency object
    assert job_dep1 != 42, "Not a JobDependency object"


@pytest.mark.unit
def test_itemToJobDependency(
    example_job_dependency, example_job_dependency_minimal
):
    """Test the item_to_job_dependency() function."""
    # Test with full job dependency
    item = example_job_dependency.to_item()
    job_dependency = item_to_job_dependency(item)
    assert job_dependency == example_job_dependency

    # Test with minimal job dependency
    item = example_job_dependency_minimal.to_item()
    job_dependency = item_to_job_dependency(item)
    assert job_dependency == example_job_dependency_minimal

    # Test with missing required keys
    with pytest.raises(ValueError, match="Invalid item format"):
        item_to_job_dependency(
            {"PK": {"S": "JOB#id"}, "SK": {"S": "DEPENDS_ON#dependency_id"}}
        )

    # Test with invalid item format
    with pytest.raises(
        ValueError, match="Error converting item to JobDependency"
    ):
        item_to_job_dependency(
            {
                "PK": {"S": "JOB#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
                "SK": {"S": "DEPENDS_ON#4f52804b-2fad-4e00-92c8-b593da3a8ed4"},
                "TYPE": {"S": "JOB_DEPENDENCY"},
                "dependent_job_id": {
                    "S": "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
                },
                "dependency_job_id": {
                    "INVALID_TYPE": "4f52804b-2fad-4e00-92c8-b593da3a8ed4"
                },  # Invalid type
                "type": {"S": "SUCCESS"},
                "created_at": {"S": "2021-01-01T12:30:45"},
            }
        )
