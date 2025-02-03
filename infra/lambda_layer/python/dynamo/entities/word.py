from typing import Generator, Tuple
from dynamo.entities.util import (
    assert_valid_uuid,
    compute_histogram,
    assert_valid_bounding_box,
    assert_valid_point,
    _format_float,
    shear_point,
)
from math import atan2, degrees, sin, cos, pi, radians


class Word:
    """
    Represents a word extracted from an image, including its text content,
    bounding box, corner coordinates, angles, confidence, tags, histogram, and character count.

    Provides methods for generating DynamoDB key structures, performing geometric
    transformations, calculating centroids, and serializing the word's data for storage.
    """

    def __init__(
        self,
        image_id: str,
        line_id: int,
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
        tags: list[str] = None,
        histogram: dict = None,
        num_chars: int = None,
    ):
        """Initializes a new Word object for DynamoDB.

        Args:
            image_id (str): UUID identifying the image.
            line_id (int): Identifier for the line containing the word.
            id (int): Identifier for the word.
            text (str): The text of the word.
            bounding_box (dict): The bounding box of the word with keys 'x', 'y', 'width', and 'height'.
            top_right (dict): The top-right corner coordinates of the word with keys 'x' and 'y'.
            top_left (dict): The top-left corner coordinates of the word with keys 'x' and 'y'.
            bottom_right (dict): The bottom-right corner coordinates of the word with keys 'x' and 'y'.
            bottom_left (dict): The bottom-left corner coordinates of the word with keys 'x' and 'y'.
            angle_degrees (float): The angle of the word in degrees.
            angle_radians (float): The angle of the word in radians.
            confidence (float): The confidence level of the word (between 0 and 1).
            tags (list[str], optional): A list of tags associated with the word.
            histogram (dict, optional): A histogram representing character frequencies of the word.
            num_chars (int, optional): The number of characters in the word.

        Attributes:
            image_id (str): UUID identifying the image.
            line_id (int): Identifier for the line containing the word.
            id (int): Identifier for the word.
            text (str): The text of the word.
            bounding_box (dict): The bounding box of the word.
            top_right (dict): The top-right corner coordinates.
            top_left (dict): The top-left corner coordinates.
            bottom_right (dict): The bottom-right corner coordinates.
            bottom_left (dict): The bottom-left corner coordinates.
            angle_degrees (float): The angle of the word in degrees.
            angle_radians (float): The angle of the word in radians.
            confidence (float): The confidence level.
            tags (list[str]): The tags associated with the word.
            histogram (dict): The histogram of the word's text.
            num_chars (int): The number of characters in the word.

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

        if not isinstance(id, int):
            raise ValueError("id must be an integer")
        if id < 0:
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
            raise ValueError("angle_degrees must be a float or int")
        self.angle_degrees = angle_degrees

        if not isinstance(angle_radians, (float, int)):
            raise ValueError("angle_radians must be a float or int")
        self.angle_radians = angle_radians

        if isinstance(confidence, int):
            confidence = float(confidence)
        if not isinstance(confidence, float):
            raise ValueError("confidence must be a float")
        if confidence <= 0.0 or confidence > 1.0:
            raise ValueError("confidence must be between 0 and 1")
        self.confidence = confidence

        if tags is not None and not isinstance(tags, list):
            raise ValueError("tags must be a list")
        self.tags = tags if tags is not None else []

        if histogram is None:
            self.histogram = compute_histogram(self.text)
        else:
            self.histogram = histogram

        if num_chars is None:
            self.num_chars = len(text)
        else:
            self.num_chars = num_chars

    def key(self) -> dict:
        """
        Generates the primary key for this Word in DynamoDB.

        Returns:
            dict: A dictionary containing "PK" and "SK" for DynamoDB.
                  - "PK" is of the form "IMAGE#<image_id>"
                  - "SK" is of the form "LINE#<line_id>#WORD#<word_id>"
        """
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {"S": f"LINE#{self.line_id:05d}#WORD#{self.id:05d}"},
        }

    def to_item(self) -> dict:
        """
        Serializes this Word into a dictionary compatible with DynamoDB.

        Returns:
            dict: A dictionary representing the Word's data in DynamoDB's
                  key-value structure, including bounding box, corners, angles,
                  confidence, histogram, number of characters, and tags.
        """
        item = {
            **self.key(),
            "TYPE": {"S": "WORD"},
            "text": {"S": self.text},
            "bounding_box": {
                "M": {
                    "x": {"N": _format_float(self.bounding_box["x"], 20, 22)},
                    "y": {"N": _format_float(self.bounding_box["y"], 20, 22)},
                    "width": {"N": _format_float(self.bounding_box["width"], 20, 22)},
                    "height": {"N": _format_float(self.bounding_box["height"], 20, 22)},
                }
            },
            "top_right": {
                "M": {
                    "x": {"N": _format_float(self.top_right["x"], 20, 22)},
                    "y": {"N": _format_float(self.top_right["y"], 20, 22)},
                }
            },
            "top_left": {
                "M": {
                    "x": {"N": _format_float(self.top_left["x"], 20, 22)},
                    "y": {"N": _format_float(self.top_left["y"], 20, 22)},
                }
            },
            "bottom_right": {
                "M": {
                    "x": {"N": _format_float(self.bottom_right["x"], 20, 22)},
                    "y": {"N": _format_float(self.bottom_right["y"], 20, 22)},
                }
            },
            "bottom_left": {
                "M": {
                    "x": {"N": _format_float(self.bottom_left["x"], 20, 22)},
                    "y": {"N": _format_float(self.bottom_left["y"], 20, 22)},
                }
            },
            "angle_degrees": {"N": _format_float(self.angle_degrees, 18, 20)},
            "angle_radians": {"N": _format_float(self.angle_radians, 18, 20)},
            "confidence": {"N": _format_float(self.confidence, 2, 2)},
            "histogram": {
                "M": {
                    k: {"N": _format_float(v, 10, 12)}
                    for k, v in self.histogram.items()
                }
            },
            "num_chars": {"N": str(self.num_chars)},
        }

        if self.tags:
            item["tags"] = {"SS": self.tags}

        return item

    def calculate_centroid(self) -> Tuple[float, float]:
        """
        Calculates the centroid (geometric center) of the Word from its corners.

        Returns:
            Tuple[float, float]: The (x, y) coordinates of the Word's centroid.
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
        Translates the Word by (x, y).

        Args:
            x (float): The amount to translate in the x-direction.
            y (float): The amount to translate in the y-direction.
        """
        self.top_right["x"] += x
        self.top_right["y"] += y
        self.top_left["x"] += x
        self.top_left["y"] += y
        self.bottom_right["x"] += x
        self.bottom_right["y"] += y
        self.bottom_left["x"] += x
        self.bottom_left["y"] += y
        self.bounding_box["x"] += x
        self.bounding_box["y"] += y

    def scale(self, sx: float, sy: float) -> None:
        """
        Scales the Word's coordinates and bounding box by the specified x and y factors.

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
        Rotates the Word by the specified angle around (rotate_origin_x, rotate_origin_y).

        Only rotates if angle is within:
            - [-π/2, π/2] when use_radians=True
            - [-90°, 90°] when use_radians=False

        Updates top_right, topLeft, bottomRight, bottomLeft in-place,
        and also updates angleDegrees/angleRadians.

        Args:
            angle (float): The angle by which to rotate. Interpreted as degrees
                if `use_radians=False`, else radians.
            rotate_origin_x (float): The x-coordinate of the rotation origin.
            rotate_origin_y (float): The y-coordinate of the rotation origin.
            use_radians (bool, optional): Indicates if the angle is in radians.
                Defaults to True.

        Raises:
            ValueError: If the angle is outside the allowed range
                ([-π/2, π/2] in radians or [-90°, 90°] in degrees).
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

        # 4) Recalculate the axis-aligned bounding box from the rotated corners
        xs = [pt["x"] for pt in corners]
        ys = [pt["y"] for pt in corners]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)

        self.bounding_box["x"] = min_x
        self.bounding_box["y"] = min_y
        self.bounding_box["width"] = max_x - min_x
        self.bounding_box["height"] = max_y - min_y

    def shear(
        self, shx: float, shy: float, pivot_x: float = 0.0, pivot_y: float = 0.0
    ) -> None:
        """
        Shears the Word by shx (horizontal shear) and shy (vertical shear)
        around a pivot point (pivot_x, pivot_y).

        - (shx, shy) = (0.2, 0.0) would produce a horizontal slant
        - (shx, shy) = (0.0, 0.2) would produce a vertical slant
        - You can combine both for a more general shear.

        Modifies top_right, top_left, bottom_right, bottom_left,
        and then recalculates the axis-aligned bounding box.
        """
        corners = [self.top_right, self.top_left, self.bottom_right, self.bottom_left]
        for corner in corners:
            x_new, y_new = shear_point(
                corner["x"], corner["y"], pivot_x, pivot_y, shx, shy
            )
            corner["x"] = x_new
            corner["y"] = y_new

        # Recalculate axis-aligned bounding box from new corners
        xs = [pt["x"] for pt in corners]
        ys = [pt["y"] for pt in corners]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)

        self.bounding_box["x"] = min_x
        self.bounding_box["y"] = min_y
        self.bounding_box["width"] = max_x - min_x
        self.bounding_box["height"] = max_y - min_y

    def warp_affine(self, a, b, c, d, e, f):
        """
        Applies the forward 2x3 affine transform to this lines corners:
        x' = a*x + b*y + c
        y' = d*x + e*y + f
        Then recomputes the axis-aligned bounding box and angle.
        """
        corners = [self.top_left, self.top_right, self.bottom_left, self.bottom_right]

        # 1) Transform corners in-place
        for corner in corners:
            x_old = corner["x"]
            y_old = corner["y"]
            x_new = a * x_old + b * y_old + c
            y_new = d * x_old + e * y_old + f
            corner["x"] = x_new
            corner["y"] = y_new

        # 2) Recompute bounding_box
        xs = [pt["x"] for pt in corners]
        ys = [pt["y"] for pt in corners]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)

        self.bounding_box["x"] = min_x
        self.bounding_box["y"] = min_y
        self.bounding_box["width"] = max_x - min_x
        self.bounding_box["height"] = max_y - min_y

        dx = self.top_right["x"] - self.top_left["x"]
        dy = self.top_right["y"] - self.top_left["y"]

        # angle_radians is angle from x-axis
        new_angle_radians = atan2(dy, dx)  # range [-pi, pi]
        new_angle_degrees = new_angle_radians * 180.0 / pi

        self.angle_radians = new_angle_radians
        self.angle_degrees = new_angle_degrees

    def warp_affine_normalized_forward(
        self,
        a_f,
        b_f,
        c_f,
        d_f,
        e_f,
        f_f,
        orig_width,
        orig_height,
        new_width,
        new_height,
        flip_y=False,
    ):
        """
        Applies the 'forward' 2x3 transform:
            x_new = a_f * x_old + b_f * y_old + c_f
            y_new = d_f * x_old + e_f * y_old + f_f
        where (x_old, y_old) are normalized wrt the original image,
        and (x_new, y_new) become normalized wrt the new subimage.

        So the final corners are in [0..1] of the new image.

        Args:
            a_f,b_f,c_f,d_f,e_f,f_f (float):
                The forward transform old->new in pixel space.
            orig_width, orig_height (int):
                Dimensions of the original image in pixels.
            new_width, new_height (int):
                Dimensions of the new warped/cropped image.
            flip_y (bool):
                If your original coords treat y=0 at the bottom, you might do
                y_old_pixels = (1 - y_old) * orig_height.
                Conversely for the final y.
                Adjust as needed so you only do one consistent flip.
        """

        corners = [self.top_left, self.top_right, self.bottom_left, self.bottom_right]

        # 1) For each corner (in old [0..1] coords):
        for corner in corners:
            # Convert from normalized old -> pixel old
            x_o = corner["x"] * orig_width
            y_o = corner["y"] * orig_height

            if flip_y:
                y_o = orig_height - y_o

            # 2) Apply the forward transform (old->new) in pixel space:
            x_new_px = a_f * x_o + b_f * y_o + c_f
            y_new_px = d_f * x_o + e_f * y_o + f_f

            # 3) Convert the new pixel coords to new [0..1]
            if flip_y:
                # If you want the new image to keep top=0, bottom=1,
                # you might do y_new_norm = 1 - (y_new_px / new_height).
                # Or do no flip if you prefer.
                corner["x"] = x_new_px / new_width
                corner["y"] = 1 - (y_new_px / new_height)
            else:
                corner["x"] = x_new_px / new_width
                corner["y"] = y_new_px / new_height

        # 4) Recompute bounding box, angle, etc. same as before
        xs = [pt["x"] for pt in corners]
        ys = [pt["y"] for pt in corners]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        self.bounding_box["x"] = min_x
        self.bounding_box["y"] = min_y
        self.bounding_box["width"] = max_x - min_x
        self.bounding_box["height"] = max_y - min_y

        dx = self.top_right["x"] - self.top_left["x"]
        dy = self.top_right["y"] - self.top_left["y"]
        angle_rad = atan2(dy, dx)
        self.angle_radians = angle_rad
        self.angle_degrees = degrees(angle_rad)

    def rotate_90_ccw_in_place(self, old_w: int, old_h: int):
        """
        Rotates the object 90 degrees counter-clockwise in-place
        about the (0,0) origin in a standard image coordinate system
        (origin at top-left, y increasing downward).

        old_w, old_h are the image dimensions before rotation.
        """
        # Convert normalized -> pixel
        corners = [self.top_left, self.top_right, self.bottom_right, self.bottom_left]
        for corner in corners:
            corner["x"] *= old_w
            corner["y"] *= old_h

        # Now do the standard 90° CCW about (0,0) in pixel space
        for corner in corners:
            x_old = corner["x"]
            y_old = corner["y"]
            x_new = y_old
            y_new = old_w - x_old
            corner["x"] = x_new
            corner["y"] = y_new

        # The new image is (old_h, old_w) in pixel dims if you rotate 90°, so re‐normalize
        # (and optionally flip Y if you want the bottom to be y=0).
        final_w = old_h
        final_h = old_w
        for corner in corners:
            corner["x"] /= final_w
            # maybe corner["y"] = 1 - corner["y"]/final_h if you want 0 at bottom
            corner["y"] /= final_h

        # 2) Recompute the bounding box
        xs = [pt["x"] for pt in corners]
        ys = [pt["y"] for pt in corners]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        self.bounding_box["x"] = min_x
        self.bounding_box["y"] = min_y
        self.bounding_box["width"] = max_x - min_x
        self.bounding_box["height"] = max_y - min_y

        # 3) Update the angle
        self.angle_degrees += 90
        self.angle_radians += pi / 2

    def __repr__(self):
        """
        Returns a string representation of the Word object.

        Returns:
            str: The string representation of the Word object.
        """
        # fmt: off
        return (
            f"Word("
                f"id={self.id}, "
                f"text='{self.text}', "
                "bounding_box=("
                    f"x= {self.bounding_box['x']}, "
                    f"y= {self.bounding_box['y']}, "
                    f"width= {self.bounding_box['width']}, "
                    f"height= {self.bounding_box['height']}), "
                "top_right=("
                    f"x= {self.top_right['x']}, "
                    f"y= {self.top_right['y']}), "
                "top_left=("
                    f"x= {self.top_left['x']}, "
                    f"y= {self.top_left['y']}), "
                "bottom_right=("
                    f"x= {self.bottom_right['x']}, "
                    f"y= {self.bottom_right['y']}), "
                "bottom_left=("
                    f"x= {self.bottom_left['x']}, "
                    f"y= {self.bottom_left['y']}), "
                f"angle_degrees={self.angle_degrees}, "
                f"angle_radians={self.angle_radians}, "
                f"confidence={self.confidence:.2}"
            f")"
        )
        # fmt: on

    def __iter__(self) -> Generator[Tuple[str, str], None, None]:
        """
        Yields the Word object's attributes as (key, value) pairs.

        Yields:
            Generator[Tuple[str, any], None, None]: Each yield is a tuple
            of (attribute_name, attribute_value).
        """
        yield "image_id", self.image_id
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
        yield "tags", self.tags
        yield "histogram", self.histogram
        yield "num_chars", self.num_chars

    def __eq__(self, other: object) -> bool:
        """
        Compares two Word objects for equality based on their attributes.

        Args:
            other (object): The object to compare.

        Returns:
            bool: True if the objects have the same attributes, False otherwise.
        """
        if not isinstance(other, Word):
            return False
        return (
            self.image_id == other.image_id
            and self.line_id == other.line_id
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
            and self.tags == other.tags
        )


def itemToWord(item: dict) -> Word:
    """
    Converts a DynamoDB item dictionary into a Word object.

    This function expects a dictionary that contains:
    - PK: {"S": "IMAGE#<uuid>"}
    - SK: {"S": "LINE#<line_id>#WORD#<word_id>"}
    - text: {"S": <string>}
    - bounding_box, top_right, top_left, bottom_right, bottom_left:
      nested dicts with numeric values
    - angle_degrees, angle_radians, confidence: numeric values
    - Optional: tags ({"SS": [<tag1>, <tag2>, ...]})

    Args:
        item (dict): A dictionary in the DynamoDB format containing all required keys.

    Returns:
        Word: An instance of the Word class populated from the given item.

    Raises:
        ValueError: If the item is missing required keys
            or if any required field is malformed (e.g., numeric parsing fails).
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
        return Word(
            image_id=item["PK"]["S"][6:],  # strip off "IMAGE#"
            line_id=int(item["SK"]["S"].split("#")[1]),
            id=int(item["SK"]["S"].split("#")[3]),
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
            tags=item.get("tags", {}).get("SS", []),
        )
    except (KeyError, ValueError) as e:
        raise ValueError(f"Error converting item to Word: {e}")
