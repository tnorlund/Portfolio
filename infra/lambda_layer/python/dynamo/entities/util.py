from decimal import Decimal, ROUND_HALF_UP
import re

# Regex for UUID version 4 (case-insensitive, enforcing the '4' and the [89AB] variant).
UUID_V4_REGEX = re.compile(
    r'^[0-9A-Fa-f]{8}-'
    r'[0-9A-Fa-f]{4}-'
    r'4[0-9A-Fa-f]{3}-'
    r'[89ABab][0-9A-Fa-f]{3}-'
    r'[0-9A-Fa-f]{12}$'
)

def compute_histogram(text: str) -> dict:
    known_letters = [
        " ",
        "!",
        '"',
        "#",
        "$",
        "%",
        "&",
        "'",
        "(",
        ")",
        "*",
        "+",
        ",",
        "-",
        ".",
        "/",
        "0",
        "1",
        "2",
        "3",
        "4",
        "5",
        "6",
        "7",
        "8",
        "9",
        ":",
        ";",
        "<",
        "=",
        ">",
        "?",
        "@",
        "A",
        "B",
        "C",
        "D",
        "E",
        "F",
        "G",
        "H",
        "I",
        "J",
        "K",
        "L",
        "M",
        "N",
        "O",
        "P",
        "Q",
        "R",
        "S",
        "T",
        "U",
        "V",
        "W",
        "X",
        "Y",
        "Z",
        "[",
        "\\",
        "]",
        "_",
        "a",
        "b",
        "c",
        "d",
        "e",
        "f",
        "g",
        "h",
        "i",
        "j",
        "k",
        "l",
        "m",
        "n",
        "o",
        "p",
        "q",
        "r",
        "s",
        "t",
        "u",
        "v",
        "w",
        "x",
        "y",
        "z",
        "{",
        "}",
        "|",
        "~"
    ]
    histogram = {letter: 0 for letter in known_letters}
    for letter in text:
        if letter in known_letters:
            histogram[letter] += 1
    total_letters = sum(histogram.values())
    if total_letters > 0:
        histogram = {letter: count / total_letters for letter, count in histogram.items()}
    return histogram

def assert_valid_bounding_box(bounding_box):
    """
    Assert that the bounding box is valid.
    """
    if not isinstance(bounding_box, dict):
        raise ValueError("bounding_box must be a dictionary")
    for key in ["x", "y", "width", "height"]:
        if key not in bounding_box:
            raise ValueError(f"bounding_box must contain the key '{key}'")
        if not isinstance(bounding_box[key], (int, float)):
            raise ValueError(f"bounding_box['{key}'] must be a number")
    return bounding_box


def assert_valid_point(point):
    """
    Assert that the point is valid.
    """
    if not isinstance(point, dict):
        raise ValueError("point must be a dictionary")
    for key in ["x", "y"]:
        if key not in point:
            raise ValueError(f"point must contain the key '{key}'")
        if not isinstance(point[key], (int, float)):
            raise ValueError(f"point['{key}'] must be a number")
    return point


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

    # If instead you wanted trailing zeros, you could do:
    # formatted = formatted.ljust(total_length, '0')

    return formatted


def assert_valid_uuid(uuid) -> None:
    """
    Assert that the UUID is valid.
    """
    if not isinstance(uuid, str):
        raise ValueError("uuid must be a string")
    if not UUID_V4_REGEX.match(uuid):
        raise ValueError("uuid must be a valid UUIDv4")


def shear_point(px, py, pivot_x, pivot_y, shx, shy):
    """
    Shears point (px, py) around pivot (pivot_x, pivot_y) 
    by shear factors `shx` (x-shear) and `shy` (y-shear).

    Forward transform (source -> dest):
        [ x' ] = [ 1    shx ] [ x - pivot_x ]
        [ y' ]   [ shy    1 ] [ y - pivot_y ]

    Then translate back by adding pivot_x, pivot_y.
    """
    # 1) Translate so pivot is at origin
    translated_x = px - pivot_x
    translated_y = py - pivot_y

    # 2) Apply shear
    sheared_x = translated_x + shx * translated_y
    sheared_y = shy * translated_x + translated_y

    # 3) Translate back
    final_x = sheared_x + pivot_x
    final_y = sheared_y + pivot_y
    return final_x, final_y

