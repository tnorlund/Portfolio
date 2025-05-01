import random
import json
from receipt_label.utils.clients import get_clients

dynamo_client, openai_client, pinecone_index = get_clients()

# IMage 752cf8e2-cb69-4643-8f28-0ac9e3d2df25 needs to be cleaned

receipts, lek = dynamo_client.listReceipts()
while lek:
    next_receipts, lek = dynamo_client.listReceipts(LastEvaluatedKey=lek)
    receipts.extend(next_receipts)

receipt_areas = {}
for receipt in receipts:
    receipt, lines, words, letters, tags, labels = dynamo_client.getReceiptDetails(
        receipt.image_id, receipt.receipt_id
    )

    # Assuming line has .tl, .tr, .bl, .br attributes each with (x, y)
    def polygon_area(coords):
        # Shoelace formula for quadrilateral area
        x = [p["x"] for p in coords]
        y = [p["y"] for p in coords]
        return 0.5 * abs(
            x[0] * y[1]
            + x[1] * y[2]
            + x[2] * y[3]
            + x[3] * y[0]
            - y[0] * x[1]
            - y[1] * x[2]
            - y[2] * x[3]
            - y[3] * x[0]
        )

    receipt_area_total = 0.0
    for line in lines:
        coords = [
            line.top_left,
            line.top_right,
            line.bottom_right,
            line.bottom_left,
        ]  # Ensure clockwise order
        area = polygon_area(coords)
        receipt_area_total += area

    normalized_receipt_area = 1.0  # Assuming 1x1 normalized
    unused_area = normalized_receipt_area - receipt_area_total
    key = f"{receipt.image_id}#{receipt.receipt_id}"
    receipt_areas[key] = {
        "used_area": receipt_area_total,
        "unused_area": unused_area,
    }
    print(
        f"Receipt {receipt.receipt_id} used area: {receipt_area_total:.4f}, unused area: {unused_area:.4f}"
    )
# save to file
with open("receipt_areas.json", "w") as f:
    json.dump(receipt_areas, f)
