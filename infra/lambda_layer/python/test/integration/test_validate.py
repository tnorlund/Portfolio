import json
from os.path import dirname, join
import pytest

from dynamo.data.validate import validate
from dynamo.data.dynamo_client import DynamoClient
from dynamo.entities.gpt_initial_tagging import GPTInitialTagging
from dynamo.entities.gpt_validation import GPTValidation
from dynamo.entities.image import Image
from dynamo.entities.letter import Letter
from dynamo.entities.line import Line
from dynamo.entities.receipt import Receipt
from dynamo.entities.receipt_letter import ReceiptLetter
from dynamo.entities.receipt_line import ReceiptLine
from dynamo.entities.receipt_word import ReceiptWord
from dynamo.entities.receipt_word_tag import ReceiptWordTag
from dynamo.entities.word import Word
from dynamo.entities.word_tag import WordTag

TEST_UUID = "a0c7ea56-fcdc-4866-80dc-1d3509f3f58f"

@pytest.mark.parametrize("expected_results", [TEST_UUID], indirect=True)
def test_validate_receipt(dynamodb_table, expected_results, mocker):
    """
    Test the validate function with mocked DynamoDB and GPT response.
    
    This test:
    1. Sets up mock data in DynamoDB
    2. Mocks the GPT response using pytest-mock
    3. Runs the validate function
    4. Verifies the results match expected values
    """
    # Load initial test data and GPT response
    base_dir = dirname(__file__)
    with open(join(base_dir, "JSON", f"{TEST_UUID}_GPT.json"), "r", encoding="utf-8") as f:
        gpt_response = json.load(f)
    
    with open(join(base_dir, "JSON", f"{TEST_UUID}_RECEIPT.json"), "r", encoding="utf-8") as f:
        receipt_data = json.load(f)

    # Initialize DynamoDB client and populate with test data
    dynamo_client = DynamoClient(dynamodb_table)
    
    # Add all entities to DynamoDB from receipt_data
    images = [Image(**image) for image in receipt_data.get("images")]
    lines = [Line(**line) for line in receipt_data.get("lines")]
    words = [Word(**word) for word in receipt_data.get("words")]
    word_tags = [WordTag(**tag) for tag in receipt_data.get("word_tags")]
    letters = [Letter(**letter) for letter in receipt_data.get("letters")]
    receipts = [Receipt(**receipt) for receipt in receipt_data.get("receipts")]
    receipt_lines = [ReceiptLine(**line) for line in receipt_data.get("receipt_lines")]
    receipt_words = [ReceiptWord(**word) for word in receipt_data.get("receipt_words")]
    receipt_word_tags = [ReceiptWordTag(**tag) for tag in receipt_data.get("receipt_word_tags")]
    receipt_letters = [ReceiptLetter(**letter) for letter in receipt_data.get("receipt_letters")]
    gpt_initial_taggings = [GPTInitialTagging(**tag) for tag in receipt_data.get("gpt_initial_taggings")]
    gpt_validations = [GPTValidation(**validation) for validation in receipt_data.get("gpt_validations")]

    # Add all entities to DynamoDB
    if images:
        dynamo_client.addImages(images)
    if lines:
        dynamo_client.addLines(lines)
    if words:
        dynamo_client.addWords(words)
    if word_tags:
        dynamo_client.addWordTags(word_tags)
    if letters:
        dynamo_client.addLetters(letters)
    if receipts:
        dynamo_client.addReceipts(receipts)
    if receipt_lines:
        dynamo_client.addReceiptLines(receipt_lines)
    if receipt_words:
        dynamo_client.addReceiptWords(receipt_words)
    if receipt_word_tags:
        dynamo_client.addReceiptWordTags(receipt_word_tags)
    if receipt_letters:
        dynamo_client.addReceiptLetters(receipt_letters)

    # Mock the GPT request function
    mock_gpt = mocker.patch("dynamo.data.validate.gpt_request_tagging_validation")
    mock_gpt.return_value = (
        json.loads(gpt_response["parsed_response"]),
        gpt_response["query"],
        gpt_response["response"]
    )

    # Run the validate function
    validate(dynamodb_table, TEST_UUID)

    # Get the updated data from DynamoDB
    (
        _,
        _,
        _,
        updated_word_tags,
        _,
        _,
        _,
        _,
        updated_receipt_word_tags,
        _,
        _,
        gpt_validations,
    ) = dynamo_client.getImageDetails(TEST_UUID)

    # Verify GPT validation record matches expected values
    assert len(gpt_validations) == 1
    validation = gpt_validations[0]
    assert validation.image_id == TEST_UUID
    assert validation.query == gpt_response["query"]
    assert validation.response == gpt_response["response_text"]

    # Verify receipt word tags were updated according to GPT response
    for word_data in gpt_response["content"]:
        matching_tag = next(
            (tag for tag in updated_receipt_word_tags 
             if tag.word_id == word_data["word_id"] 
             and tag.line_id == word_data["line_id"]
             and tag.tag == word_data["initial_tag"]),
            None
        )
        assert matching_tag is not None
        assert matching_tag.validated == (word_data["flag"] == "ok")
        assert matching_tag.gpt_confidence == word_data["confidence"]
        assert matching_tag.flag == word_data["flag"]

    # Verify word tags were updated with validation timestamps
    for receipt_tag in updated_receipt_word_tags:
        matching_word_tag = next(
            (tag for tag in updated_word_tags 
             if tag.word_id == receipt_tag.word_id 
             and tag.line_id == receipt_tag.line_id
             and tag.tag == receipt_tag.tag),
            None
        )
        assert matching_word_tag is not None
        assert matching_word_tag.timestamp_validated is not None
