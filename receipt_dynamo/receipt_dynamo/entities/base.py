from dataclasses import asdict, dataclass, fields
from typing import Any, Dict, Iterable


@dataclass(eq=True, unsafe_hash=False)
class DynamoDBEntity:
    """Base dataclass for DynamoDB entities."""

    def __iter__(self):
        for f in fields(self):
            yield f.name, getattr(self, f.name)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def validate_keys(item: Dict[str, Any], required_keys: Iterable[str]) -> set[str]:
        """Return any missing required keys."""
        return set(required_keys) - set(item.keys())
