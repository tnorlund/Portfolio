from pathlib import Path
import json

from receipt_dynamo.entities import (
    ReceiptWordLabel,
)
from receipt_label.utils import get_clients

dynamo_client, openai_client, _ = get_clients()

file_path = Path("/Users/tnorlund/Downloads/merged_8079x5kf.ndjson")

label_indices = []
with file_path.open("r") as f:
    for line in f:
        data = json.loads(line)
        custom_id = data["custom_id"]
        split = custom_id.split("#")
        image_id = split[1]
        receipt_id = int(split[3])
        line_id = int(split[5])
        word_id = int(split[7])
        label = split[9]
        label_indices.append((image_id, receipt_id, line_id, word_id, label))

labels = dynamo_client.getReceiptWordLabelsByIndices(label_indices)
print(f"Found {len(labels)} labels")
# for label in labels:
#     print(label)

# # for label_index in label_indices:
# #     print(label_index)
# #     labels = dynamo_client.getReceiptWordLabelsByIndices([label_index])
# #     print(labels)
