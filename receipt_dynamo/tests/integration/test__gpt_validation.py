# test__gpt_validation.py
from datetime import datetime
from typing import Literal

import pytest

from receipt_dynamo import DynamoClient, GPTValidation


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


def test_gpt_validation_batch_add_list(
    dynamodb_table: Literal["MyMockedTable"],
):
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
    listed, _ = client.listGPTValidations()
    # Filter on image_id and receipt_id to ensure we are only looking at test
    # records
    filtered = [
        v
        for v in listed
        if v.image_id == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    ]
    assert len(filtered) >= 3
    # Verify that the queries for our batch records are present
    queries = set(v.query for v in validations)
    listed_queries = set(v.query for v in filtered)
    assert queries.issubset(listed_queries)


def test_gpt_validation_get_nonexistent(
    dynamodb_table: Literal["MyMockedTable"],
):
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


@pytest.mark.integration
def test_listGPTValidations_no_limit(dynamodb_table, sample_gpt_validation):
    """
    Tests that listGPTValidations (without a limit) returns all GPTValidation
    records.
    """
    client = DynamoClient(dynamodb_table)
    # Clear out any pre-existing GPTValidation records for a clean test.
    # (Depending on your environment you may wish to delete records
    #  beforehand.)

    # Add two sample records.
    validation1 = sample_gpt_validation
    validation2 = GPTValidation(
        image_id=sample_gpt_validation.image_id,
        receipt_id=8,
        query="What is the tax?",
        response="Tax is $5.00.",
        timestamp_added=datetime(2021, 1, 2, 0, 0, 0),
    )
    client.addGPTValidation(validation1)
    client.addGPTValidation(validation2)

    # Call listGPTValidations with no limit.
    validations, lek = client.listGPTValidations()
    # Verify that both records are returned.
    returned_ids = {(v.image_id, v.receipt_id) for v in validations}
    expected_ids = {
        (validation1.image_id, validation1.receipt_id),
        (validation2.image_id, validation2.receipt_id),
    }
    assert expected_ids.issubset(returned_ids)
    # With no limit, we expect pagination to complete.
    assert lek is None


@pytest.mark.integration
def test_listGPTValidations_with_limit_and_pagination(dynamodb_table):
    """
    Tests that listGPTValidations returns a limited number of items and a valid
    LastEvaluatedKey when more records remain, and that subsequent calls with
    the LEK return the remaining records.
    """
    client = DynamoClient(dynamodb_table)
    # Create several sample GPTValidation records.
    validations_to_add = []
    base_image_id = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    for i in range(5):
        validation = GPTValidation(
            image_id=base_image_id,
            receipt_id=10 + i,
            query=f"Query {i}",
            response=f"Response {i}",
            timestamp_added=datetime(2021, 1, 1, 0, 0, 0),
        )
        validations_to_add.append(validation)
        client.addGPTValidation(validation)

    # Request only 2 items at a time.
    page1, lek1 = client.listGPTValidations(limit=2)
    assert len(page1) == 2
    assert lek1 is not None

    page2, lek2 = client.listGPTValidations(limit=2, lastEvaluatedKey=lek1)
    # Depending on the data, page2 might have 2 items if there are enough.
    assert len(page2) >= 1
    # If there are still more records, lek2 should be non-null.
    # Otherwise, if page2 returned the last items, then lek2 is None.
    # For our 5 records: page1 (2), page2 (2), page3 (1).
    page3, lek3 = client.listGPTValidations(limit=2, lastEvaluatedKey=lek2)
    total_returned = len(page1) + len(page2) + len(page3)
    assert total_returned >= 5
    # Final page should have no LEK.
    if len(page3) < 2:
        assert lek3 is None


@pytest.mark.integration
def test_listGPTValidations_empty_table(dynamodb_table):
    """
    Tests that listGPTValidations returns an empty list and no LEK when there
    are no GPTValidation records.
    """
    client = DynamoClient(dynamodb_table)
    # Ensure the table is empty for GPTValidation records by using a unique
    # query value if necessary.
    # (In a shared table, you might need to filter by a test-specific
    # image_id.)
    validations, lek = client.listGPTValidations()
    # Assuming your test table is isolated, there should be no GPTValidation
    # records.
    assert len(validations) == 0
    assert lek is None
