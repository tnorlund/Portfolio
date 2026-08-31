"""Unit tests for DynamoVectorSearchClient over a stubbed boto3 client.

moto cannot mock SearchVectors, so the request/response wiring is
verified against a stub that records requests and replays canned
service-shaped responses (spec §6 H test strategy).
"""

from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError

from receipt_embeddings import ScoredItem, VectorSearchClient
from receipt_embeddings.dynamo_client import (
    DEFAULT_TABLE_NAME,
    DynamoVectorSearchClient,
    create_client_from_env,
)

LINE_KEY = "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3#RECEIPT#00001#LINE#00002"
WORD_KEY = LINE_KEY + "#WORD#00003"


class StubDynamo:
    """Records requests and replays canned SearchVectors/GetItem output."""

    def __init__(self):
        self.search_requests = []
        self.get_requests = []
        self.search_response = {"SearchResults": []}
        self.get_response = {}
        self.search_error = None

    def search_vectors(self, **kwargs):
        if self.search_error is not None:
            raise self.search_error
        self.search_requests.append(kwargs)
        return self.search_response

    def get_item(self, **kwargs):
        self.get_requests.append(kwargs)
        return self.get_response


def line_result(score: float, sk: str, **attrs):
    item = {
        "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": sk},
    }
    item.update(attrs)
    return {"Item": item, "Score": score}


@pytest.fixture
def stub():
    return StubDynamo()


@pytest.fixture
def client(stub):
    return DynamoVectorSearchClient("ReceiptsTable-dc5be22", client=stub)


@pytest.mark.unit
def test_client_satisfies_protocol(client):
    assert isinstance(client, VectorSearchClient)


@pytest.mark.unit
def test_client_requires_search_vectors_support():
    with pytest.raises(RuntimeError, match="1.43.64"):
        DynamoVectorSearchClient(client=SimpleNamespace())


@pytest.mark.unit
def test_search_request_wiring(client, stub):
    stub.search_response = {
        "SearchResults": [
            line_result(
                0.125,
                "RECEIPT#00001#LINE#00002#EMBEDDING",
                TYPE={"S": "RECEIPT_LINE_EMBEDDING"},
                text={"S": "COSTCO"},
                merchant_name={"S": "Costco"},
                receipt_id={"N": "1"},
                line_id={"N": "2"},
                row_line_ids={"L": [{"N": "2"}, {"N": "3"}]},
            )
        ],
        "ConsumedCapacity": {"VectorSearchRequestBytes": 42.5},
    }

    results = client.search(
        [0.5, 6.6e-05],
        "lines-vectors",
        20,
        {"section_type": "HEADER"},
    )

    request = stub.search_requests[0]
    assert request["TableName"] == "ReceiptsTable-dc5be22"
    # Protocol index names map onto the judge-provisioned physical index.
    assert request["IndexName"] == "line-embeddings"
    assert request["TopK"] == 20
    # Vector components serialize positionally, never scientific.
    assert request["SearchVector"] == [{"N": "0.5"}, {"N": "0.000066"}]
    assert request["SearchConditionExpression"] == "#f0 = :v0"
    assert request["ExpressionAttributeNames"] == {"#f0": "section_type"}
    assert request["ExpressionAttributeValues"] == {":v0": {"S": "HEADER"}}
    assert request["ReturnConsumedCapacity"] == "TOTAL"

    assert results == [
        ScoredItem(
            key=LINE_KEY,
            distance=0.125,
            metadata={
                "text": "COSTCO",
                "merchant_name": "Costco",
                "receipt_id": 1,
                "line_id": 2,
                "row_line_ids": [2, 3],
            },
        )
    ]
    assert client.last_request_units == 42.5
    assert client.last_latency_ms >= 0.0


@pytest.mark.unit
def test_search_multiple_filters_sorted_and_joined(client, stub):
    client.search(
        [1.0],
        "words-vectors",
        5,
        {"label_status": "validated", "merchant_name": "Costco"},
    )
    request = stub.search_requests[0]
    assert request["IndexName"] == "word-embeddings"
    assert (
        request["SearchConditionExpression"] == "#f0 = :v0 AND #f1 = :v1"
    )
    assert request["ExpressionAttributeNames"] == {
        "#f0": "label_status",
        "#f1": "merchant_name",
    }


@pytest.mark.unit
def test_search_result_tie_break_matches_fake(client, stub):
    stub.search_response = {
        "SearchResults": [
            line_result(0.5, "RECEIPT#00001#LINE#00009#EMBEDDING"),
            line_result(0.5, "RECEIPT#00001#LINE#00002#EMBEDDING"),
        ]
    }
    results = client.search([1.0], "lines-vectors", 10)
    assert [item.key for item in results] == [
        LINE_KEY,
        LINE_KEY.replace("LINE#00002", "LINE#00009"),
    ]


@pytest.mark.unit
def test_search_rejects_quota_violations_before_any_request(client, stub):
    with pytest.raises(ValueError):
        client.search([1.0], "lines-vectors", 101)
    with pytest.raises(TypeError):
        client.search([1.0], "lines-vectors", "20")
    with pytest.raises(ValueError, match="operator key"):
        client.search([1.0], "lines-vectors", 10, {"$and": "x"})
    with pytest.raises(ValueError, match="unknown vector index"):
        client.search([1.0], "letters-vectors", 10)
    assert stub.search_requests == []


@pytest.mark.unit
def test_search_throttle_propagates_client_error(client, stub):
    """Throttles surface as ClientError for callers to degrade on."""

    stub.search_error = ClientError(
        {"Error": {"Code": "ThrottlingException", "Message": "slow down"}},
        "SearchVectors",
    )
    with pytest.raises(ClientError):
        client.search([1.0], "lines-vectors", 10)


@pytest.mark.unit
def test_get_vector_line_and_word_attribute_selection(client, stub):
    stub.get_response = {
        "Item": {"line_vector": {"L": [{"N": "0.5"}, {"N": "0.000066"}]}}
    }
    assert client.get_vector(LINE_KEY) == [0.5, 6.6e-05]
    request = stub.get_requests[0]
    assert request["Key"] == {
        "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "RECEIPT#00001#LINE#00002#EMBEDDING"},
    }
    assert request["ExpressionAttributeNames"] == {"#v": "line_vector"}

    stub.get_response = {"Item": {"word_vector": {"L": [{"N": "1"}]}}}
    assert client.get_vector(WORD_KEY) == [1.0]
    request = stub.get_requests[1]
    assert request["Key"]["SK"] == {
        "S": "RECEIPT#00001#LINE#00002#WORD#00003#EMBEDDING"
    }
    assert request["ExpressionAttributeNames"] == {"#v": "word_vector"}


@pytest.mark.unit
def test_get_vector_missing_item_raises_key_error(client, stub):
    """Missing vectors degrade as KeyError, matching fake and replay."""

    stub.get_response = {}
    with pytest.raises(KeyError, match="unknown vector key"):
        client.get_vector(LINE_KEY)


@pytest.mark.unit
def test_get_vector_malformed_key_raises_key_error(client, stub):
    with pytest.raises(KeyError, match="unknown vector key"):
        client.get_vector("RECEIPT#00001#LINE#00002")
    assert stub.get_requests == []


@pytest.mark.unit
def test_create_client_from_env_defaults(monkeypatch, stub):
    monkeypatch.delenv("DYNAMODB_TABLE_NAME", raising=False)
    monkeypatch.setattr(
        DynamoVectorSearchClient,
        "__init__",
        lambda self, table_name=DEFAULT_TABLE_NAME, *, client=None, region="": (
            setattr(self, "table_name", table_name) or None
        ),
    )
    assert create_client_from_env().table_name == DEFAULT_TABLE_NAME
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "SomeOtherTable")
    assert create_client_from_env().table_name == "SomeOtherTable"
