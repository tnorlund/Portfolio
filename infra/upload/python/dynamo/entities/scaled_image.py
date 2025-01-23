from typing import Generator, Tuple
from datetime import datetime


class ScaledImage:
    def __init__(
        self,
        image_id: int,
        timestamp_added: datetime,
        base64: str,
        quality: int,
    ):
        if image_id <= 0 or not isinstance(image_id, int):
            raise ValueError("image_id must be a positive integer")
        self.image_id = image_id
        if isinstance(timestamp_added, datetime):
            self.timestamp_added = timestamp_added.isoformat()
        elif isinstance(timestamp_added, str):
            self.timestamp_added = timestamp_added
        else:
            raise ValueError("timestamp_added must be a datetime object or a string")
        if base64 is None or not isinstance(base64, str):
            raise ValueError("base64 must be a string")
        self.base64 = base64
        if quality <= 0 or not isinstance(quality, int):
            raise ValueError("quality must be a positive int")
        self.quality = quality

    def key(self) -> dict:
        formatted_pk = f"IMAGE#{self.image_id:05d}"
        formatted_sk = f"IMAGE_SCALE#{self.quality:05d}"

        return {
            "PK": {"S": formatted_pk},
            "SK": {"S": formatted_sk},
        }

    def to_item(self) -> dict:
        return {
            **self.key(),
            "Type": {"S": "IMAGE_SCALE"},
            "TimestampAdded": {"S": self.timestamp_added},
            "Base64": {"S": self.base64},
            "Quality": {"N": str(self.quality)},
        }

    def __repr__(self):
        return f"ScaledImage(image_id={int(self.image_id)}, quality={self.quality})"

    def __iter__(self) -> Generator[Tuple[str, int], None, None]:
        yield "image_id", self.image_id
        yield "timestamp_added", self.timestamp_added
        yield "base64", self.base64
        yield "quality", self.quality

    def __eq__(self, value):
        return (
            self.image_id == value.image_id
            and self.timestamp_added == value.timestamp_added
            and self.base64 == value.base64
            and self.quality == value.quality
        )


def ItemToScaledImage(item: dict) -> ScaledImage:
    return ScaledImage(
        image_id=int(item["PK"]["S"].split("#")[1]),
        timestamp_added=item["TimestampAdded"]["S"],
        base64=item["Base64"]["S"],
        quality=int(item["Quality"]["N"]),
    )
