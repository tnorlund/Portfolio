# test__gpt.py

import json
import pytest
import requests
from receipt_dynamo.data._gpt import (_llm_prompt_initial_tagging,
    _llm_prompt_tagging_validation,
    _reduce_precision,
    _validate_gpt_response_initial_tagging,
    _validate_gpt_response_tagging_validation,
    gpt_request_initial_tagging,
    gpt_request_tagging_validation,)

# --- Dummy Classes for Testing ---


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


# Updated DummyWord now includes receipt_id, tags, and bounding_box.
class DummyWord:
    def __init__(self,
        text,
        centroid,
        line_id,
        word_id,
        receipt_id,
        tags=None,
        bounding_box=None,):
        self.text = text
        self._centroid = centroid
        self.line_id = line_id
        self.word_id = word_id
        self.receipt_id = receipt_id
        self.tags = tags if tags is not None else []
        self.bounding_box = (bounding_box
            if bounding_box is not None
            else {"x": 0, "y": 0, "width": 0, "height": 0})

    def calculate_centroid(self):
        return self._centroid


# A simple dummy class to simulate receipt objects.
# Now supports attribute access for 'width' and 'height'.
class DummyReceipt(dict):
    def __init__(self, data):
        super().__init__(data)
        self.receipt_id = data.get("receipt_id")
        self.width = data.get("width")
        self.height = data.get("height")


# A dummy for a receipt line.
class DummyLine:
    def __init__(self, line_id, text, bounding_box, receipt_id):
        self.line_id = line_id
        self.text = text
        self.bounding_box = bounding_box
        self.receipt_id = receipt_id


# A dummy for a receipt word tag.
class DummyWordTag:
    def __init__(self, tag, validated, line_id, word_id, receipt_id):
        self.tag = tag
        self.validated = validated
        self.line_id = line_id
        self.word_id = word_id
        self.receipt_id = receipt_id


# --- Tests for _reduce_precision ---
@pytest.mark.unit
def test_gpt_reduce_precision():
    assert _reduce_precision((1.23456789, 2.3456789), 4) == (1.2346, 2.3457)
    assert _reduce_precision((1.23456789, 2.3456789), 2) == (1.23, 2.35)
    assert _reduce_precision((1.23456789, 2.3456789), 0) == (1.0, 2.0)


# --- Tests for _llm_prompt_initial_tagging ---
@pytest.mark.unit
def test_gpt_llm_prompt_initial_tagging():
    dummy_receipt = DummyReceipt({"receipt_id": 123, "width": 100, "height": 50})
    # For initial tagging, tags are not required.
    dummy_word = DummyWord("STORE", (0.1, 0.2), 0, 0, 123)
    prompt = _llm_prompt_initial_tagging(dummy_receipt, [dummy_word])
    assert prompt.startswith("\nYou are a helpful assistant that extracts structured data from a "
        "receipt."), "Prompt does not start with the expected introductory text."
    assert "Output must be valid JSON" in prompt
    assert "Return only this JSON structure. Nothing else." in prompt
    for key in ["store_name",
        "date",
        "time",
        "phone_number",
        "total_amount",
        "taxes",
        "address",
        "line_item",
        "line_item_name",
        "line_item_price",]:
        assert key in prompt, f"Expected key '{key}' not found in prompt."
    assert "```json" in prompt
    expected_snippet = '"store_name": [{"l": 0, "w": 0}, {"l": 0, "w": 1}]'
    assert expected_snippet in prompt


# --- Tests for _validate_gpt_response_initial_tagging ---
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
    resp = DummyResponse({"choices": [{"role": "assistant"},  # missing message
                {"role": "assistant", "message": {"content": "{}"}},]})
    with pytest.raises(ValueError, match="The response does not contain a message."):
        _validate_gpt_response_initial_tagging(resp)


@pytest.mark.integration
def test_gpt_validate_missing_content():
    resp = DummyResponse({"choices": [{"role": "assistant", "message": {}},
                {"role": "assistant", "message": {"content": "{}"}},]})
    with pytest.raises(ValueError, match="The response message does not contain content."):
        _validate_gpt_response_initial_tagging(resp)


@pytest.mark.integration
def test_gpt_validate_empty_content():
    resp = DummyResponse({"choices": [{"role": "assistant", "message": {"content": ""}},
                {"role": "assistant", "message": {"content": "{}"}},]})
    with pytest.raises(ValueError, match="The response message content is empty."):
        _validate_gpt_response_initial_tagging(resp)


@pytest.mark.integration
def test_gpt_validate_invalid_json():
    resp = DummyResponse({"choices": [{"role": "assistant", "message": {"content": "not a json"}},
                {"role": "assistant", "message": {"content": "{}"}},]})
    with pytest.raises(ValueError, match="The response message content is not valid JSON."):
        _validate_gpt_response_initial_tagging(resp)


@pytest.mark.integration
def test_gpt_validate_non_string_keys(monkeypatch):
    import receipt_dynamo.data._gpt as gpt_module

    def fake_loads(s):
        return {1: [{"l": 1, "w": 2}]}  # non-string key

    monkeypatch.setattr(gpt_module, "loads", fake_loads)
    resp = DummyResponse({"choices": [{"role": "assistant", "message": {"content": "ignored"}},
                {"role": "assistant", "message": {"content": "{}"}},]})
    with pytest.raises(ValueError, match="The response message content keys are not strings."):
        _validate_gpt_response_initial_tagging(resp)


@pytest.mark.integration
def test_gpt_validate_missing_l_or_w():
    content = {"store_name": [{"l": 0, "w": 0}],
        "date": [{"l": 1}],}  # missing "w" in date
    resp = DummyResponse({"choices": [{"role": "assistant",
                    "message": {"content": json.dumps(content)},},
                {"role": "assistant", "message": {"content": "{}"}},]})
    with pytest.raises(ValueError,
        match="The response message content values do not contain "
        "'l' and 'w'.",):
        _validate_gpt_response_initial_tagging(resp)


@pytest.mark.integration
def test_gpt_validate_value_dict_missing_key():
    content = {"store_name": {"l": 0}}
    resp = DummyResponse({"choices": [{"role": "assistant",
                    "message": {"content": json.dumps(content)},},
                {"role": "assistant", "message": {"content": "{}"}},]})
    with pytest.raises(ValueError,
        match="The response message content values do not contain "
        "'l' and 'w'.",):
        _validate_gpt_response_initial_tagging(resp)


@pytest.mark.integration
def test_gpt_validate_value_wrong_type():
    content = {"store_name": "invalid"}
    resp = DummyResponse({"choices": [{"role": "assistant",
                    "message": {"content": json.dumps(content)},},
                {"role": "assistant", "message": {"content": "{}"}},]})
    with pytest.raises(ValueError,
        match="The response message content values must be a list or a dict.",):
        _validate_gpt_response_initial_tagging(resp)


@pytest.mark.integration
def test_gpt_validate_valid_response():
    content = {"store_name": [{"l": 0, "w": 0}], "date": [{"l": 1, "w": 2}]}
    resp = DummyResponse({"choices": [{"role": "assistant",
                    "message": {"content": json.dumps(content)},},
                {"role": "assistant", "message": {"content": "{}"}},]})
    result = _validate_gpt_response_initial_tagging(resp)
    assert result == content


# --- Tests for _validate_gpt_response_tagging_validation ---
@pytest.mark.integration
def test_gpt_validate_tagging_validation_missing_choices():
    resp = DummyResponse({})
    with pytest.raises(ValueError, match="The response does not contain any choices."):
        _validate_gpt_response_tagging_validation(resp)


@pytest.mark.integration
def test_gpt_validate_tagging_validation_empty_choices():
    resp = DummyResponse({"choices": []})
    with pytest.raises(ValueError, match="The response does not contain any choices."):
        _validate_gpt_response_tagging_validation(resp)


@pytest.mark.integration
def test_gpt_validate_tagging_validation_non_list_choices():
    resp = DummyResponse({"choices": {"role": "assistant", "message": {"content": "[]"}}})
    with pytest.raises(ValueError, match="The response choices are not a list."):
        _validate_gpt_response_tagging_validation(resp)


@pytest.mark.integration
def test_gpt_validate_tagging_validation_missing_message():
    resp = DummyResponse({"choices": [{"role": "assistant"},
                {"role": "assistant", "message": {"content": "[]"}},]})
    with pytest.raises(ValueError, match="The response does not contain a message."):
        _validate_gpt_response_tagging_validation(resp)


@pytest.mark.integration
def test_gpt_validate_tagging_validation_missing_content():
    resp = DummyResponse({"choices": [{"role": "assistant", "message": {}},
                {"role": "assistant", "message": {"content": "[]"}},]})
    with pytest.raises(ValueError, match="The response message does not contain content."):
        _validate_gpt_response_tagging_validation(resp)


@pytest.mark.integration
def test_gpt_validate_tagging_validation_empty_content():
    resp = DummyResponse({"choices": [{"role": "assistant", "message": {"content": ""}},
                {"role": "assistant", "message": {"content": "[]"}},]})
    with pytest.raises(ValueError, match="The response message content is empty."):
        _validate_gpt_response_tagging_validation(resp)


@pytest.mark.integration
def test_gpt_validate_tagging_validation_invalid_json():
    resp = DummyResponse({"choices": [{"role": "assistant", "message": {"content": "not a json"}},
                {"role": "assistant", "message": {"content": "[]"}},]})
    with pytest.raises(ValueError, match="The response message content is not valid JSON."):
        _validate_gpt_response_tagging_validation(resp)


@pytest.mark.integration
def test_gpt_validate_tagging_validation_not_list():
    # _validate_gpt_response_tagging_validation expects the parsed JSON to be
    # a list.
    content = {"line_id": 1,
        "word_id": 10,
        "initial_tag": "foo",
        "revised_tag": "bar",
        "confidence": 5,
        "flag": "ok",}
    resp = DummyResponse({"choices": [{"role": "assistant",
                    "message": {"content": json.dumps(content)},},
                {"role": "assistant", "message": {"content": "[]"}},]})
    with pytest.raises(ValueError, match="The response message content is not a list."):
        _validate_gpt_response_tagging_validation(resp)


@pytest.mark.integration
def test_gpt_validate_tagging_validation_item_not_dict():
    content = [123]
    resp = DummyResponse({"choices": [{"role": "assistant",
                    "message": {"content": json.dumps(content)},},
                {"role": "assistant", "message": {"content": "[]"}},]})
    with pytest.raises(ValueError, match="The response items are not dictionaries."):
        _validate_gpt_response_tagging_validation(resp)


@pytest.mark.integration
def test_gpt_validate_tagging_validation_missing_keys():
    # Provide an item missing one of the required keys (e.g., missing "flag")
    content = [{"line_id": 1,
            "word_id": 10,
            "initial_tag": "foo",
            "revised_tag": "bar",
            "confidence": 5,}]
    resp = DummyResponse({"choices": [{"role": "assistant",
                    "message": {"content": json.dumps(content)},},
                {"role": "assistant", "message": {"content": "[]"}},]})
    with pytest.raises(ValueError,
        match="The response items do not contain the expected fields.",):
        _validate_gpt_response_tagging_validation(resp)


@pytest.mark.integration
def test_gpt_validate_tagging_validation_valid_response():
    valid_content = [{"line_id": 1,
            "word_id": 10,
            "initial_tag": "foo",
            "revised_tag": "foo",
            "confidence": 5,
            "flag": "ok",}]
    resp = DummyResponse({"choices": [{"role": "assistant",
                    "message": {"content": json.dumps(valid_content)},},
                {"role": "assistant", "message": {"content": "[]"}},]})
    result = _validate_gpt_response_tagging_validation(resp)
    assert result == valid_content


# --- Tests for gpt_request_initial_tagging ---
@pytest.mark.integration
def test_gpt_request_initial_tagging_missing_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    dummy_receipt = DummyReceipt({"receipt_id": 123})
    dummy_words = []  # content not required for this test
    with pytest.raises(ValueError, match="The OPENAI_API_KEY environment variable is not set."):
        gpt_request_initial_tagging(dummy_receipt, dummy_words)


@pytest.mark.integration
def test_gpt_request_initial_tagging(monkeypatch):
    valid_content = {"store_name": [{"l": 0, "w": 0}, {"l": 0, "w": 1}],
        "date": [{"l": 1, "w": 2}],
        "time": [],
        "phone_number": [],
        "total_amount": [],
        "taxes": [],
        "address": [],
        "line_item": [{"l": 5, "w": 1}],
        "line_item_name": [{"l": 5, "w": 2}],
        "line_item_price": [{"l": 5, "w": 3}],}

    def fake_post(url, headers, **kwargs):
        response_json = {"choices": [{"role": "assistant",
                    "message": {"content": json.dumps(valid_content)},},
                {"role": "assistant", "message": {"content": "{}"}},]}
        return DummyResponse(response_json)

    monkeypatch.setattr(requests, "post", fake_post)
    dummy_receipt = DummyReceipt({"receipt_id": 123, "width": 100, "height": 50})
    dummy_word = DummyWord("STORE", (0.1, 0.2), 0, 0, 123)
    formatted_response, query_sent, response_text = (gpt_request_initial_tagging(dummy_receipt, [dummy_word], gpt_api_key="dummy_key"))
    assert formatted_response == valid_content
    assert query_sent.startswith("\nYou are a helpful assistant that extracts structured data from a "
        "receipt.")
    assert response_text == "string for testing purposes"


# --- Tests for gpt_request_tagging_validation ---
@pytest.mark.integration
def test_gpt_request_tagging_validation_missing_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    dummy_receipt = DummyReceipt({"receipt_id": 123, "width": 100, "height": 50})
    dummy_line = DummyLine(0, "Test line", {"x": 0, "y": 0, "width": 100, "height": 20}, 123)
    dummy_word = DummyWord("Test", (0.1, 0.2), 0, 0, 123)
    dummy_word_tag = DummyWordTag("tag", True, 0, 0, 123)
    with pytest.raises(ValueError, match="The OPENAI_API_KEY environment variable is not set."):
        gpt_request_tagging_validation(dummy_receipt, [dummy_line], [dummy_word], [dummy_word_tag])


@pytest.mark.integration
def test_gpt_request_tagging_validation(monkeypatch):
    valid_content = [{"line_id": 1,
            "word_id": 10,
            "initial_tag": "tag1",
            "revised_tag": "tag1",
            "confidence": 5,
            "flag": "ok",}]

    def fake_post(url, headers, **kwargs):
        response_json = {"choices": [{"role": "assistant",
                    "message": {"content": json.dumps(valid_content)},},
                {"role": "assistant", "message": {"content": "[]"}},]}
        return DummyResponse(response_json)

    monkeypatch.setattr(requests, "post", fake_post)
    dummy_receipt = DummyReceipt({"receipt_id": 123, "width": 100, "height": 50})
    dummy_line = DummyLine(0, "Test line", {"x": 0, "y": 0, "width": 100, "height": 20}, 123)
    # Provide a non-empty tags list so the word is included in the prompt.
    dummy_word = DummyWord("Test",
        (0.1, 0.2),
        0,
        0,
        123,
        tags=[{"tag": "tag", "validated": True}],
        bounding_box={"x": 0, "y": 0, "width": 50, "height": 20},)
    dummy_word_tag = DummyWordTag("tag", True, 0, 0, 123)
    formatted_response, query_sent, response_text = (gpt_request_tagging_validation(dummy_receipt,
            [dummy_line],
            [dummy_word],
            [dummy_word_tag],
            gpt_api_key="dummy_key",))
    assert formatted_response == valid_content
    assert ("You are provided with JSON data that conforms to the "
        "following structure:" in query_sent)
    assert response_text == "string for testing purposes"


# --- Test for _llm_prompt_tagging_validation ---
@pytest.mark.unit
def test_llm_prompt_tagging_validation():
    dummy_receipt = DummyReceipt({"receipt_id": 123, "width": 100, "height": 50})
    dummy_line = DummyLine(0, "Test line", {"x": 0, "y": 0, "width": 100, "height": 20}, 123)
    dummy_word = DummyWord("Test",
        (0.1, 0.2),
        0,
        0,
        123,
        tags=[{"tag": "tag", "validated": True}],
        bounding_box={"x": 0, "y": 0, "width": 10, "height": 10},)
    dummy_word_tag = DummyWordTag("tag", True, 0, 0, 123)
    prompt = _llm_prompt_tagging_validation(dummy_receipt, [dummy_line], [dummy_word], [dummy_word_tag])
    assert "```json" in prompt
    assert '"receipt":' in prompt
    assert '"lines":' in prompt
    assert "line_id" in prompt
    assert "tagged_words" in prompt
