"""Integration tests for delta producer using moto."""

import tempfile
from pathlib import Path

import boto3
import pytest
from moto import mock_aws
from receipt_chroma.embedding.delta.producer import produce_embedding_delta


@pytest.fixture
def s3_bucket(request):
    """Create a mock S3 bucket using moto."""
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        bucket_name = request.param
        s3.create_bucket(Bucket=bucket_name)
        yield bucket_name


@pytest.fixture
def sqs_queue(request):
    """Create a mock SQS queue using moto."""
    with mock_aws():
        sqs = boto3.client("sqs", region_name="us-east-1")
        queue_name = request.param
        queue_url = sqs.create_queue(QueueName=queue_name)["QueueUrl"]
        yield queue_url


@pytest.fixture
def temp_dir():
    """Create a temporary directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.mark.integration
class TestProduceEmbeddingDelta:
    """Test producing embedding deltas."""

    @pytest.mark.parametrize("s3_bucket", ["test-delta-bucket"], indirect=True)
    def test_produce_delta_basic(self, s3_bucket, temp_dir):
        """Test basic delta production."""
        with mock_aws():
            ids = ["WORD#img1#rec1#line1#word1", "WORD#img1#rec1#line1#word2"]
            embeddings = [[0.1] * 384, [0.2] * 384]
            documents = ["hello", "world"]
            metadatas = [{"text": "hello"}, {"text": "world"}]

            result = produce_embedding_delta(
                ids=ids,
                embeddings=embeddings,
                documents=documents,
                metadatas=metadatas,
                bucket_name=s3_bucket,
                collection_name="words",
                local_temp_dir=temp_dir,
            )

            assert result["status"] == "success"
            assert "delta_key" in result

            # Verify delta was uploaded to S3
            s3 = boto3.client("s3", region_name="us-east-1")
            objects = s3.list_objects_v2(Bucket=s3_bucket, Prefix="delta/")
            assert objects["KeyCount"] > 0

    @pytest.mark.parametrize("s3_bucket", ["test-delta-bucket"], indirect=True)
    @pytest.mark.parametrize("sqs_queue", ["test-compaction-queue"], indirect=True)
    def test_produce_delta_with_sqs(self, s3_bucket, sqs_queue, temp_dir):
        """Test delta production with SQS notification."""
        with mock_aws():
            ids = ["WORD#img1#rec1#line1#word1"]
            embeddings = [[0.1] * 384]
            documents = ["hello"]
            metadatas = [{"text": "hello"}]

            result = produce_embedding_delta(
                ids=ids,
                embeddings=embeddings,
                documents=documents,
                metadatas=metadatas,
                bucket_name=s3_bucket,
                collection_name="words",
                sqs_queue_url=sqs_queue,
                local_temp_dir=temp_dir,
            )

            assert result["status"] == "success"

            # Verify SQS message was sent
            sqs = boto3.client("sqs", region_name="us-east-1")
            messages = sqs.receive_message(QueueUrl=sqs_queue, MaxNumberOfMessages=1)
            assert "Messages" in messages

    @pytest.mark.parametrize("s3_bucket", ["test-delta-bucket"], indirect=True)
    def test_produce_delta_with_database_name(self, s3_bucket, temp_dir):
        """Test delta production with database name."""
        with mock_aws():
            ids = ["WORD#img1#rec1#line1#word1"]
            embeddings = [[0.1] * 384]
            documents = ["hello"]
            metadatas = [{"text": "hello"}]

            result = produce_embedding_delta(
                ids=ids,
                embeddings=embeddings,
                documents=documents,
                metadatas=metadatas,
                bucket_name=s3_bucket,
                collection_name="words",
                database_name="test_db",
                local_temp_dir=temp_dir,
            )

            assert result["status"] == "success"
            assert "test_db" in result.get("delta_key", "")

    @pytest.mark.parametrize("s3_bucket", ["test-delta-bucket"], indirect=True)
    def test_produce_delta_with_batch_id(self, s3_bucket, temp_dir):
        """Test delta production with batch ID."""
        with mock_aws():
            ids = ["WORD#img1#rec1#line1#word1"]
            embeddings = [[0.1] * 384]
            documents = ["hello"]
            metadatas = [{"text": "hello"}]

            result = produce_embedding_delta(
                ids=ids,
                embeddings=embeddings,
                documents=documents,
                metadatas=metadatas,
                bucket_name=s3_bucket,
                collection_name="words",
                batch_id="test-batch-123",
                local_temp_dir=temp_dir,
            )

            assert result["status"] == "success"
