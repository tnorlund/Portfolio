# infra/lambda_layer/python/dynamo/entities/word_tag.py
from datetime import datetime
from typing import Generator, Optional, Tuple, Union

from receipt_dynamo.entities.util import _repr_str, assert_valid_uuid


class WordTag:
    """Represents a tag applied to a word for DynamoDB.

    This class encapsulates tag-related information such as the associated image,
    line, and word identifiers, the tag itself, and the timestamp when the tag was added.
    It provides operations for generating DynamoDB keys for both the primary table and
    a secondary index.

    Attributes:
        image_id (str): UUID identifying the image.
        line_id (int): The line number of the word.
        word_id (int): The word number of the word.
        tag (str): The tag applied to the word.
        timestamp_added (str): The ISO formatted timestamp when the tag was added.
        validated (bool): Whether the tag has been validated.
        revised_tag (str, optional): The revised tag for the word.
        human_validated (bool): Whether the tag has been validated by a human.
    """

    image_id: str
    line_id: int
    word_id: int
    tag: str
    timestamp_added: str
    validated: Optional[bool]
    timestamp_validated: Optional[str]
    gpt_confidence: Optional[int]
    flag: Optional[str]
    revised_tag: Optional[str]
    human_validated: Optional[bool]
    timestamp_human_validated: Optional[str]

    def __init__(self,
        image_id: str,
        line_id: int,
        word_id: int,
        tag: str,
        timestamp_added: datetime,
        validated: Optional[bool] = None,
        timestamp_validated: Optional[Union[str, datetime]] = None,
        gpt_confidence: Optional[int] = None,
        flag: Optional[str] = None,
        revised_tag: Optional[str] = None,
        human_validated: Optional[bool] = None,
        timestamp_human_validated: Optional[Union[str, datetime]] = None, ):
        """Initializes a new WordTag object for DynamoDB.

        Args:
            image_id (str): UUID identifying the image.
            line_id (int): The line number of the word.
            word_id (int): The word number of the word.
            tag (str): The tag to apply to the word. Must be non-empty, not exceed 40 characters,
                and must not start with an underscore.
            timestamp_added (datetime): The timestamp when the tag was added.
            validated (bool, optional): Whether the tag has been validated. Defaults to None.
            timestamp_human_validated (datetime, optional): The timestamp when the tag was validated by a human. Defaults to None.

        Raises:
            ValueError: If any parameter is of an invalid type or has an invalid value.
        """
        assert_valid_uuid(image_id)
        self.image_id = image_id

        if not isinstance(line_id, int):
            raise ValueError("line_id must be an integer")
        if line_id < 0:
            raise ValueError("line_id must be positive")
        self.line_id = line_id

        if not isinstance(word_id, int):
            raise ValueError("word_id must be an integer")
        if word_id < 0:
            raise ValueError("word_id must be positive")
        self.word_id = word_id

        if not tag:
            raise ValueError("tag must not be empty")
        if not isinstance(tag, str):
            raise ValueError("tag must be a string")
        if len(tag) > 40:
            raise ValueError("tag must not exceed 40 characters")
        if tag.startswith("_"):
            raise ValueError("tag must not start with an underscore")
        self.tag = tag.strip()

        if isinstance(timestamp_added, datetime):
            self.timestamp_added = timestamp_added.isoformat()
        elif isinstance(timestamp_added, str):
            self.timestamp_added = timestamp_added
        else:
            raise ValueError("timestamp_added must be a datetime object or a string")

        if validated not in (True, False, None):
            raise ValueError("validated must be a boolean or None")
        self.validated = validated

        if isinstance(timestamp_validated, datetime):
            self.timestamp_validated = timestamp_validated.isoformat()
        elif not isinstance(timestamp_validated, (str, type(None))):
            raise ValueError("timestamp_validated must be a datetime object, a string, or None")
        else:
            self.timestamp_validated = timestamp_validated

        if gpt_confidence is not None and not isinstance(gpt_confidence, int):
            raise ValueError("gpt_confidence must be an integer")
        self.gpt_confidence = gpt_confidence

        if flag is not None and not isinstance(flag, str):
            raise ValueError("flag must be a string")
        self.flag = flag

        if revised_tag is not None and not isinstance(revised_tag, str):
            raise ValueError("revised_tag must be a string")
        self.revised_tag = revised_tag

        if human_validated not in (True, False, None):
            raise ValueError("human_validated must be a boolean or None")
        self.human_validated = human_validated

        if isinstance(timestamp_human_validated, datetime):
            self.timestamp_human_validated = (timestamp_human_validated.isoformat())
        elif not isinstance(timestamp_human_validated, (str, type(None))):
            raise ValueError("timestamp_human_validated must be a datetime object, a string, or None")
        else:
            self.timestamp_human_validated = timestamp_human_validated

    def __eq__(self, other: object) -> bool:
        """Checks equality between this WordTag and another object.

        Args:
            other (object): The object to compare against.

        Returns:
            bool: True if the other object is a WordTag with the same image, line, and word identifiers
                  and tag, False otherwise.
        """
        if not isinstance(other, WordTag):
            return False
        return (self.image_id == other.image_id
            and self.line_id == other.line_id
            and self.word_id == other.word_id
            and self.tag == other.tag
            and self.validated == other.validated
            and self.timestamp_added == other.timestamp_added
            and self.timestamp_validated == other.timestamp_validated
            and self.gpt_confidence == other.gpt_confidence
            and self.flag == other.flag
            and self.revised_tag == other.revised_tag
            and self.human_validated == other.human_validated
            and self.timestamp_human_validated
            == other.timestamp_human_validated)

    def __hash__(self) -> int:
        """Generates a hash value for the WordTag.

        Returns:
            int: The hash value for the WordTag.
        """
        return hash((self.image_id,
                self.line_id,
                self.word_id,
                self.tag,
                self.validated,
                self.timestamp_added,
                self.timestamp_validated,
                self.gpt_confidence,
                self.flag,
                self.revised_tag,
                self.human_validated,
                self.timestamp_human_validated, ))

    def __iter__(self) -> Generator[Tuple[str, str], None, None]:
        """Yields the attributes of the WordTag as key-value pairs.

        Yields:
            Generator[Tuple[str, str], None, None]: Tuples containing attribute names and their corresponding values.
        """
        yield "image_id", self.image_id
        yield "line_id", self.line_id
        yield "word_id", self.word_id
        yield "tag", self.tag
        yield "timestamp_added", self.timestamp_added
        yield "validated", self.validated
        yield "timestamp_validated", self.timestamp_validated
        yield "gpt_confidence", self.gpt_confidence
        yield "flag", self.flag
        yield "revised_tag", self.revised_tag
        yield "human_validated", self.human_validated
        yield "timestamp_human_validated", self.timestamp_human_validated

    def __repr__(self) -> str:
        """Returns a string representation of the WordTag.

        Returns:
            str: A string representation of the WordTag.
        """
        return (f"WordTag(image_id='{self.image_id}', "
            f"line_id={self.line_id}, "
            f"word_id={self.word_id}, "
            f"tag={_repr_str(self.tag)}, "
            f"timestamp_added={_repr_str(self.timestamp_added)}, "
            f"validated={self.validated}, "
            f"timestamp_validated={_repr_str(self.timestamp_validated)}, "
            f"gpt_confidence={self.gpt_confidence}, "
            f"flag={_repr_str(self.flag)}, "
            f"revised_tag={_repr_str(self.revised_tag)}, "
            f"human_validated={self.human_validated}, "
            f"timestamp_human_validated={_repr_str(self.timestamp_human_validated)}"
            ")")

    def key(self) -> dict:
        """Generates the primary key for the WordTag.

        The primary key is constructed using the image_id, line_id, word_id, and tag.

        Returns:
            dict: A dictionary containing the primary key for the WordTag.
        """
        tag_upper = self.tag
        spaced_tag_upper = f"{tag_upper:_>40}"
        return {"PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {"S": f"LINE#{self.line_id:05d}"
                f"#WORD#{self.word_id:05d}"
                f"#TAG#{spaced_tag_upper}"}, }

    def gsi1_key(self) -> dict:
        """Generates the secondary index key for the WordTag.

        This key is used to query tags in DynamoDB based on the tag attribute.

        Returns:
            dict: A dictionary containing the secondary index key for the WordTag.
        """
        tag_upper = self.tag
        spaced_tag_upper = f"{tag_upper:_>40}"
        return {"GSI1PK": {"S": f"TAG#{spaced_tag_upper}"},
            "GSI1SK": {"S": f"IMAGE#{self.image_id}#LINE#{self.line_id:05d}#WORD#{self.word_id:05d}"}, }

    def gsi2_key(self) -> dict:
        """Generates the secondary index key for the WordTag.

        This key is used to query tags in DynamoDB based on the tag attribute.

        Returns:
            dict: A dictionary containing the secondary index key for the WordTag.
        """
        tag_upper = self.tag
        spaced_tag_upper = f"{tag_upper:_>40}"
        return {"GSI2PK": {"S": f"IMAGE#{self.image_id}"},
            "GSI2SK": {"S": f"LINE#{self.line_id:05d}#WORD#{self.word_id:05d}#TAG#{spaced_tag_upper}"}, }

    def to_item(self) -> dict:
        """Converts the WordTag object to a DynamoDB item.

        Returns:
            dict: A dictionary representing the WordTag as a DynamoDB item.
        """
        return {**self.key(),
            **self.gsi1_key(),
            **self.gsi2_key(),
            "TYPE": {"S": "WORD_TAG"},
            "tag_name": {"S": self.tag},
            "timestamp_added": {"S": self.timestamp_added},
            "validated": ({"BOOL": self.validated}
                if self.validated is not None
                else {"NULL": True}),
            "timestamp_validated": ({"S": self.timestamp_validated}
                if self.timestamp_validated is not None
                else {"NULL": True}),
            "gpt_confidence": ({"N": str(self.gpt_confidence)}
                if self.gpt_confidence is not None
                else {"NULL": True}),
            "flag": ({"S": self.flag} if self.flag is not None else {"NULL": True}),
            "revised_tag": ({"S": self.revised_tag}
                if self.revised_tag is not None
                else {"NULL": True}),
            "human_validated": ({"BOOL": self.human_validated}
                if self.human_validated is not None
                else {"NULL": True}),
            "timestamp_human_validated": ({"S": self.timestamp_human_validated}
                if self.timestamp_human_validated is not None
                else {"NULL": True}), }

    def to_Word_key(self) -> dict:
        """Generates the key for the Word table associated with this tag.

        Returns:
            dict: A dictionary representing the key for the Word in DynamoDB.
        """
        return {"PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {"S": f"LINE#{self.line_id:05d}"
                f"#WORD#{self.word_id:05d}"}, }


def itemToWordTag(item: dict) -> WordTag:
    """Converts a DynamoDB item to a WordTag object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        WordTag: The WordTag object represented by the DynamoDB item.

    Raises:
        ValueError: If the item is missing required keys or has malformed fields.
    """
    required_keys = {"PK", "SK", "validated", "timestamp_added"}
    if not required_keys.issubset(item.keys()):
        missing_keys = required_keys - set(item.keys())
        raise ValueError(f"Item is missing required keys: {missing_keys}")
    try:
        pk_parts = item["PK"]["S"].split("#")
        sk_parts = item["SK"]["S"].split("#")
        validated = (bool(item["validated"]["BOOL"])
            if "BOOL" in item["validated"]
            else None)
        if "timestamp_validated" in item:
            timestamp_validated = (datetime.fromisoformat(item["timestamp_validated"]["S"])
                if "S" in item["timestamp_validated"]
                else None)
        else:
            timestamp_validated = None
        if "gpt_confidence" in item:
            gpt_confidence = (int(item["gpt_confidence"]["N"])
                if "N" in item["gpt_confidence"]
                else None)
        else:
            gpt_confidence = None
        if "flag" in item:
            flag = item["flag"]["S"] if "S" in item["flag"] else None
        else:
            flag = None
        if "revised_tag" in item:
            revised_tag = (item["revised_tag"]["S"]
                if "S" in item["revised_tag"]
                else None)
        else:
            revised_tag = None
        if "human_validated" in item:
            human_validated = (bool(item["human_validated"]["BOOL"])
                if "BOOL" in item["human_validated"]
                else None)
        else:
            human_validated = None
        if "timestamp_human_validated" in item:
            timestamp_human_validated = (item["timestamp_human_validated"]["S"]
                if "S" in item["timestamp_human_validated"]
                else None)
        else:
            timestamp_human_validated = None
        tag = sk_parts[-1].lstrip("_").strip()
        return WordTag(image_id=pk_parts[1],
            line_id=int(sk_parts[1]),
            word_id=int(sk_parts[3]),
            tag=tag,
            timestamp_added=datetime.fromisoformat(item["timestamp_added"]["S"]),
            validated=validated,
            timestamp_validated=timestamp_validated,
            gpt_confidence=gpt_confidence,
            flag=flag,
            revised_tag=revised_tag,
            human_validated=human_validated,
            timestamp_human_validated=timestamp_human_validated, )
    except (IndexError, ValueError, KeyError) as e:
        raise ValueError(f"Error converting item to WordTag: {e}")
