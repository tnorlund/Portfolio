import json
import os
from pathlib import Path
from uuid import uuid4
import boto3
import tempfile
from receipt_label.submit_completion_batch import merge_ndjsons

max_lines = 50000  # 50k lines
max_size = 100000000  # 100MB
s3_bucket = "validation-pipeline-completion-batch-bucket-d6d21d8"
event = {
    "s3_keys": [
        "completion_batches/03fa2d0f-33c6-43be-88b0-dae73ec26c93_1_d3e5a737-9684-4f61-96b6-3f147b553890.ndjson",
        "completion_batches/04a1dfe7-1858-403a-b74f-6edb23d0deb4_1_f56c998b-7c5d-4d26-8ac5-a9e0e4581a9f.ndjson",
        "completion_batches/04b16930-7baf-4539-866f-b77d8e73cff8_1_0690ca48-dd8a-41d7-95c7-9bc8685671ac.ndjson",
        "completion_batches/04b16930-7baf-4539-866f-b77d8e73cff8_2_26305f99-003a-49b0-bb0f-82020eeff909.ndjson",
        "completion_batches/04ebdb8a-b560-4c00-aa09-f6afa3bda458_1_d5a28562-0f60-4a8a-9f24-d3d62c688efc.ndjson",
        "completion_batches/05410ae8-bf1c-488b-87e5-1f7b006a442a_1_40ed40f1-8611-46d4-b0b7-c304823d9ef8.ndjson",
        "completion_batches/058b662d-86d7-4e5f-9dd4-e77335d4197f_1_2a9f9702-aa32-4d58-8645-d6331417160d.ndjson",
        "completion_batches/069e270a-a3ce-40dc-81bc-1f05d7f23ac2_1_48fbbb46-13fe-4f20-9aff-6bc76c78d8a4.ndjson",
    ]
}


if __name__ == "__main__":
    merged_files = merge_ndjsons(s3_bucket, event["s3_keys"], max_lines, max_size)
    for merged_file, consumed_keys in merged_files:
        print(f"{merged_file}: {consumed_keys}")
