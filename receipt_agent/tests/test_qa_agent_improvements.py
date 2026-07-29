"""Tests for QA agent date anchoring, evidence capture, and OCR filtering."""

from datetime import date

import pytest

from receipt_agent.agents.question_answering.graph import (
    MAX_EVIDENCE_ITEMS,
    build_date_context,
    build_evidence,
)
from receipt_agent.agents.question_answering.state import (
    AmountItem,
    ReceiptSummary,
)
from receipt_agent.agents.question_answering.tools.search import (
    OCR_MAX_LINE_ITEM_AMOUNT,
    OCR_MAX_RECEIPT_TOTAL,
    partition_ocr_outliers,
)

# ==============================================================================
# Date anchoring
# ==============================================================================


def test_date_context_states_the_anchor_date():
    context = build_date_context(date(2026, 7, 28))

    assert "2026-07-28" in context
    assert "Tuesday" in context


def test_date_context_resolves_relative_ranges():
    context = build_date_context(date(2026, 7, 28))

    assert '"last month" -> 2026-06-01 to 2026-06-30' in context
    assert '"this month" -> 2026-07-01 to 2026-07-31' in context
    assert '"this year" -> 2026-01-01 to 2026-12-31' in context
    assert '"last year" -> 2025-01-01 to 2025-12-31' in context
    assert '"this quarter" -> 2026-07-01 to 2026-09-30' in context


def test_date_context_rolls_last_month_back_a_year_in_january():
    context = build_date_context(date(2026, 1, 15))

    assert '"last month" -> 2025-12-01 to 2025-12-31' in context
    assert '"this quarter" -> 2026-01-01 to 2026-03-31' in context


def test_date_context_handles_leap_february():
    context = build_date_context(date(2028, 2, 10))

    assert '"this month" -> 2028-02-01 to 2028-02-29' in context


def test_date_context_defaults_to_today():
    from datetime import datetime

    assert datetime.now().date().isoformat() in build_date_context()


def test_prompts_carry_date_context():
    """Every prompt that can emit or read a date filter is anchored."""
    from receipt_agent.agents.question_answering import graph as qa_graph

    captured: list = []

    class FakeLLM:
        def invoke(self, messages):
            captured.append(messages)
            raise RuntimeError("stop after capturing the prompt")

        def with_structured_output(self, _schema):
            return self

    plan_node = qa_graph.create_plan_node(FakeLLM())
    state = qa_graph.QAState(question="How much did I spend last month?")

    # Classification failure falls back to defaults, which is fine here —
    # we only care that the prompt it sent was anchored.
    plan_node(state)

    system_prompt = captured[0][0].content
    assert "## Today's Date" in system_prompt


# ==============================================================================
# Evidence capture
# ==============================================================================


def _summary(image_id="img-1", receipt_id=1, **kwargs):
    kwargs.setdefault("merchant", "Costco")
    return ReceiptSummary(image_id=image_id, receipt_id=receipt_id, **kwargs)


def test_evidence_includes_line_items_when_present():
    summaries = [
        _summary(
            line_items=[
                AmountItem(
                    label="LINE_TOTAL", amount=12.99, item_text="COFFEE"
                ),
                AmountItem(label="LINE_TOTAL", amount=4.50, item_text="MILK"),
            ]
        )
    ]

    evidence = build_evidence(summaries)

    assert [e["item"] for e in evidence] == ["COFFEE", "MILK"]
    assert all(e["image_id"] == "img-1" for e in evidence)


def test_summary_tier_receipts_still_produce_evidence():
    """The regression that left 31 of 32 questions without thumbnails.

    Receipts from get_receipt_summaries carry no line items, so evidence
    built only from line items dropped them entirely and the frontend had
    no imageId to join a thumbnail against.
    """
    summaries = [
        _summary(
            image_id="img-a",
            receipt_id=1,
            grand_total=48.20,
            item_count=7,
            line_items=[],
        ),
        _summary(
            image_id="img-b",
            receipt_id=2,
            grand_total=15.00,
            line_items=[],
        ),
    ]

    evidence = build_evidence(summaries)

    assert len(evidence) == 2
    assert {e["image_id"] for e in evidence} == {"img-a", "img-b"}
    assert evidence[0]["item"] == "7 items"
    assert evidence[0]["amount"] == 48.20
    assert evidence[1]["item"] == "Receipt total"


def test_single_item_receipt_is_not_pluralized():
    evidence = build_evidence([_summary(item_count=1, line_items=[])])

    assert evidence[0]["item"] == "1 item"


def test_detail_receipt_without_extractable_items_still_appears():
    """A fetched receipt whose line items failed to parse keeps its row."""
    evidence = build_evidence(
        [_summary(grand_total=33.10, line_items=[], labels_found=["TAX"])]
    )

    assert len(evidence) == 1
    assert evidence[0]["amount"] == 33.10


def test_evidence_skips_summaries_without_an_image_id():
    evidence = build_evidence([_summary(image_id="", grand_total=10.0)])

    assert evidence == []


def test_evidence_cap_keeps_one_row_per_receipt():
    """Trimming must never cost a receipt its only chance at a thumbnail."""
    summaries = [
        _summary(
            image_id=f"img-{i}",
            receipt_id=i,
            line_items=[
                AmountItem(label="LINE_TOTAL", amount=1.0, item_text=f"x{j}")
                for j in range(10)
            ],
        )
        for i in range(60)
    ]

    evidence = build_evidence(summaries)

    assert len(evidence) == MAX_EVIDENCE_ITEMS
    covered = {e["image_id"] for e in evidence}
    assert len(covered) == 60


# ==============================================================================
# Routing to the shape node
# ==============================================================================


def _agent_state_with_text_reply():
    from langchain_core.messages import AIMessage

    from receipt_agent.agents.question_answering.state import QAState

    return QAState(
        question="How much did I spend at Costco last month?",
        messages=[AIMessage(content="You spent $312.40 across 6 receipts.")],
    )


def test_summary_only_retrieval_routes_to_shape():
    """Aggregation questions never touch the detail tier, but still have
    receipts worth shaping into evidence."""
    from receipt_agent.agents.question_answering.graph import (
        route_after_agent,
    )

    state_holder = {
        "retrieved_receipts": [],
        "summary_receipts": [{"image_id": "img-a", "receipt_id": 1}],
    }

    assert (
        route_after_agent(_agent_state_with_text_reply(), state_holder)
        == "shape"
    )


def test_detail_retrieval_still_routes_to_shape():
    from receipt_agent.agents.question_answering.graph import (
        route_after_agent,
    )

    state_holder = {
        "retrieved_receipts": [{"image_id": "img-a", "receipt_id": 1}],
        "summary_receipts": [],
    }

    assert (
        route_after_agent(_agent_state_with_text_reply(), state_holder)
        == "shape"
    )


def test_no_receipts_still_routes_to_shape():
    # A correct "nothing found" must reach synthesize so the trace carries
    # a final answer; ending early left the viz with an empty Result panel.
    from receipt_agent.agents.question_answering.graph import (
        route_after_agent,
    )

    state_holder = {"retrieved_receipts": [], "summary_receipts": []}

    assert (
        route_after_agent(_agent_state_with_text_reply(), state_holder)
        == "shape"
    )
    assert "answer" not in state_holder


# ==============================================================================
# OCR outlier filtering
# ==============================================================================


def test_amounts_over_the_ceiling_are_dropped():
    records = [
        {"amount": 12.99},
        {"amount": 1_299_999.00},
        {"amount": 4.50},
    ]

    kept, outliers = partition_ocr_outliers(records)

    assert [r["amount"] for r in kept] == [12.99, 4.50]
    assert [r["amount"] for r in outliers] == [1_299_999.00]


def test_plausible_large_purchase_is_kept():
    """A big-but-real Costco run must survive the filter."""
    records = [{"amount": 20.0} for _ in range(20)]
    records.append({"amount": 1_450.00})

    kept, outliers = partition_ocr_outliers(records)

    assert outliers == []
    assert len(kept) == 21


def test_relative_rule_drops_garbage_under_the_ceiling():
    records = [{"amount": 20.0} for _ in range(20)]
    records.append({"amount": 9_000.00})

    kept, outliers = partition_ocr_outliers(records)

    assert [r["amount"] for r in outliers] == [9_000.00]
    assert len(kept) == 20


def test_relative_rule_needs_enough_samples():
    """Too few samples means no reliable median, so only the ceiling applies."""
    records = [{"amount": 20.0}, {"amount": 9_000.00}]

    kept, outliers = partition_ocr_outliers(records)

    assert outliers == []
    assert len(kept) == 2


def test_missing_and_non_numeric_amounts_are_kept():
    records = [
        {"amount": None},
        {"amount": "not a number"},
        {"merchant": "Costco"},
    ]

    kept, outliers = partition_ocr_outliers(records)

    assert len(kept) == 3
    assert outliers == []


def test_negative_amounts_are_kept_as_discounts():
    records = [{"amount": -5.00}, {"amount": 12.99}]

    kept, outliers = partition_ocr_outliers(records)

    assert len(kept) == 2
    assert outliers == []


def test_grand_total_ceiling_is_higher_than_line_item_ceiling():
    assert OCR_MAX_RECEIPT_TOTAL > OCR_MAX_LINE_ITEM_AMOUNT

    record = [{"grand_total": 20_000.00}]

    kept, outliers = partition_ocr_outliers(
        record, amount_key="grand_total", ceiling=OCR_MAX_RECEIPT_TOTAL
    )
    assert len(kept) == 1

    kept, outliers = partition_ocr_outliers(
        record, amount_key="grand_total", ceiling=OCR_MAX_LINE_ITEM_AMOUNT
    )
    assert len(outliers) == 1


def test_input_order_is_preserved():
    records = [{"amount": float(i)} for i in range(1, 11)]
    records.insert(5, {"amount": 999_999.0})

    kept, _ = partition_ocr_outliers(records)

    assert [r["amount"] for r in kept] == [float(i) for i in range(1, 11)]


@pytest.mark.parametrize("empty", [[], ()])
def test_empty_input_is_handled(empty):
    kept, outliers = partition_ocr_outliers(list(empty))

    assert kept == []
    assert outliers == []
