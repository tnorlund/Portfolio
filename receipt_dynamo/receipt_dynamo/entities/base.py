from dataclasses import asdict, dataclass, fields
from typing import Any, Iterable


@dataclass
class DynamoDBEntity:
    """Base dataclass for DynamoDB entities."""

    def __iter__(self):
        for f in fields(self):
            yield f.name, getattr(self, f.name)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def validate_keys(
        item: dict[str, Any], required_keys: Iterable[str]
    ) -> set[str]:
        """Return any missing required keys."""
        return set(required_keys) - set(item.keys())
