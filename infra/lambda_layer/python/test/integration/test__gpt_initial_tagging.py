# test__gpt_initial_tagging.py
from typing import Literal
import pytest
from datetime import datetime
from dynamo import DynamoClient, GPTInitialTagging

@pytest.fixture
def sample_gpt_initial_tagging():
    """
    Returns a sample GPTInitialTagging instance with valid attributes.
    """
    return GPTInitialTagging(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=7,
        query="Is this the total amount?",
        response="Yes, it appears to be the total.",
        timestamp_added=datetime(2021, 1, 1, 0, 0, 0),
    )

def test_add_and_get_gpt_initial_tagging(
    dynamodb_table: Literal["MyMockedTable"], sample_gpt_initial_tagging: GPTInitialTagging
):
    """
    Tests adding a GPTInitialTagging record and retrieving it.
    """
    client = DynamoClient(dynamodb_table)
    # Add the record
    client.addGPTInitialTagging(sample_gpt_initial_tagging)
    # Retrieve it back using only image_id and receipt_id
    retrieved = client.getGPTInitialTagging(
        image_id=sample_gpt_initial_tagging.image_id,
        receipt_id=sample_gpt_initial_tagging.receipt_id,
    )
    assert retrieved == sample_gpt_initial_tagging

def test_update_gpt_initial_tagging(
    dynamodb_table: Literal["MyMockedTable"], sample_gpt_initial_tagging: GPTInitialTagging
):
    """
    Tests updating an existing GPTInitialTagging record.
    """
    client = DynamoClient(dynamodb_table)
    # First add the record
    client.addGPTInitialTagging(sample_gpt_initial_tagging)
    # Modify an attribute (e.g., update the response)
    sample_gpt_initial_tagging.response = "Updated response."
    # Update the record in DynamoDB
    client.updateGPTInitialTagging(sample_gpt_initial_tagging)
    # Retrieve the updated record and verify the change
    updated = client.getGPTInitialTagging(
        image_id=sample_gpt_initial_tagging.image_id,
        receipt_id=sample_gpt_initial_tagging.receipt_id,
    )
    assert updated.response == "Updated response."

def test_delete_gpt_initial_tagging(
    dynamodb_table: Literal["MyMockedTable"], sample_gpt_initial_tagging: GPTInitialTagging
):
    """
    Tests deleting a GPTInitialTagging record.
    """
    client = DynamoClient(dynamodb_table)
    # Add the record and then delete it
    client.addGPTInitialTagging(sample_gpt_initial_tagging)
    client.deleteGPTInitialTagging(sample_gpt_initial_tagging)
    # Attempting to retrieve it should raise a ValueError
    with pytest.raises(ValueError, match="GPTInitialTagging record not found"):
        client.getGPTInitialTagging(
            image_id=sample_gpt_initial_tagging.image_id,
            receipt_id=sample_gpt_initial_tagging.receipt_id,
        )

def test_batch_add_and_list_gpt_initial_tagging(
    dynamodb_table: Literal["MyMockedTable"],
):
    """
    Tests adding multiple GPTInitialTagging records in batch and then listing them.
    """
    taggings = []
    client = DynamoClient(dynamodb_table)
    # Create several sample records with different receipt_ids so they are unique
    for i in range(3):
        tagging = GPTInitialTagging(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=7 + i,
            query=f"Query {i}",
            response=f"Response {i}",
            timestamp_added=datetime(2021, 1, 1, 0, 0, 0),
        )
        taggings.append(tagging)
    # Batch add the records
    client.addGPTInitialTaggings(taggings)
    # List records by scanning the table filtered by TYPE
    listed = client.listGPTInitialTaggings()
    # Filter results for our specific image_id
    filtered = [t for t in listed if t.image_id == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"]
    # We expect at least our 3 records to be present
    assert len(filtered) >= 3
    # Check that each of the batch items is present (by matching the query)
    queries = set(t.query for t in taggings)
    listed_queries = set(t.query for t in filtered)
    assert queries.issubset(listed_queries)

def test_gpt_initial_tagging_get_nonexistent(dynamodb_table: Literal["MyMockedTable"]):
    """
    Tests that attempting to get a non-existent record raises a ValueError.
    """
    with pytest.raises(ValueError, match="GPTInitialTagging record not found"):
        DynamoClient(dynamodb_table).getGPTInitialTagging(
            image_id="nonexistent-id",
            receipt_id=1,
        )

def test_update_nonexistent_gpt_initial_tagging(
    dynamodb_table: Literal["MyMockedTable"], sample_gpt_initial_tagging: GPTInitialTagging
):
    """
    Tests that attempting to update a non-existent record raises a ValueError.
    """
    # Do not add the record first.
    with pytest.raises(ValueError, match="GPTInitialTagging record not found"):
        DynamoClient(dynamodb_table).updateGPTInitialTagging(sample_gpt_initial_tagging)

def test_delete_nonexistent_gpt_initial_tagging(
    dynamodb_table: Literal["MyMockedTable"], sample_gpt_initial_tagging: GPTInitialTagging
):
    """
    Tests that attempting to delete a non-existent record raises a ValueError.
    """
    with pytest.raises(ValueError, match="GPTInitialTagging record not found"):
        DynamoClient(dynamodb_table).deleteGPTInitialTagging(sample_gpt_initial_tagging)