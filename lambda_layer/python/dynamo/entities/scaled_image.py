from typing import Generator, Tuple
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime


def _format_float(
    value: float, decimal_places: int = 10, total_length: int = 20
) -> str:
    # Convert float → string → Decimal to avoid float binary representation issues
    d_value = Decimal(str(value))

    # Create a "quantizer" for the desired number of decimal digits
    # e.g. decimal_places=10 → quantizer = Decimal('1.0000000000')
    quantizer = Decimal("1." + "0" * decimal_places)

    # Round using the chosen rounding mode (e.g. HALF_UP)
    d_rounded = d_value.quantize(quantizer, rounding=ROUND_HALF_UP)

    # Format as a string with exactly `decimal_places` decimals
    formatted = f"{d_rounded:.{decimal_places}f}"

    # Optional: Pad to `total_length` characters
    # If you want leading zeros:
    if len(formatted) < total_length:
        formatted = formatted.zfill(total_length)

    # If instead you wanted trailing zeros, you could do:
    # formatted = formatted.ljust(total_length, '0')

    return formatted


class ScaledImage:
    def __init__(
        self,
        image_id: int,
        width: int,
        height: int,
        timestamp_added: datetime,
        base64: str,
        scale: float,
    ):
        if image_id <= 0 or not isinstance(image_id, int):
            raise ValueError("image_id must be a positive integer")
        self.image_id = image_id
        if (
            width <= 0
            or height <= 0
            or not isinstance(width, int)
            or not isinstance(height, int)
        ):
            raise ValueError("width and height must be positive integers")
        self.width = width
        self.height = height
        if isinstance(timestamp_added, datetime):
            self.timestamp_added = timestamp_added.isoformat()
        elif isinstance(timestamp_added, str):
            self.timestamp_added = timestamp_added
        else:
            raise ValueError("timestamp_added must be a datetime object or a string")
        if base64 is None or not isinstance(base64, str):
            raise ValueError("base64 must be a string")
        self.base64 = base64
        if scale <= 0 or not isinstance(scale, float):
            raise ValueError("scale must be a positive float")
        self.scale = scale

    def key(self) -> dict:
        formatted_pk = f"IMAGE#{self.image_id:05d}"
        formatted_sk = (
            f"IMAGE_SCALE#{_format_float(self.scale, 4, 6).replace('.', '_')}"
        )

        return {
            "PK": {"S": formatted_pk},
            "SK": {"S": formatted_sk},
        }

    def to_item(self) -> dict:
        return {
            **self.key(),
            "Type": {"S": "IMAGE_SCALE"},
            "Width": {"N": str(self.width)},
            "Height": {"N": str(self.height)},
            "TimestampAdded": {"S": self.timestamp_added},
            "Base64": {"S": self.base64},
            "Scale": {"N": str(self.scale)},
        }

    def __repr__(self):
        return f"ScaledImage(image_id={int(self.image_id)}, scale={self.scale})"

    def __iter__(self) -> Generator[Tuple[str, int], None, None]:
        yield "image_id", self.image_id
        yield "width", self.width
        yield "height", self.height
        yield "timestamp_added", self.timestamp_added
        yield "base64", self.base64
        yield "scale", self.scale

    def __eq__(self, value):
        return (
            self.image_id == value.image_id
            and self.width == value.width
            and self.height == value.height
            and self.timestamp_added == value.timestamp_added
            and self.base64 == value.base64
            and self.scale == value.scale
        )


def ItemToScaledImage(item: dict) -> ScaledImage:
    return ScaledImage(
        image_id=int(item["PK"]["S"].split("#")[1]),
        width=int(item["Width"]["N"]),
        height=int(item["Height"]["N"]),
        timestamp_added=item["TimestampAdded"]["S"],
        base64=item["Base64"]["S"],
        scale=float(item["Scale"]["N"]),
    )
