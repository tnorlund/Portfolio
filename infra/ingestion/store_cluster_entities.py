import numpy as np
import logging
from math import atan2, degrees
from typing import List, Dict

logger = logging.getLogger(__name__)

class DynamoClient:
    def __init__(self, table_name: str):
        self.table_name = table_name
        # Stub: Implement your real DynamoDB client here

    def addReceiptLines(self, lines):
        pass  # Stub

    def addReceiptWords(self, words):
        pass  # Stub

    def addReceiptLetters(self, letters):
        pass  # Stub

# Stub classes to show usage:
class Image:
    def __init__(self, width: float, height: float):
        self.width = width
        self.height = height

class ReceiptLine:
    def __init__(self, **kwargs):
        pass

class ReceiptWord:
    def __init__(self, **kwargs):
        pass

class ReceiptLetter:
    def __init__(self, **kwargs):
        pass

class Line:
    # Example fields; adjust as needed
    def __init__(self, id, text, top_left, top_right, bottom_left, bottom_right, confidence):
        self.id = id
        self.text = text
        self.top_left = top_left
        self.top_right = top_right
        self.bottom_left = bottom_left
        self.bottom_right = bottom_right
        self.confidence = confidence

class Word:
    def __init__(self, id, line_id, text, top_left, top_right, bottom_left, bottom_right, confidence):
        self.id = id
        self.line_id = line_id
        self.text = text
        self.top_left = top_left
        self.top_right = top_right
        self.bottom_left = bottom_left
        self.bottom_right = bottom_right
        self.confidence = confidence

class Letter:
    def __init__(self, id, line_id, word_id, text, top_left, top_right, bottom_left, bottom_right, confidence):
        self.id = id
        self.line_id = line_id
        self.word_id = word_id
        self.text = text
        self.top_left = top_left
        self.top_right = top_right
        self.bottom_left = bottom_left
        self.bottom_right = bottom_right
        self.confidence = confidence

def store_cluster_entities(
    cluster_id: int,
    image_id: str,
    lines: List[Line],
    words: List[Word],
    letters: List[Letter],
    M: np.ndarray,
    receipt_width: int,
    receipt_height: int,
    table_name: str,
    image_obj: Image,
) -> None:
    """
    Given a single cluster's lines/words/letters from the *original* image,
    warp them into the cluster's local coordinate space (using the 3x3 perspective
    transform matrix M), then store them as ReceiptLine, ReceiptWord,
    and ReceiptLetter in DynamoDB.

    Args:
        cluster_id: The integer ID you want to use for 'receipt_id'.
        image_id: The integer ID for the original image.
        lines, words, letters: The original OCR items that belong to this cluster.
        M: A 3x3 perspective transform matrix (originally from e.g. cv2.getPerspectiveTransform).
        receipt_width, receipt_height: The final width/height of the warped cluster.
        table_name: Name of your DynamoDB table with the "ReceiptLine," etc.
        image_obj: The original image object, with width and height.
    """

    def warp_point(x_abs, y_abs, matrix: np.ndarray):
        """Apply a 3x3 perspective transform matrix manually."""
        # Convert point to homogeneous coords
        pt_hom = np.array([x_abs, y_abs, 1.0], dtype=np.float32)
        # Matrix multiplication
        transformed = matrix @ pt_hom
        # Normalize by w
        w = transformed[2]
        if abs(w) < 1e-9:
            # Avoid division by zero; handle or raise an error
            return (float(x_abs), float(y_abs))
        x_prime = transformed[0] / w
        y_prime = transformed[1] / w
        return (float(x_prime), float(y_prime))

    def compute_angle_in_warped_space(w_tl, w_tr):
        """
        Compute angle (in radians/degrees) of the top edge,
        given top-left and top-right points in the *warped* coordinate system.
        """
        dx = w_tr[0] - w_tl[0]
        dy = w_tr[1] - w_tl[1]
        angle_radians = atan2(dy, dx)
        angle_degrees = degrees(angle_radians)
        return angle_degrees, angle_radians

    dynamo_client = DynamoClient(table_name)

    receipt_lines = []
    receipt_words = []
    receipt_letters = []

    # For each original line, warp corners
    for ln in lines:
        tl_abs = (
            ln.top_left["x"] * image_obj.width,
            (1 - ln.top_left["y"]) * image_obj.height,
        )
        tr_abs = (
            ln.top_right["x"] * image_obj.width,
            (1 - ln.top_right["y"]) * image_obj.height,
        )
        bl_abs = (
            ln.bottom_left["x"] * image_obj.width,
            (1 - ln.bottom_left["y"]) * image_obj.height,
        )
        br_abs = (
            ln.bottom_right["x"] * image_obj.width,
            (1 - ln.bottom_right["y"]) * image_obj.height,
        )

        # Warp each corner
        w_tl = warp_point(*tl_abs, M)
        w_tr = warp_point(*tr_abs, M)
        w_bl = warp_point(*bl_abs, M)
        w_br = warp_point(*br_abs, M)

        angle_degrees, angle_radians = compute_angle_in_warped_space(w_tl, w_tr)

        top_left = {
            "x": w_tl[0] / receipt_width,
            "y": 1.0 - (w_tl[1] / receipt_height),
        }
        top_right = {
            "x": w_tr[0] / receipt_width,
            "y": 1.0 - (w_tr[1] / receipt_height),
        }
        bottom_left = {
            "x": w_bl[0] / receipt_width,
            "y": 1.0 - (w_bl[1] / receipt_height),
        }
        bottom_right = {
            "x": w_br[0] / receipt_width,
            "y": 1.0 - (w_br[1] / receipt_height),
        }

        min_x = min(
            top_left["x"], top_right["x"], bottom_left["x"], bottom_right["x"]
        )
        max_x = max(
            top_left["x"], top_right["x"], bottom_left["x"], bottom_right["x"]
        )
        min_y = min(
            top_left["y"], top_right["y"], bottom_left["y"], bottom_right["y"]
        )
        max_y = max(
            top_left["y"], top_right["y"], bottom_left["y"], bottom_right["y"]
        )
        bounding_box = {
            "x": min_x,
            "y": min_y,
            "width": (max_x - min_x),
            "height": (max_y - min_y),
        }

        # Create a ReceiptLine that references “cluster_id” as the receipt_id
        receipt_line = ReceiptLine(
            receipt_id=int(cluster_id),
            image_id=image_id,
            id=ln.id,  # or reassign new IDs if you prefer
            text=ln.text,
            bounding_box=bounding_box,
            top_right=top_right,
            top_left=top_left,
            bottom_right=bottom_right,
            bottom_left=bottom_left,
            angle_degrees=angle_degrees,
            angle_radians=angle_radians,
            confidence=ln.confidence,
        )
        receipt_lines.append(receipt_line)

    # Same approach for Words
    for wd in words:
        tl_abs = (
            wd.top_left["x"] * image_obj.width,
            (1 - wd.top_left["y"]) * image_obj.height,
        )
        tr_abs = (
            wd.top_right["x"] * image_obj.width,
            (1 - wd.top_right["y"]) * image_obj.height,
        )
        bl_abs = (
            wd.bottom_left["x"] * image_obj.width,
            (1 - wd.bottom_left["y"]) * image_obj.height,
        )
        br_abs = (
            wd.bottom_right["x"] * image_obj.width,
            (1 - wd.bottom_right["y"]) * image_obj.height,
        )

        w_tl = warp_point(*tl_abs, M)
        w_tr = warp_point(*tr_abs, M)
        w_bl = warp_point(*bl_abs, M)
        w_br = warp_point(*br_abs, M)

        angle_degrees, angle_radians = compute_angle_in_warped_space(w_tl, w_tr)

        top_left = {
            "x": w_tl[0] / receipt_width,
            "y": 1.0 - (w_tl[1] / receipt_height),
        }
        top_right = {
            "x": w_tr[0] / receipt_width,
            "y": 1.0 - (w_tr[1] / receipt_height),
        }
        bottom_left = {
            "x": w_bl[0] / receipt_width,
            "y": 1.0 - (w_bl[1] / receipt_height),
        }
        bottom_right = {
            "x": w_br[0] / receipt_width,
            "y": 1.0 - (w_br[1] / receipt_height),
        }

        min_x = min(
            top_left["x"], top_right["x"], bottom_left["x"], bottom_right["x"]
        )
        max_x = max(
            top_left["x"], top_right["x"], bottom_left["x"], bottom_right["x"]
        )
        min_y = min(
            top_left["y"], top_right["y"], bottom_left["y"], bottom_right["y"]
        )
        max_y = max(
            top_left["y"], top_right["y"], bottom_left["y"], bottom_right["y"]
        )
        bounding_box = {
            "x": min_x,
            "y": min_y,
            "width": (max_x - min_x),
            "height": (max_y - min_y),
        }

        receipt_word = ReceiptWord(
            receipt_id=int(cluster_id),
            image_id=image_id,
            line_id=wd.line_id,
            id=wd.id,
            text=wd.text,
            bounding_box=bounding_box,
            top_right=top_right,
            top_left=top_left,
            bottom_right=bottom_right,
            bottom_left=bottom_left,
            angle_degrees=angle_degrees,
            angle_radians=angle_radians,
            confidence=wd.confidence,
        )
        receipt_words.append(receipt_word)

    # And for Letters
    for lt in letters:
        tl_abs = (
            lt.top_left["x"] * image_obj.width,
            (1 - lt.top_left["y"]) * image_obj.height,
        )
        tr_abs = (
            lt.top_right["x"] * image_obj.width,
            (1 - lt.top_right["y"]) * image_obj.height,
        )
        bl_abs = (
            lt.bottom_left["x"] * image_obj.width,
            (1 - lt.bottom_left["y"]) * image_obj.height,
        )
        br_abs = (
            lt.bottom_right["x"] * image_obj.width,
            (1 - lt.bottom_right["y"]) * image_obj.height,
        )

        w_tl = warp_point(*tl_abs, M)
        w_tr = warp_point(*tr_abs, M)
        w_bl = warp_point(*bl_abs, M)
        w_br = warp_point(*br_abs, M)

        angle_degrees, angle_radians = compute_angle_in_warped_space(w_tl, w_tr)

        top_left = {
            "x": w_tl[0] / receipt_width,
            "y": 1.0 - (w_tl[1] / receipt_height),
        }
        top_right = {
            "x": w_tr[0] / receipt_width,
            "y": 1.0 - (w_tr[1] / receipt_height),
        }
        bottom_left = {
            "x": w_bl[0] / receipt_width,
            "y": 1.0 - (w_bl[1] / receipt_height),
        }
        bottom_right = {
            "x": w_br[0] / receipt_width,
            "y": 1.0 - (w_br[1] / receipt_height),
        }

        min_x = min(
            top_left["x"], top_right["x"], bottom_left["x"], bottom_right["x"]
        )
        max_x = max(
            top_left["x"], top_right["x"], bottom_left["x"], bottom_right["x"]
        )
        min_y = min(
            top_left["y"], top_right["y"], bottom_left["y"], bottom_right["y"]
        )
        max_y = max(
            top_left["y"], top_right["y"], bottom_left["y"], bottom_right["y"]
        )
        bounding_box = {
            "x": min_x,
            "y": min_y,
            "width": (max_x - min_x),
            "height": (max_y - min_y),
        }

        receipt_letter = ReceiptLetter(
            receipt_id=int(cluster_id),
            image_id=image_id,
            line_id=lt.line_id,
            word_id=lt.word_id,
            id=lt.id,
            text=lt.text,
            bounding_box=bounding_box,
            top_right=top_right,
            top_left=top_left,
            bottom_right=bottom_right,
            bottom_left=bottom_left,
            angle_degrees=angle_degrees,
            angle_radians=angle_radians,
            confidence=lt.confidence,
        )
        receipt_letters.append(receipt_letter)

    # Finally, store them
    dynamo_client.addReceiptLines(receipt_lines)
    dynamo_client.addReceiptWords(receipt_words)
    dynamo_client.addReceiptLetters(receipt_letters)

    logger.info(
        f"Added {len(receipt_lines)} receipt lines, "
        f"{len(receipt_words)} words, and "
        f"{len(receipt_letters)} letters for receipt {cluster_id}."
    )