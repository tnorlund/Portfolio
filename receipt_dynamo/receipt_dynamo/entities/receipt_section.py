from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import TYPE_CHECKING, Any, Generator, Sequence

from receipt_dynamo.constants import SectionType, ValidationStatus
from receipt_dynamo.entities.util import (
    _repr_str,
    assert_valid_uuid,
    validate_non_negative_int,
    validate_positive_int,
)

if TYPE_CHECKING:
    from receipt_dynamo.entities.receipt_row import ReceiptRow


@dataclass(eq=True, unsafe_hash=False)
class ReceiptSection:
    """
    Represents a classified section of a receipt stored in DynamoDB.

    This entity tracks sections identified by machine learning models,
    including header, body, footer, and other semantically meaningful
    segments of a receipt.

    Attributes:
        receipt_id (int): Identifier for the receipt.
        image_id (str): UUID identifying the image to which the section
            belongs.
        section_type (str): The type of section (e.g., "HEADER", "FOOTER",
            "LINE_ITEMS", etc.)
        line_ids (list[int]): The line IDs in this section.
        created_at (datetime): Timestamp when this section was created.
        confidence (float | None): Optional model confidence in [0, 1].
        model_source (str | None): Optional model / pipeline that produced this
            section (e.g. "section-seed-v0"). Used to version writes and to
            distinguish generations of rows.
        validation_status (str | None): Optional QA status
            (PENDING / VALID / INVALID / NEEDS_REVIEW) — machine-seeded rows
            land PENDING and are promoted by QA.
        row_ids (list[int] | None): Optional ReceiptRow references (each id
            is a row's primary line id). When set, the section is
            row-granular and ``line_ids`` must equal the union of the
            referenced rows' ``line_ids`` (see
            ``validate_section_row_coverage``). ``line_ids`` remains the
            authoritative field for all existing consumers.

    The optional fields are additive: rows written before they existed
    (and readers that ignore them) remain valid.
    """

    REQUIRED_KEYS = {
        "PK",
        "SK",
        "section_type",
        "line_ids",
        "created_at",
    }

    receipt_id: int
    image_id: str
    section_type: str | SectionType
    line_ids: list[int]
    created_at: datetime | str
    confidence: float | None = None
    model_source: str | None = None
    validation_status: str | None = None
    row_ids: list[int] | None = None
    verification_source: str | None = None
    verification_status: str | None = None
    verification_section_type: str | None = None
    verification_confidence: float | None = None
    disagreement_row_ids: list[int] | None = None
    verified_at: datetime | str | None = None

    def __post_init__(self):
        """Validate and initialize the ReceiptSection instance."""
        validate_positive_int("receipt_id", self.receipt_id)

        assert_valid_uuid(self.image_id)

        # Normalize and validate section_type (allow enum or string)
        if isinstance(self.section_type, SectionType):
            section_type_value = self.section_type.value
        elif isinstance(self.section_type, str):
            section_type_value = self.section_type
        else:
            raise ValueError(
                "section_type must be a string or SectionType enum"
            )
        valid_section_types = [t.value for t in SectionType]
        if section_type_value not in valid_section_types:
            raise ValueError(
                f"section_type must be one of: "
                f"{', '.join(valid_section_types)}\nGot: {section_type_value}"
            )
        self.section_type = section_type_value

        if not isinstance(self.line_ids, list):
            raise ValueError("line_ids must be a list")
        if not self.line_ids:
            raise ValueError("line_ids must not be empty")
        for line_id in self.line_ids:
            try:
                validate_non_negative_int("line_id", line_id)
            except ValueError as exc:
                raise ValueError(
                    "line_ids must contain only integers greater than or "
                    "equal to zero"
                ) from exc
        self.line_ids = list(self.line_ids)

        if isinstance(self.created_at, str):
            self.created_at = datetime.fromisoformat(self.created_at)
        elif isinstance(self.created_at, datetime):
            pass  # Already a datetime
        else:
            raise ValueError("created_at must be a datetime or ISO string")

        # Optional fields (additive) --------------------------------------
        if self.confidence is not None:
            if isinstance(self.confidence, bool) or not isinstance(
                self.confidence, (int, float)
            ):
                raise ValueError("confidence must be a number or None")
            self.confidence = float(self.confidence)
            if not isfinite(self.confidence):
                raise ValueError("confidence must be finite")
            if not 0.0 <= self.confidence <= 1.0:
                raise ValueError("confidence must be in [0, 1]")

        if self.model_source is not None and not isinstance(
            self.model_source, str
        ):
            raise ValueError("model_source must be a string or None")

        if self.validation_status is not None:
            status = self.validation_status
            if isinstance(status, ValidationStatus):
                status = status.value
            status = str(status).upper()
            allowed = {s.value for s in ValidationStatus}
            if status not in allowed:
                raise ValueError(
                    "validation_status must be one of "
                    f"{sorted(allowed)} or None; got "
                    f"{self.validation_status!r}"
                )
            self.validation_status = status

        if self.row_ids is not None:
            if not isinstance(self.row_ids, list):
                raise ValueError("row_ids must be a list or None")
            if not self.row_ids:
                raise ValueError("row_ids must not be empty when set")
            if not all(
                isinstance(row_id, int) and not isinstance(row_id, bool)
                for row_id in self.row_ids
            ):
                raise ValueError("row_ids must contain only integers")
            if any(row_id < 0 for row_id in self.row_ids):
                # ReceiptRow.row_id is non-negative; match its contract.
                raise ValueError("row_ids must be non-negative")
            if len(set(self.row_ids)) != len(self.row_ids):
                raise ValueError("row_ids must not contain duplicates")

        self._validate_verification()

    def _validate_verification(self) -> None:
        """Validate optional KNN-verifier provenance."""

        if self.verification_source is not None and not isinstance(
            self.verification_source, str
        ):
            raise ValueError("verification_source must be a string or None")
        if self.verification_status is not None:
            self.verification_status = str(self.verification_status).upper()
            if self.verification_status not in {
                "AGREED",
                "DISAGREED",
                "ABSTAINED",
            }:
                raise ValueError(
                    "verification_status must be AGREED, DISAGREED, "
                    "ABSTAINED, or None"
                )
        if self.verification_section_type is not None:
            valid = {section.value for section in SectionType}
            if self.verification_section_type not in valid:
                raise ValueError("verification_section_type is invalid")
        if self.verification_confidence is not None:
            value = self.verification_confidence
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(
                    "verification_confidence must be a number or None"
                )
            self.verification_confidence = float(value)
            if not isfinite(self.verification_confidence) or not (
                0.0 <= self.verification_confidence <= 1.0
            ):
                raise ValueError("verification_confidence must be in [0, 1]")
        if self.disagreement_row_ids is not None:
            if not isinstance(self.disagreement_row_ids, list) or any(
                isinstance(row_id, bool)
                or not isinstance(row_id, int)
                or row_id < 0
                for row_id in self.disagreement_row_ids
            ):
                raise ValueError(
                    "disagreement_row_ids must contain non-negative integers"
                )
            if len(set(self.disagreement_row_ids)) != len(
                self.disagreement_row_ids
            ):
                raise ValueError(
                    "disagreement_row_ids must not contain duplicates"
                )
        if isinstance(self.verified_at, str):
            self.verified_at = datetime.fromisoformat(self.verified_at)
        elif self.verified_at is not None and not isinstance(
            self.verified_at, datetime
        ):
            raise ValueError(
                "verified_at must be a datetime, ISO string, or None"
            )

    @property
    def key(self) -> dict[str, Any]:
        """Generate the primary key for the receipt section."""
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {
                "S": (
                    f"RECEIPT#{self.receipt_id:05d}#"
                    f"SECTION#{self.section_type}"
                )
            },
        }

    def to_item(self) -> dict[str, Any]:
        """Convert the ReceiptSection to a DynamoDB item.

        Optional fields are only emitted when set, so rows stay identical to
        the pre-existing schema when the new fields are unused.
        """
        self.__post_init__()
        if not isinstance(self.created_at, datetime):
            raise ValueError("created_at must be a datetime or ISO string")
        item = {
            **self.key,
            "TYPE": {"S": "RECEIPT_SECTION"},
            "section_type": {"S": self.section_type},
            "line_ids": {
                "L": [{"N": str(line_id)} for line_id in self.line_ids]
            },
            "created_at": {"S": self.created_at.isoformat()},
        }
        if self.confidence is not None:
            item["confidence"] = {"N": str(self.confidence)}
        if self.model_source is not None:
            item["model_source"] = {"S": self.model_source}
        if self.validation_status is not None:
            item["validation_status"] = {"S": self.validation_status}
        if self.row_ids is not None:
            item["row_ids"] = {
                "L": [{"N": str(row_id)} for row_id in self.row_ids]
            }
        if self.verification_source is not None:
            item["verification_source"] = {"S": self.verification_source}
        if self.verification_status is not None:
            item["verification_status"] = {"S": self.verification_status}
        if self.verification_section_type is not None:
            item["verification_section_type"] = {
                "S": self.verification_section_type
            }
        if self.verification_confidence is not None:
            item["verification_confidence"] = {
                "N": str(self.verification_confidence)
            }
        if self.disagreement_row_ids is not None:
            item["disagreement_row_ids"] = {
                "L": [
                    {"N": str(row_id)} for row_id in self.disagreement_row_ids
                ]
            }
        verified_at = self.verified_at
        if isinstance(verified_at, datetime):
            item["verified_at"] = {"S": verified_at.isoformat()}
        return item

    def __repr__(self) -> str:
        """Returns a string representation of the ReceiptSection object."""
        created_at = (
            self.created_at.isoformat()
            if isinstance(self.created_at, datetime)
            else self.created_at
        )
        return (
            f"ReceiptSection("
            f"receipt_id={self.receipt_id}, "
            f"image_id={_repr_str(self.image_id)}, "
            f"section_type='{self.section_type}', "
            f"line_ids={self.line_ids}, "
            f"created_at={_repr_str(created_at)}, "
            f"confidence={self.confidence}, "
            f"model_source={_repr_str(self.model_source)}, "
            f"validation_status={_repr_str(self.validation_status)}, "
            f"row_ids={self.row_ids}"
            f", verification_status={_repr_str(self.verification_status)}"
            f")"
        )

    def __iter__(self) -> Generator[tuple[str, Any], None, None]:
        """Iterate over the attributes of the ReceiptSection."""
        yield "image_id", self.image_id
        yield "receipt_id", self.receipt_id

        yield "section_type", self.section_type
        yield "line_ids", self.line_ids
        yield "created_at", (
            self.created_at.isoformat()
            if isinstance(self.created_at, datetime)
            else self.created_at
        )
        yield "confidence", self.confidence
        yield "model_source", self.model_source
        yield "validation_status", self.validation_status
        yield "row_ids", self.row_ids
        yield "verification_source", self.verification_source
        yield "verification_status", self.verification_status
        yield "verification_section_type", self.verification_section_type
        yield "verification_confidence", self.verification_confidence
        yield "disagreement_row_ids", self.disagreement_row_ids
        yield "verified_at", (
            self.verified_at.isoformat()
            if isinstance(self.verified_at, datetime)
            else self.verified_at
        )

    def __hash__(self) -> int:
        """Return a hash of the ReceiptSection."""
        return hash(
            (
                self.receipt_id,
                self.image_id,
                self.section_type,
                tuple(self.line_ids),
                (
                    self.created_at.isoformat()
                    if isinstance(self.created_at, datetime)
                    else self.created_at
                ),
                self.confidence,
                self.model_source,
                self.validation_status,
                tuple(self.row_ids) if self.row_ids is not None else None,
                self.verification_source,
                self.verification_status,
                self.verification_section_type,
                self.verification_confidence,
                (
                    tuple(self.disagreement_row_ids)
                    if self.disagreement_row_ids is not None
                    else None
                ),
                (
                    self.verified_at.isoformat()
                    if isinstance(self.verified_at, datetime)
                    else self.verified_at
                ),
            )
        )

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> "ReceiptSection":
        """Converts a DynamoDB item to a ReceiptSection object.

        Args:
            item: The DynamoDB item to convert.

        Returns:
            ReceiptSection: The ReceiptSection object.

        Raises:
            ValueError: When the item format is invalid.
        """
        if not cls.REQUIRED_KEYS.issubset(item.keys()):
            missing_keys = cls.REQUIRED_KEYS - set(item.keys())
            raise ValueError(f"Item is missing required keys: {missing_keys}")

        try:
            image_id = item["PK"]["S"].split("#")[1]
            sk_parts = item["SK"]["S"].split("#")
            receipt_id = int(sk_parts[1])

            # Extract other attributes
            section_type = item["section_type"]["S"]
            line_ids = [int(li["N"]) for li in item["line_ids"]["L"]]
            created_at = datetime.fromisoformat(item["created_at"]["S"])

            # Optional fields (absent on legacy rows)
            confidence = (
                float(item["confidence"]["N"])
                if "confidence" in item
                else None
            )
            model_source = (
                item["model_source"]["S"] if "model_source" in item else None
            )
            validation_status = (
                item["validation_status"]["S"]
                if "validation_status" in item
                else None
            )
            row_ids = (
                [int(ri["N"]) for ri in item["row_ids"]["L"]]
                if "row_ids" in item
                else None
            )
            verification_source = (
                item["verification_source"]["S"]
                if "verification_source" in item
                else None
            )
            verification_status = (
                item["verification_status"]["S"]
                if "verification_status" in item
                else None
            )
            verification_section_type = (
                item["verification_section_type"]["S"]
                if "verification_section_type" in item
                else None
            )
            verification_confidence = (
                float(item["verification_confidence"]["N"])
                if "verification_confidence" in item
                else None
            )
            disagreement_row_ids = (
                [int(row["N"]) for row in item["disagreement_row_ids"]["L"]]
                if "disagreement_row_ids" in item
                else None
            )
            verified_at = (
                datetime.fromisoformat(item["verified_at"]["S"])
                if "verified_at" in item
                else None
            )

            return cls(
                receipt_id=receipt_id,
                image_id=image_id,
                section_type=section_type,
                line_ids=line_ids,
                created_at=created_at,
                confidence=confidence,
                model_source=model_source,
                validation_status=validation_status,
                row_ids=row_ids,
                verification_source=verification_source,
                verification_status=verification_status,
                verification_section_type=verification_section_type,
                verification_confidence=verification_confidence,
                disagreement_row_ids=disagreement_row_ids,
                verified_at=verified_at,
            )
        except (KeyError, IndexError, ValueError) as e:
            raise ValueError(
                f"Error converting item to ReceiptSection: {e}"
            ) from e


def validate_section_row_coverage(
    section: ReceiptSection,
    rows: Sequence["ReceiptRow"],
) -> None:
    """Validate the row-granularity invariant of a ReceiptSection.

    When a section carries ``row_ids``, its ``line_ids`` must equal the
    union of the referenced ReceiptRows' ``line_ids`` (as sets — ordering
    is not significant). Sections without ``row_ids`` are exempt (legacy
    line-granular sections).

    Args:
        section: The section to validate.
        rows: ReceiptRow entities for the section's receipt. Extra rows
            (not referenced by the section) are ignored; every referenced
            row must be present.

    Raises:
        ValueError: If a referenced row is missing from ``rows``, a row
            belongs to a different receipt, or the line_ids union does not
            match the section's line_ids.
    """
    if section.row_ids is None:
        return

    rows_by_id: dict[int, "ReceiptRow"] = {}
    for row in rows:
        if (
            row.image_id != section.image_id
            or row.receipt_id != section.receipt_id
        ):
            raise ValueError(
                f"row {row.row_id} belongs to "
                f"image_id={row.image_id} receipt_id={row.receipt_id}, "
                f"not the section's image_id={section.image_id} "
                f"receipt_id={section.receipt_id}"
            )
        rows_by_id[row.row_id] = row

    missing = [rid for rid in section.row_ids if rid not in rows_by_id]
    if missing:
        raise ValueError(
            f"section {section.section_type} references row_ids {missing} "
            "with no matching ReceiptRow"
        )

    union: set[int] = set()
    for rid in section.row_ids:
        union.update(rows_by_id[rid].line_ids)

    if union != set(section.line_ids):
        raise ValueError(
            f"section {section.section_type} line_ids do not equal the "
            f"union of its rows' line_ids: "
            f"line_ids={sorted(set(section.line_ids))}, "
            f"row union={sorted(union)}"
        )


def item_to_receipt_section(item: dict[str, Any]) -> ReceiptSection:
    """Converts a DynamoDB item to a ReceiptSection object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        ReceiptSection: The ReceiptSection object.

    Raises:
        ValueError: When the item format is invalid.
    """
    return ReceiptSection.from_item(item)
