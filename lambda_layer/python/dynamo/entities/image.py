from typing import Generator, Tuple


class Image:
    def __init__(self, id: int, width: int, height: int):
        """Constructs a new Image object for DynamoDB

        Args:
            id (int): Number identifying the image
            width (int): The width of the image in pixels
            height (int): The height of the image in pixels

        Raises:
            ValueError: When the ID is not a positive integer
        """
        # Ensure the ID is a positive integer
        if id <= 0:
            raise ValueError("id must be a positive integer")
        self.id = id
        self.width = width
        self.height = height

    def key(self) -> dict:
        """Generates the primary key for the image

        Returns:
            str: The primary key for the image
        """
        return {"PK": {"S": f"IMAGE#{self.id}"}, "SK": {"S": "IMAGE"}}

    def to_item(self) -> dict:
        """Converts the Image object to a DynamoDB item

        Returns:
            dict: The DynamoDB item representation of the Image
        """
        return {**self.key(), "Width": {"N": str(self.width)}, "Height": {"N": str(self.height)}}

    def __repr__(self) -> str:
        """Returns a string representation of the Image object

        Returns:
            str: The string representation of the Image object
        """
        return f"Image(id={self.id}, width={self.width}, height={self.height})"

    def __iter__(self) -> Generator[Tuple[str, int], None, None]:
        """Returns an iterator over the Image object

        Returns:
            dict: The iterator over the Image object
        """
        yield "id", self.id
        yield "width", self.width
        yield "height", self.height
