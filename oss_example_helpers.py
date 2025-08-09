import os
from receipt_dynamo.entities.receipt_line import ReceiptLine
from receipt_dynamo import DynamoClient


def _format_receipt_lines(lines: list[ReceiptLine]) -> str:
    """
    Format receipt text by grouping visually contiguous lines and
    prefixing each group with its line ID or ID range.
    """
    if not lines:
        return ""

    # Helper to format ID or ID range
    def format_ids(ids: list[int]) -> str:
        if len(ids) == 1:
            return f"{ids[0]}:"
        return f"{ids[0]}-{ids[-1]}:"

    # Initialize first group
    grouped: list[tuple[list[int], str]] = []
    current_ids = [lines[0].line_id]
    current_text = lines[0].text

    for prev_line, curr_line in zip(lines, lines[1:]):
        curr_id = curr_line.line_id
        centroid = curr_line.calculate_centroid()
        # Decide if on same visual line as previous
        if prev_line.bottom_left["y"] < centroid[1] < prev_line.top_left["y"]:
            # Same group: append text
            current_ids.append(curr_id)
            current_text += " " + curr_line.text
        else:
            # Flush previous group
            grouped.append((current_ids, current_text))
            # Start new group
            current_ids = [curr_id]
            current_text = curr_line.text

    # Flush final group
    grouped.append((current_ids, current_text))

    # Build formatted lines
    formatted_lines = [f"{format_ids(ids)} {text}" for ids, text in grouped]
    return "\n".join(formatted_lines)

def main():
    """Lists all receipts and all receipt_metadatas in the database. Then finds
    all receipts that don't have a receipt_metadata.
    """
    client = DynamoClient(os.getenv("DYNAMO_TABLE_NAME"))
