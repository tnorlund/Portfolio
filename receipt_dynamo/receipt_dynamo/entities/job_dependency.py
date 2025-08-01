from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Generator, Optional, Tuple

from receipt_dynamo.entities.util import _repr_str, assert_valid_uuid


@dataclass(eq=True, unsafe_hash=False)
class JobDependency:
    """
    Represents a dependency relationship between jobs stored in DynamoDB.

    This class encapsulates job dependency information including the
    dependent job, dependency job, type of dependency, condition, and
    when it was created.
    It supports operations like generating DynamoDB keys and converting
    dependency data to DynamoDB-compatible items.

    Attributes:
        dependent_job_id (str): UUID identifying the job that depends on
            another.
        dependency_job_id (str): UUID identifying the job that is
            depended on.
        type (str): The type of dependency (e.g., "COMPLETION", "SUCCESS",
            "ARTIFACT").
        condition (str, optional): Specific condition for the dependency.
        created_at (str): The timestamp when the dependency was created.
    """

    dependent_job_id: str
    dependency_job_id: str
    type: str
    created_at: str
    condition: Optional[str] = None

    def __post_init__(self):
        """Validates fields after dataclass initialization.

        Raises:
            ValueError: If any parameter is of invalid type or has
                invalid value.
        """
        assert_valid_uuid(self.dependent_job_id)
        assert_valid_uuid(self.dependency_job_id)

        if self.dependent_job_id == self.dependency_job_id:
            raise ValueError("A job cannot depend on itself")

        valid_types = ["COMPLETION", "SUCCESS", "FAILURE", "ARTIFACT"]
        if not isinstance(self.type, str) or self.type.upper() not in valid_types:
            raise ValueError(f"type must be one of {valid_types}")
        self.type = self.type.upper()

        # Handle created_at conversion
        if isinstance(self.created_at, datetime):
            self.created_at = self.created_at.isoformat()
        elif not isinstance(self.created_at, str):
            raise ValueError(
                "created_at must be a datetime object or a string"
            )

        if self.condition is not None and not isinstance(self.condition, str):
            raise ValueError("condition must be a string")

    @property
    def key(self) -> Dict[str, Any]:
        """Generates the primary key for the job dependency.

        Returns:
            dict: The primary key for the job dependency.
        """
        return {
            "PK": {"S": f"JOB#{self.dependent_job_id}"},
            "SK": {"S": f"DEPENDS_ON#{self.dependency_job_id}"},
        }

    def gsi1_key(self) -> Dict[str, Any]:
        """Generates the GSI1 key for the job dependency.

        Returns:
            dict: The GSI1 key for the job dependency.
        """
        return {
            "GSI1PK": {"S": "DEPENDENCY"},
            "GSI1SK": {
                "S": (
                    f"DEPENDENT#{self.dependent_job_id}"
                    f"#DEPENDENCY#{self.dependency_job_id}"
                )
            },
        }

    def gsi2_key(self) -> Dict[str, Any]:
        """Generates the GSI2 key for the job dependency.

        Returns:
            dict: The GSI2 key for the job dependency.
        """
        return {
            "GSI2PK": {"S": "DEPENDENCY"},
            "GSI2SK": {
                "S": (
                    f"DEPENDED_BY#{self.dependency_job_id}"
                    f"#DEPENDENT#{self.dependent_job_id}"
                )
            },
        }

    def to_item(self) -> Dict[str, Any]:
        """Converts the JobDependency object to a DynamoDB item.

        Returns:
            dict: A dictionary representing the JobDependency object as a
                DynamoDB item.
        """
        item = {
            **self.key,
            **self.gsi1_key(),
            **self.gsi2_key(),
            "TYPE": {"S": "JOB_DEPENDENCY"},
            "dependent_job_id": {"S": self.dependent_job_id},
            "dependency_job_id": {"S": self.dependency_job_id},
            "type": {"S": self.type},
            "created_at": {"S": self.created_at},
        }

        if self.condition is not None:
            item["condition"] = {"S": self.condition}

        return item

    def __repr__(self) -> str:
        """Returns a string representation of the JobDependency object.

        Returns:
            str: A string representation of the JobDependency object.
        """
        return (
            "JobDependency("
            f"\n  dependent_job_id={_repr_str(self.dependent_job_id)},"
            f"\n  dependency_job_id={_repr_str(self.dependency_job_id)},"
            f"\n  type={_repr_str(self.type)},"
            f"\n  created_at={_repr_str(self.created_at)},"
            f"\n  condition={_repr_str(self.condition)}"
            "\n)"
        )

    def __iter__(self) -> Generator[Tuple[str, Any], None, None]:
        """Returns an iterator over the JobDependency object's attributes.

        Returns:
            Generator[Tuple[str, Any], None, None]: An iterator over the
                JobDependency object's attribute name/value pairs.
        """
        yield "dependent_job_id", self.dependent_job_id
        yield "dependency_job_id", self.dependency_job_id
        yield "type", self.type
        yield "created_at", self.created_at
        yield "condition", self.condition


    def __hash__(self) -> int:
        """Returns the hash value of the JobDependency object.

        Returns:
            int: The hash value of the JobDependency object.
        """
        return hash(
            (
                self.dependent_job_id,
                self.dependency_job_id,
                self.type,
                self.created_at,
                self.condition,
            )
        )


def item_to_job_dependency(item: Dict[str, Any]) -> JobDependency:
    """Converts a DynamoDB item to a JobDependency object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        JobDependency: The JobDependency object represented by the
            DynamoDB item.

    Raises:
        ValueError: When the item format is invalid.
    """
    required_keys = {
        "PK",
        "SK",
        "TYPE",
        "dependent_job_id",
        "dependency_job_id",
        "type",
        "created_at",
    }
    if not required_keys.issubset(item.keys()):
        missing_keys = required_keys - item.keys()
        additional_keys = item.keys() - required_keys
        message = (
            "Invalid item format\n"
            f"missing keys: {missing_keys}\n"
            f"additional keys: {additional_keys}"
        )
        raise ValueError(message)

    try:
        # Extract required fields
        dependent_job_id = item["dependent_job_id"]["S"]
        dependency_job_id = item["dependency_job_id"]["S"]
        type = item["type"]["S"]
        created_at = item["created_at"]["S"]

        # Extract optional fields
        condition = item.get("condition", {}).get("S")

        return JobDependency(
            dependent_job_id=dependent_job_id,
            dependency_job_id=dependency_job_id,
            type=type,
            created_at=created_at,
            condition=condition,
        )
    except KeyError as e:
        raise ValueError(f"Error converting item to JobDependency: {e}") from e
