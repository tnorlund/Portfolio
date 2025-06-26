from typing import Any, Dict, Optional, Union

from ..models.position import BoundingBox, Point


def dict_to_bounding_box(bbox_dict: Dict[str, Any]) -> Optional[BoundingBox]:
    """
    Convert a dictionary representation of a bounding box to a BoundingBox object.

    This function handles both the receipt_label and receipt_dynamo formats.

    Args:
        bbox_dict: Dictionary containing bounding box data

    Returns:
        BoundingBox object or None if input is None or empty
    """
    if not bbox_dict:
        return None

    # Handle DynamoDB format if present
    if "M" in bbox_dict:
        dynamo_dict = bbox_dict["M"]
        return BoundingBox(
            x=float(dynamo_dict["x"]["N"]),
            y=float(dynamo_dict["y"]["N"]),
            width=float(dynamo_dict["width"]["N"]),
            height=float(dynamo_dict["height"]["N"]),
        )

    # Regular dictionary format
    return BoundingBox(
        x=float(bbox_dict.get("x", 0)),
        y=float(bbox_dict.get("y", 0)),
        width=float(bbox_dict.get("width", 0)),
        height=float(bbox_dict.get("height", 0)),
    )


def dict_to_point(point_dict: Dict[str, Any]) -> Optional[Point]:
    """
    Convert a dictionary representation of a point to a Point object.

    This function handles both the receipt_label and receipt_dynamo formats.

    Args:
        point_dict: Dictionary containing point data

    Returns:
        Point object or None if input is None or empty
    """
    if not point_dict:
        return None

    # Handle DynamoDB format if present
    if "M" in point_dict:
        dynamo_dict = point_dict["M"]
        return Point(x=float(dynamo_dict["x"]["N"]), y=float(dynamo_dict["y"]["N"]))

    # Regular dictionary format
    return Point(x=float(point_dict.get("x", 0)), y=float(point_dict.get("y", 0)))


def bounding_box_to_dynamo_dict(bbox: Optional[BoundingBox]) -> Dict:
    """
    Convert a BoundingBox object to a DynamoDB formatted dictionary.

    Args:
        bbox: BoundingBox object to convert

    Returns:
        Dictionary in DynamoDB format
    """
    if bbox is None:
        return {
            "M": {
                "x": {"N": "0"},
                "y": {"N": "0"},
                "width": {"N": "0"},
                "height": {"N": "0"},
            }
        }

    return {
        "M": {
            "x": {"N": str(bbox.x)},
            "y": {"N": str(bbox.y)},
            "width": {"N": str(bbox.width)},
            "height": {"N": str(bbox.height)},
        }
    }


def point_to_dynamo_dict(point: Optional[Point]) -> Dict:
    """
    Convert a Point object to a DynamoDB formatted dictionary.

    Args:
        point: Point object to convert

    Returns:
        Dictionary in DynamoDB format
    """
    if point is None:
        return {"M": {"x": {"N": "0"}, "y": {"N": "0"}}}

    return {"M": {"x": {"N": str(point.x)}, "y": {"N": str(point.y)}}}


def dynamo_word_to_bounding_box_data(item: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract bounding box and point data from a DynamoDB ReceiptWord item.

    Args:
        item: DynamoDB item representing a ReceiptWord

    Returns:
        Dictionary with bounding box and corner point data as BoundingBox and Point objects
    """
    result = {}

    if "bounding_box" in item:
        result["bounding_box"] = dict_to_bounding_box(item["bounding_box"])

    for point_key in ["top_left", "top_right", "bottom_left", "bottom_right"]:
        if point_key in item:
            result[point_key] = dict_to_point(item[point_key])

    return result
