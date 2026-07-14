from dataclasses import dataclass
from typing import Any, ClassVar

from receipt_dynamo.entities.entity_factory import (
    create_receipt_barcode_sk_parser,
)
from receipt_dynamo.entities.text_geometry_entity import TextGeometryEntity
from receipt_dynamo.entities.util import (
    validate_non_negative_int,
    validate_positive_int,
)


@dataclass(kw_only=True)
class ReceiptBarcode(TextGeometryEntity):
    """A barcode / QR code detected on a receipt image.

    Detected by Apple Vision (VNDetectBarcodesRequest) during OCR. Geometry uses
    the same normalized coordinate space as receipt words/lines, so a barcode box
    is directly comparable to the text boxes on the same receipt. The decoded
    payload is stored in the inherited ``text`` field (empty string when the
    symbol was located but not decodable); the ``payload`` property exposes it as
    ``str | None``.

    Attributes:
        image_id (str): UUID of the source image.
        receipt_id (int): Identifier for the receipt on the image.
        barcode_id (int): Index of the barcode within the receipt (0-based).
        text (str): The decoded barcode payload ("" when undecoded).
        symbology (str): Vision symbology name, e.g. "Code128", "QR", "ITF14",
            "I2of5", "EAN13", "PDF417".
        bounding_box, top_left, top_right, bottom_left, bottom_right (dict):
            Normalized geometry (keys 'x'/'y', plus 'width'/'height' for the box).
        angle_degrees (float), angle_radians (float): Rotation of the symbol.
        confidence (float): Detection confidence in (0, 1].
    """

    # Entity-specific ID fields
    receipt_id: int
    barcode_id: int
    symbology: str

    # `text` (inherited) requires text + geometry; add "symbology".
    REQUIRED_KEYS: ClassVar[set[str]] = (
        TextGeometryEntity.BASE_REQUIRED_KEYS | {"symbology"}
    )

    def __post_init__(self) -> None:
        """Validate and normalize initialization arguments."""
        validate_positive_int("receipt_id", self.receipt_id)
        validate_non_negative_int("barcode_id", self.barcode_id)

        if not isinstance(self.symbology, str) or not self.symbology:
            raise ValueError("symbology must be a non-empty string")

        # Base class geometry validation (image_id, text, bbox, corners,
        # angles, confidence).
        self._validate_geometry()

    @property
    def payload(self) -> str | None:
        """The decoded barcode payload, or None if the symbol was not decoded."""
        return self.text or None

    @property
    def key(self) -> dict[str, Any]:
        """The DynamoDB primary key for the receipt barcode."""
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {
                "S": (
                    f"RECEIPT#{self.receipt_id:05d}#"
                    f"BARCODE#{self.barcode_id:05d}"
                )
            },
        }

    def gsi3_key(self) -> dict[str, Any]:
        """GSI3 key: list all barcodes for a receipt (mirrors words/lines)."""
        return {
            "GSI3PK": {
                "S": f"IMAGE#{self.image_id}#RECEIPT#{self.receipt_id:05d}"
            },
            "GSI3SK": {"S": "BARCODE"},
        }

    def gsi4_key(self) -> dict[str, Any]:
        """GSI4 key: the single-query receipt-details access pattern.

        Shares GSI4PK with the receipt's other entities; the ``6_BARCODE``
        prefix orders barcodes after Receipt/Place/Line/Word/Label/Summary
        (0..5) so ``get_receipt_details`` returns them alongside the receipt.
        """
        return {
            "GSI4PK": {
                "S": f"IMAGE#{self.image_id}#RECEIPT#{self.receipt_id:05d}"
            },
            "GSI4SK": {"S": f"6_BARCODE#{self.barcode_id:05d}"},
        }

    def to_item(self) -> dict[str, Any]:
        """Convert the ReceiptBarcode to a DynamoDB item."""
        self.__post_init__()
        return {
            **self.key,
            "TYPE": {"S": "RECEIPT_BARCODE"},
            **self.gsi3_key(),
            **self.gsi4_key(),
            **self._get_geometry_fields(),
            "symbology": {"S": self.symbology},
        }

    def __repr__(self) -> str:
        """Return a string representation of the ReceiptBarcode object."""
        geometry_fields = self._get_geometry_repr_fields()
        return (
            f"ReceiptBarcode("
            f"image_id='{self.image_id}', "
            f"receipt_id={self.receipt_id}, "
            f"barcode_id={self.barcode_id}, "
            f"symbology='{self.symbology}', "
            f"{geometry_fields}"
            f")"
        )

    def _get_geometry_hash_fields(self) -> tuple[Any, ...]:
        """Include entity-specific ID fields in hash computation."""
        return self._get_base_geometry_hash_fields() + (
            self.image_id,
            self.receipt_id,
            self.barcode_id,
            self.symbology,
        )

    def __hash__(self) -> int:
        """Return hash (required for dataclass with eq=True, frozen=False)."""
        return hash(self._get_geometry_hash_fields())

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> "ReceiptBarcode":
        """Convert a DynamoDB item to a ReceiptBarcode object."""

        def extract_symbology(itm: dict[str, Any]) -> str:
            attribute = itm["symbology"]
            if (
                not isinstance(attribute, dict)
                or set(attribute) != {"S"}
                or not isinstance(attribute["S"], str)
            ):
                raise ValueError("symbology must be a DynamoDB string (S)")
            return attribute["S"]

        return cls._from_item_with_geometry(
            item,
            sk_parser=create_receipt_barcode_sk_parser(),
            additional_extractors={"symbology": extract_symbology},
        )


def item_to_receipt_barcode(item: dict[str, Any]) -> ReceiptBarcode:
    """Convert a DynamoDB item to a ReceiptBarcode object.

    Convenience wrapper that delegates to ReceiptBarcode.from_item().
    """
    return ReceiptBarcode.from_item(item)
