from unittest.mock import MagicMock, patch

from receipt_dynamo import DynamoClient


def test_dynamo_client_accepts_local_endpoint():
    boto_client = MagicMock()

    with patch(
        "receipt_dynamo.data.dynamo_client.boto3.client",
        return_value=boto_client,
    ) as create_client:
        client = DynamoClient(
            "receipts-local",
            endpoint_url="http://127.0.0.1:8000",
        )

    assert client.table_name == "receipts-local"
    create_client.assert_called_once_with(
        "dynamodb",
        region_name="us-east-1",
        config=None,
        endpoint_url="http://127.0.0.1:8000",
        aws_access_key_id="local",
        aws_secret_access_key="local",
    )
    boto_client.describe_table.assert_called_once_with(
        TableName="receipts-local"
    )


def test_dynamo_client_reads_local_endpoint_from_environment(monkeypatch):
    monkeypatch.setenv("DYNAMODB_ENDPOINT_URL", "http://localhost:9000")
    boto_client = MagicMock()

    with patch(
        "receipt_dynamo.data.dynamo_client.boto3.client",
        return_value=boto_client,
    ) as create_client:
        DynamoClient("receipts-local")

    assert (
        create_client.call_args.kwargs["endpoint_url"]
        == "http://localhost:9000"
    )
    assert create_client.call_args.kwargs["aws_access_key_id"] == "local"
