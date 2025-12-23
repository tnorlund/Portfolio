"""
Receipt grouping agent tools.

This agent helps identify the correct grouping of OCR lines/words into receipts
by trying different combinations and evaluating which makes the most sense.
"""

import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ==============================================================================
# Image Context - Injected at runtime
# ==============================================================================


@dataclass
class ImageContext:
    """Context for the image being analyzed. Injected into tools at runtime."""

    image_id: str

    # Cached data (loaded once)
    lines: Optional[list[dict]] = None
    words: Optional[list[dict]] = None
    receipts: Optional[list[dict]] = None


# ==============================================================================
# Tool Input Schemas
# ==============================================================================


class GetImageLinesInput(BaseModel):
    """Input for get_image_lines tool."""

    pass


class GetImageWordsInput(BaseModel):
    """Input for get_image_words tool."""

    pass


class GetCurrentReceiptsInput(BaseModel):
    """Input for get_current_receipts tool."""

    pass


class TryGroupingInput(BaseModel):
    """Input for try_grouping tool."""

    grouping: dict = Field(
        description="Dictionary mapping receipt_id (int) to list of line_ids (list[int])"
    )


class EvaluateGroupingInput(BaseModel):
    """Input for evaluate_grouping tool."""

    grouping: dict = Field(
        description="Dictionary mapping receipt_id (int) to list of line_ids (list[int])"
    )


class CompareCoordinatesInput(BaseModel):
    """Input for compare_coordinates tool."""

    receipt_id: int = Field(
        description="Receipt ID to compare coordinates for"
    )


class TryMergeInput(BaseModel):
    """Input for try_merge tool."""

    receipt_id_1: int = Field(description="First receipt ID to merge")
    receipt_id_2: int = Field(description="Second receipt ID to merge")


class SubmitGroupingInput(BaseModel):
    """Input for submit_grouping tool."""

    grouping: dict = Field(
        description="Final recommended grouping: receipt_id -> list of line_ids"
    )
    reasoning: str = Field(
        description="Explanation of why this grouping is correct"
    )
    confidence: float = Field(
        ge=0.0, le=1.0, description="Confidence score 0.0 to 1.0"
    )


# ==============================================================================
# Tool Factory - Creates tools with injected dependencies
# ==============================================================================


def create_receipt_grouper_tools(
    dynamo_client: Any,
) -> tuple[list[Any], dict]:
    """
    Create tools for the receipt grouping agent.

    Returns:
        (tools, state_holder) - tools list and a dict to hold runtime state
    """
    # State holder - will be populated before each run
    state = {"context": None, "grouping": None}

    # ========== CONTEXT TOOLS ==========

    @tool
    def get_image_lines() -> list[dict]:
        """
        Get all OCR lines from the image (not grouped by receipt).

        Returns a list of lines with:
        - line_id: Unique ID for this line
        - text: The text content of the line
        - centroid_x, centroid_y: Approximate center position
        - bounding_box: Bounding box coordinates
        - top_left, top_right, bottom_left, bottom_right: Corner coordinates

        Use this to see all available text on the image with full coordinate information.
        """
        ctx: ImageContext = state["context"]
        if ctx is None:
            return [{"error": "No image context set"}]

        if ctx.lines is None:
            # Load all lines from the image
            try:
                image_lines = dynamo_client.list_lines_from_image(ctx.image_id)
                ctx.lines = []
                for line in image_lines:
                    centroid = line.calculate_centroid()
                    ctx.lines.append(
                        {
                            "line_id": line.line_id,
                            "text": line.text,
                            "centroid_x": centroid[0],
                            "centroid_y": centroid[1],
                            "bounding_box": {
                                "x": line.bounding_box.get("x", 0),
                                "y": line.bounding_box.get("y", 0),
                                "width": line.bounding_box.get("width", 0),
                                "height": line.bounding_box.get("height", 0),
                            },
                            "top_left": {
                                "x": line.top_left.get("x", 0),
                                "y": line.top_left.get("y", 0),
                            },
                            "top_right": {
                                "x": line.top_right.get("x", 0),
                                "y": line.top_right.get("y", 0),
                            },
                            "bottom_left": {
                                "x": line.bottom_left.get("x", 0),
                                "y": line.bottom_left.get("y", 0),
                            },
                            "bottom_right": {
                                "x": line.bottom_right.get("x", 0),
                                "y": line.bottom_right.get("y", 0),
                            },
                        }
                    )
                # Sort by Y position (top to bottom), then X (left to right)
                ctx.lines.sort(
                    key=lambda l: (l["centroid_y"], l["centroid_x"])
                )
            except Exception as e:
                logger.error("Error loading lines: %s", e)
                return [{"error": str(e)}]

        return ctx.lines

    @tool
    def get_image_words() -> list[dict]:
        """
        Get all OCR words from the image (not grouped by receipt).

        Returns a list of words with:
        - line_id: Line containing this word
        - word_id: Unique ID for this word within the line
        - text: The word text
        - centroid_x, centroid_y: Approximate center position

        Use this to see all available words on the image.
        """
        ctx: ImageContext = state["context"]
        if ctx is None:
            return [{"error": "No image context set"}]

        if ctx.words is None:
            # Load all words from the image
            try:
                image_lines = dynamo_client.list_lines_from_image(ctx.image_id)
                ctx.words = []
                for line in image_lines:
                    line_words = dynamo_client.list_words_from_line(
                        ctx.image_id, line.line_id
                    )
                    for word in line_words:
                        centroid = word.calculate_centroid()
                        ctx.words.append(
                            {
                                "line_id": word.line_id,
                                "word_id": word.word_id,
                                "text": word.text,
                                "centroid_x": centroid[0],
                                "centroid_y": centroid[1],
                            }
                        )
                # Sort by Y position (top to bottom), then X (left to right)
                ctx.words.sort(
                    key=lambda w: (w["centroid_y"], w["centroid_x"])
                )
            except Exception as e:
                logger.error("Error loading words: %s", e)
                return [{"error": str(e)}]

        return ctx.words

    @tool
    def get_current_receipts() -> list[dict]:
        """
        Get the current receipt groupings from the database.

        Returns a list of receipts with:
        - receipt_id: Receipt identifier
        - line_ids: List of line IDs currently assigned to this receipt
        - metadata: Current metadata (merchant_name, address, phone, etc.) if available

        Use this to see how receipts are currently grouped.
        """
        ctx: ImageContext = state["context"]
        if ctx is None:
            return [{"error": "No image context set"}]

        if ctx.receipts is None:
            try:
                # Get image details which includes receipts
                image_details = dynamo_client.get_image_details(ctx.image_id)
                ctx.receipts = []

                for receipt in image_details.receipts:
                    # Get lines for this receipt
                    receipt_lines = [
                        rl
                        for rl in image_details.receipt_lines
                        if rl.receipt_id == receipt.receipt_id
                    ]
                    line_ids = sorted([rl.line_id for rl in receipt_lines])

                    # Try to get place data
                    metadata = None
                    try:
                        receipt_place = dynamo_client.get_receipt_place(
                            ctx.image_id, receipt.receipt_id
                        )
                        if receipt_place:
                            metadata = {
                                "merchant_name": receipt_place.merchant_name,
                                "address": receipt_place.formatted_address,
                                "phone": receipt_place.phone_number,
                                "place_id": receipt_place.place_id,
                            }
                    except Exception:
                        pass  # Place data might not exist

                    ctx.receipts.append(
                        {
                            "receipt_id": receipt.receipt_id,
                            "line_ids": line_ids,
                            "line_count": len(line_ids),
                            "metadata": metadata,
                        }
                    )

                # Sort by receipt_id
                ctx.receipts.sort(key=lambda r: r["receipt_id"])
            except Exception as e:
                logger.error("Error loading receipts: %s", e)
                return [{"error": str(e)}]

        return ctx.receipts

    # ========== COORDINATE COMPARISON TOOLS ==========

    @tool(args_schema=CompareCoordinatesInput)
    def compare_coordinates(receipt_id: int) -> dict:
        """
        Compare receipt-level line coordinates with image-level line coordinates.

        This helps verify that receipt lines correctly correspond to image lines
        and can reveal if lines are incorrectly assigned to receipts.

        Args:
            receipt_id: Receipt ID to compare coordinates for

        Returns:
            Comparison showing:
            - receipt_lines: Lines assigned to this receipt with their coordinates
            - image_lines: Corresponding image-level lines with their coordinates
            - matches: Which receipt lines match which image lines
            - mismatches: Any discrepancies in coordinate assignments
            - spatial_gaps: Gaps or overlaps in the spatial layout
        """
        ctx: ImageContext = state["context"]
        if ctx is None:
            return {"error": "No image context set"}

        if ctx.receipts is None:
            get_current_receipts()

        if ctx.lines is None:
            get_image_lines()

        try:
            # Find the receipt
            receipt = None
            for r in ctx.receipts:
                if r["receipt_id"] == receipt_id:
                    receipt = r
                    break

            if receipt is None:
                return {"error": f"Receipt {receipt_id} not found"}

            # Get receipt lines from DynamoDB
            receipt_details = dynamo_client.get_receipt_details(
                ctx.image_id, receipt_id
            )
            receipt_lines = receipt_details.lines if receipt_details else []

            # Build image line map
            image_line_map = {line["line_id"]: line for line in ctx.lines}

            # Compare coordinates
            matches = []
            mismatches = []
            receipt_line_data = []

            for receipt_line in receipt_lines:
                image_line = image_line_map.get(receipt_line.line_id)

                if image_line:
                    # Calculate centroids
                    receipt_centroid = receipt_line.calculate_centroid()
                    image_centroid = (
                        image_line["centroid_x"],
                        image_line["centroid_y"],
                    )

                    # Calculate distance between centroids
                    distance = (
                        (receipt_centroid[0] - image_centroid[0]) ** 2
                        + (receipt_centroid[1] - image_centroid[1]) ** 2
                    ) ** 0.5

                    receipt_line_data.append(
                        {
                            "line_id": receipt_line.line_id,
                            "text": receipt_line.text,
                            "receipt_centroid": {
                                "x": receipt_centroid[0],
                                "y": receipt_centroid[1],
                            },
                            "image_centroid": {
                                "x": image_centroid[0],
                                "y": image_centroid[1],
                            },
                            "distance": round(distance, 4),
                            "matches": distance < 0.01,  # Very close = matches
                        }
                    )

                    if distance < 0.01:
                        matches.append(
                            {
                                "line_id": receipt_line.line_id,
                                "text": receipt_line.text[:50],
                                "distance": round(distance, 4),
                            }
                        )
                    else:
                        mismatches.append(
                            {
                                "line_id": receipt_line.line_id,
                                "text": receipt_line.text[:50],
                                "distance": round(distance, 4),
                                "receipt_centroid": {
                                    "x": receipt_centroid[0],
                                    "y": receipt_centroid[1],
                                },
                                "image_centroid": {
                                    "x": image_centroid[0],
                                    "y": image_centroid[1],
                                },
                            }
                        )
                else:
                    mismatches.append(
                        {
                            "line_id": receipt_line.line_id,
                            "text": receipt_line.text[:50],
                            "error": "Line not found in image lines",
                        }
                    )

            # Analyze spatial layout
            if receipt_line_data:
                y_positions = [
                    line["receipt_centroid"]["y"] for line in receipt_line_data
                ]
                y_positions.sort()

                gaps = []
                for i in range(len(y_positions) - 1):
                    gap = y_positions[i + 1] - y_positions[i]
                    if gap > 0.05:  # Significant gap
                        gaps.append(
                            {
                                "between_lines": f"{y_positions[i]:.3f} and {y_positions[i+1]:.3f}",
                                "gap_size": round(gap, 3),
                            }
                        )
            else:
                gaps = []

            return {
                "receipt_id": receipt_id,
                "total_receipt_lines": len(receipt_lines),
                "matched_lines": len(matches),
                "mismatched_lines": len(mismatches),
                "receipt_line_data": receipt_line_data,
                "matches": matches,
                "mismatches": mismatches,
                "spatial_gaps": gaps,
                "all_lines_match": len(mismatches) == 0,
            }

        except Exception as e:
            logger.error("Error comparing coordinates: %s", e)
            return {"error": str(e)}

    # ========== GROUPING TOOLS ==========

    @tool(args_schema=TryGroupingInput)
    def try_grouping(grouping: dict) -> dict:
        """
        Try a specific grouping of lines into receipts.

        Args:
            grouping: Dictionary mapping receipt_id (int) to list of line_ids (list[int])
                     Example: {1: [1, 2, 3, 4], 2: [5, 6, 7, 8]}

        Returns:
            Analysis of this grouping including:
            - text_per_receipt: Full text for each receipt
            - has_merchant_name: Whether each receipt has a merchant name
            - has_address: Whether each receipt has an address
            - has_phone: Whether each receipt has a phone number
            - has_total: Whether each receipt has a total
            - coherence_score: How coherent each receipt grouping looks (0-1)
            - issues: List of potential issues with this grouping

        Use this to test different combinations of line groupings.
        """
        ctx: ImageContext = state["context"]
        if ctx is None:
            return {"error": "No image context set"}

        if ctx.lines is None:
            # Load lines if not already loaded
            get_image_lines()

        try:
            # Build line_id -> line mapping
            line_map = {line["line_id"]: line for line in ctx.lines}

            result = {
                "grouping": grouping,
                "receipts": {},
                "overall_coherence": 0.0,
                "issues": [],
            }

            total_coherence = 0.0
            receipt_count = len(grouping)

            for receipt_id, line_ids in grouping.items():
                # Get lines for this receipt
                receipt_lines = [
                    line_map[line_id]
                    for line_id in line_ids
                    if line_id in line_map
                ]

                if not receipt_lines:
                    result["issues"].append(
                        f"Receipt {receipt_id}: No valid lines found"
                    )
                    continue

                # Build full text
                receipt_text = " ".join(line["text"] for line in receipt_lines)

                # Check for common receipt elements
                text_lower = receipt_text.lower()
                has_merchant = (
                    any(
                        word in text_lower
                        for word in [
                            "market",
                            "store",
                            "restaurant",
                            "cafe",
                            "shop",
                            "inc",
                            "llc",
                            "corp",
                        ]
                    )
                    or len(
                        [
                            w
                            for w in receipt_lines[0]["text"].split()
                            if len(w) > 3
                        ]
                    )
                    > 0
                )

                has_address = any(
                    word in text_lower
                    for word in [
                        "street",
                        "st",
                        "avenue",
                        "ave",
                        "road",
                        "rd",
                        "blvd",
                        "boulevard",
                        "drive",
                        "dr",
                        "way",
                        "lane",
                        "ln",
                    ]
                ) or any(
                    char.isdigit() for char in receipt_text
                )  # Addresses usually have numbers

                has_phone = any(
                    char in receipt_text for char in ["(", ")", "-"]
                ) or any(
                    len(part) == 10 and part.isdigit()
                    for part in receipt_text.replace("(", "")
                    .replace(")", "")
                    .replace("-", "")
                    .split()
                )

                has_total = any(
                    word in text_lower
                    for word in ["total", "amount", "sum", "$"]
                ) or any(char == "$" for char in receipt_text)

                # Calculate coherence score
                coherence = 0.0
                if has_merchant:
                    coherence += 0.3
                if has_address:
                    coherence += 0.3
                if has_phone:
                    coherence += 0.2
                if has_total:
                    coherence += 0.2

                # Check for issues
                receipt_issues = []
                if len(receipt_lines) < 3:
                    receipt_issues.append(
                        f"Very few lines ({len(receipt_lines)})"
                    )
                if not has_merchant:
                    receipt_issues.append("No clear merchant name")
                if not has_address:
                    receipt_issues.append("No clear address")
                if not has_phone:
                    receipt_issues.append("No clear phone number")
                if not has_total:
                    receipt_issues.append("No clear total")

                result["receipts"][receipt_id] = {
                    "line_ids": line_ids,
                    "line_count": len(receipt_lines),
                    "text": (
                        receipt_text[:200] + "..."
                        if len(receipt_text) > 200
                        else receipt_text
                    ),
                    "has_merchant_name": has_merchant,
                    "has_address": has_address,
                    "has_phone": has_phone,
                    "has_total": has_total,
                    "coherence_score": coherence,
                    "issues": receipt_issues,
                }

                total_coherence += coherence
                if receipt_issues:
                    result["issues"].extend(
                        [
                            f"Receipt {receipt_id}: {issue}"
                            for issue in receipt_issues
                        ]
                    )

            if receipt_count > 0:
                result["overall_coherence"] = total_coherence / receipt_count

            return result

        except Exception as e:
            logger.error("Error trying grouping: %s", e)
            return {"error": str(e)}

    @tool(args_schema=EvaluateGroupingInput)
    def evaluate_grouping(grouping: dict) -> dict:
        """
        Evaluate a grouping more thoroughly by checking if it forms coherent receipts.

        This is similar to try_grouping but provides more detailed analysis including:
        - Spatial coherence (are lines close together?)
        - Text coherence (does the text make sense as a receipt?)
        - Completeness (does it have all expected receipt fields?)

        Args:
            grouping: Dictionary mapping receipt_id (int) to list of line_ids (list[int])

        Returns:
            Detailed evaluation with scores and recommendations.
        """
        ctx: ImageContext = state["context"]
        if ctx is None:
            return {"error": "No image context set"}

        if ctx.lines is None:
            get_image_lines()

        try:
            line_map = {line["line_id"]: line for line in ctx.lines}

            result = {
                "grouping": grouping,
                "receipts": {},
                "overall_score": 0.0,
                "recommendation": "",
            }

            total_score = 0.0
            receipt_count = len(grouping)

            for receipt_id, line_ids in grouping.items():
                receipt_lines = [
                    line_map[line_id]
                    for line_id in line_ids
                    if line_id in line_map
                ]

                if not receipt_lines:
                    continue

                # Spatial analysis
                y_positions = [line["centroid_y"] for line in receipt_lines]
                y_range = (
                    max(y_positions) - min(y_positions) if y_positions else 0
                )
                spatial_score = 1.0 / (
                    1.0 + y_range * 0.1
                )  # Closer lines = higher score

                # Text analysis (from try_grouping)
                receipt_text = " ".join(line["text"] for line in receipt_lines)
                text_lower = receipt_text.lower()

                has_merchant = (
                    any(
                        word in text_lower
                        for word in [
                            "market",
                            "store",
                            "restaurant",
                            "cafe",
                            "shop",
                            "inc",
                            "llc",
                            "corp",
                        ]
                    )
                    or len(
                        [
                            w
                            for w in receipt_lines[0]["text"].split()
                            if len(w) > 3
                        ]
                    )
                    > 0
                )
                has_address = any(
                    word in text_lower
                    for word in [
                        "street",
                        "st",
                        "avenue",
                        "ave",
                        "road",
                        "rd",
                        "blvd",
                        "boulevard",
                        "drive",
                        "dr",
                        "way",
                        "lane",
                        "ln",
                    ]
                ) or any(char.isdigit() for char in receipt_text)
                has_phone = any(
                    char in receipt_text for char in ["(", ")", "-"]
                ) or any(
                    len(part) == 10 and part.isdigit()
                    for part in receipt_text.replace("(", "")
                    .replace(")", "")
                    .replace("-", "")
                    .split()
                )
                has_total = any(
                    word in text_lower
                    for word in ["total", "amount", "sum", "$"]
                ) or any(char == "$" for char in receipt_text)

                completeness_score = (
                    sum([has_merchant, has_address, has_phone, has_total])
                    / 4.0
                )

                # Line count score (more lines = better, but not too many)
                line_count_score = (
                    min(len(receipt_lines) / 10.0, 1.0)
                    if len(receipt_lines) >= 3
                    else len(receipt_lines) / 3.0
                )

                # Overall score for this receipt
                receipt_score = (
                    spatial_score * 0.3
                    + completeness_score * 0.5
                    + line_count_score * 0.2
                )

                result["receipts"][receipt_id] = {
                    "line_ids": line_ids,
                    "line_count": len(receipt_lines),
                    "spatial_score": round(spatial_score, 3),
                    "completeness_score": round(completeness_score, 3),
                    "line_count_score": round(line_count_score, 3),
                    "overall_score": round(receipt_score, 3),
                    "has_merchant": has_merchant,
                    "has_address": has_address,
                    "has_phone": has_phone,
                    "has_total": has_total,
                    "text_preview": (
                        receipt_text[:150] + "..."
                        if len(receipt_text) > 150
                        else receipt_text
                    ),
                }

                total_score += receipt_score

            if receipt_count > 0:
                result["overall_score"] = round(total_score / receipt_count, 3)

            # Generate recommendation
            if result["overall_score"] > 0.7:
                result["recommendation"] = (
                    "This grouping looks good - receipts appear complete and coherent"
                )
            elif result["overall_score"] > 0.5:
                result["recommendation"] = (
                    "This grouping is reasonable but could be improved"
                )
            else:
                result["recommendation"] = (
                    "This grouping has issues - receipts may be incomplete or incorrectly split"
                )

            return result

        except Exception as e:
            logger.error("Error evaluating grouping: %s", e)
            return {"error": str(e)}

    @tool(args_schema=TryMergeInput)
    def try_merge(receipt_id_1: int, receipt_id_2: int) -> dict:
        """
        Try merging two receipts together and evaluate if it makes sense.

        This tool combines the lines from two receipts and evaluates whether
        the merged result forms a coherent, complete receipt.

        Args:
            receipt_id_1: First receipt ID to merge
            receipt_id_2: Second receipt ID to merge

        Returns:
            Analysis of the merged receipt including:
            - merged_line_ids: Combined list of line IDs
            - merged_text: Full text of merged receipt
            - coherence_score: How coherent the merged receipt looks (0-1)
            - has_merchant_name: Whether merged receipt has merchant name
            - has_address: Whether merged receipt has address
            - has_phone: Whether merged receipt has phone
            - has_total: Whether merged receipt has total
            - makes_sense: Boolean indicating if merge makes sense
            - issues: List of potential issues with the merge
        """
        ctx: ImageContext = state["context"]
        if ctx is None:
            return {"error": "No image context set"}

        if ctx.receipts is None:
            get_current_receipts()

        try:
            # Find the two receipts
            receipt_1 = None
            receipt_2 = None
            for receipt in ctx.receipts:
                if receipt["receipt_id"] == receipt_id_1:
                    receipt_1 = receipt
                if receipt["receipt_id"] == receipt_id_2:
                    receipt_2 = receipt

            if receipt_1 is None:
                return {"error": f"Receipt {receipt_id_1} not found"}
            if receipt_2 is None:
                return {"error": f"Receipt {receipt_id_2} not found"}

            # Merge line IDs
            merged_line_ids = sorted(
                list(set(receipt_1["line_ids"] + receipt_2["line_ids"]))
            )

            # Get lines for merged receipt
            if ctx.lines is None:
                get_image_lines()
            line_map = {line["line_id"]: line for line in ctx.lines}
            merged_lines = [
                line_map[line_id]
                for line_id in merged_line_ids
                if line_id in line_map
            ]

            # Analyze spatial layout of merged lines
            if merged_lines:
                y_positions = [line["centroid_y"] for line in merged_lines]
                y_range = (
                    max(y_positions) - min(y_positions) if y_positions else 0
                )
                y_positions.sort()

                # Check for large gaps that might indicate separate receipts
                large_gaps = []
                for i in range(len(y_positions) - 1):
                    gap = y_positions[i + 1] - y_positions[i]
                    if gap > 0.1:  # Large gap might indicate separate receipts
                        large_gaps.append(
                            {
                                "gap_size": round(gap, 3),
                                "between_y": f"{y_positions[i]:.3f} and {y_positions[i+1]:.3f}",
                            }
                        )
            else:
                y_range = 0
                large_gaps = []

            if not merged_lines:
                return {"error": "No valid lines found in merged receipt"}

            # Build merged text
            merged_text = " ".join(line["text"] for line in merged_lines)
            text_lower = merged_text.lower()

            # Check for receipt elements
            has_merchant = (
                any(
                    word in text_lower
                    for word in [
                        "market",
                        "store",
                        "restaurant",
                        "cafe",
                        "shop",
                        "inc",
                        "llc",
                        "corp",
                        "farmers",
                        "provisions",
                    ]
                )
                or len(
                    [w for w in merged_lines[0]["text"].split() if len(w) > 3]
                )
                > 0
            )

            has_address = any(
                word in text_lower
                for word in [
                    "street",
                    "st",
                    "avenue",
                    "ave",
                    "road",
                    "rd",
                    "blvd",
                    "boulevard",
                    "drive",
                    "dr",
                    "way",
                    "lane",
                    "ln",
                    "westlake",
                    "thousand",
                    "oaks",
                ]
            ) or any(char.isdigit() for char in merged_text)

            has_phone = any(
                char in merged_text for char in ["(", ")", "-"]
            ) or any(
                len(part) == 10 and part.isdigit()
                for part in merged_text.replace("(", "")
                .replace(")", "")
                .replace("-", "")
                .split()
            )

            has_total = any(
                word in text_lower
                for word in ["total", "amount", "sum", "$", "subtotal"]
            ) or any(char == "$" for char in merged_text)

            # Calculate coherence score
            coherence = 0.0
            if has_merchant:
                coherence += 0.3
            if has_address:
                coherence += 0.3
            if has_phone:
                coherence += 0.2
            if has_total:
                coherence += 0.2

            # Check for duplicate merchant names (bad sign)
            merchant_names_1 = receipt_1.get("metadata", {}).get(
                "merchant_name", ""
            )
            merchant_names_2 = receipt_2.get("metadata", {}).get(
                "merchant_name", ""
            )
            has_duplicate_merchants = (
                merchant_names_1
                and merchant_names_2
                and merchant_names_1.lower() != merchant_names_2.lower()
            )

            # Check for duplicate addresses (bad sign)
            address_1 = receipt_1.get("metadata", {}).get("address", "")
            address_2 = receipt_2.get("metadata", {}).get("address", "")
            has_duplicate_addresses = (
                address_1
                and address_2
                and address_1.lower() != address_2.lower()
            )

            # Check for duplicate phones (bad sign)
            phone_1 = receipt_1.get("metadata", {}).get("phone", "")
            phone_2 = receipt_2.get("metadata", {}).get("phone", "")
            has_duplicate_phones = (
                phone_1
                and phone_2
                and phone_1.replace("-", "")
                .replace("(", "")
                .replace(")", "")
                .replace(" ", "")
                != phone_2.replace("-", "")
                .replace("(", "")
                .replace(")", "")
                .replace(" ", "")
            )

            # Determine if merge makes sense
            issues = []
            if has_duplicate_merchants:
                issues.append(
                    f"Different merchants: '{merchant_names_1}' vs '{merchant_names_2}'"
                )
                coherence *= 0.5  # Penalize heavily
            if has_duplicate_addresses:
                issues.append(
                    f"Different addresses: '{address_1}' vs '{address_2}'"
                )
                coherence *= 0.7
            if has_duplicate_phones:
                issues.append(
                    f"Different phone numbers: '{phone_1}' vs '{phone_2}'"
                )
                coherence *= 0.7

            if len(merged_lines) < 3:
                issues.append(f"Very few lines ({len(merged_lines)})")
            if not has_merchant:
                issues.append("No clear merchant name")
            if not has_address:
                issues.append("No clear address")
            if not has_phone:
                issues.append("No clear phone number")
            if not has_total:
                issues.append("No clear total")

            # Check spatial layout
            if large_gaps:
                issues.append(
                    f"Large spatial gaps detected ({len(large_gaps)} gaps) - might be separate receipts"
                )
                coherence *= 0.8  # Penalize for spatial gaps
            if y_range > 0.5:
                issues.append(
                    f"Very large vertical spread ({y_range:.3f}) - lines might not belong together"
                )
                coherence *= 0.9

            makes_sense = (
                coherence > 0.6
                and not has_duplicate_merchants
                and not has_duplicate_addresses
                and not has_duplicate_phones
                and len(merged_lines) >= 3
            )

            return {
                "receipt_id_1": receipt_id_1,
                "receipt_id_2": receipt_id_2,
                "merged_line_ids": merged_line_ids,
                "merged_line_count": len(merged_lines),
                "merged_text": (
                    merged_text[:300] + "..."
                    if len(merged_text) > 300
                    else merged_text
                ),
                "coherence_score": round(coherence, 3),
                "has_merchant_name": has_merchant,
                "has_address": has_address,
                "has_phone": has_phone,
                "has_total": has_total,
                "has_duplicate_merchants": has_duplicate_merchants,
                "has_duplicate_addresses": has_duplicate_addresses,
                "has_duplicate_phones": has_duplicate_phones,
                "spatial_analysis": {
                    "y_range": round(y_range, 3),
                    "large_gaps": large_gaps,
                    "gap_count": len(large_gaps),
                },
                "makes_sense": makes_sense,
                "issues": issues,
                "recommendation": (
                    "Merge makes sense"
                    if makes_sense
                    else "Merge does NOT make sense - these appear to be different receipts"
                ),
            }

        except Exception as e:
            logger.error("Error trying merge: %s", e)
            return {"error": str(e)}

    @tool(args_schema=SubmitGroupingInput)
    def submit_grouping(
        grouping: dict, reasoning: str, confidence: float
    ) -> dict:
        """
        Submit the final recommended grouping.

        This stores the grouping in the state so it can be retrieved after the agent completes.

        Args:
            grouping: Final recommended grouping (receipt_id -> list of line_ids)
            reasoning: Explanation of why this grouping is correct
            confidence: Confidence score (0.0 to 1.0)

        Returns:
            Confirmation that the grouping was submitted.
        """
        state["grouping"] = {
            "grouping": grouping,
            "reasoning": reasoning,
            "confidence": confidence,
        }
        return {
            "status": "submitted",
            "message": "Grouping submitted successfully",
            "grouping": grouping,
            "reasoning": reasoning,
            "confidence": confidence,
        }

    # Return all tools
    tools = [
        get_image_lines,
        get_image_words,
        get_current_receipts,
        compare_coordinates,
        try_grouping,
        evaluate_grouping,
        try_merge,
        submit_grouping,
    ]

    return tools, state
