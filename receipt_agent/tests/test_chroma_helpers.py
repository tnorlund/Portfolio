"""Unit tests for targeted Chroma evidence helpers."""

import pytest

from receipt_agent.utils.chroma_helpers import (
    LabelEvidence,
    _discover_and_evaluate_unlabeled,
    _discover_candidate_label,
    build_consensus_auto_review,
    chroma_resolve_words,
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


# ---------------------------------------------------------------------------
# Helpers for unlabeled-word consensus tests
# ---------------------------------------------------------------------------

def _make_word_dict(
    image_id="img1",
    receipt_id=1,
    line_id=2,
    word_id=3,
    current_label="",
    word_text="12.99",
):
    return {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "line_id": line_id,
        "word_id": word_id,
        "current_label": current_label,
        "word_text": word_text,
    }


def _word_chroma_id(image_id="img1", receipt_id=1, line_id=2, word_id=3):
    return (
        f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}"
        f"#LINE#{line_id:05d}#WORD#{word_id:05d}"
    )


class UnfilteredFakeChromaClient:
    """Fake Chroma client that supports both filtered and unfiltered queries.

    ``get`` returns an embedding for the query word.
    ``query`` (unfiltered — no ``where``) returns neighbor metadata with
    ``valid_labels_array`` populated so that ``_discover_candidate_label``
    and ``_compute_top_k_word_consensus`` can work.
    """

    def __init__(
        self,
        get_embedding=None,
        query_neighbors=None,
    ):
        self._get_embedding = get_embedding or [0.1, 0.2, 0.3]
        self._query_neighbors = query_neighbors or []

    def get(self, collection_name, ids=None, where=None, include=None):
        if collection_name == "words" and ids:
            if self._get_embedding is None:
                return {"embeddings": []}
            return {"embeddings": [self._get_embedding]}
        return {"embeddings": []}

    def query(self, collection_name, query_embeddings, n_results, include, **kwargs):
        metadatas = []
        distances = []
        for neighbor in self._query_neighbors:
            metadatas.append(neighbor["metadata"])
            distances.append(neighbor["distance"])
        return {
            "metadatas": [metadatas],
            "distances": [distances],
        }


# ---------------------------------------------------------------------------
# _discover_candidate_label tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_discover_candidate_label_returns_dominant_label():
    """Should return the most frequent label across neighbors."""
    neighbors = [
        {
            "metadata": {
                "image_id": "img2", "receipt_id": 2, "line_id": 1, "word_id": i,
                "text": "item", "valid_labels_array": ["LINE_TOTAL"],
            },
            "distance": 0.1,
        }
        for i in range(5)
    ]
    # Add a couple with a different label
    neighbors.append({
        "metadata": {
            "image_id": "img3", "receipt_id": 3, "line_id": 1, "word_id": 1,
            "text": "tax", "valid_labels_array": ["TAX"],
        },
        "distance": 0.2,
    })

    result = _discover_candidate_label(
        chroma_client=UnfilteredFakeChromaClient(query_neighbors=neighbors),
        query_embedding=[0.1, 0.2, 0.3],
        exclude_chroma_id=_word_chroma_id(),
    )
    assert result == "LINE_TOTAL"


@pytest.mark.unit
def test_discover_candidate_label_returns_none_when_no_dominant():
    """Should return None when all labels appear only once."""
    neighbors = [
        {
            "metadata": {
                "image_id": "img2", "receipt_id": 2, "line_id": 1, "word_id": i,
                "text": f"word{i}", "valid_labels_array": [f"LABEL_{i}"],
            },
            "distance": 0.1,
        }
        for i in range(3)
    ]

    result = _discover_candidate_label(
        chroma_client=UnfilteredFakeChromaClient(query_neighbors=neighbors),
        query_embedding=[0.1, 0.2, 0.3],
        exclude_chroma_id=_word_chroma_id(),
    )
    assert result is None


@pytest.mark.unit
def test_discover_candidate_label_returns_none_when_no_neighbors():
    """Should return None with empty neighbor list."""
    result = _discover_candidate_label(
        chroma_client=UnfilteredFakeChromaClient(query_neighbors=[]),
        query_embedding=[0.1, 0.2, 0.3],
        exclude_chroma_id=_word_chroma_id(),
    )
    assert result is None


@pytest.mark.unit
def test_discover_candidate_label_skips_unlabeled_labels():
    """Should not count O, NONE, UNLABELED, etc. as candidate labels."""
    neighbors = [
        {
            "metadata": {
                "image_id": "img2", "receipt_id": 2, "line_id": 1, "word_id": i,
                "text": "word", "valid_labels_array": ["O"],
            },
            "distance": 0.1,
        }
        for i in range(5)
    ]

    result = _discover_candidate_label(
        chroma_client=UnfilteredFakeChromaClient(query_neighbors=neighbors),
        query_embedding=[0.1, 0.2, 0.3],
        exclude_chroma_id=_word_chroma_id(),
    )
    assert result is None


@pytest.mark.unit
def test_discover_candidate_label_excludes_self():
    """Should skip the query word's own chroma ID."""
    self_id = _word_chroma_id(image_id="img2", receipt_id=2, line_id=1, word_id=1)
    neighbors = [
        {
            "metadata": {
                "image_id": "img2", "receipt_id": 2, "line_id": 1, "word_id": 1,
                "text": "self", "valid_labels_array": ["TOTAL"],
            },
            "distance": 0.05,
        },
        {
            "metadata": {
                "image_id": "img3", "receipt_id": 3, "line_id": 1, "word_id": 1,
                "text": "other1", "valid_labels_array": ["TOTAL"],
            },
            "distance": 0.1,
        },
        {
            "metadata": {
                "image_id": "img4", "receipt_id": 4, "line_id": 1, "word_id": 1,
                "text": "other2", "valid_labels_array": ["TOTAL"],
            },
            "distance": 0.15,
        },
    ]

    result = _discover_candidate_label(
        chroma_client=UnfilteredFakeChromaClient(query_neighbors=neighbors),
        query_embedding=[0.1, 0.2, 0.3],
        exclude_chroma_id=self_id,
    )
    assert result == "TOTAL"


# ---------------------------------------------------------------------------
# _discover_and_evaluate_unlabeled tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_discover_and_evaluate_returns_candidate_and_consensus():
    """Should return (candidate, consensus, pos, neg) in a single query."""
    neighbors = [
        {
            "metadata": {
                "image_id": f"nb{i}", "receipt_id": i + 10, "line_id": 1, "word_id": 1,
                "text": "item", "valid_labels_array": ["LINE_TOTAL"],
                "invalid_labels_array": [],
            },
            "distance": 0.1,
        }
        for i in range(5)
    ]
    result = _discover_and_evaluate_unlabeled(
        chroma_client=UnfilteredFakeChromaClient(query_neighbors=neighbors),
        query_embedding=[0.1, 0.2, 0.3],
        exclude_chroma_id=_word_chroma_id(),
    )
    assert result is not None
    candidate, consensus, pos, neg = result
    assert candidate == "LINE_TOTAL"
    assert consensus == 1.0  # all positive
    assert pos == 5
    assert neg == 0


@pytest.mark.unit
def test_discover_and_evaluate_returns_none_no_dominant():
    """Should return None when no label has >=2 occurrences."""
    neighbors = [
        {
            "metadata": {
                "image_id": f"nb{i}", "receipt_id": i + 10, "line_id": 1, "word_id": 1,
                "text": "word", "valid_labels_array": [f"UNIQUE_{i}"],
                "invalid_labels_array": [],
            },
            "distance": 0.1,
        }
        for i in range(3)
    ]
    result = _discover_and_evaluate_unlabeled(
        chroma_client=UnfilteredFakeChromaClient(query_neighbors=neighbors),
        query_embedding=[0.1, 0.2, 0.3],
        exclude_chroma_id=_word_chroma_id(),
    )
    assert result is None


@pytest.mark.unit
def test_discover_and_evaluate_returns_none_no_neighbors():
    """Should return None with empty neighbor list."""
    result = _discover_and_evaluate_unlabeled(
        chroma_client=UnfilteredFakeChromaClient(query_neighbors=[]),
        query_embedding=[0.1, 0.2, 0.3],
        exclude_chroma_id=_word_chroma_id(),
    )
    assert result is None


@pytest.mark.unit
def test_discover_and_evaluate_mixed_consensus():
    """Consensus should reflect valid vs invalid neighbor split."""
    neighbors = [
        # 3 neighbors with LINE_TOTAL as valid
        {
            "metadata": {
                "image_id": f"v{i}", "receipt_id": i + 10, "line_id": 1, "word_id": 1,
                "text": "amount", "valid_labels_array": ["LINE_TOTAL"],
                "invalid_labels_array": [],
            },
            "distance": 0.1 + i * 0.01,
        }
        for i in range(3)
    ] + [
        # 2 neighbors with LINE_TOTAL as invalid
        {
            "metadata": {
                "image_id": f"i{i}", "receipt_id": i + 20, "line_id": 1, "word_id": 1,
                "text": "price", "valid_labels_array": [],
                "invalid_labels_array": ["LINE_TOTAL"],
            },
            "distance": 0.15 + i * 0.01,
        }
        for i in range(2)
    ]
    # Discovery needs >=2 occurrences in valid_labels_array: 3 valid neighbors → OK
    result = _discover_and_evaluate_unlabeled(
        chroma_client=UnfilteredFakeChromaClient(query_neighbors=neighbors),
        query_embedding=[0.1, 0.2, 0.3],
        exclude_chroma_id=_word_chroma_id(),
    )
    assert result is not None
    candidate, consensus, pos, neg = result
    assert candidate == "LINE_TOTAL"
    assert pos == 3
    assert neg == 2
    assert abs(consensus - 0.2) < 0.01  # (3-2)/5 = 0.2


# ---------------------------------------------------------------------------
# chroma_resolve_words tests for unlabeled path
# ---------------------------------------------------------------------------


def _make_consensus_neighbors(target_label, count=5, valid=True):
    """Build neighbor list where all have target_label as valid or invalid."""
    neighbors = []
    for i in range(count):
        meta = {
            "image_id": f"nb{i}", "receipt_id": i + 10, "line_id": 1, "word_id": 1,
            "text": "amount",
        }
        if valid:
            meta["valid_labels_array"] = [target_label]
            meta["invalid_labels_array"] = []
        else:
            meta["valid_labels_array"] = []
            meta["invalid_labels_array"] = [target_label]
        neighbors.append({"metadata": meta, "distance": 0.1 + i * 0.02})
    return neighbors


@pytest.mark.unit
def test_chroma_resolve_unlabeled_word_with_strong_consensus():
    """Unlabeled word with strong neighbor consensus should be resolved."""
    # Neighbors: 5 with LINE_TOTAL as valid label → strong positive consensus
    neighbors = _make_consensus_neighbors("LINE_TOTAL", count=5, valid=True)
    # Also add discovery neighbors (same set works for both queries)
    for n in neighbors:
        n["metadata"]["valid_labels_array"] = ["LINE_TOTAL"]

    client = UnfilteredFakeChromaClient(query_neighbors=neighbors)
    word = _make_word_dict(current_label="", word_text="12.99")

    resolved, unresolved = chroma_resolve_words(
        chroma_client=client,
        words=[word],
        merchant_name="Test",
        threshold=0.60,
        min_evidence=2,
    )

    assert len(resolved) == 1
    assert len(unresolved) == 0
    _, decision = resolved[0]
    assert decision["decision"] == "INVALID"
    assert decision["suggested_label"] == "LINE_TOTAL"


@pytest.mark.unit
def test_chroma_resolve_unlabeled_word_no_embedding():
    """Unlabeled word with no embedding should be unresolved."""
    client = UnfilteredFakeChromaClient(get_embedding=None, query_neighbors=[])
    word = _make_word_dict(current_label="O", word_text="???")

    resolved, unresolved = chroma_resolve_words(
        chroma_client=client,
        words=[word],
        merchant_name="Test",
    )

    assert len(resolved) == 0
    assert len(unresolved) == 1


@pytest.mark.unit
def test_chroma_resolve_unlabeled_word_no_dominant_label():
    """Unlabeled word with no dominant label in neighbors → unresolved."""
    # Each neighbor has a unique label — no label reaches count >= 2
    neighbors = [
        {
            "metadata": {
                "image_id": f"nb{i}", "receipt_id": i + 10, "line_id": 1, "word_id": 1,
                "text": "word", "valid_labels_array": [f"UNIQUE_{i}"],
            },
            "distance": 0.1 + i * 0.02,
        }
        for i in range(5)
    ]

    client = UnfilteredFakeChromaClient(query_neighbors=neighbors)
    word = _make_word_dict(current_label="UNLABELED", word_text="stuff")

    resolved, unresolved = chroma_resolve_words(
        chroma_client=client,
        words=[word],
        merchant_name="Test",
    )

    assert len(resolved) == 0
    assert len(unresolved) == 1


@pytest.mark.unit
def test_chroma_resolve_labeled_word_still_works():
    """Labeled word should still be resolved via the original path."""
    neighbors = _make_consensus_neighbors("TOTAL", count=5, valid=True)
    client = UnfilteredFakeChromaClient(query_neighbors=neighbors)
    word = _make_word_dict(current_label="TOTAL", word_text="12.99")

    resolved, unresolved = chroma_resolve_words(
        chroma_client=client,
        words=[word],
        merchant_name="Test",
        threshold=0.60,
        min_evidence=2,
    )

    assert len(resolved) == 1
    assert len(unresolved) == 0
    _, decision = resolved[0]
    assert decision["decision"] == "VALID"


@pytest.mark.unit
def test_chroma_resolve_mixed_labeled_and_unlabeled():
    """Mix of labeled and unlabeled words should each follow correct path."""
    neighbors = _make_consensus_neighbors("TOTAL", count=5, valid=True)
    # Add LINE_TOTAL to valid_labels_array for discovery
    for n in neighbors:
        n["metadata"]["valid_labels_array"] = ["TOTAL", "LINE_TOTAL"]

    client = UnfilteredFakeChromaClient(query_neighbors=neighbors)

    labeled_word = _make_word_dict(current_label="TOTAL", word_text="12.99")
    unlabeled_word = _make_word_dict(
        current_label="", word_text="5.00", word_id=4,
    )

    resolved, unresolved = chroma_resolve_words(
        chroma_client=client,
        words=[labeled_word, unlabeled_word],
        merchant_name="Test",
        threshold=0.60,
        min_evidence=2,
    )

    # Both should be resolved — labeled as VALID, unlabeled as INVALID with
    # suggested_label derived from neighbor discovery.
    assert len(resolved) == 2
    assert len(unresolved) == 0
