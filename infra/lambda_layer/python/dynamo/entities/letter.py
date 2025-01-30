from typing import Generator, Tuple
from dynamo.entities.util import (
    assert_valid_uuid,
    assert_valid_bounding_box,
    assert_valid_point,
    _format_float,
)
from math import pi, radians, sin, cos


class Letter:
    """
    Represents a single Letter within an image, including its text, bounding box,
    positional corners, angle, and confidence score.

    This class provides methods to generate DynamoDB key structures, transform
    the letter's coordinates (translate, scale, rotate), calculate its centroid,
    and serialize the data for DynamoDB.
    """

    def __init__(
        self,
        image_id: str,
        line_id: int,
        word_id: int,
        id: int,
        text: str,
        bounding_box: dict,
        top_right: dict,
        top_left: dict,
        bottom_right: dict,
        bottom_left: dict,
        angle_degrees: float,
        angle_radians: float,
        confidence: float,
    ):
        """
        Constructs a new Letter object for DynamoDB.

        Args:
            image_id (str): The UUID of the image the letter belongs to.
            line_id (int): The ID of the line the letter belongs to.
            word_id (int): The ID of the word the letter belongs to.
            id (int): The ID of the letter.
            text (str): The text of the letter (must be exactly one character).
            bounding_box (dict): The bounding box of the letter
                (keys: 'x', 'y', 'width', 'height').
            top_right (dict): The top right point of the letter (keys: 'x', 'y').
            top_left (dict): The top left point of the letter (keys: 'x', 'y').
            bottom_right (dict): The bottom right point of the letter (keys: 'x', 'y').
            bottom_left (dict): The bottom left point of the letter (keys: 'x', 'y').
            angle_degrees (float): The angle of the letter in degrees.
            angle_radians (float): The angle of the letter in radians.
            confidence (float): The confidence of the letter (0 < confidence <= 1).

        Raises:
            ValueError: If any of the inputs are invalid (e.g., invalid UUID,
                non-positive IDs, text not exactly one character, invalid bounding box,
                points, angles, or out-of-range confidence).
        """
        assert_valid_uuid(image_id)
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

        assert_valid_bounding_box(bounding_box)
        self.bounding_box = bounding_box

        assert_valid_point(top_right)
        self.top_right = top_right

        assert_valid_point(top_left)
        self.top_left = top_left

        assert_valid_point(bottom_right)
        self.bottom_right = bottom_right

        assert_valid_point(bottom_left)
        self.bottom_left = bottom_left

        if not isinstance(angle_degrees, (float, int)):
            raise ValueError(
                f"angle_degrees must be a float or int"
            )
        self.angle_degrees = angle_degrees

        if not isinstance(angle_radians, (float, int)):
            raise ValueError(
                f"angle_radians must be a float or int"
            )
        self.angle_radians = angle_radians

        if confidence <= 0 or confidence > 1:
            raise ValueError("confidence must be a float between 0 and 1")
        self.confidence = confidence

    def key(self) -> dict:
        """
        Generates the primary key for this Letter in DynamoDB.

        Returns:
            dict: A dictionary containing "PK" and "SK" for DynamoDB. The "PK" uses
            the image_id, and the "SK" encodes the line_id, word_id, and letter id
            in zero-padded format.
        """
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {
                "S": f"LINE#{self.line_id:05d}#WORD#{self.word_id:05d}#LETTER#{self.id:05d}"
            },
        }

    def to_item(self) -> dict:
        """
        Serializes this Letter into a dictionary compatible with DynamoDB.

        Returns:
            dict: A dictionary representing the Letter’s data in DynamoDB’s
            key-value structure, including bounding box, corners, angles, and confidence.
        """
        return {
            **self.key(),
            "TYPE": {"S": "LETTER"},
            "text": {"S": self.text},
            "bounding_box": {
                "M": {
                    "x": {"N": _format_float(self.bounding_box["x"], 18, 20)},
                    "y": {"N": _format_float(self.bounding_box["y"], 18, 20)},
                    "width": {"N": _format_float(self.bounding_box["width"], 18, 20)},
                    "height": {"N": _format_float(self.bounding_box["height"], 18, 20)},
                }
            },
            "top_right": {
                "M": {
                    "x": {"N": _format_float(self.top_right["x"], 18, 20)},
                    "y": {"N": _format_float(self.top_right["y"], 18, 20)},
                }
            },
            "top_left": {
                "M": {
                    "x": {"N": _format_float(self.top_left["x"], 18, 20)},
                    "y": {"N": _format_float(self.top_left["y"], 18, 20)},
                }
            },
            "bottom_right": {
                "M": {
                    "x": {"N": _format_float(self.bottom_right["x"], 18, 20)},
                    "y": {"N": _format_float(self.bottom_right["y"], 18, 20)},
                }
            },
            "bottom_left": {
                "M": {
                    "x": {"N": _format_float(self.bottom_left["x"], 18, 20)},
                    "y": {"N": _format_float(self.bottom_left["y"], 18, 20)},
                }
            },
            "angle_degrees": {"N": _format_float(self.angle_degrees, 10, 12)},
            "angle_radians": {"N": _format_float(self.angle_radians, 10, 12)},
            "confidence": {"N": _format_float(self.confidence, 2, 2)},
        }

    def calculate_centroid(self) -> Tuple[float, float]:
        """
        Calculates the centroid (geometric center) of the Letter from its corners.

        Returns:
            Tuple[float, float]: The (x, y) coordinates of the Letter’s centroid.
        """
        x = (
            self.top_right["x"]
            + self.top_left["x"]
            + self.bottom_right["x"]
            + self.bottom_left["x"]
        ) / 4
        y = (
            self.top_right["y"]
            + self.top_left["y"]
            + self.bottom_right["y"]
            + self.bottom_left["y"]
        ) / 4
        return x, y

    def translate(self, x: float, y: float) -> None:
        """
        Translates the Letter by (x, y).

        Args:
            x (float): The amount to translate in the x-direction.
            y (float): The amount to translate in the y-direction.

        Warning:
            This method updates top_right, top_left, bottom_right, and bottom_left
            but does **not** update the bounding_box.
        """
        self.top_right["x"] += x
        self.top_right["y"] += y
        self.top_left["x"] += x
        self.top_left["y"] += y
        self.bottom_right["x"] += x
        self.bottom_right["y"] += y
        self.bottom_left["x"] += x
        self.bottom_left["y"] += y
        Warning("This function does not update the bounding box")

    def scale(self, sx: float, sy: float) -> None:
        """
        Scales the Letter’s coordinates and bounding box by the specified x and y factors.

        Args:
            sx (float): Scale factor in the x-direction.
            sy (float): Scale factor in the y-direction.
        """
        self.top_right["x"] *= sx
        self.top_right["y"] *= sy
        self.top_left["x"] *= sx
        self.top_left["y"] *= sy
        self.bottom_right["x"] *= sx
        self.bottom_right["y"] *= sy
        self.bottom_left["x"] *= sx
        self.bottom_left["y"] *= sy
        self.bounding_box["x"] *= sx
        self.bounding_box["y"] *= sy
        self.bounding_box["width"] *= sx
        self.bounding_box["height"] *= sy

    def rotate(
        self,
        angle: float,
        rotate_origin_x: float,
        rotate_origin_y: float,
        use_radians: bool = True,
    ) -> None:
        """
        Rotates the Letter by the specified angle around (rotate_origin_x, rotate_origin_y).

        Only rotates if angle is within:
            - [-π/2, π/2] when use_radians=True
            - [-90°, 90°] when use_radians=False

        Otherwise, raises ValueError.

        After rotation, the letter's angle_degrees/angle_radians are updated
        by adding the rotation. The bounding_box is **not** updated.

        Args:
            angle (float): The angle by which to rotate. Interpreted as degrees
                if `use_radians=False`, else radians.
            rotate_origin_x (float): The x-coordinate of the rotation origin.
            rotate_origin_y (float): The y-coordinate of the rotation origin.
            use_radians (bool, optional): Indicates if the angle is in radians.
                Defaults to True.

        Raises:
            ValueError: If the angle is outside the allowed range.
        """
        if use_radians:
            if not (-pi / 2 <= angle <= pi / 2):
                raise ValueError(
                    f"Angle {angle} (radians) is outside the allowed range [-π/2, π/2]."
                )
            angle_radians = angle
        else:
            if not (-90 <= angle <= 90):
                raise ValueError(
                    f"Angle {angle} (degrees) is outside the allowed range [-90°, 90°]."
                )
            angle_radians = radians(angle)

        def rotate_point(px, py, ox, oy, theta):
            translated_x = px - ox
            translated_y = py - oy
            rotated_x = translated_x * cos(theta) - translated_y * sin(theta)
            rotated_y = translated_x * sin(theta) + translated_y * cos(theta)
            final_x = rotated_x + ox
            final_y = rotated_y + oy
            return final_x, final_y

        corners = [self.top_right, self.top_left, self.bottom_right, self.bottom_left]
        for corner in corners:
            x_new, y_new = rotate_point(
                corner["x"],
                corner["y"],
                rotate_origin_x,
                rotate_origin_y,
                angle_radians,
            )
            corner["x"] = x_new
            corner["y"] = y_new

        if use_radians:
            self.angle_radians += angle_radians
            self.angle_degrees += angle_radians * 180.0 / pi
        else:
            self.angle_degrees += angle
            self.angle_radians += radians(angle)

        Warning("This function does not update the bounding box")

    def __repr__(self):
        """
        Returns a string representation of the Letter object.

        Returns:
            str: The string representation of the Letter object.
        """
        return f"Letter(id={self.id}, text='{self.text}')"

    def __iter__(self) -> Generator[Tuple[str, dict], None, None]:
        """
        Yields the Letter object’s attributes as a series of (key, value) pairs.

        Yields:
            Generator[Tuple[str, dict], None, None]: Each yield is a tuple
            of (attribute_name, attribute_value).
        """
        yield "image_id", self.image_id
        yield "word_id", self.word_id
        yield "line_id", self.line_id
        yield "id", self.id
        yield "text", self.text
        yield "bounding_box", self.bounding_box
        yield "top_right", self.top_right
        yield "top_left", self.top_left
        yield "bottom_right", self.bottom_right
        yield "bottom_left", self.bottom_left
        yield "angle_degrees", self.angle_degrees
        yield "angle_radians", self.angle_radians
        yield "confidence", self.confidence

    def __eq__(self, other: object) -> bool:
        """
        Compares two Letter objects for equality based on their attributes.

        Args:
            other (object): The object to compare to this Letter.

        Returns:
            bool: True if the objects have the same attributes, False otherwise.
        """
        if not isinstance(other, Letter):
            return False
        return (
            self.image_id == other.image_id
            and self.line_id == other.line_id
            and self.word_id == other.word_id
            and self.id == other.id
            and self.text == other.text
            and self.bounding_box == other.bounding_box
            and self.top_right == other.top_right
            and self.top_left == other.top_left
            and self.bottom_right == other.bottom_right
            and self.bottom_left == other.bottom_left
            and self.angle_degrees == other.angle_degrees
            and self.angle_radians == other.angle_radians
            and self.confidence == other.confidence
        )


def itemToLetter(item: dict) -> Letter:
    """
    Converts a DynamoDB item dictionary into a Letter object.

    This function expects a dictionary that contains:
    - PK: {"S": "IMAGE#<uuid>"}
    - SK: {"S": "LINE#<line_id>#WORD#<word_id>#LETTER#<letter_id>"}
    - text: {"S": <single_character_string>}
    - bounding_box, top_right, top_left, bottom_right, bottom_left:
      nested dicts with numeric values
    - angle_degrees, angle_radians, confidence: numeric values

    Args:
        item (dict): A dictionary in the DynamoDB format containing all required keys.

    Returns:
        Letter: An instance of the Letter class populated from the given item.

    Raises:
        ValueError: If the item is missing required keys or fails the conversion
            (e.g., numeric parsing issues).
    """
    required_keys = {
        "PK",
        "SK",
        "text",
        "bounding_box",
        "top_right",
        "top_left",
        "bottom_right",
        "bottom_left",
        "angle_degrees",
        "angle_radians",
        "confidence",
    }
    if not required_keys.issubset(item.keys()):
        missing_keys = required_keys - set(item.keys())
        raise ValueError("Item is missing required keys", missing_keys)

    try:
        return Letter(
            image_id=item["PK"]["S"][6:],  # strip off "IMAGE#"
            id=int(item["SK"]["S"].split("#")[5]),
            line_id=int(item["SK"]["S"].split("#")[1]),
            word_id=int(item["SK"]["S"].split("#")[3]),
            text=item["text"]["S"],
            bounding_box={
                key: float(value["N"])
                for key, value in item["bounding_box"]["M"].items()
            },
            top_right={
                key: float(value["N"]) for key, value in item["top_right"]["M"].items()
            },
            top_left={
                key: float(value["N"]) for key, value in item["top_left"]["M"].items()
            },
            bottom_right={
                key: float(value["N"])
                for key, value in item["bottom_right"]["M"].items()
            },
            bottom_left={
                key: float(value["N"])
                for key, value in item["bottom_left"]["M"].items()
            },
            angle_degrees=float(item["angle_degrees"]["N"]),
            angle_radians=float(item["angle_radians"]["N"]),
            confidence=float(item["confidence"]["N"]),
        )
    except KeyError as e:
        raise ValueError(f"Error converting item to Letter: {e}")
