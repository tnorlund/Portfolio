import logging
import os
import subprocess
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("entrypoint")


def resolve_pointer(bucket: str, collection: str) -> str:
    s3 = boto3.client("s3")
    pointer_key = f"{collection}/snapshot/latest-pointer.txt"
    try:
        resp = s3.get_object(Bucket=bucket, Key=pointer_key)
        version_id = resp["Body"].read().decode("utf-8").strip()
        logger.info("Resolved pointer for %s: %s", collection, version_id)
        return f"{collection}/snapshot/timestamped/{version_id}/"
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "NoSuchKey":
            logger.warning(
                "Pointer missing, falling back to /latest/ for %s", collection
            )
            return f"{collection}/snapshot/latest/"
        raise


def download_prefix(bucket: str, prefix: str, dest: str) -> None:
    s3 = boto3.client("s3")
    Path(dest).mkdir(parents=True, exist_ok=True)
    paginator = s3.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=bucket, Prefix=prefix)
    found = False
    for page in pages:
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith("/") or key == prefix:
                continue
            found = True
            rel = key[len(prefix) :]
            out_path = Path(dest) / rel
            out_path.parent.mkdir(parents=True, exist_ok=True)
            logger.debug("Downloading s3://%s/%s -> %s", bucket, key, out_path)
            s3.download_file(bucket, key, str(out_path))
    if not found:
        raise RuntimeError(f"No objects found at s3://{bucket}/{prefix}")


def main() -> int:
    bucket = os.environ.get("CHROMADB_BUCKET") or os.environ.get("VECTORS_BUCKET")
    if not bucket:
        logger.error("CHROMADB_BUCKET or VECTORS_BUCKET must be set")
        return 1

    collection = os.environ.get("CHROMA_COLLECTION", "words")
    persist_dir = os.environ.get("CHROMA_PERSIST_DIR", "/data/chroma")

    logger.info(
        "Preparing snapshot: bucket=%s collection=%s dest=%s",
        bucket,
        collection,
        persist_dir,
    )

    # Resolve atomic pointer and download snapshot
    prefix = resolve_pointer(bucket, collection)
    download_prefix(bucket, prefix, persist_dir)

    # Start Chroma server
    host = os.environ.get("CHROMA_SERVER_HOST", "0.0.0.0")
    port = os.environ.get("CHROMA_SERVER_HTTP_PORT", "8000")

    cmd = [
        "chroma",
        "run",
        "--path",
        persist_dir,
        "--host",
        host,
        "--port",
        str(port),
    ]

    logger.info("Starting Chroma server: %s", " ".join(cmd))
    proc = subprocess.Popen(cmd)
    try:
        return proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        return 0


if __name__ == "__main__":
    sys.exit(main())
