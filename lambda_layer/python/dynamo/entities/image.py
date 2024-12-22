from typing import Generator, Tuple


class Image:
    def __init__(self, id: int, width: int, height: int):
        """Constructs a new Image object for DynamoDB

        Args:
            id (int): Number identifying the image
            width (int): The width of the image in pixels
            height (int): The height of the image in pixels

        Attributes:
            id (int): Number identifying the image
            width (int): The width of the image in pixels
            height (int): The height of the image in pixels

        Raises:
            ValueError: When the ID is not a positive integer
        """
        # Ensure the ID is a positive integer
        if id <= 0:
            raise ValueError("id must be a positive integer")
        self.id = f"{id:05d}" # Zero pad the ID to 5 digits
        # Ensure the width and height are positive integers
        if (
            width <= 0
            or height <= 0
            or not isinstance(width, int)
            or not isinstance(height, int)
        ):
            raise ValueError("width and height must be positive integers")
        self.width = width
        self.height = height

    def key(self) -> dict:
        """Generates the primary key for the image

        Returns:
            dict: The primary key for the image
        """
        return {"PK": {"S": f"IMAGE#{self.id}"}, "SK": {"S": "IMAGE"}}

    def to_item(self) -> dict:
        """Converts the Image object to a DynamoDB item

        Returns:
            dict: The DynamoDB item representation of the Image
        """
        return {
            **self.key(),
            "Width": {"N": str(self.width)},
            "Height": {"N": str(self.height)},
        }

    def __repr__(self) -> str:
        """Returns a string representation of the Image object

        Returns:
            str: The string representation of the Image object
        """
        return f"Image(id={int(self.id)}, width={self.width}, height={self.height})"

    def __iter__(self) -> Generator[Tuple[str, int], None, None]:
        """Returns an iterator over the Image object

        Returns:
            dict: The iterator over the Image object
        """
        yield "id", int(self.id)
        yield "width", self.width
        yield "height", self.height

    def __eq__(self, other) -> bool:
        """Checks if two Image objects are equal

        Args:
            other (Image): The other Image object to compare

        Returns:
            bool: True if the Image objects are equal, False otherwise
        """
        if not isinstance(other, Image):
            return NotImplemented
        return (
            int(self.id) == int(other.id)
            and self.width == other.width
            and self.height == other.height
        )


def itemToImage(item: dict) -> Image:
    """Converts a DynamoDB item to an Image object

    Args:
        item (dict): The DynamoDB item to convert

    Returns:
        Image: The Image object represented by the DynamoDB item

    Raises:
        ValueError: When the item format is invalid
    """
    if item.keys() != {"PK", "SK", "Width", "Height"}:
        raise ValueError("Invalid item format")
    try:
        return Image(
            id=int(item["PK"]["S"].split("#")[1]),
            width=int(item["Width"]["N"]),
            height=int(item["Height"]["N"]),
        )
    except KeyError:
        raise ValueError("Invalid item format")
