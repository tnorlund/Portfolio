from typing import Any, Dict, Generator, List, Optional, Tuple, Union
from datetime import datetime
from receipt_dynamo.entities.util import assert_valid_uuid, _repr_str


class Instance:
    """
    Represents an EC2 instance and its associated metadata stored in a DynamoDB table.

    This class encapsulates instance-related information such as its unique identifier,
    instance type, GPU count, status, and other attributes. It is designed to support
    operations such as generating DynamoDB keys and converting instance metadata to a
    DynamoDB-compatible item.

    Attributes:
        instance_id (str): UUID identifying the instance.
        instance_type (str): The EC2 instance type (e.g., p3.2xlarge).
        gpu_count (int): Number of GPUs on the instance.
        status (str): The current status of the instance (pending, running, stopped, terminated).
        launched_at (datetime): The timestamp when the instance was launched.
        ip_address (str): The IP address of the instance.
        availability_zone (str): The AWS availability zone of the instance.
        is_spot (bool): Whether the instance is a spot instance.
        health_status (str): The health status of the instance (healthy, unhealthy).
    """

    def __init__(
        self,
        instance_id: str,
        instance_type: str,
        gpu_count: int,
        status: str,
        launched_at: datetime,
        ip_address: str,
        availability_zone: str,
        is_spot: bool,
        health_status: str,
    ):
        """Initializes a new Instance object for DynamoDB.

        Args:
            instance_id (str): UUID identifying the instance.
            instance_type (str): The EC2 instance type.
            gpu_count (int): Number of GPUs on the instance.
            status (str): The current status of the instance.
            launched_at (datetime): The timestamp when the instance was launched.
            ip_address (str): The IP address of the instance.
            availability_zone (str): The AWS availability zone of the instance.
            is_spot (bool): Whether the instance is a spot instance.
            health_status (str): The health status of the instance.

        Raises:
            ValueError: If any parameter is of an invalid type or has an invalid value.
        """
        assert_valid_uuid(instance_id)
        self.instance_id = instance_id

        if not isinstance(instance_type, str) or not instance_type:
            raise ValueError("instance_type must be a non-empty string")
        self.instance_type = instance_type

        if not isinstance(gpu_count, int) or gpu_count < 0:
            raise ValueError("gpu_count must be a non-negative integer")
        self.gpu_count = gpu_count

        valid_statuses = ["pending", "running", "stopped", "terminated"]
        if not isinstance(status, str) or status.lower() not in valid_statuses:
            raise ValueError(f"status must be one of {valid_statuses}")
        self.status = status.lower()

        if isinstance(launched_at, datetime):
            self.launched_at = launched_at.isoformat()
        elif isinstance(launched_at, str):
            self.launched_at = launched_at
        else:
            raise ValueError("launched_at must be a datetime object or a string")

        if not isinstance(ip_address, str):
            raise ValueError("ip_address must be a string")
        self.ip_address = ip_address

        if not isinstance(availability_zone, str) or not availability_zone:
            raise ValueError("availability_zone must be a non-empty string")
        self.availability_zone = availability_zone

        if not isinstance(is_spot, bool):
            raise ValueError("is_spot must be a boolean")
        self.is_spot = is_spot

        valid_health_statuses = ["healthy", "unhealthy", "unknown"]
        if not isinstance(health_status, str) or health_status.lower() not in valid_health_statuses:
            raise ValueError(f"health_status must be one of {valid_health_statuses}")
        self.health_status = health_status.lower()

    def key(self) -> dict:
        """Generates the primary key for the instance.

        Returns:
            dict: The primary key for the instance.
        """
        return {"PK": {"S": f"INSTANCE#{self.instance_id}"}, "SK": {"S": "INSTANCE"}}

    def gsi1_key(self) -> dict:
        """Generates the GSI1 key for the instance.

        Returns:
            dict: The GSI1 key for the instance.
        """
        return {
            "GSI1PK": {"S": f"STATUS#{self.status}"},
            "GSI1SK": {"S": f"INSTANCE#{self.instance_id}"},
        }

    def to_item(self) -> dict:
        """Converts the Instance object to a DynamoDB item.

        Returns:
            dict: A dictionary representing the Instance object as a DynamoDB item.
        """
        item = {
            **self.key(),
            **self.gsi1_key(),
            "TYPE": {"S": "INSTANCE"},
            "instance_type": {"S": self.instance_type},
            "gpu_count": {"N": str(self.gpu_count)},
            "status": {"S": self.status},
            "launched_at": {"S": self.launched_at},
            "ip_address": {"S": self.ip_address},
            "availability_zone": {"S": self.availability_zone},
            "is_spot": {"BOOL": self.is_spot},
            "health_status": {"S": self.health_status},
        }
        return item

    def __repr__(self) -> str:
        """Returns a string representation of the Instance object.

        Returns:
            str: A string representation of the Instance object.
        """
        return (
            "Instance("
            f"instance_id={_repr_str(self.instance_id)}, "
            f"instance_type={_repr_str(self.instance_type)}, "
            f"gpu_count={self.gpu_count}, "
            f"status={_repr_str(self.status)}, "
            f"launched_at={_repr_str(self.launched_at)}, "
            f"ip_address={_repr_str(self.ip_address)}, "
            f"availability_zone={_repr_str(self.availability_zone)}, "
            f"is_spot={self.is_spot}, "
            f"health_status={_repr_str(self.health_status)}"
            ")"
        )

    def __iter__(self) -> Generator[Tuple[str, Any], None, None]:
        """Returns an iterator over the Instance object's attributes.

        Returns:
            Generator[Tuple[str, Any], None, None]: An iterator over the Instance object's attribute name/value pairs.
        """
        yield "instance_id", self.instance_id
        yield "instance_type", self.instance_type
        yield "gpu_count", self.gpu_count
        yield "status", self.status
        yield "launched_at", self.launched_at
        yield "ip_address", self.ip_address
        yield "availability_zone", self.availability_zone
        yield "is_spot", self.is_spot
        yield "health_status", self.health_status

    def __eq__(self, other) -> bool:
        """Determines whether two Instance objects are equal.

        Args:
            other (Instance): The other Instance object to compare.

        Returns:
            bool: True if the Instance objects are equal, False otherwise.

        Note:
            If other is not an instance of Instance, False is returned.
        """
        if not isinstance(other, Instance):
            return False
        return (
            self.instance_id == other.instance_id
            and self.instance_type == other.instance_type
            and self.gpu_count == other.gpu_count
            and self.status == other.status
            and self.launched_at == other.launched_at
            and self.ip_address == other.ip_address
            and self.availability_zone == other.availability_zone
            and self.is_spot == other.is_spot
            and self.health_status == other.health_status
        )

    def __hash__(self) -> int:
        """Returns the hash value of the Instance object.

        Returns:
            int: The hash value of the Instance object.
        """
        return hash(
            (
                self.instance_id,
                self.instance_type,
                self.gpu_count,
                self.status,
                self.launched_at,
                self.ip_address,
                self.availability_zone,
                self.is_spot,
                self.health_status,
            )
        )


def itemToInstance(item: dict) -> Instance:
    """Converts a DynamoDB item to an Instance object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        Instance: The Instance object represented by the DynamoDB item.

    Raises:
        ValueError: When the item format is invalid.
    """
    required_keys = {
        "PK",
        "SK",
        "TYPE",
        "instance_type",
        "gpu_count",
        "status",
        "launched_at",
        "ip_address",
        "availability_zone",
        "is_spot",
        "health_status",
    }
    if not required_keys.issubset(item.keys()):
        missing_keys = required_keys - item.keys()
        additional_keys = item.keys() - required_keys
        raise ValueError(
            f"Invalid item format\nmissing keys: {missing_keys}\nadditional keys: {additional_keys}"
        )

    try:
        # Parse instance_id from the PK
        instance_id = item["PK"]["S"].split("#")[1]

        # Extract basic fields
        instance_type = item["instance_type"]["S"]
        gpu_count = int(item["gpu_count"]["N"])
        status = item["status"]["S"]
        launched_at = item["launched_at"]["S"]
        ip_address = item["ip_address"]["S"]
        availability_zone = item["availability_zone"]["S"]
        is_spot = item["is_spot"]["BOOL"]
        health_status = item["health_status"]["S"]

        return Instance(
            instance_id=instance_id,
            instance_type=instance_type,
            gpu_count=gpu_count,
            status=status,
            launched_at=launched_at,
            ip_address=ip_address,
            availability_zone=availability_zone,
            is_spot=is_spot,
            health_status=health_status,
        )
    except KeyError as e:
        raise ValueError(f"Error converting item to Instance: {e}") 