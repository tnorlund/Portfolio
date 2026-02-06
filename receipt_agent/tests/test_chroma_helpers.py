"""Unit tests for targeted Chroma evidence helpers."""

import pytest

from receipt_agent.utils.chroma_helpers import (
    LabelEvidence,
    format_label_evidence_for_prompt,
    query_label_evidence,
)


class FakeChromaClient:
    """Minimal fake Chroma client for label evidence tests."""

    def get(self, collection_name, ids=None, where=None, include=None):
        if collection_name == "words" and ids:
            return {"embeddings": [[0.1, 0.2, 0.3]]}

        if collection_name == "lines" and ids:
            # Force row-based fallback path for line lookup.
            return {"embeddings": []}

        if collection_name == "lines" and where:
            return {
                "ids": ["IMAGE#img1#RECEIPT#00001#LINE#00005"],
                "metadatas": [
                    {
                        "image_id": "img1",
                        "receipt_id": 1,
                        "line_id": 5,
                        "row_line_ids": "[2, 5]",
                    }
                ],
                "embeddings": [[0.9, 0.8, 0.7]],
            }

        return {"embeddings": []}

    def query(
        self,
        collection_name,
        query_embeddings,
        n_results,
        where,
        include,
    ):
        del query_embeddings, n_results, include

        label_value = where.get("label_TOTAL")
        if collection_name == "words" and label_value is True:
            return {
                "metadatas": [
                    [
                        {
                            "image_id": "img2",
                            "receipt_id": 2,
                            "line_id": 4,
                            "word_id": 1,
                            "text": "12.99",
                            "merchant_name": "Test Merchant",
                            "x": 0.92,
                            "y": 0.10,
                            "left": "TOTAL",
                            "right": "<EDGE>",
                        }
                    ]
                ],
                "distances": [[0.2]],
            }
        if collection_name == "words" and label_value is False:
            return {
                "metadatas": [
                    [
                        {
                            "image_id": "img3",
                            "receipt_id": 3,
                            "line_id": 2,
                            "word_id": 3,
                            "text": "12.99",
                            "merchant_name": "Other Merchant",
                            "x": 0.2,
                            "y": 0.9,
                            "left": "ITEM",
                            "right": "A",
                        }
                    ]
                ],
                "distances": [[0.4]],
            }
        if collection_name == "lines" and label_value is True:
            return {
                "metadatas": [
                    [
                        {
                            "image_id": "img4",
                            "receipt_id": 4,
                            "line_id": 9,
                            "text": "GRAND TOTAL 12.99",
                            "merchant_name": "Test Merchant",
                            "x": 0.9,
                            "y": 0.08,
                            "prev_line": "SUBTOTAL 10.99",
                            "next_line": "THANK YOU",
                        }
                    ]
                ],
                "distances": [[0.3]],
            }
        if collection_name == "lines" and label_value is False:
            return {
                "metadatas": [
                    [
                        {
                            "image_id": "img5",
                            "receipt_id": 5,
                            "line_id": 8,
                            "text": "ITEM 12.99",
                            "merchant_name": "Other Merchant",
                            "x": 0.1,
                            "y": 0.7,
                            "prev_line": "ITEM A",
                            "next_line": "ITEM C",
                        }
                    ]
                ],
                "distances": [[0.5]],
            }
        return {"metadatas": [[]], "distances": [[]]}


@pytest.mark.unit
def test_query_label_evidence_combines_words_and_lines():
    """Evidence query should include both words and lines collections."""
    evidence = query_label_evidence(
        chroma_client=FakeChromaClient(),
        image_id="img1",
        receipt_id=1,
        line_id=2,
        word_id=3,
        target_label="TOTAL",
        target_merchant="Test Merchant",
        include_collections=("words", "lines"),
        n_results_per_query=5,
        min_similarity=0.6,
    )

    assert len(evidence) == 4
    sources = {item.evidence_source for item in evidence}
    assert sources == {"words", "lines"}
    assert any(item.label_valid for item in evidence)
    assert any(not item.label_valid for item in evidence)


@pytest.mark.unit
def test_format_label_evidence_for_prompt_includes_source_tags():
    """Prompt formatting should tag evidence origin as WORD or LINE."""
    evidence = [
        LabelEvidence(
            word_text="12.99",
            similarity_score=0.91,
            chroma_id="word-id",
            label_valid=True,
            merchant_name="Test Merchant",
            is_same_merchant=True,
            position_description="bottom-right",
            left_neighbor="TOTAL",
            right_neighbor="<EDGE>",
            evidence_source="words",
        ),
        LabelEvidence(
            word_text="GRAND TOTAL 12.99",
            similarity_score=0.85,
            chroma_id="line-id",
            label_valid=False,
            merchant_name="Other Merchant",
            is_same_merchant=False,
            position_description="bottom-right",
            left_neighbor="SUBTOTAL",
            right_neighbor="THANK YOU",
            evidence_source="lines",
        ),
    ]

    prompt_text = format_label_evidence_for_prompt(
        evidence=evidence,
        target_label="TOTAL",
    )

    assert "[WORD]" in prompt_text
    assert "[LINE]" in prompt_text
