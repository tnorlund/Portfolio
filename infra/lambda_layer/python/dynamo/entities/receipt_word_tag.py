from typing import Generator, Tuple

class ReceiptWordTag:
    def __init__(
        self,
        image_id: int,
        receipt_id: int,
        line_id: int,
        word_id: int,
        tag: str,
    ):
        """
        Constructs a new ReceiptWordTag object for DynamoDB.

        Table Schema (simplified):
          PK =  IMAGE#<image_id>
          SK =  TAG#<UPPER_TAG>#RECEIPT#<receipt_id>#WORD#<word_id>
          GSI1PK = TAG#<UPPER_TAG>
          GSI1SK = IMAGE#<image_id>#RECEIPT#<receipt_id>#LINE#<line_id>#WORD#<word_id>
        """
        self.image_id = image_id
        self.receipt_id = receipt_id
        self.line_id = line_id
        self.word_id = word_id
        if not tag:
            raise ValueError("tag must not be empty")
        if len(tag) > 20:
            raise ValueError("tag must not exceed 20 characters")
        if tag.startswith("_"):
            raise ValueError("tag must not start with an underscore")
        self.tag = tag.strip()

    def __eq__(self, other: object) -> bool:
        """
        Equality can be defined in various ways. Here, let's consider
        two ReceiptWordTag objects equal if they have the same
        (word_id, receipt_id, tag).
        """
        if not isinstance(other, ReceiptWordTag):
            return False
        return (
            self.word_id == other.word_id and
            self.receipt_id == other.receipt_id and
            self.tag == other.tag
        )

    def __iter__(self) -> Generator[Tuple[str, str], None, None]:
        """
        Allows dict(...) conversion, e.g. dict(my_receipt_word_tag).
        Yields (field_name, field_value).
        """
        yield "image_id", self.image_id
        yield "receipt_id", self.receipt_id
        yield "line_id", self.line_id
        yield "word_id", self.word_id
        yield "tag", self.tag

    def __repr__(self) -> str:
        """
        Developer-friendly string for debugging/logging.
        """
        return (
            f"ReceiptWordTag(image_id={self.image_id}, "
            f"receipt_id={self.receipt_id}, "
            f"line_id={self.line_id}, "
            f"word_id={self.word_id}, "
            f"tag={self.tag})"
        )

    def key(self) -> dict:
        """
        Main-table key: 
          PK = "IMAGE#<image_id>"
          SK = "TAG#<tag_upper_20>#RECEIPT#<receipt_id>#WORD#<word_id>"
        We use an underscore-padded uppercase tag to match the pattern from word_tag.py.
        """
        tag_upper = self.tag
        spaced_tag_upper = f"{tag_upper:_>20}"  # e.g. "___________FOO" (20 chars total)

        return {
            "PK": {"S": f"IMAGE#{self.image_id:05d}"},
            "SK": {
                "S": (
                    f"RECEIPT#{self.receipt_id:05d}"
                    f"#LINE#{self.line_id:05d}"
                    f"#WORD#{self.word_id:05d}"
                    f"#TAG#{spaced_tag_upper}"
                )
            },
        }

    def gsi1_key(self) -> dict:
        """
        GSI1 key:
          GSI1PK = "TAG#<tag_upper_20>"
          GSI1SK = "IMAGE#<image_id>#RECEIPT#<receipt_id>#LINE#<line_id>#WORD#<word_id>"
        """
        tag_upper = self.tag
        spaced_tag_upper = f"{tag_upper:_>20}"

        return {
            "GSI1PK": {"S": f"TAG#{spaced_tag_upper}"},
            "GSI1SK": {
                "S": (
                    f"IMAGE#{self.image_id:05d}"
                    f"#RECEIPT#{self.receipt_id:05d}"
                    f"#LINE#{self.line_id:05d}"
                    f"#WORD#{self.word_id:05d}"
                )
            },
        }

    def to_item(self) -> dict:
        """
        Consolidates the main keys and GSI1 keys into a single DynamoDB item,
        along with a TYPE attribute and an optional tag_name attribute.
        """
        return {
            **self.key(),
            **self.gsi1_key(),
            "TYPE": {"S": "RECEIPT_WORD_TAG"},
            "tag_name": {"S": self.tag.upper()},
        }


def itemToReceiptWordTag(item: dict) -> ReceiptWordTag:
    """
    Reverse mapping from a DynamoDB item back to a ReceiptWordTag object.

    Expects:
      PK  = "IMAGE#<image_id>"
      SK  = f"RECEIPT#<receipt_id>#LINE#<line_id>#WORD#<word_id>#TAG#<tag>"
      GSI1SK = "IMAGE#<image_id>#RECEIPT#<receipt_id>#LINE#<line_id>#WORD#<word_id>"

    Returns a ReceiptWordTag instance.
    """
    required_keys = {"PK", "SK", "GSI1SK"}
    if not required_keys.issubset(item.keys()):
        missing_keys = required_keys - item.keys()
        raise ValueError(f"Item is missing required keys: {missing_keys}")

    try:
        # Parse PK for image_id
        pk_parts = item["PK"]["S"].split("#")  # ["IMAGE", "00042"]
        image_id_str = pk_parts[1]  # "00042"
        image_id = int(image_id_str)

        # Parse SK for tag & receipt_id & word_id
        sk_parts = item["SK"]["S"].split("#")
        raw_tag = sk_parts[-1]  # e.g. "__________FOO"
        # remove underscores from the left:
        tag = raw_tag.lstrip("_").strip()  # "FOO"

        receipt_id_str = sk_parts[1]
        receipt_id = int(receipt_id_str)

        word_id_str = sk_parts[5]
        word_id = int(word_id_str)

        # Parse GSI1SK for line_id
        gsi1sk_parts = item["GSI1SK"]["S"].split("#")
        # e.g. ["IMAGE", "00042", "RECEIPT", "00001", "LINE", "00003", "WORD", "00010"]
        line_id_str = gsi1sk_parts[5]  # "00003"
        line_id = int(line_id_str)

        return ReceiptWordTag(
            image_id=image_id,
            receipt_id=receipt_id,
            line_id=line_id,
            word_id=word_id,
            tag=tag,
        )

    except (IndexError, ValueError) as e:
        raise ValueError(f"Item is missing or has invalid values: {e}") from e