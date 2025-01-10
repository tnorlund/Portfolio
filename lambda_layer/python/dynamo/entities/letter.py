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

class Letter:
    def __init__(
        self,
        image_id: int,
        line_id: int,
        word_id: int,
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
        if word_id <= 0 or not isinstance(word_id, int):
            raise ValueError("word_id must be a positive integer")
        self.word_id = word_id
        if id <= 0 or not isinstance(id, int):
            raise ValueError("id must be a positive integer")
        self.id = id
        if text is None or len(text) != 1 or not isinstance(text, str):
            raise ValueError("text must be exactly one character")
        self.text = text
        if x == 0:
            x = 0.0
        if not isinstance(x, float):
            raise ValueError(f"x must be a float! {x}")
        self.x = x
        if y == 0:
            y = 0.0
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

    def key(self) -> dict:
        return {
            "PK": {"S": f"IMAGE#{self.image_id:05d}"},
            "SK": {"S": f"LINE#{self.line_id:05d}#WORD#{self.word_id:05d}#LETTER#{self.id:05d}"}
        }
    
    def to_item(self) -> dict:
        return {
            **self.key(),
            "Type": {"S": "LETTER"},
            "Text": {"S": self.text},
            "X": {"N": _format_float(self.x, 20, 22)},
            "Y": {"N": _format_float(self.y, 20, 22)},
            "Width": {"N": _format_float(self.width, 20, 22)},
            "Height": {"N": _format_float(self.height, 20, 22)},
            "Angle": {"N": _format_float(self.angle, 10, 12)},
            "Confidence": {"N": _format_float(self.confidence, 2, 2)},
        }
    
    def __repr__(self):
        """Returns a string representation of the Letter object
        
        Returns:
            str: The string representation of the Letter object
        """
        return f"Letter(id={self.id}, text='{self.text}')"
    
    def __iter__(self) -> Generator[Tuple[str, dict], None, None]:
        """Yields the Letter object as a series of key-value pairs
        """
        yield "image_id", self.image_id
        yield "word_id", self.word_id
        yield "line_id", self.line_id
        yield "id", self.id
        yield "text", self.text
        yield "x", self.x
        yield "y", self.y
        yield "width", self.width
        yield "height", self.height
        yield "angle", self.angle
        yield "confidence", self.confidence

    def __eq__(self, value):
        """Compares two Letter objects for equality
        
        Args:
            other (object): The object to compare
        
        Returns:
            bool: True if the objects are equal, False otherwise"""
        if not isinstance(value, Letter):
            return False
        return (
            self.image_id == value.image_id
            and self.line_id == value.line_id
            and self.word_id == value.word_id
            and self.id == value.id
            and self.text == value.text
            and self.x == value.x
            and self.y == value.y
            and self.width == value.width
            and self.height == value.height
            and self.angle == value.angle
            and self.confidence == value.confidence
        )
    
def itemToLetter(item: dict) -> Letter:
    return Letter(
        int(item["PK"]["S"].split("#")[1]),
        int(item["SK"]["S"].split("#")[1]),
        int(item["SK"]["S"].split("#")[3]),
        int(item["SK"]["S"].split("#")[5]),
        item["Text"]["S"],
        float(item["X"]["N"]),
        float(item["Y"]["N"]),
        float(item["Width"]["N"]),
        float(item["Height"]["N"]),
        float(item["Angle"]["N"]),
        float(item["Confidence"]["N"]),
    )
