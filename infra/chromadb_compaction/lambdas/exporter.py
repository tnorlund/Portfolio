import os
import boto3
from pathlib import Path

s3 = boto3.client("s3")
BUCKET = os.environ["S3_BUCKET"]
ROOT = Path("/mnt/chroma")
COLLECTIONS = [
    c.strip()
    for c in os.environ.get("COLLECTIONS", "lines,words").split(",")
    if c.strip()
]


def upload_dir(local_dir: Path, s3_prefix: str) -> None:
    for p in local_dir.rglob("*"):
        if p.is_file():
            key = f"{s3_prefix}/{p.relative_to(local_dir)}"
            s3.upload_file(str(p), BUCKET, key)


def handler(event, context):
    for col in COLLECTIONS:
        pointer_path = ROOT / col / "snapshot" / "latest-pointer.txt"
        if not pointer_path.exists():
            continue
        version_id = pointer_path.read_text().strip()
        src = ROOT / col / "snapshot" / "timestamped" / version_id
        if not src.exists():
            continue
        dst_prefix = f"{col}/snapshot/timestamped/{version_id}"
        upload_dir(src, dst_prefix)
        s3.put_object(
            Bucket=BUCKET,
            Key=f"{col}/snapshot/latest-pointer.txt",
            Body=version_id.encode("utf-8"),
        )
    return {"status": "ok"}
