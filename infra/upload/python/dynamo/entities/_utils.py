def assert_valid_boundingBox(boundingBox):
    """
    Assert that the bounding box is valid.
    """
    if not isinstance(boundingBox, dict):
        raise ValueError("boundingBox must be a dictionary")
    for key in ["x", "y", "width", "height"]:
        if key not in boundingBox:
            raise ValueError(f"boundingBox must contain the key '{key}'")
        if not isinstance(boundingBox[key], (int, float)):
            raise ValueError(f"boundingBox['{key}'] must be a number")
    return boundingBox

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