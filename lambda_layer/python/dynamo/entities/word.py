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
        confidence: float,
        tags: list[str] = None
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
        if isinstance(angle, int):
            angle = float(angle)
        if not isinstance(angle, float):
            raise ValueError("angle must be a float or int")
        self.angle = angle
        if confidence <= 0 or confidence > 1:
            raise ValueError("confidence must be a float between 0 and 1")
        self.confidence = confidence
        if tags is not None and not isinstance(tags, list):
            raise ValueError("tags must be a list")
        self.tags = tags if tags is not None else []
    
    def key(self) -> dict:
        return {
            "PK": {"S": f"IMAGE#{self.image_id:05d}"},
            "SK": {"S": f"LINE#{self.line_id:05d}#WORD#{self.id:05d}"}
        }
    
    def to_item(self) -> dict:
        item = {
            **self.key(),
            "Type": {"S": "WORD"},
            "Text": {"S": self.text},
            "X": {"N": _format_float(self.x, 20, 22)},
            "Y": {"N": _format_float(self.y, 20, 22)},
            "Width": {"N": _format_float(self.width, 20, 22)},
            "Height": {"N": _format_float(self.height, 20, 22)},
            "Angle": {"N": _format_float(self.angle, 10, 12)},
            "Confidence": {"N": _format_float(self.confidence, 2, 2)},
        }
        if self.tags:
            item["Tags"] = {"SS": self.tags}
        return item

    def __repr__(self):
        """Returns a string representation of the Word object
        
        Returns:
            str: The string representation of the Word object
        """
        return f"Word(id={int(self.id)}, text='{self.text}')"
    
    def __iter__(self) -> Generator[Tuple[str, str], None, None]:
        yield "PK", f"IMAGE#{self.image_id:05d}"
        yield "SK", f"LINE#{self.line_id:05d}#WORD#{self.id:05d}"
        yield "text", self.text
        yield "tags", self.tags
        yield "x", _format_float(self.x, 20, 22)
        yield "y", _format_float(self.y, 20, 22)
        yield "width", _format_float(self.width, 20, 22)
        yield "height", _format_float(self.height, 20, 22)
        yield "angle", _format_float(self.angle, 10, 12)
        yield "confidence", _format_float(self.confidence, 2, 2)

    def __eq__(self, other: object) -> bool:
        """Compares two Word objects
        
        Args:
            other (object): The object to compare
        
        Returns:
            bool: True if the objects are equal, False otherwise
        """
        if not isinstance(other, Word):
            return False
        return all(
            getattr(self, attr) == getattr(other, attr)
            for attr in [
                "image_id",
                "line_id",
                "id",
                "text",
                "x",
                "y",
                "width",
                "height",
                "angle",
                "confidence",
                "tags"
            ]
        )
        
    
def itemToWord(item: dict) -> Word:
    """Converts a DynamoDB item to a Word object
    
    Args:
        item (dict): The DynamoDB item to convert
    
    Returns:
        Word: The Word object created from the item
    """
    return Word(
        int(item["PK"]["S"].split("#")[1]),
        int(item["SK"]["S"].split("#")[1]),
        int(item["SK"]["S"].split("#")[3]),
        item["Text"]["S"],
        float(item["X"]["N"]),
        float(item["Y"]["N"]),
        float(item["Width"]["N"]),
        float(item["Height"]["N"]),
        float(item["Angle"]["N"]),
        float(item["Confidence"]["N"]),
        item.get("Tags", {}).get("SS", [])
    )
