from typing import Generator, Tuple
from decimal import Decimal, ROUND_HALF_UP


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

class Word:
    def __init__(
        self,
        image_id: int,
        line_id: int,
        id: int,
        text: str,
        x: float,
        y: float,
        width: float,
        height: float,
        angle: float,
        confidence: float
    ):
        if image_id <= 0 or not isinstance(image_id, int):
            raise ValueError("image_id must be a positive integer")
        self.image_id = image_id
        if line_id <= 0 or not isinstance(line_id, int):
            raise ValueError("line_id must be a positive integer")
        self.line_id = line_id
        if id <= 0 or not isinstance(id, int):
            raise ValueError("id must be a positive integer")
        self.id = id
        if not isinstance(text, str):
            raise ValueError("text must be a string")
        self.text = text
        if not isinstance(x, float):
            raise ValueError("x must be a float")
        self.x = x
        if not isinstance(y, float):
            raise ValueError("y must be a float")
        self.y = y
        if not isinstance(width, float):
            raise ValueError("width must be a float")
        self.width = width
        if not isinstance(height, float):
            raise ValueError("height must be a float")
        self.height = height
        if not isinstance(angle, float):
            raise ValueError("angle must be a float")
        self.angle = angle
        if not isinstance(confidence, float):
            raise ValueError("confidence must be a float")
        self.confidence = confidence
    
    def key(self) -> dict:
        return {
            "PK": {"S": f"IMAGE#{self.image_id:05d}"},
            "SK": {"S": f"LINE#{self.line_id:05d}#WORD#{self.id:05d}"}
        }
    
    def to_item(self) -> dict:
        return {
            **self.key(),
            "text": {"S": self.text},
            "x": {"N": _format_float(self.x, 20, 22)},
            "y": {"N": _format_float(self.y, 20, 22)},
            "width": {"N": _format_float(self.width, 20, 22)},
            "height": {"N": _format_float(self.height, 20, 22)},
            "angle": {"N": _format_float(self.angle, 10, 12)},
            "confidence": {"N": _format_float(self.confidence, 2, 2)}
        }
    
# TODO: Implement a itemToWord Function
