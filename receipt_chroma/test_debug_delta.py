"""Debug delta merging test."""
import os
import tempfile
import pytest
from receipt_dynamo.constants import ChromaDBCollection
from receipt_chroma import ChromaClient
from receipt_chroma.compaction.deltas import merge_compaction_deltas
from tests.helpers.factories import create_compaction_run_message, create_mock_logger

def test_debug_delta_merge(mock_s3_bucket_compaction, temp_chromadb_dir):
    """Debug delta merging."""
    s3_client, bucket_name = mock_s3_bucket_compaction
    
    # Create real logger to see messages
    import logging
    logging.basicConfig(level=logging.DEBUG)
    logger = logging.getLogger(__name__)
    
    # Create a delta ChromaDB snapshot
    delta_dir = tempfile.mkdtemp()
    print(f"Delta dir: {delta_dir}")
    delta_client = ChromaClient(persist_directory=delta_dir, mode="write")
    
    # Add delta data
    delta_client.upsert(
        collection_name="words",
        ids=["IMAGE#delta-id#RECEIPT#00001#LINE#00001#WORD#00001"],
        embeddings=[[0.8] * 1536],
        metadatas=[{"text": "Delta"}],
    )
    delta_client.close()
    
    # Check what files were created
    print("\nDelta directory contents:")
    for root, dirs, files in os.walk(delta_dir):
        for file in files:
            full_path = os.path.join(root, file)
            print(f"  {full_path}")
    
    # Upload delta directory to S3
    delta_prefix = "deltas/run-456"
    uploaded_files = []
    for root, dirs, files in os.walk(delta_dir):
        for file in files:
            local_path = os.path.join(root, file)
            relative_path = os.path.relpath(local_path, delta_dir)
            s3_key = f"{delta_prefix}/{relative_path}"
            s3_client.upload_file(local_path, bucket_name, s3_key)
            uploaded_files.append(s3_key)
            print(f"Uploaded: {s3_key}")
    
    # List S3 objects
    print("\nS3 objects:")
    response = s3_client.list_objects_v2(Bucket=bucket_name, Prefix=delta_prefix)
    for obj in response.get("Contents", []):
        print(f"  {obj['Key']}")
    
    # Create main snapshot
    snapshot_client = ChromaClient(persist_directory=temp_chromadb_dir, mode="write")
    snapshot_client.upsert(
        collection_name="words",
        ids=["IMAGE#snapshot-id#RECEIPT#00001#LINE#00001#WORD#00001"],
        embeddings=[[0.1] * 1536],
        metadatas=[{"text": "Snapshot"}],
    )
    
    # Create compaction run message
    compaction_msg = create_compaction_run_message(
        image_id="delta-id",
        receipt_id=1,
        run_id="run-456",
        delta_s3_prefix=f"s3://{bucket_name}/{delta_prefix}/",
        event_name="INSERT",
        collection=ChromaDBCollection.WORDS,
    )
    
    # Merge delta into snapshot
    total_merged, per_run_results = merge_compaction_deltas(
        chroma_client=snapshot_client,
        compaction_runs=[compaction_msg],
        collection=ChromaDBCollection.WORDS,
        logger=logger,
        bucket=bucket_name,
    )
    
    print(f"\nMerge results: total_merged={total_merged}, per_run_results={per_run_results}")
    
    # Cleanup
    import shutil
    shutil.rmtree(delta_dir, ignore_errors=True)

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
