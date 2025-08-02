from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from receipt_dynamo.constants import LabelStatus
from receipt_dynamo.entities.util import (
    _repr_str,
    assert_type,
    normalize_enum,
)


@dataclass(eq=True, unsafe_hash=False)
class LabelMetadata:
    label: str
    status: str
    aliases: List[str]
    description: str
    schema_version: int
    last_updated: datetime
    label_target: Optional[str] = None
    receipt_refs: Optional[list[tuple[str, int]]] = None

    def __post_init__(self) -> None:
        # Convert datetime to str if needed for last_updated
        if isinstance(self.last_updated, datetime):
            # Keep as datetime - no conversion needed for this field
            pass
        
        assert_type("label", self.label, str, ValueError)

        self.status = normalize_enum(self.status, LabelStatus)

        assert_type("aliases", self.aliases, list, ValueError)

        assert_type("description", self.description, str, ValueError)

        assert_type("schema_version", self.schema_version, int, ValueError)

        assert_type("last_updated", self.last_updated, datetime, ValueError)

        if self.label_target is not None:
            assert_type("label_target", self.label_target, str, ValueError)

        if self.receipt_refs is not None:
            assert_type("receipt_refs", self.receipt_refs, list, ValueError)
            if not all(
                isinstance(ref, tuple)
                and len(ref) == 2
                and isinstance(ref[0], str)
                and isinstance(ref[1], int)
                for ref in self.receipt_refs
            ):
                raise ValueError(
                    "receipt_refs must be a list of (image_id: str, "
                    "receipt_id: int) tuples"
                )

    @property
    def key(self) -> Dict[str, Any]:
        return {
            "PK": {"S": f"LABEL#{self.label}"},
            "SK": {"S": "METADATA"},
        }

    def gsi1_key(self) -> Dict[str, Any]:
        return {
            "GSI1PK": {"S": f"LABEL#{self.label}"},
            "GSI1SK": {"S": "METADATA"},
        }

    def gsi2_key(self) -> Dict[str, Any]:
        return {
            "GSI2PK": {"S": f"LABEL_TARGET#{self.label_target}"},
            "GSI2SK": {"S": f"LABEL#{self.label}"},
        }

    def to_item(self) -> Dict[str, Any]:
        return {
            **self.key,
            **self.gsi1_key(),
            "status": {"S": self.status},
            "aliases": {"SS": self.aliases},
            "description": {"S": self.description},
            "schema_version": {"N": str(self.schema_version)},
            "last_updated": {"S": self.last_updated.isoformat()},
            "label_target": (
                {"S": self.label_target}
                if self.label_target
                else {"NULL": True}
            ),
            "receipt_refs": (
                {
                    "L": [
                        {
                            "M": {
                                "image_id": {"S": image_id},
                                "receipt_id": {"N": str(receipt_id)},
                            }
                        }
                        for image_id, receipt_id in self.receipt_refs
                    ]
                }
                if self.receipt_refs
                else {"NULL": True}
            ),
        }

    def __repr__(self) -> str:
        return (
            "LabelMetadata("
            f"label={_repr_str(self.label)}, "
            f"status={_repr_str(self.status)}, "
            f"aliases={_repr_str(self.aliases)}, "
            f"description={_repr_str(self.description)}, "
            f"schema_version={_repr_str(self.schema_version)}, "
            f"last_updated={_repr_str(self.last_updated)}, "
            f"receipt_refs={_repr_str(self.receipt_refs)}"
            ")"
        )

    def __str__(self) -> str:
        return self.__repr__()


def item_to_label_metadata(item: Dict[str, Any]) -> LabelMetadata:
    required_keys = {
        "status",
        "aliases",
        "description",
        "schema_version",
        "last_updated",
    }
    if not required_keys.issubset(item.keys()):
        missing_keys = required_keys - item.keys()
        additional_keys = item.keys() - required_keys
        raise ValueError(
            f"Invalid item format\nmissing keys: {missing_keys}\n"
            f"additional keys: {additional_keys}"
        )

    try:
        label = item["PK"]["S"].split("#")[1]
        status = item["status"]["S"]
        aliases = item["aliases"]["SS"]
        description = item["description"]["S"]
        schema_version = int(item["schema_version"]["N"])
        last_updated = datetime.fromisoformat(item["last_updated"]["S"])
        label_target = (
            item["label_target"]["S"] if item["label_target"]["S"] else None
        )
        receipt_refs = (
            [
                (r["M"]["image_id"]["S"], int(r["M"]["receipt_id"]["N"]))
                for r in item["receipt_refs"]["L"]
            ]
            if "receipt_refs" in item
            and item["receipt_refs"] != {"NULL": True}
            else None
        )

        return LabelMetadata(
            label=label,
            status=status,
            aliases=aliases,
            description=description,
            schema_version=schema_version,
            last_updated=last_updated,
            label_target=label_target,
            receipt_refs=receipt_refs,
        )
    except Exception as e:
        raise ValueError(f"Error converting item to LabelMetadata: {e}") from e
