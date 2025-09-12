import json
from typing import Union
from pathlib import Path

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities.receipt_word import ReceiptWord
from receipt_dynamo.entities.receipt_line import ReceiptLine
from receipt_dynamo.data._pulumi import load_env

from receipt_label.utils.chroma_s3_helpers import download_snapshot_atomic
from receipt_label.vector_store import VectorClient

words_export = Path(__file__).parent / "dev.words.ndjson"
lines_export = Path(__file__).parent / "dev.lines.ndjson"
chroma_words_dir = (Path(__file__).parent / "dev.chroma_words").resolve()
chroma_lines_dir = (Path(__file__).parent / "dev.chroma_lines").resolve()

dynamo_client = DynamoClient(load_env().get("dynamodb_table_name"))
chroma_bucket = load_env().get("chroma_bucket")


def to_line_chroma_id(item: Union[ReceiptWord, ReceiptLine]) -> str:
    return f"IMAGE#{item.image_id}#RECEIPT#{item.receipt_id:05d}#LINE#{item.line_id:05d}"


def to_word_chroma_id(item: Union[ReceiptWord, ReceiptLine]) -> str:
    return f"IMAGE#{item.image_id}#RECEIPT#{item.receipt_id:05d}#LINE#{item.line_id:05d}#WORD#{item.word_id:05d}"


# If the files do not exist export it from DynamoDB using the client
if not words_export.exists() and not lines_export.exists():
    # Export the words and lines from DynamoDB
    words, _ = dynamo_client.list_receipt_words()
    lines, _ = dynamo_client.list_receipt_lines()

    # Save the words and lines to the files
    if words_export.exists():
        words_export.unlink()
    with open(words_export, "w", encoding="utf-8") as f:
        for word in words:
            f.write(json.dumps(dict(word)) + "\n")

    if lines_export.exists():
        lines_export.unlink()
    with open(lines_export, "w", encoding="utf-8") as f:
        for line in lines:
            f.write(json.dumps(dict(line)) + "\n")
# If the files exist, load them
else:
    with open(words_export, "r", encoding="utf-8") as f:
        words = [ReceiptWord(**json.loads(line)) for line in f]

    with open(lines_export, "r", encoding="utf-8") as f:
        lines = [ReceiptLine(**json.loads(line)) for line in f]


local_lines_path = str(chroma_lines_dir)
local_words_path = str(chroma_words_dir)
use_existing_lines = chroma_lines_dir.exists() and any(
    chroma_lines_dir.rglob("*")
)
use_existing_words = chroma_words_dir.exists() and any(
    chroma_words_dir.rglob("*")
)
if not chroma_bucket:
    raise ValueError(
        "No VECTORS_BUCKET configured and no local lines or words snapshot found; cannot download."
    )

if not use_existing_lines or not use_existing_words:
    try:
        print(
            f"Downloading lines snapshot from s3://{chroma_bucket}/lines/snapshot/... to {local_lines_path}"
        )
        _ = download_snapshot_atomic(
            bucket=chroma_bucket,
            collection="lines",
            local_path=local_lines_path,
        )
        print(
            f"Downloading words snapshot from s3://{chroma_bucket}/words/snapshot/... to {local_words_path}"
        )
        _ = download_snapshot_atomic(
            bucket=chroma_bucket,
            collection="words",
            local_path=local_words_path,
        )
    except Exception as e:  # pylint: disable=broad-except
        raise ValueError(
            f"Snapshot (lines) download failed and no local snapshot present: {e}"
        )


line_client = VectorClient.create_line_client(
    persist_directory=local_lines_path, mode="read"
)
words_client = VectorClient.create_word_client(
    persist_directory=local_words_path, mode="read"
)


words_with_extracted_data = [
    w
    for w in words
    if w.extracted_data and w.extracted_data["type"] in ["address", "phone"]
]
