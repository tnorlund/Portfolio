"""DynamoDB entity for a materialized visual receipt row."""

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Generator

from receipt_dynamo.entities.util import (
    _repr_str,
    assert_valid_uuid,
)


@dataclass(eq=True, unsafe_hash=False)
class ReceiptRow:
    """
    Represents a materialized visual row of a receipt stored in DynamoDB.

    Apple Vision OCR frequently splits one printed row into multiple
    ReceiptLine entities (e.g. "TOTAL" on the left and "6.70" on the right).
    A ReceiptRow persists the visual-row grouping
    (``receipt_chroma.embedding.formatting.line_format
    .group_lines_into_visual_rows``) as a first-class entity so downstream
    consumers (row-granularity sections, the Chroma lines collection, the
    embedding batch pipeline) share one durable row identity instead of
    re-deriving it.

    Identity convention: ``row_id`` is the row's *primary line id* — the
    ``line_id`` of the leftmost member line (``get_primary_line_id``). This
    is the same id the Chroma lines collection uses for the row's embedding
    (``IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{primary:05d}``), so
    ReceiptRow rows and lines-collection entries join without translation.

    Attributes:
        receipt_id (int): Identifier for the receipt.
        image_id (str): UUID identifying the image to which the row belongs.
        row_id (int): The row's primary line id (leftmost member line).
            Must equal ``line_ids[0]``.
        line_ids (list[int]): Member line ids ordered left-to-right.
        grouping_version (str): Version tag of the grouping algorithm that
            produced this row (e.g. "visual-rows-v1"). Lets a future grouping
            change coexist with, and be distinguished from, old rows.
        y_min (float): Top of the row's y-band (min bounding-box y over
            member lines, normalized image coordinates).
        y_max (float): Bottom of the row's y-band (max y + height).
        x_min (float): Left edge of the row's x-extent (min bounding-box x).
        x_max (float): Right edge of the row's x-extent (max x + width).
        created_at (datetime): Timestamp when this row was created.
    """

    REQUIRED_KEYS = {
        "PK",
        "SK",
        "line_ids",
        "grouping_version",
        "y_min",
        "y_max",
        "x_min",
        "x_max",
        "created_at",
    }

    receipt_id: int
    image_id: str
    row_id: int
    line_ids: list[int]
    grouping_version: str
    y_min: float
    y_max: float
    x_min: float
    x_max: float
    created_at: datetime | str
    price_column_x: float | None = None
    label_text: str | None = None
    amount_text: str | None = None
    amount_line_id: int | None = None
    amount_word_id: int | None = None

    def __post_init__(self):
        """Validate and initialize the ReceiptRow instance."""
        if not isinstance(self.receipt_id, int):
            raise ValueError("receipt_id must be an integer")
        if self.receipt_id <= 0:
            raise ValueError("receipt_id must be positive")

        assert_valid_uuid(self.image_id)

        if isinstance(self.row_id, bool) or not isinstance(self.row_id, int):
            raise ValueError("row_id must be an integer")
        if self.row_id < 0:
            raise ValueError("row_id must be non-negative")

        if not isinstance(self.line_ids, list):
            raise ValueError("line_ids must be a list")
        if not self.line_ids:
            raise ValueError("line_ids must not be empty")
        if not all(
            isinstance(line_id, int) and not isinstance(line_id, bool)
            for line_id in self.line_ids
        ):
            raise ValueError("line_ids must contain only integers")
        if any(line_id < 0 for line_id in self.line_ids):
            # Members reference ReceiptLine.line_id, which is non-negative.
            raise ValueError("line_ids must be non-negative")
        if len(set(self.line_ids)) != len(self.line_ids):
            raise ValueError("line_ids must not contain duplicates")
        if self.row_id != self.line_ids[0]:
            raise ValueError(
                "row_id must equal line_ids[0] (the primary/leftmost "
                f"line id); got row_id={self.row_id}, "
                f"line_ids[0]={self.line_ids[0]}"
            )

        if (
            not isinstance(self.grouping_version, str)
            or not self.grouping_version
        ):
            raise ValueError("grouping_version must be a non-empty string")

        for name in ("y_min", "y_max", "x_min", "x_max"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be a number")
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
            setattr(self, name, float(value))
        if self.y_min > self.y_max:
            raise ValueError("y_min must be <= y_max")
        if self.x_min > self.x_max:
            raise ValueError("x_min must be <= x_max")

        self._validate_pairing()

        if isinstance(self.created_at, str):
            self.created_at = datetime.fromisoformat(self.created_at)
        elif isinstance(self.created_at, datetime):
            pass  # Already a datetime
        else:
            raise ValueError("created_at must be a datetime or ISO string")

    def _validate_pairing(self) -> None:
        """Validate optional price-column label/amount provenance."""

        if self.price_column_x is not None:
            if isinstance(self.price_column_x, bool) or not isinstance(
                self.price_column_x, (int, float)
            ):
                raise ValueError("price_column_x must be a number or None")
            if not math.isfinite(self.price_column_x):
                raise ValueError("price_column_x must be finite")
            self.price_column_x = float(self.price_column_x)

        pairing = (
            self.amount_text,
            self.amount_line_id,
            self.amount_word_id,
        )
        if any(value is not None for value in pairing) and not all(
            value is not None for value in pairing
        ):
            raise ValueError(
                "amount_text, amount_line_id, and amount_word_id must be "
                "set together"
            )
        if self.label_text is not None and not isinstance(
            self.label_text, str
        ):
            raise ValueError("label_text must be a string or None")
        if self.amount_text is not None and not isinstance(
            self.amount_text, str
        ):
            raise ValueError("amount_text must be a string or None")
        for name in ("amount_line_id", "amount_word_id"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
            ):
                raise ValueError(f"{name} must be a non-negative integer")
        if self.amount_line_id is not None:
            if self.amount_line_id not in self.line_ids:
                raise ValueError("amount_line_id must belong to the row")
            if self.price_column_x is None:
                raise ValueError(
                    "price_column_x is required for an amount pairing"
                )

    @property
    def key(self) -> dict[str, Any]:
        """Generate the primary key for the receipt row."""
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {
                "S": (
                    f"RECEIPT#{self.receipt_id:05d}#" f"ROW#{self.row_id:05d}"
                )
            },
        }

    def to_item(self) -> dict[str, Any]:
        """Convert the ReceiptRow to a DynamoDB item."""
        item = {
            **self.key,
            "TYPE": {"S": "RECEIPT_ROW"},
            "line_ids": {
                "L": [{"N": str(line_id)} for line_id in self.line_ids]
            },
            "grouping_version": {"S": self.grouping_version},
            "y_min": {"N": str(self.y_min)},
            "y_max": {"N": str(self.y_max)},
            "x_min": {"N": str(self.x_min)},
            "x_max": {"N": str(self.x_max)},
            "created_at": {"S": self._created_at_iso()},
        }
        if self.price_column_x is not None:
            item["price_column_x"] = {"N": str(self.price_column_x)}
        if self.label_text is not None:
            item["label_text"] = {"S": self.label_text}
        if self.amount_text is not None:
            item["amount_text"] = {"S": self.amount_text}
            item["amount_line_id"] = {"N": str(self.amount_line_id)}
            item["amount_word_id"] = {"N": str(self.amount_word_id)}
        return item

    def __repr__(self) -> str:
        """Returns a string representation of the ReceiptRow object."""
        return (
            f"ReceiptRow("
            f"receipt_id={self.receipt_id}, "
            f"image_id={_repr_str(self.image_id)}, "
            f"row_id={self.row_id}, "
            f"line_ids={self.line_ids}, "
            f"grouping_version={_repr_str(self.grouping_version)}, "
            f"y_min={self.y_min}, "
            f"y_max={self.y_max}, "
            f"x_min={self.x_min}, "
            f"x_max={self.x_max}, "
            f"price_column_x={self.price_column_x}, "
            f"label_text={_repr_str(self.label_text)}, "
            f"amount_text={_repr_str(self.amount_text)}, "
            f"amount_line_id={self.amount_line_id}, "
            f"amount_word_id={self.amount_word_id}, "
            f"created_at={_repr_str(self._created_at_iso())}"
            f")"
        )

    def __iter__(self) -> Generator[tuple[str, Any], None, None]:
        """Iterate over the attributes of the ReceiptRow."""
        yield "image_id", self.image_id
        yield "receipt_id", self.receipt_id
        yield "row_id", self.row_id
        yield "line_ids", self.line_ids
        yield "grouping_version", self.grouping_version
        yield "y_min", self.y_min
        yield "y_max", self.y_max
        yield "x_min", self.x_min
        yield "x_max", self.x_max
        yield "price_column_x", self.price_column_x
        yield "label_text", self.label_text
        yield "amount_text", self.amount_text
        yield "amount_line_id", self.amount_line_id
        yield "amount_word_id", self.amount_word_id
        yield "created_at", self._created_at_iso()

    def __hash__(self) -> int:
        """Return a hash of the ReceiptRow."""
        return hash(
            (
                self.receipt_id,
                self.image_id,
                self.row_id,
                tuple(self.line_ids),
                self.grouping_version,
                self.y_min,
                self.y_max,
                self.x_min,
                self.x_max,
                self.price_column_x,
                self.label_text,
                self.amount_text,
                self.amount_line_id,
                self.amount_word_id,
                self._created_at_iso(),
            )
        )

    def _created_at_iso(self) -> str:
        """Return the normalized timestamp after ``__post_init__``."""

        if not isinstance(self.created_at, datetime):  # pragma: no cover
            raise TypeError("created_at was not normalized")
        return self.created_at.isoformat()

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> "ReceiptRow":
        """Converts a DynamoDB item to a ReceiptRow object.

        Args:
            item: The DynamoDB item to convert.

        Returns:
            ReceiptRow: The ReceiptRow object.

        Raises:
            ValueError: When the item format is invalid.
        """
        if not cls.REQUIRED_KEYS.issubset(item.keys()):
            missing_keys = cls.REQUIRED_KEYS - set(item.keys())
            raise ValueError(f"Item is missing required keys: {missing_keys}")

        try:
            pk = item["PK"]["S"]
            if not pk.startswith("IMAGE#"):
                raise ValueError(f"PK must start with IMAGE#, got {pk!r}")
            image_id = pk.split("#")[1]
            sk_parts = item["SK"]["S"].split("#")
            if (
                len(sk_parts) != 4
                or sk_parts[0] != "RECEIPT"
                or sk_parts[2] != "ROW"
            ):
                raise ValueError(
                    "SK must have the form RECEIPT#{id:05d}#ROW#{row:05d}, "
                    f"got {item['SK']['S']!r}"
                )
            receipt_id = int(sk_parts[1])
            row_id = int(sk_parts[3])

            line_ids = [int(li["N"]) for li in item["line_ids"]["L"]]
            grouping_version = item["grouping_version"]["S"]
            y_min = float(item["y_min"]["N"])
            y_max = float(item["y_max"]["N"])
            x_min = float(item["x_min"]["N"])
            x_max = float(item["x_max"]["N"])
            created_at = datetime.fromisoformat(item["created_at"]["S"])
            price_column_x = (
                float(item["price_column_x"]["N"])
                if "price_column_x" in item
                else None
            )
            label_text = (
                item["label_text"]["S"] if "label_text" in item else None
            )
            amount_text = (
                item["amount_text"]["S"] if "amount_text" in item else None
            )
            amount_line_id = (
                int(item["amount_line_id"]["N"])
                if "amount_line_id" in item
                else None
            )
            amount_word_id = (
                int(item["amount_word_id"]["N"])
                if "amount_word_id" in item
                else None
            )

            return cls(
                receipt_id=receipt_id,
                image_id=image_id,
                row_id=row_id,
                line_ids=line_ids,
                grouping_version=grouping_version,
                y_min=y_min,
                y_max=y_max,
                x_min=x_min,
                x_max=x_max,
                created_at=created_at,
                price_column_x=price_column_x,
                label_text=label_text,
                amount_text=amount_text,
                amount_line_id=amount_line_id,
                amount_word_id=amount_word_id,
            )
        except (KeyError, IndexError, ValueError) as e:
            raise ValueError(
                f"Error converting item to ReceiptRow: {e}"
            ) from e


def item_to_receipt_row(item: dict[str, Any]) -> ReceiptRow:
    """Converts a DynamoDB item to a ReceiptRow object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        ReceiptRow: The ReceiptRow object.

    Raises:
        ValueError: When the item format is invalid.
    """
    return ReceiptRow.from_item(item)
