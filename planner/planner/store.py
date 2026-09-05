"""Public storage interface; DynamoDB is the sole backend."""

from planner.data.client import DynamoClient as Store
from planner.data.client import encode

__all__ = ["Store", "encode"]
