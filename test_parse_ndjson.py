import re
import json
from pathlib import Path
from receipt_label.submit_completion_batch.submit_completion import (
    get_labels_from_ndjson,
)

file_path = Path("/Users/tnorlund/Downloads/merged_6k6c11k0.ndjson")

labels, receipt_refs = get_labels_from_ndjson(file_path)
print(len(labels))
print(len(receipt_refs))

# with file_path.open("r") as f:
#     ids = []
#     for line in f:
#         data = json.loads(line)
#         messages = data["body"]["messages"]
#         if len(messages) != 1:
#             raise ValueError(f"Expected 1 message, got {len(messages)}")
#         message = messages[0]
#         match = re.search(
#             r"### Targets\s*\n(\[.*?\])\s*(?:\n###|\Z)",
#             message["content"],
#             flags=re.DOTALL,
#         )
#         if not match:
#             raise ValueError("Could not find ### Targets array in prompt")
#         array_text = match.group(1)
#         try:
#             targets = json.loads(array_text)
#             for target in targets:
#                 ids.append(target["id"])
#         except json.JSONDecodeError as e:
#             raise ValueError(f"Failed to parse Targets JSON: {e.msg}")
# print(len(ids))
