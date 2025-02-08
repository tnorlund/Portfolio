# test__gpt.py

import json
import boto3
import pytest
import os
import requests
from typing import Dict, Literal, Tuple, Union
from dynamo import (
    DynamoClient,
    Image,
    Line,
    Word,
    WordTag,
    Letter,
    Receipt,
    ReceiptLine,
    ReceiptWord,
    ReceiptWordTag,
    ReceiptLetter,
)
from dynamo.data._gpt import _llm_prompt_initial_tagging, _validate_gpt_response_initial_tagging, gpt_request_initial_tagging


@pytest.mark.unit
def test_gpt_reduce_precision():
    from dynamo.data._gpt import _reduce_precision

    assert _reduce_precision((1.23456789, 2.3456789), 4) == (1.2346, 2.3457)
    assert _reduce_precision((1.23456789, 2.3456789), 2) == (1.23, 2.35)
    assert _reduce_precision((1.23456789, 2.3456789), 0) == (1.0, 2.0)


@pytest.mark.unit
@pytest.mark.parametrize(
    "expected_results", ["2f05267d-86df-42b3-8a14-e29c5ea567b3"], indirect=True
)
def test_gpt_llm_prompt_initial_tagging(expected_results):
    # Unpack the expected results tuple:
    _, _, _, _, _, receipts, _, receipt_words, _, _ = expected_results

    # For this test, assume the first receipt is the one to test.
    receipt = receipts[0]

    # Call your function with receipt and its words (or however your API expects it).
    prompt = _llm_prompt_initial_tagging(receipt, receipt_words)

    # Assert that the prompt starts with the expected introductory text.
    assert prompt.startswith(
        "\nYou are a helpful assistant that extracts structured data from a receipt."
    ), "Prompt does not start with the expected introductory text."

    # Assert that the prompt contains instructions for outputting valid JSON.
    assert (
        "Output must be valid JSON" in prompt
    ), "Prompt does not specify that output must be valid JSON."
    assert (
        "Return only this JSON structure. Nothing else." in prompt
    ), "Prompt does not instruct to return only the JSON structure."

    # Assert that all required field names are present in the prompt.
    for key in [
        "store_name",
        "date",
        "time",
        "phone_number",
        "total_amount",
        "taxes",
        "address",
        "line_item",
        "line_item_name",
        "line_item_price",
    ]:
        assert key in prompt, f"Expected key '{key}' not found in prompt."

    # Assert that the sample JSON code fence is present.
    assert (
        "```json" in prompt
    ), "Prompt does not contain the expected JSON code fence marker."

    # Optionally, you can check that a sample output snippet is present.
    expected_snippet = '"store_name": [{"l": 0, "w": 0}, {"l": 0, "w": 1}]'
    assert (
        expected_snippet in prompt
    ), "Prompt does not contain the expected sample JSON snippet."


# DummyResponse simulates a requests.Response object.
class DummyResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code
        self.text = "string for testing purposes"

    def raise_for_status(self):
        if self.status_code != 200:
            raise Exception("HTTP error")

    def json(self):
        return self._json

# A simple dummy class to simulate receipt word objects.
class DummyWord:
    def __init__(self, text, centroid, line_id, word_id):
        self.text = text
        self._centroid = centroid
        self.line_id = line_id
        self.word_id = word_id

    def calculate_centroid(self):
        return self._centroid

# A simple dummy class to simulate receipt objects.
class DummyReceipt(dict):
    def __init__(self, data):
        super().__init__(data)

@pytest.mark.integration
def test_gpt_validate_missing_choices():
    resp = DummyResponse({})
    with pytest.raises(ValueError, match="The response does not contain any choices."):
        _validate_gpt_response_initial_tagging(resp)

@pytest.mark.integration
def test_gpt_validate_empty_choices():
    resp = DummyResponse({"choices": []})
    with pytest.raises(ValueError, match="The response does not contain any choices."):
        _validate_gpt_response_initial_tagging(resp)

@pytest.mark.integration
def test_gpt_validate_non_list_choices():
    resp = DummyResponse({"choices": {"role": "assistant", "message": {"content": "{}"}}})
    with pytest.raises(ValueError, match="The response choices are not a list."):
        _validate_gpt_response_initial_tagging(resp)

@pytest.mark.integration
def test_gpt_validate_missing_message():
    resp = DummyResponse({
        "choices": [
            {"role": "assistant"},  # missing message
            {"role": "assistant", "message": {"content": "{}"}},
        ]
    })
    with pytest.raises(ValueError, match="The response does not contain a message."):
        _validate_gpt_response_initial_tagging(resp)

@pytest.mark.integration
def test_gpt_validate_missing_content():
    resp = DummyResponse({
        "choices": [
            {"role": "assistant", "message": {}},
            {"role": "assistant", "message": {"content": "{}"}},
        ]
    })
    with pytest.raises(ValueError, match="The response message does not contain content."):
        _validate_gpt_response_initial_tagging(resp)

@pytest.mark.integration
def test_gpt_validate_empty_content():
    resp = DummyResponse({
        "choices": [
            {"role": "assistant", "message": {"content": ""}},
            {"role": "assistant", "message": {"content": "{}"}},
        ]
    })
    with pytest.raises(ValueError, match="The response message content is empty."):
        _validate_gpt_response_initial_tagging(resp)

@pytest.mark.integration
def test_gpt_validate_invalid_json():
    resp = DummyResponse({
        "choices": [
            {"role": "assistant", "message": {"content": "not a json"}},
            {"role": "assistant", "message": {"content": "{}"}},
        ]
    })
    with pytest.raises(ValueError, match="The response message content is not valid JSON."):
        _validate_gpt_response_initial_tagging(resp)

@pytest.mark.integration
def test_gpt_validate_non_string_keys(monkeypatch):
    # Patch the loads used in the module (_validate_gpt_response_initial_tagging uses loads from json).
    # Since _gpt.py imported loads directly, we must patch that binding.
    import dynamo.data._gpt as gpt_module
    def fake_loads(s):
        return {1: [{"l": 1, "w": 2}]}  # non-string key
    monkeypatch.setattr(gpt_module, "loads", fake_loads)
    resp = DummyResponse({
        "choices": [
            {"role": "assistant", "message": {"content": "ignored"}},
            {"role": "assistant", "message": {"content": "{}"}},
        ]
    })
    with pytest.raises(ValueError, match="The response message content keys are not strings."):
        _validate_gpt_response_initial_tagging(resp)

@pytest.mark.integration
def test_gpt_validate_missing_l_or_w():
    content = {"store_name": [{"l": 0, "w": 0}], "date": [{"l": 1}]}  # missing "w" in date
    resp = DummyResponse({
        "choices": [
            {"role": "assistant", "message": {"content": json.dumps(content)}},
            {"role": "assistant", "message": {"content": "{}"}},
        ]
    })
    with pytest.raises(ValueError, match="The response message content values do not contain 'l' and 'w'."):
        _validate_gpt_response_initial_tagging(resp)

@pytest.mark.integration
def test_gpt_validate_value_dict_missing_key():
    # Here, "store_name" is a dict missing the "w" key.
    content = {"store_name": {"l": 0}}
    resp = DummyResponse({
        "choices": [
            {
                "role": "assistant",
                "message": {"content": json.dumps(content)}
            },
            {
                "role": "assistant",
                "message": {"content": "{}"}
            }
        ]
    })
    with pytest.raises(ValueError, match="The response message content values do not contain 'l' and 'w'."):
        _validate_gpt_response_initial_tagging(resp)

@pytest.mark.integration
def test_gpt_validate_value_wrong_type():
    # Here, "store_name" is a string instead of a list or dict.
    content = {"store_name": "invalid"}
    resp = DummyResponse({
        "choices": [
            {
                "role": "assistant",
                "message": {"content": json.dumps(content)}
            },
            {
                "role": "assistant",
                "message": {"content": "{}"}
            }
        ]
    })
    with pytest.raises(ValueError, match="The response message content values must be a list or a dict."):
        _validate_gpt_response_initial_tagging(resp)

@pytest.mark.integration
def test_gpt_validate_valid_response():
    content = {"store_name": [{"l": 0, "w": 0}], "date": [{"l": 1, "w": 2}]}
    resp = DummyResponse({
        "choices": [
            {"role": "assistant", "message": {"content": json.dumps(content)}},
            {"role": "assistant", "message": {"content": "{}"}},
        ]
    })
    result = _validate_gpt_response_initial_tagging(resp)
    assert result == content

@pytest.mark.integration
def test_gpt_request_initial_tagging_missing_api_key(monkeypatch):
    # Ensure the environment variable is not set.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    # Create a dummy receipt and empty receipt words.
    dummy_receipt = DummyReceipt({"receipt_id": 123})
    dummy_words = []  # Can be an empty list; the content doesn't matter here.

    # Calling gpt_request_initial_tagging without an API key should raise the expected ValueError.
    with pytest.raises(ValueError, match="The OPENAI_API_KEY environment variable is not set."):
        gpt_request_initial_tagging(dummy_receipt, dummy_words)

@pytest.mark.integration
def test_gpt_request(monkeypatch):
    # Define the valid content that we expect _validate_gpt_response_initial_tagging to return.
    valid_content = {
        "store_name": [{"l": 0, "w": 0}, {"l": 0, "w": 1}],
        "date": [{"l": 1, "w": 2}],
        "time": [],
        "phone_number": [],
        "total_amount": [],
        "taxes": [],
        "address": [],
        "line_item": [{"l": 5, "w": 1}],
        "line_item_name": [{"l": 5, "w": 2}],
        "line_item_price": [{"l": 5, "w": 3}],
    }

    # Create a fake post function that accepts arbitrary keyword arguments.
    def fake_post(url, headers, **kwargs):
        # Here you could inspect kwargs["json"] if necessary.
        response_json = {
            "choices": [
                {
                    "role": "assistant",
                    "message": {"content": json.dumps(valid_content)},
                },
                {"role": "assistant", "message": {"content": "{}"}},
            ]
        }
        return DummyResponse(response_json)

    # Patch requests.post to use our fake_post.
    monkeypatch.setattr(requests, "post", fake_post)

    # Create a dummy receipt and a list of dummy receipt words.
    receipt = {"receipt_id": 123}  # _llm_prompt_initial_tagging() uses dict(receipt)
    receipt_words = [
        DummyWord("STORE", (0.1, 0.2), 0, 0),
        DummyWord("NAME", (0.3, 0.4), 0, 1),
        DummyWord("2025-01-01", (0.5, 0.6), 1, 2),
    ]

    # Call gpt_request_initial_tagging() with a dummy API key.
    formatted_response, query_sent, response_text = gpt_request_initial_tagging(receipt, receipt_words, gpt_api_key="dummy_key")

    # Assert that the returned dict equals the valid content we defined.
    assert formatted_response == valid_content
    assert query_sent.startswith("\nYou are a helpful assistant that extracts structured data from a receipt."
    ), "Prompt does not start with the expected introductory text."
    assert response_text == "string for testing purposes"