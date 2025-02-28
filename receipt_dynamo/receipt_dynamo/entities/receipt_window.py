# dynamo/entities/receipt_window.py
from typing import Any, Generator, List, Tuple

from receipt_dynamo.entities.util import assert_valid_uuid


class ReceiptWindow:
    def __init__(self,
        image_id: str,
        receipt_id: int,
        cdn_s3_bucket: str,
        cdn_s3_key: str,
        corner_name: str,
        width: int,
        height: int,
        inner_corner_coordinates: Tuple[float, float] | List[float],
        gpt_guess: list[int] | None = None, ):
        assert_valid_uuid(image_id)
        self.image_id = image_id
        if receipt_id <= 0:
            raise ValueError("id must be positive")
        self.receipt_id = receipt_id
        if cdn_s3_bucket and not isinstance(cdn_s3_bucket, str):
            raise ValueError("cdn_s3_bucket must be a string")
        self.cdn_s3_bucket = cdn_s3_bucket
        if cdn_s3_key and not isinstance(cdn_s3_key, str):
            raise ValueError("cdn_s3_key must be a string")
        self.cdn_s3_key = cdn_s3_key
        if corner_name and not isinstance(corner_name, str):
            raise ValueError("corner_name must be a string")
        corner_name = corner_name.upper()
        if corner_name not in ["TOP_LEFT",
            "TOP_RIGHT",
            "BOTTOM_RIGHT",
            "BOTTOM_LEFT", ]:
            raise ValueError("corner_name must be one of: TOP_LEFT, TOP_RIGHT, BOTTOM_RIGHT, BOTTOM_LEFT")
        self.corner_name = corner_name
        if width <= 0:
            raise ValueError("width must be positive")
        self.width = width
        if height <= 0:
            raise ValueError("height must be positive")
        self.height = height
        if not isinstance(inner_corner_coordinates, (tuple, list)):
            raise ValueError("inner_corner_coordinates must be a tuple or list")
        # Always store as tuple
        self.inner_corner_coordinates = tuple(inner_corner_coordinates)
        self.gpt_guess = gpt_guess

    def key(self) -> dict:
        return {"PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {"S": f"RECEIPT#{self.receipt_id:05d}#RECEIPT_WINDOW#{self.corner_name}"}, }

    def gsi3_key(self) -> dict:
        return {"GSI3PK": {"S": "RECEIPT"},
            "GSI3SK": {"S": f"RECEIPT#{self.receipt_id:05d}#RECEIPT_WINDOW#{self.corner_name}"}, }

    def to_item(self) -> dict:
        return {**self.key(),
            **self.gsi3_key(),
            "TYPE": {"S": "RECEIPT_WINDOW"},
            "cdn_s3_bucket": {"S": self.cdn_s3_bucket},
            "cdn_s3_key": {"S": self.cdn_s3_key},
            "corner_name": {"S": self.corner_name},
            "width": {"N": str(self.width)},
            "height": {"N": str(self.height)},
            "inner_corner_coordinates": {"L": [{"N": str(coord)}
                    for coord in self.inner_corner_coordinates]},
            "gpt_guess": ({"L": [{"N": str(guess)} for guess in self.gpt_guess]}
                if self.gpt_guess
                else {"NULL": True}), }

    def __repr__(self) -> str:
        return f"ReceiptWindow(image_id={self.image_id}, receipt_id={self.receipt_id}, corner_name={self.corner_name}, width={self.width}, height={self.height}, gpt_guess={self.gpt_guess})"

    def __iter__(self) -> Generator[Tuple[str, Any], None, None]:
        yield "image_id", self.image_id
        yield "receipt_id", self.receipt_id
        yield "cdn_s3_bucket", self.cdn_s3_bucket
        yield "cdn_s3_key", self.cdn_s3_key
        yield "corner_name", self.corner_name
        yield "width", self.width
        yield "height", self.height
        yield "inner_corner_coordinates", self.inner_corner_coordinates
        yield "gpt_guess", self.gpt_guess

    def __eq__(self, other) -> bool:
        if not isinstance(other, ReceiptWindow):
            return False
        return (self.image_id == other.image_id
            and self.receipt_id == other.receipt_id
            and self.cdn_s3_bucket == other.cdn_s3_bucket
            and self.cdn_s3_key == other.cdn_s3_key
            and self.corner_name == other.corner_name
            and self.inner_corner_coordinates == other.inner_corner_coordinates
            and self.gpt_guess == other.gpt_guess)

    def __hash__(self) -> int:
        # Convert inner_corner_coordinates tuple of floats to hashable form
        hashable_coords = tuple(float(x) for x in self.inner_corner_coordinates)
        return hash((self.image_id,
                self.receipt_id,
                self.cdn_s3_bucket,
                self.cdn_s3_key,
                self.corner_name,
                hashable_coords,  # Now hashable while preserving precision
                (tuple(self.gpt_guess) if self.gpt_guess else None)))  # Make gpt_guess hashable too


def itemToReceiptWindow(item: dict) -> ReceiptWindow:
    required_keys = {"PK",
        "SK",
        "TYPE",
        "cdn_s3_bucket",
        "cdn_s3_key",
        "corner_name",
        "width",
        "height",
        "inner_corner_coordinates", }
    if not required_keys.issubset(item.keys()):
        missing_keys = required_keys - item.keys()
        additional_keys = item.keys() - required_keys
        raise ValueError(f"Invalid item format\nmissing keys: {missing_keys}\nadditional keys: {additional_keys}")
    try:
        # Process gpt_guess correctly: if the item indicates NULL, set it to
        # None.
        gpt_guess_field = item.get("gpt_guess")
        if gpt_guess_field and gpt_guess_field.get("NULL", False):
            gpt_guess = None
        else:
            gpt_guess = ([int(guess["N"]) for guess in gpt_guess_field["L"]]
                if gpt_guess_field
                else None)

        return ReceiptWindow(image_id=item["PK"]["S"].split("#")[1],
            receipt_id=int(item["SK"]["S"].split("#")[1].split("RECEIPT_WINDOW")[0]),
            cdn_s3_bucket=item["cdn_s3_bucket"]["S"],
            cdn_s3_key=item["cdn_s3_key"]["S"],
            corner_name=item["corner_name"]["S"],
            width=int(item["width"]["N"]),
            height=int(item["height"]["N"]),
            inner_corner_coordinates=tuple(float(coord["N"])
                for coord in item["inner_corner_coordinates"]["L"]),
            gpt_guess=gpt_guess, )
    except Exception as e:
        raise ValueError(f"Error converting item to ReceiptWindow: {e}")
