"""
DynamoDB entity for compiled merchant font artifacts.

A MerchantFont is a "current pointer" record: the compiled font artifact (an
npz glyph atlas) lives in S3, and this Dynamo item records where it is and what
it contains. One item exists per (merchant, face) pair.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from receipt_dynamo.entities.base import DynamoDBEntity
from receipt_dynamo.entities.util import _repr_str

VALID_FACES = ("regular", "heavy")


@dataclass(eq=True, unsafe_hash=True)
class MerchantFont(DynamoDBEntity):
    """
    Pointer to a compiled merchant font artifact stored in S3.

    Attributes
    ----------
    merchant_name : str
        Exact merchant string as used by get_receipt_places_by_merchant
        (e.g. "Sprouts Farmers Market").
    face : str
        Font face, one of "regular" or "heavy".
    s3_bucket : str
        S3 bucket holding the compiled npz artifact.
    s3_key : str
        S3 key of the compiled npz artifact.
    content_hash : str
        sha256 hex digest of the npz artifact.
    source_commit : str
        Git sha the artifact was compiled from.
    compiled_at : str | datetime
        ISO timestamp when the artifact was compiled.
    cap_h : float
        Capital-letter height used when compiling the font.
    advance_ratio : float
        Horizontal advance ratio of the monospace grid.
    pitch_check : str
        Human-readable pitch validation note
        (e.g. "0.544 vs 0.545 OK").
    glyph_count : int
        Number of glyphs in the compiled atlas.
    stylemap_s3_key : str | None
        Optional S3 key of a stylemap shipped alongside the regular face.
    """

    REQUIRED_KEYS = {
        "PK",
        "SK",
        "TYPE",
        "s3_bucket",
        "s3_key",
        "content_hash",
        "source_commit",
        "compiled_at",
        "cap_h",
        "advance_ratio",
        "pitch_check",
        "glyph_count",
    }

    merchant_name: str
    face: str
    s3_bucket: str
    s3_key: str
    content_hash: str
    source_commit: str
    compiled_at: str | datetime
    cap_h: float
    advance_ratio: float
    pitch_check: str
    glyph_count: int
    stylemap_s3_key: str | None = None
    # Canonical merchant logo master (black-on-white PNG) in S3; carried on
    # the regular face like the stylemap.
    logo_s3_key: str | None = None
    # Local cache filename this artifact serves (e.g. "sprouts.glyphs.npz").
    # Resolvers MUST match it before fetching: a merchant can have multiple
    # atlases (Costco's chart-derived production font vs a studio build) and
    # a pointer must never masquerade as a file it isn't.
    cache_filename: str | None = None

    # ────────────────────────── validation ────────────────────────────
    def __post_init__(self) -> None:
        if (
            not isinstance(self.merchant_name, str)
            or not self.merchant_name.strip()
        ):
            raise ValueError("merchant_name must be a non-empty string")

        if not isinstance(self.face, str) or self.face not in VALID_FACES:
            raise ValueError(
                f"face must be one of {VALID_FACES}, got: {self.face}"
            )

        for field_name in (
            "s3_bucket",
            "s3_key",
            "content_hash",
            "source_commit",
            "pitch_check",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{field_name} must be a non-empty string")

        if isinstance(self.compiled_at, datetime):
            self.compiled_at = self.compiled_at.isoformat()
        elif not isinstance(self.compiled_at, str):
            raise ValueError("compiled_at must be datetime or ISO-8601 string")

        if isinstance(self.cap_h, bool) or not isinstance(
            self.cap_h, (int, float)
        ):
            raise ValueError("cap_h must be a number")
        self.cap_h = float(self.cap_h)

        if isinstance(self.advance_ratio, bool) or not isinstance(
            self.advance_ratio, (int, float)
        ):
            raise ValueError("advance_ratio must be a number")
        self.advance_ratio = float(self.advance_ratio)

        if isinstance(self.glyph_count, bool) or not isinstance(
            self.glyph_count, int
        ):
            raise ValueError("glyph_count must be an integer")
        if self.glyph_count < 0:
            raise ValueError("glyph_count must be non-negative")

        if self.stylemap_s3_key is not None and not isinstance(
            self.stylemap_s3_key, str
        ):
            raise ValueError("stylemap_s3_key must be a string or None")

        if self.cache_filename is not None and not isinstance(
            self.cache_filename, str
        ):
            raise ValueError("cache_filename must be a string or None")

        if self.logo_s3_key is not None and not isinstance(
            self.logo_s3_key, str
        ):
            raise ValueError("logo_s3_key must be a string or None")

    # ───────────────────────── DynamoDB keys ──────────────────────────
    @property
    def key(self) -> dict[str, Any]:
        return {
            "PK": {"S": f"MERCHANT_FONT#{self.merchant_name}"},
            "SK": {"S": f"FACE#{self.face}"},
        }

    # ───────────────────── DynamoDB marshalling ───────────────────────
    def to_item(self) -> dict[str, Any]:
        compiled_at_str = (
            self.compiled_at
            if isinstance(self.compiled_at, str)
            else self.compiled_at.isoformat()
        )
        return {
            **self.key,
            "TYPE": {"S": "MERCHANT_FONT"},
            "s3_bucket": {"S": self.s3_bucket},
            "s3_key": {"S": self.s3_key},
            "content_hash": {"S": self.content_hash},
            "source_commit": {"S": self.source_commit},
            "compiled_at": {"S": compiled_at_str},
            "cap_h": {"N": str(self.cap_h)},
            "advance_ratio": {"N": str(self.advance_ratio)},
            "pitch_check": {"S": self.pitch_check},
            "glyph_count": {"N": str(self.glyph_count)},
            "cache_filename": (
                {"S": self.cache_filename}
                if self.cache_filename
                else {"NULL": True}
            ),
            "logo_s3_key": (
                {"S": self.logo_s3_key} if self.logo_s3_key else {"NULL": True}
            ),
            "stylemap_s3_key": (
                {"S": self.stylemap_s3_key}
                if self.stylemap_s3_key
                else {"NULL": True}
            ),
        }

    # ───────────────────────── string repr ────────────────────────────
    def __repr__(self) -> str:
        return (
            "MerchantFont("
            f"merchant_name={_repr_str(self.merchant_name)}, "
            f"face={_repr_str(self.face)}, "
            f"s3_bucket={_repr_str(self.s3_bucket)}, "
            f"s3_key={_repr_str(self.s3_key)}, "
            f"content_hash={_repr_str(self.content_hash)}, "
            f"source_commit={_repr_str(self.source_commit)}, "
            f"compiled_at={self.compiled_at}, "
            f"cap_h={self.cap_h}, "
            f"advance_ratio={self.advance_ratio}, "
            f"pitch_check={_repr_str(self.pitch_check)}, "
            f"glyph_count={self.glyph_count}, "
            f"stylemap_s3_key={_repr_str(self.stylemap_s3_key)}"
            ")"
        )

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> "MerchantFont":
        """Converts a DynamoDB item to a MerchantFont object.

        Args:
            item: The DynamoDB item to convert.

        Returns:
            MerchantFont: The MerchantFont object.

        Raises:
            ValueError: When the item format is invalid.
        """
        missing = DynamoDBEntity.validate_keys(item, cls.REQUIRED_KEYS)
        if missing:
            raise ValueError(f"MerchantFont item missing keys: {missing}")

        try:
            pk = item["PK"]["S"]
            if not pk.startswith("MERCHANT_FONT#"):
                raise ValueError(f"Invalid MerchantFont PK format: {pk}")
            # Rejoin in case merchant_name contains '#'
            merchant_name = pk.split("#", 1)[1]

            sk = item["SK"]["S"]
            if not sk.startswith("FACE#"):
                raise ValueError(f"Invalid MerchantFont SK format: {sk}")
            face = sk.split("#", 1)[1]

            return cls(
                merchant_name=merchant_name,
                face=face,
                s3_bucket=item["s3_bucket"]["S"],
                s3_key=item["s3_key"]["S"],
                content_hash=item["content_hash"]["S"],
                source_commit=item["source_commit"]["S"],
                compiled_at=item["compiled_at"]["S"],
                cap_h=float(item["cap_h"]["N"]),
                advance_ratio=float(item["advance_ratio"]["N"]),
                pitch_check=item["pitch_check"]["S"],
                glyph_count=int(item["glyph_count"]["N"]),
                stylemap_s3_key=item.get("stylemap_s3_key", {}).get("S"),
                cache_filename=item.get("cache_filename", {}).get("S"),
                logo_s3_key=item.get("logo_s3_key", {}).get("S"),
            )
        except (KeyError, IndexError, ValueError) as e:
            raise ValueError(
                f"Error converting item to MerchantFont: {e}"
            ) from e


def item_to_merchant_font(item: dict[str, Any]) -> "MerchantFont":
    """Converts a DynamoDB item to a MerchantFont object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        MerchantFont: The MerchantFont object.

    Raises:
        ValueError: When the item format is invalid.
    """
    return MerchantFont.from_item(item)
