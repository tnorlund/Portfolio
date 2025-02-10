# test__gpt_validation.py
from typing import Literal
import pytest
from datetime import datetime
from dynamo import DynamoClient, GPTValidation

@pytest.fixture
def sample_gpt_validation():
    """
    Returns a sample GPTValidation instance with valid attributes.
    """
    return GPTValidation(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=7,
        query="Is this the total amount?",
        response="Yes, it is the total.",
        timestamp_added=datetime(2021, 1, 1, 0, 0, 0),
    )

def test_add_and_get_gpt_validation(
    dynamodb_table: Literal["MyMockedTable"], sample_gpt_validation
):
    """
    Tests adding a GPTValidation record and retrieving it.
    """
    client = DynamoClient(dynamodb_table)
    # Add the record
    client.addGPTValidation(sample_gpt_validation)
    # Retrieve it back using the key parameters
    retrieved = client.getGPTValidation(
        image_id=sample_gpt_validation.image_id,
        receipt_id=sample_gpt_validation.receipt_id,
    )
    assert retrieved == sample_gpt_validation

def test_update_gpt_validation(
    dynamodb_table: Literal["MyMockedTable"], sample_gpt_validation
):
    """
    Tests updating an existing GPTValidation record.
    """
    client = DynamoClient(dynamodb_table)
    # First add the record
    client.addGPTValidation(sample_gpt_validation)
    # Modify an attribute (e.g., update the response)
    sample_gpt_validation.response = "Updated response."
    # Update the record in DynamoDB
    client.updateGPTValidation(sample_gpt_validation)
    # Retrieve the updated record and verify the change
    updated = client.getGPTValidation(
        image_id=sample_gpt_validation.image_id,
        receipt_id=sample_gpt_validation.receipt_id,
    )
    assert updated.response == "Updated response."

def test_delete_gpt_validation(
    dynamodb_table: Literal["MyMockedTable"], sample_gpt_validation
):
    """
    Tests deleting a GPTValidation record.
    """
    client = DynamoClient(dynamodb_table)
    # Add the record and then delete it
    client.addGPTValidation(sample_gpt_validation)
    client.deleteGPTValidation(sample_gpt_validation)
    # Attempting to retrieve it should raise a ValueError
    with pytest.raises(ValueError, match="GPTValidation record not found"):
        client.getGPTValidation(
            image_id=sample_gpt_validation.image_id,
            receipt_id=sample_gpt_validation.receipt_id,
        )

def test_gpt_validation_batch_add_list(dynamodb_table: Literal["MyMockedTable"]):
    """
    Tests adding multiple GPTValidation records in batch and then listing them.
    """
    validations = []
    client = DynamoClient(dynamodb_table)
    # Create several sample records with different keys (varying word_id)
    for i in range(3):
        validation = GPTValidation(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=7 + i,
            query=f"Query {i}",
            response=f"Response {i}",
            timestamp_added=datetime(2021, 1, 1, 0, 0, 0),
        )
        validations.append(validation)
    # Batch add the records
    client.addGPTValidations(validations)
    # List records via the GSI
    listed = client.listGPTValidations()
    # Filter on image_id and receipt_id to ensure we are only looking at test records
    filtered = [
        v for v in listed
        if v.image_id == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    ]
    assert len(filtered) >= 3
    # Verify that the queries for our batch records are present
    queries = set(v.query for v in validations)
    listed_queries = set(v.query for v in filtered)
    assert queries.issubset(listed_queries)

def test_gpt_validation_get_nonexistent(dynamodb_table: Literal["MyMockedTable"]):
    """
    Tests that attempting to get a non-existent record raises a ValueError.
    """
    with pytest.raises(ValueError, match="GPTValidation record not found"):
        DynamoClient(dynamodb_table).getGPTValidation(
            image_id="nonexistent-id",
            receipt_id=1,
        )

def test_update_nonexistent_gpt_validation(
    dynamodb_table: Literal["MyMockedTable"], sample_gpt_validation
):
    """
    Tests that attempting to update a non-existent record raises a ValueError.
    """
    # Do not add the record first.
    with pytest.raises(ValueError, match="GPTValidation record not found"):
        DynamoClient(dynamodb_table).updateGPTValidation(sample_gpt_validation)

def test_delete_nonexistent_gpt_validation(
    dynamodb_table: Literal["MyMockedTable"], sample_gpt_validation
):
    """
    Tests that attempting to delete a non-existent record raises a ValueError.
    """
    with pytest.raises(ValueError, match="GPTValidation record not found"):
        DynamoClient(dynamodb_table).deleteGPTValidation(sample_gpt_validation)