"""Tests for mark_words_embedded (#990: words stranded in PENDING because
the word poller never flipped embedding_status after a successful delta
save — only the line poller did)."""

from dataclasses import dataclass
from unittest.mock import MagicMock

from receipt_chroma.embedding.openai import mark_words_embedded
from receipt_dynamo.constants import EmbeddingStatus


@dataclass
class FakeWord:
    line_id: int
    word_id: int
    embedding_status: str = EmbeddingStatus.PENDING.value


def _custom_id(image_id, receipt_id, line_id, word_id):
    return f"IMAGE#{image_id}#RECEIPT#{receipt_id}#LINE#{line_id}#WORD#{word_id}"


IMG = "3f52ce10-0000-4000-8000-000000000001"


def _descriptions(words):
    return {IMG: {1: {"words": words}}}


def test_marks_matched_words_success_and_updates_dynamo():
    words = [FakeWord(1, 1), FakeWord(1, 2), FakeWord(2, 1)]
    results = [
        {"custom_id": _custom_id(IMG, 1, 1, 1)},
        {"custom_id": _custom_id(IMG, 1, 2, 1)},
    ]
    client = MagicMock()

    marked = mark_words_embedded(results, _descriptions(words), client)

    assert marked == 2
    assert words[0].embedding_status == EmbeddingStatus.SUCCESS.value
    assert words[2].embedding_status == EmbeddingStatus.SUCCESS.value
    # untouched word stays PENDING
    assert words[1].embedding_status == EmbeddingStatus.PENDING.value
    client.update_receipt_words.assert_called_once()
    (updated,), _ = client.update_receipt_words.call_args
    assert {(w.line_id, w.word_id) for w in updated} == {(1, 1), (2, 1)}


def test_skips_words_missing_from_current_receipt():
    """A word replaced between submit and poll has no row to match."""
    words = [FakeWord(1, 1)]
    results = [
        {"custom_id": _custom_id(IMG, 1, 1, 1)},
        {"custom_id": _custom_id(IMG, 1, 9, 9)},  # no longer exists
        {"custom_id": _custom_id(IMG, 2, 1, 1)},  # receipt filtered out
    ]
    client = MagicMock()

    marked = mark_words_embedded(results, _descriptions(words), client)

    assert marked == 1
    assert words[0].embedding_status == EmbeddingStatus.SUCCESS.value


def test_already_success_words_not_rewritten():
    words = [FakeWord(1, 1, EmbeddingStatus.SUCCESS.value)]
    results = [{"custom_id": _custom_id(IMG, 1, 1, 1)}]
    client = MagicMock()

    marked = mark_words_embedded(results, _descriptions(words), client)

    assert marked == 0
    client.update_receipt_words.assert_not_called()


def test_invalid_custom_ids_skipped():
    words = [FakeWord(1, 1)]
    results = [
        {"custom_id": "GARBAGE"},
        {"no_custom_id": True},
        {"custom_id": _custom_id(IMG, 1, 1, 1) + "#LINE#extra"},
        {"custom_id": _custom_id(IMG, 1, 1, 1)},
    ]
    client = MagicMock()

    marked = mark_words_embedded(results, _descriptions(words), client)

    assert marked == 1


def test_line_custom_id_rejected():
    """Line-format ids (no WORD component) must not match words."""
    words = [FakeWord(1, 1)]
    results = [{"custom_id": f"IMAGE#{IMG}#RECEIPT#1#LINE#1"}]
    client = MagicMock()

    marked = mark_words_embedded(results, _descriptions(words), client)

    assert marked == 0
    client.update_receipt_words.assert_not_called()


def test_updates_are_per_receipt():
    """One update_receipt_words call per receipt, never cross-receipt."""
    img2 = "3f52ce10-0000-4000-8000-000000000002"
    w1, w2 = FakeWord(1, 1), FakeWord(1, 1)
    descriptions = {IMG: {1: {"words": [w1]}}, img2: {2: {"words": [w2]}}}
    results = [
        {"custom_id": _custom_id(IMG, 1, 1, 1)},
        {"custom_id": _custom_id(img2, 2, 1, 1)},
    ]
    client = MagicMock()

    marked = mark_words_embedded(results, descriptions, client)

    assert marked == 2
    assert client.update_receipt_words.call_count == 2
