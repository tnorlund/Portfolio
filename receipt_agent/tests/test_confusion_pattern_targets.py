"""Tests for confusion-matrix driven pattern discovery targets."""

from receipt_agent.agents.label_evaluator.pattern_discovery import (
    build_confusion_pattern_targets,
    build_discovery_prompt,
    build_label_heatmap,
    build_synthetic_receipt_plan,
    build_top_confusion_pairs,
    format_confusion_targets_for_prompt,
    format_similar_merchant_examples_for_prompt,
    generate_synthetic_receipt_candidates,
    query_similar_merchant_examples_from_chroma,
)


def _sample_receipts_data():
    return [
        {
            "receipt_id": "img_1",
            "lines": [
                {
                    "y": 0.92,
                    "words": [
                        {
                            "text": "SPROUTS",
                            "x": 0.48,
                            "labels": ["MERCHANT_NAME"],
                        },
                        {"text": "#447", "x": 0.78, "labels": None},
                    ],
                },
                {
                    "y": 0.76,
                    "words": [
                        {
                            "text": "101",
                            "x": 0.12,
                            "labels": ["ADDRESS_LINE"],
                        },
                        {
                            "text": "Market",
                            "x": 0.24,
                            "labels": ["ADDRESS_LINE"],
                        },
                    ],
                },
                {
                    "y": 0.18,
                    "words": [
                        {
                            "text": "TOTAL",
                            "x": 0.66,
                            "labels": ["GRAND_TOTAL"],
                        },
                        {
                            "text": "12.34",
                            "x": 0.91,
                            "labels": ["GRAND_TOTAL"],
                        },
                    ],
                },
            ],
        }
    ]


def test_build_top_confusion_pairs_ranks_off_diagonal_errors():
    labels = ["MERCHANT_NAME", "ADDRESS_LINE", "GRAND_TOTAL", "O"]
    matrix = [
        [30, 0, 0, 7],
        [1, 22, 0, 3],
        [0, 2, 25, 4],
        [9, 4, 1, 100],
    ]

    pairs = build_top_confusion_pairs(labels, matrix, limit=3)

    assert [pair.actual_label for pair in pairs] == [
        "O",
        "MERCHANT_NAME",
        "GRAND_TOTAL",
    ]
    assert [pair.predicted_label for pair in pairs] == [
        "MERCHANT_NAME",
        "O",
        "O",
    ]
    assert pairs[0].share == 9 / 114


def test_build_label_heatmap_groups_labels_into_receipt_zones():
    heatmap = build_label_heatmap(_sample_receipts_data())

    merchant_cell = heatmap["MERCHANT_NAME"][0]
    assert merchant_cell.y_band == "top"
    assert merchant_cell.x_zone == "center"
    assert merchant_cell.examples == ["SPROUTS"]

    address_cell = heatmap["ADDRESS_LINE"][0]
    assert address_cell.y_band == "top"
    assert address_cell.x_zone == "left"
    assert address_cell.count == 2


def test_build_confusion_pattern_targets_adds_heatmap_synthesis_briefs():
    labels = ["MERCHANT_NAME", "ADDRESS_LINE", "GRAND_TOTAL", "O"]
    matrix = [
        [30, 0, 0, 7],
        [1, 22, 0, 3],
        [0, 2, 25, 4],
        [9, 4, 1, 100],
    ]

    targets = build_confusion_pattern_targets(
        labels,
        matrix,
        receipts_data=_sample_receipts_data(),
        limit=2,
    )

    assert len(targets) == 2
    assert targets[0].actual_label == "O"
    assert targets[0].predicted_label == "MERCHANT_NAME"
    assert targets[0].error_kind == "false_positive"
    assert targets[0].pattern_family == "merchant header"
    assert targets[0].heatmap_cells[0].examples == ["SPROUTS"]
    assert "hard-negative merchant header receipts" in (
        targets[0].synthetic_receipt_brief
    )


def test_format_confusion_targets_for_prompt_and_discovery_prompt_section():
    labels = ["MERCHANT_NAME", "ADDRESS_LINE", "O"]
    matrix = [
        [30, 0, 7],
        [1, 22, 3],
        [9, 4, 100],
    ]
    targets = build_confusion_pattern_targets(
        labels,
        matrix,
        receipts_data=_sample_receipts_data(),
        limit=1,
    )

    section = format_confusion_targets_for_prompt(targets)
    assert "CONFUSION-DRIVEN HEATMAP TARGETS" in section
    assert "O -> MERCHANT_NAME" in section
    assert "MERCHANT_NAME@top/center" in section

    prompt = build_discovery_prompt(
        "Sprouts Farmers Market",
        _sample_receipts_data(),
        confusion_targets=targets,
    )
    assert "CONFUSION-DRIVEN HEATMAP TARGETS" in prompt
    assert "synthetic_receipt_guidance" in prompt
    assert "merchant-name zones" in prompt


def test_similar_merchant_evidence_is_included_in_discovery_prompt():
    similar_examples = [
        {
            "merchant_name": "Trader Joe's",
            "label": "MERCHANT_NAME",
            "word_text": "TRADER",
            "x_position": 0.48,
            "y_position": 0.08,
            "left_neighbor": "<EDGE>",
            "right_neighbor": "JOE'S",
            "query": "receipt merchant header merchant name",
            "similarity": 0.91,
        }
    ]

    section = format_similar_merchant_examples_for_prompt(similar_examples)
    assert "SIMILAR-MERCHANT EVIDENCE FROM CHROMA" in section
    assert "Trader Joe's" in section
    assert '"TRADER"[MERCHANT_NAME]' in section
    assert "similarity=0.91" in section

    prompt = build_discovery_prompt(
        "Sprouts Farmers Market",
        _sample_receipts_data(),
        similar_merchant_examples=similar_examples,
    )
    assert "SIMILAR-MERCHANT EVIDENCE FROM CHROMA" in prompt
    assert "stable" in prompt and "similar merchants" in prompt
    assert "Fold this" in prompt and "synthetic_receipt_strategy" in prompt


def test_build_synthetic_receipt_plan_uses_heatmap_and_llm_guidance():
    labels = ["MERCHANT_NAME", "ADDRESS_LINE", "O"]
    matrix = [
        [30, 0, 7],
        [1, 22, 3],
        [9, 4, 100],
    ]
    targets = build_confusion_pattern_targets(
        labels,
        matrix,
        receipts_data=_sample_receipts_data(),
        limit=1,
    )
    llm_patterns = {
        "confusion_patterns": [
            {
                "actual_label": "O",
                "predicted_label": "MERCHANT_NAME",
                "pattern": "Store-number text sits beside the header logo",
                "heatmap_rationale": "Errors cluster in the top-center header band",
                "synthetic_receipt_strategy": "Add hard-negative header variants",
            }
        ],
        "synthetic_receipt_guidance": [
            "Add merchant-header hard negatives before retraining."
        ],
    }
    similar_examples = [
        {
            "merchant_name": "Trader Joe's",
            "label": "MERCHANT_NAME",
            "word_text": "TRADER",
            "x_position": 0.49,
            "y_position": 0.93,
            "left_neighbor": "<EDGE>",
            "right_neighbor": "JOE'S",
            "query": "receipt merchant header merchant name",
            "similarity": 0.91,
        }
    ]

    plan = build_synthetic_receipt_plan(
        "Sprouts Farmers Market",
        targets,
        receipts_data=_sample_receipts_data(),
        llm_patterns=llm_patterns,
        similar_merchant_examples=similar_examples,
    )

    assert plan.merchant_name == "Sprouts Farmers Market"
    assert plan.confusion_target_count == 1
    assert plan.source_receipt_count == 1
    assert len(plan.recipes) == 1

    recipe = plan.recipes[0]
    assert recipe.actual_label == "O"
    assert recipe.predicted_label == "MERCHANT_NAME"
    assert recipe.target_zone == {
        "label": "MERCHANT_NAME",
        "y_band": "top",
        "x_zone": "center",
    }
    assert recipe.merchant_scope == "same_and_similar_merchant_headers"
    assert "SPROUTS" in recipe.source_examples
    assert "TRADER" in recipe.source_examples
    assert any(
        "similar merchant" in query for query in recipe.retrieval_queries
    )
    assert "top-center header band" in recipe.llm_rationale
    assert "O hard negatives" in recipe.expected_label_effect
    assert any("real-only" in guard for guard in recipe.safeguards)
    assert plan.synthetic_receipt_guidance == [
        "Add merchant-header hard negatives before retraining."
    ]
    assert any("val_f1" in guard for guard in plan.metric_guardrails)
    assert plan.similar_merchant_mining["evidence"][0]["merchant_name"] == (
        "Trader Joe's"
    )
    assert plan.to_dict()["recipes"][0]["target_zone"]["y_band"] == "top"


def test_query_similar_merchant_examples_from_chroma_excludes_source_merchant():
    labels = ["MERCHANT_NAME", "ADDRESS_LINE", "O"]
    matrix = [
        [30, 0, 7],
        [1, 22, 3],
        [9, 4, 100],
    ]
    targets = build_confusion_pattern_targets(
        labels,
        matrix,
        receipts_data=_sample_receipts_data(),
        limit=1,
    )

    class FakeChroma:
        def __init__(self):
            self.calls = []

        def query(self, **kwargs):
            self.calls.append(kwargs)
            return {
                "metadatas": [
                    [
                        {
                            "merchant_name": "Sprouts Farmers Market",
                            "text": "SPROUTS",
                            "x": 0.48,
                            "y": 0.93,
                            "left": "<EDGE>",
                            "right": "#447",
                            "valid_labels_array": ["MERCHANT_NAME"],
                        },
                        {
                            "merchant_name": "Trader Joe's",
                            "text": "TRADER",
                            "x": 0.51,
                            "y": 0.91,
                            "left": "<EDGE>",
                            "right": "JOE'S",
                            "valid_labels_array": ["MERCHANT_NAME"],
                        },
                    ]
                ],
                "distances": [[0.1, 0.2]],
            }

    chroma = FakeChroma()
    evidence = query_similar_merchant_examples_from_chroma(
        chroma,
        "Sprouts Farmers Market",
        lambda texts: [[0.1, 0.2, 0.3] for _ in texts],
        targets,
    )

    assert len(evidence) == 1
    assert evidence[0].merchant_name == "Trader Joe's"
    assert evidence[0].label == "MERCHANT_NAME"
    assert evidence[0].word_text == "TRADER"
    assert evidence[0].similarity == 0.9
    assert chroma.calls[0]["collection_name"] == "words"


def test_generate_synthetic_receipt_candidates_matches_layoutlm_shape():
    labels = ["MERCHANT_NAME", "ADDRESS_LINE", "O"]
    matrix = [
        [30, 0, 7],
        [1, 22, 3],
        [9, 4, 100],
    ]
    targets = build_confusion_pattern_targets(
        labels,
        matrix,
        receipts_data=_sample_receipts_data(),
        limit=1,
    )
    plan = build_synthetic_receipt_plan(
        "Sprouts Farmers Market",
        targets,
        receipts_data=_sample_receipts_data(),
    )

    candidates = generate_synthetic_receipt_candidates(plan)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.train_only is True
    assert candidate.receipt_key.startswith("synthetic-")
    assert candidate.receipt_key.endswith("#00001")
    assert (
        len(candidate.tokens)
        == len(candidate.bboxes)
        == len(candidate.ner_tags)
    )
    assert all(len(box) == 4 for box in candidate.bboxes)
    assert all(0 <= coord <= 1000 for box in candidate.bboxes for coord in box)
    assert "B-MERCHANT_NAME" in candidate.ner_tags
    assert "B-DATE" in candidate.ner_tags
    assert candidate.metadata["source"] == "confusion_heatmap_recipe"

    hard_negative_positions = [
        idx
        for idx, token in enumerate(candidate.tokens)
        if token in {"REWARDS", "STORE", "#447"}
    ]
    assert hard_negative_positions
    assert all(
        candidate.ner_tags[idx] == "O" for idx in hard_negative_positions
    )

    serialized = candidate.to_dict()
    assert serialized["tokens"] == candidate.tokens
    assert serialized["train_only"] is True
