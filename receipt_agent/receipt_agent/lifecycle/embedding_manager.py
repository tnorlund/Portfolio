"""
Embedding Management for Receipt Lifecycle

Handles creation of ChromaDB embeddings for receipts.

Note: Embedding deletion is handled automatically by the enhanced compactor
when Receipt entities are deleted from DynamoDB (via DynamoDB streams).
Do not manually delete embeddings - the compactor is the source of truth.
"""

import os
import tempfile
import uuid
import shutil
from typing import List, Optional

from receipt_dynamo import DynamoClient
from receipt_dynamo.entities import (
    ReceiptLine,
    ReceiptWord,
    CompactionRun,
)


def create_embeddings(
    client: DynamoClient,
    chromadb_bucket: str,
    image_id: str,
    receipt_id: int,
    receipt_lines: Optional[List[ReceiptLine]] = None,
    receipt_words: Optional[List[ReceiptWord]] = None,
    merchant_name: Optional[str] = None,
) -> Optional[str]:
    """
    Create embeddings in realtime using OpenAI API and create CompactionRun.

    This process:
    1. Creates local ChromaDB delta collections (temporary)
    2. Calls OpenAI Embeddings API directly (realtime, not batch) to generate embeddings
       - Uses embed_lines_realtime() which calls openai_client.embeddings.create()
       - Uses embed_words_realtime() which calls openai_client.embeddings.create()
    3. Stores embeddings in local delta collections
    4. Uploads delta files to S3 (chromadb_bucket)
    5. Creates CompactionRun entity in DynamoDB
    6. DynamoDB streams trigger compactor to merge deltas into snapshots

    This matches the approach used in split_receipt.py and combine_receipts_logic.py.
    Uses realtime OpenAI API calls (not batch API) for immediate embedding generation.

    Args:
        client: DynamoDB client
        chromadb_bucket: S3 bucket for ChromaDB deltas (e.g., "chromadb-bucket")
        image_id: Image ID
        receipt_id: Receipt ID
        receipt_lines: List of ReceiptLine entities (fetched if not provided)
        receipt_words: List of ReceiptWord entities (fetched if not provided)
        merchant_name: Optional merchant name for embedding context

    Returns:
        run_id if successful, None if embedding is not available or fails
    """
    try:
        # Try multiple import paths for ChromaClient
        try:
            from receipt_chroma.data.chroma_client import ChromaClient
        except ImportError:
            try:
                from receipt_chroma import ChromaClient
            except ImportError:
                print(f"⚠️  ChromaClient not available; skipping embedding")
                return None

        from receipt_label.merchant_resolution.embeddings import upsert_embeddings
        from receipt_label.embedding.line.realtime import embed_lines_realtime
        from receipt_label.embedding.word.realtime import embed_words_realtime

        # Check if embedding is available
        if not os.environ.get("OPENAI_API_KEY"):
            print(f"⚠️  OPENAI_API_KEY not set; skipping embedding")
            return None

        # Fetch receipt data if not provided
        if receipt_lines is None:
            receipt_lines = client.list_receipt_lines_from_receipt(image_id, receipt_id)
        if receipt_words is None:
            receipt_words = client.list_receipt_words_from_receipt(image_id, receipt_id)

        if not receipt_lines or not receipt_words:
            print(f"⚠️  No lines/words found for receipt {receipt_id}; skipping embedding")
            return None

        # Generate run ID
        run_id = str(uuid.uuid4())
        delta_lines_dir = os.path.join(tempfile.gettempdir(), f"lines_{run_id}")
        delta_words_dir = os.path.join(tempfile.gettempdir(), f"words_{run_id}")

        # Create local ChromaDB deltas
        line_client = ChromaClient(
            persist_directory=delta_lines_dir,
            mode="delta",
            metadata_only=True
        )
        word_client = ChromaClient(
            persist_directory=delta_words_dir,
            mode="delta",
            metadata_only=True
        )

        # Create embeddings using OpenAI API (realtime, not batch)
        # This calls openai_client.embeddings.create() directly for immediate results
        print(f"   Creating embeddings for receipt {receipt_id} using OpenAI API (realtime)...")
        print(f"      Embedding {len(receipt_lines)} lines and {len(receipt_words)} words...")
        upsert_embeddings(
            line_client=line_client,
            word_client=word_client,
            line_embed_fn=embed_lines_realtime,  # Calls OpenAI API directly
            word_embed_fn=embed_words_realtime,  # Calls OpenAI API directly
            ctx={"lines": receipt_lines, "words": receipt_words},
            merchant_name=merchant_name,
        )
        print(f"      ✅ Embeddings generated via OpenAI API")

        # Upload delta tarballs to S3 (source of truth for ChromaDB)
        # The compaction Lambda expects tarballs at {prefix}/delta.tar.gz
        # These delta tarballs contain the new embeddings and will be merged into snapshots by compactor
        lines_prefix = f"lines/delta/{run_id}"
        words_prefix = f"words/delta/{run_id}"

        print(f"   Creating and uploading delta tarballs to S3...")

        # Close clients to flush SQLite files before creating tarballs
        line_client.close()
        word_client.close()

        # Import delta tarball upload function from receipt_chroma
        try:
            from receipt_chroma.s3 import upload_delta_tarball
        except ImportError:
            print(f"⚠️  Could not import upload_delta_tarball from receipt_chroma.s3")
            raise ImportError(
                "upload_delta_tarball not available in receipt_chroma.s3. "
                "Please ensure receipt_chroma package is up to date."
            )

        # Create and upload tarballs
        try:
            lines_result = upload_delta_tarball(
                local_delta_dir=delta_lines_dir,
                bucket=chromadb_bucket,
                delta_prefix=lines_prefix,
            )
            words_result = upload_delta_tarball(
                local_delta_dir=delta_words_dir,
                bucket=chromadb_bucket,
                delta_prefix=words_prefix,
            )

            if lines_result.get("status") != "uploaded" or words_result.get("status") != "uploaded":
                error_msg = (
                    f"Failed to upload delta tarballs: "
                    f"lines={lines_result.get('error')}, "
                    f"words={words_result.get('error')}"
                )
                print(f"⚠️  {error_msg}")
                raise RuntimeError(error_msg)

            # The delta prefix is the prefix where the tarball is located (without /delta.tar.gz)
            # The compaction Lambda will look for {delta_prefix}/delta.tar.gz
            lines_delta_key = lines_prefix
            words_delta_key = words_prefix

            print(f"      ✅ Lines delta tarball: s3://{chromadb_bucket}/{lines_result.get('object_key')}")
            print(f"      ✅ Words delta tarball: s3://{chromadb_bucket}/{words_result.get('object_key')}")
        except Exception as e:
            error_msg = f"Failed to create/upload delta tarballs: {e}"
            print(f"⚠️  {error_msg}")
            import traceback
            traceback.print_exc()
            raise RuntimeError(error_msg) from e

        # Cleanup local delta directories
        try:
            if os.path.exists(delta_lines_dir):
                shutil.rmtree(delta_lines_dir, ignore_errors=True)
            if os.path.exists(delta_words_dir):
                shutil.rmtree(delta_words_dir, ignore_errors=True)
        except Exception as e:
            print(f"⚠️  Warning: Could not cleanup temp directories: {e}")

        # Create CompactionRun entity in DynamoDB
        # This triggers the compactor via DynamoDB streams to merge deltas into snapshots
        compaction_run = CompactionRun(
            run_id=run_id,
            image_id=image_id,
            receipt_id=receipt_id,
            lines_delta_prefix=lines_delta_key,  # S3 prefix for lines delta tarball
            words_delta_prefix=words_delta_key,  # S3 prefix for words delta tarball
        )
        client.add_compaction_run(compaction_run)

        print(f"   ✅ Created CompactionRun: {run_id}")
        print(f"      Compactor will merge deltas into snapshots via DynamoDB streams")
        return run_id

    except ImportError as e:
        print(f"⚠️  Could not import embedding modules: {e}")
        return None
    except Exception as e:
        print(f"⚠️  Error creating embeddings: {e}")
        import traceback
        traceback.print_exc()
        return None

