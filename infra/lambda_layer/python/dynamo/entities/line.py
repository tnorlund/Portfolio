from typing import Generator, Tuple
from dynamo.entities.util import (
    assert_valid_uuid,
    histogram,
    assert_valid_bounding_box,
    assert_valid_point,
    _format_float,
)
from math import sin, cos, pi, radians


class Line:
    def __init__(
        self,
        image_id: str,
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
        """Initializes a new Line object for DynamoDB

        Args:
            image_id (str): UUID identifying the image
            id (int): Identifier for the line
            text (str): The text content of the line
            bounding_box (dict): The bounding box of the line
            top_right (dict): The top-right point of the line
            top_left (dict): The top-left point of the line
            bottom_right (dict): The bottom-right point of the line
            bottom_left (dict): The bottom-left point of the line
            angle_degrees (float): The angle of the line in degrees
            angle_radians (float): The angle of the line in radians
            confidence (float): The confidence level of the line

        Attributes:
            image_id (str): UUID identifying the image
            id (int): Identifier for the line
            text (str): The text content of the line
            bounding_box (dict): The bounding box of the line
            top_right (dict): The top-right point of the line
            top_left (dict): The top-left point of the line
            bottom_right (dict): The bottom-right point of the line
            bottom_left (dict): The bottom-left point of the line
            angle_degrees (float): The angle of the line in degrees
            angle_radians (float): The angle of the line in radians
            confidence (float): The confidence level of the line

        Raises:
            ValueError: If image_id is not a valid UUID
            ValueError: If id is not a positive integer
            ValueError: If text is not a string
            ValueError: If bounding_box is not valid
            ValueError: If top_right is not valid
            ValueError: If top_left is not valid
            ValueError: If bottom_right is not valid
            ValueError: If bottom_left is not valid
            ValueError: If angle_degrees is not a float
            ValueError: If angle_radians is not a float
            ValueError: If confidence is not a float between 0 and 1
        """
        assert_valid_uuid(image_id)
        self.image_id = image_id
        # Ensure the ID is a positive integer
        if not isinstance(id, int):
            raise ValueError("id must be an integer")
        if id <= 0:
            raise ValueError("id must be positive")
        self.id = id
        if not isinstance(text, str):
            raise ValueError("text must be a string")
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
            raise ValueError(f"angle_degrees must be a float or int")
        self.angle_degrees = angle_degrees
        if not isinstance(angle_radians, (float, int)):
            raise ValueError("angle_radians must be a float or int")
        self.angle_radians = angle_radians
        # Ensure the confidence is a float between 0 and 1
        if isinstance(confidence, int):
            confidence = float(confidence)
        if not isinstance(confidence, float):
            raise ValueError("confidence must be a float")
        if confidence <= 0.0 or confidence > 1.0:
            raise ValueError("confidence must be between 0 and 1")
        self.confidence = confidence
        self.histogram = histogram(text)
        self.num_chars = len(text)

    def key(self) -> dict:
        """Generates the primary key for the line

        Returns:
            dict: The primary key for the line
        """
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {"S": f"LINE#{self.id:05d}"},
        }

    def gsi1_key(self) -> dict:
        """Generates the GSI1 key for the line

        Returns:
            dict: The GSI1 key for the line
        """
        return {
            "GSI1PK": {"S": f"IMAGE"},
            "GSI1SK": {"S": f"IMAGE#{self.image_id}#LINE#{self.id:05d}"},
        }

    def to_item(self) -> dict:
        """Converts the Line object to a DynamoDB item

        Returns:
            dict: The Line object as a DynamoDB item
        """
        return {
            **self.key(),
            **self.gsi1_key(),
            "TYPE": {"S": "LINE"},
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
            "histogram": {"M": {k: {"N": str(v)} for k, v in self.histogram.items()}},
            "num_chars": {"N": str(self.num_chars)},
        }

    def calculate_centroid(self) -> Tuple[float, float]:
        """Calculates the centroid of the line

        Returns:
            Tuple[float, float]: The x and y coordinates of the centroid
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
        """Translates the x, y position"""
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
        """Scales the line by the x and y factors"""
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
        Rotates the line by the specified angle around (rotate_origin_x, rotate_origin_y).
        ONLY rotates if angle is within:
        - [-90°, 90°], if use_radians=False
        - [-π/2, π/2], if use_radians=True
        Otherwise, raises ValueError.

        Updates top_right, topLeft, bottomRight, bottomLeft in-place,
        and also updates angleDegrees/angleRadians.

        Args:
            angle (float): The angle by which to rotate the line.
            rotate_origin_x (float): The x-coordinate of the rotation origin.
            rotate_origin_y (float): The y-coordinate of the rotation origin.
            use_radians (bool): If True, `angle` is in radians. Otherwise, degrees.
        """

        # 1) Check allowed range
        if use_radians:
            # Allowed range is [-π/2, π/2]
            if not (-pi / 2 <= angle <= pi / 2):
                raise ValueError(
                    f"Angle {angle} (radians) is outside the allowed range [-π/2, π/2]."
                )
            angle_radians = angle
        else:
            # Allowed range is [-90, 90] degrees
            if not (-90 <= angle <= 90):
                raise ValueError(
                    f"Angle {angle} (degrees) is outside the allowed range [-90°, 90°]."
                )
            # Convert to radians
            angle_radians = radians(angle)

        # 2) Rotate each corner
        def rotate_point(px, py, ox, oy, theta):
            """
            Rotates point (px, py) around (ox, oy) by theta radians.
            Returns the new (x, y) coordinates.
            """
            # Translate point so that (ox, oy) becomes the origin
            translated_x = px - ox
            translated_y = py - oy

            # Apply the rotation
            rotated_x = translated_x * cos(theta) - translated_y * sin(theta)
            rotated_y = translated_x * sin(theta) + translated_y * cos(theta)

            # Translate back
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

        # 3) Update angleDegrees and angleRadians
        if use_radians:
            # Accumulate the rotation in angleRadians
            self.angle_radians += angle_radians
            self.angle_degrees += angle_radians * 180.0 / pi
        else:
            # If it was in degrees, accumulate in degrees
            self.angle_degrees += angle
            # Convert that addition to radians
            self.angle_radians += radians(angle)

        # 4) Warn that the bounding box is not updated
        Warning("This function does not update the bounding box")

    def __repr__(self) -> str:
        """Returns a string representation of the Line object

        Returns:
            str: The string representation of the Line object
        """
        return f"Line(id={self.id}, text='{self.text}')"

    def __iter__(self) -> Generator[Tuple[str, int], None, None]:
        """Returns an iterator over the Line object

        Returns:
            dict: The iterator over the Line object
        """
        yield "image_id", self.image_id
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
        yield "histogram", self.histogram
        yield "num_chars", self.num_chars

    def __eq__(self, other: object) -> bool:
        """Compares two Line objects

        Args:
            other (object): The object to compare

        Returns:
            bool: True if the objects are equal, False otherwise
        """
        if not isinstance(other, Line):
            return False
        return (
            self.image_id == other.image_id
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


def itemToLine(item: dict) -> Line:
    """Converts a DynamoDB item to a Line object

    Args:
        item (dict): The DynamoDB item to convert

    Returns:
        Line: The Line object represented by the DynamoDB item
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
        raise ValueError(f"Item is missing required keys: {missing_keys}")
    try:
        return Line(
            image_id=item["PK"]["S"][6:],
            id=int(item["SK"]["S"][6:]),
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
        raise ValueError(f"Error converting item to Line: {e}")
