"""Tests for the minimal linear workflow."""

import pytest
from datetime import datetime

from receipt_label.langgraph_integration.workflow_v1 import create_simple_workflow
from receipt_label.langgraph_integration.state import (
    ReceiptProcessingState,
    PatternMatchResult,
    PriceColumnInfo,
)


@pytest.fixture
def sample_receipt_words():
    """Create sample receipt words for testing."""
    return [
        {"word_id": 1, "line_id": 1, "text": "WALMART", "x": 0.5, "y": 0.1, "confidence": 0.95},
        {"word_id": 2, "line_id": 2, "text": "12/25/2024", "x": 0.5, "y": 0.2, "confidence": 0.98},
        {"word_id": 3, "line_id": 3, "text": "3:45", "x": 0.3, "y": 0.25, "confidence": 0.92},
        {"word_id": 4, "line_id": 3, "text": "PM", "x": 0.4, "y": 0.25, "confidence": 0.90},
        {"word_id": 5, "line_id": 10, "text": "TOTAL", "x": 0.2, "y": 0.8, "confidence": 0.99},
        {"word_id": 6, "line_id": 10, "text": "$45.99", "x": 0.7, "y": 0.8, "confidence": 0.97},
    ]


@pytest.fixture
def sample_pattern_matches():
    """Create sample pattern matches from Phase 2."""
    return {
        "DATE": [
            {
                "word_id": 2,
                "pattern_type": "DATE",
                "confidence": 0.95,
                "matched_text": "12/25/2024",
                "extracted_value": "2024-12-25",
                "metadata": {"format": "MM/DD/YYYY"},
            }
        ],
        "MERCHANT_NAME": [
            {
                "word_id": 1,
                "pattern_type": "MERCHANT_NAME",
                "confidence": 0.90,
                "matched_text": "WALMART",
                "extracted_value": "Walmart",
                "metadata": {"source": "header_position"},
            }
        ],
    }


@pytest.fixture
def sample_currency_columns():
    """Create sample currency columns from spatial analysis."""
    return [
        {
            "column_id": 1,
            "x_center": 0.7,
            "x_min": 0.65,
            "x_max": 0.75,
            "price_count": 1,
            "prices": [
                {"word_id": 6, "value": 45.99, "y_position": 0.8}
            ],
            "confidence": 0.92,
            "alignment_score": 0.95,
        }
    ]


@pytest.fixture
def initial_state(sample_receipt_words, sample_pattern_matches, sample_currency_columns):
    """Create initial state for workflow testing."""
    state: ReceiptProcessingState = {
        # Required fields
        "receipt_id": 12345,
        "image_id": "test-uuid-1234",
        "receipt_words": sample_receipt_words,
        "pattern_matches": sample_pattern_matches,
        "currency_columns": sample_currency_columns,
        "math_solutions": [],
        "structure_sections": None,
        "four_field_summary": None,
        
        # Merchant context
        "merchant_place_id": None,
        "merchant_name": "Walmart",
        "merchant_validation_status": None,
        "merchant_matched_fields": None,
        "merchant_patterns": None,
        
        # Labeling progress
        "labels": {},
        "grouped_labels": {},
        "missing_essentials": [],
        "found_essentials": {},
        "line_items": [],
        "total_words": 0,
        "labeled_words": 0,
        "unlabeled_meaningful_words": 0,
        "coverage_percentage": 0.0,
        
        # Decision engine
        "decision_outcome": None,
        "decision_confidence": None,
        "decision_reasoning": None,
        "skip_rate": None,
        
        # GPT context
        "needs_gpt": False,
        "gpt_context_type": None,
        "gpt_spatial_context": None,
        "gpt_responses": [],
        
        # Validation
        "validation_results": {},
        "needs_review": False,
        "validation_notes": [],
        
        # Metrics
        "metrics": {
            "pattern_detection_ms": 0.0,
            "decision_engine_ms": 0.0,
            "gpt_context_building_ms": 0.0,
            "gpt_labeling_ms": 0.0,
            "validation_ms": 0.0,
            "total_processing_ms": 0.0,
            "gpt_prompt_tokens": 0,
            "gpt_completion_tokens": 0,
            "gpt_cost_usd": 0.0,
            "pinecone_queries": 0,
            "pinecone_cost_usd": 0.0,
            "total_cost_usd": 0.0,
            "pattern_coverage": 0.0,
            "gpt_skip_rate": 1.0,
            "batch_api_eligible": False,
        },
        
        # External updates
        "label_updates": [],
        "pinecone_updates": [],
        
        # Workflow control
        "current_phase": "init",
        "retry_count": 0,
        "max_retries": 3,
        "errors": [],
        "warnings": [],
        "decisions": [],
        "node_timings": {},
    }
    return state


@pytest.mark.asyncio
async def test_minimal_workflow_execution(initial_state):
    """Test that the minimal workflow executes successfully."""
    # Create workflow
    workflow = create_simple_workflow()
    
    # Run workflow
    final_state = await workflow.ainvoke(initial_state)
    
    # Basic assertions
    assert final_state is not None
    assert "labels" in final_state
    assert "validation_results" in final_state
    assert "decisions" in final_state
    
    # Check that all nodes executed
    node_names = [d["node"] for d in final_state["decisions"]]
    assert "load_merchant" in node_names
    assert "pattern_labeling" in node_names
    assert "validation" in node_names


@pytest.mark.asyncio
async def test_merchant_loading(initial_state):
    """Test merchant data loading node."""
    workflow = create_simple_workflow()
    final_state = await workflow.ainvoke(initial_state)
    
    # Check merchant patterns were loaded
    assert "merchant_patterns" in final_state
    assert len(final_state["merchant_patterns"]) > 0
    
    # Check decision was tracked
    merchant_decision = next(
        (d for d in final_state["decisions"] if d["node"] == "load_merchant"), None
    )
    assert merchant_decision is not None
    assert merchant_decision["action"] == "loaded_merchant_data"
    assert merchant_decision["merchant"] == "Walmart"


@pytest.mark.asyncio
async def test_pattern_labeling(initial_state):
    """Test pattern-based labeling node."""
    workflow = create_simple_workflow()
    final_state = await workflow.ainvoke(initial_state)
    
    # Check labels were created
    assert len(final_state["labels"]) > 0
    
    # Check DATE label was applied
    assert 2 in final_state["labels"]
    assert final_state["labels"][2]["label"] == "DATE"
    assert final_state["labels"][2]["source"] == "pattern"
    
    # Check MERCHANT_NAME label was applied
    assert 1 in final_state["labels"]
    assert final_state["labels"][1]["label"] == "MERCHANT_NAME"
    
    # Check GRAND_TOTAL was inferred from position
    assert 6 in final_state["labels"]
    assert final_state["labels"][6]["label"] == "GRAND_TOTAL"
    assert final_state["labels"][6]["source"] == "position_heuristic"
    
    # Check coverage was calculated
    assert final_state["coverage_percentage"] > 0
    assert final_state["labeled_words"] == 3  # DATE, MERCHANT_NAME, GRAND_TOTAL


@pytest.mark.asyncio
async def test_validation_missing_essential(initial_state):
    """Test validation when essential fields are missing."""
    # Remove merchant pattern to simulate missing field
    initial_state["pattern_matches"].pop("MERCHANT_NAME", None)
    
    workflow = create_simple_workflow()
    final_state = await workflow.ainvoke(initial_state)
    
    # Check validation results
    assert "essential_fields" in final_state["validation_results"]
    assert not final_state["validation_results"]["essential_fields"]["passed"]
    assert final_state["needs_review"] is True
    
    # Check missing essentials
    assert "MERCHANT_NAME" in final_state["missing_essentials"]
    assert "TIME" in final_state["missing_essentials"]  # We don't have TIME in sample data


@pytest.mark.asyncio
async def test_validation_all_essentials_found(initial_state):
    """Test validation when all essential fields are found."""
    # Add TIME pattern to have all essentials
    initial_state["pattern_matches"]["TIME"] = [
        {
            "word_id": 3,
            "pattern_type": "TIME",
            "confidence": 0.90,
            "matched_text": "3:45",
            "extracted_value": "15:45",
            "metadata": {},
        }
    ]
    
    workflow = create_simple_workflow()
    final_state = await workflow.ainvoke(initial_state)
    
    # Check validation passed
    assert final_state["validation_results"]["essential_fields"]["passed"]
    assert final_state["needs_review"] is False
    assert len(final_state["missing_essentials"]) == 0
    
    # Check all essentials were found
    assert "MERCHANT_NAME" in final_state["found_essentials"]
    assert "DATE" in final_state["found_essentials"]
    assert "TIME" in final_state["found_essentials"]
    assert "GRAND_TOTAL" in final_state["found_essentials"]


@pytest.mark.asyncio
async def test_workflow_metrics_tracking(initial_state):
    """Test that metrics are properly initialized and tracked."""
    workflow = create_simple_workflow()
    final_state = await workflow.ainvoke(initial_state)
    
    # Check metrics structure
    assert "metrics" in final_state
    assert "pattern_coverage" in final_state["metrics"]
    assert "gpt_skip_rate" in final_state["metrics"]
    
    # Check pattern coverage was calculated
    assert final_state["metrics"]["pattern_coverage"] > 0
    assert final_state["metrics"]["pattern_coverage"] <= 1.0
    
    # Check GPT wasn't used (skip rate should be 1.0)
    assert final_state["metrics"]["gpt_skip_rate"] == 1.0
    assert final_state["metrics"]["gpt_cost_usd"] == 0.0


@pytest.mark.asyncio
async def test_empty_receipt_handling(initial_state):
    """Test workflow handles empty receipt gracefully."""
    initial_state["receipt_words"] = []
    initial_state["pattern_matches"] = {}
    initial_state["currency_columns"] = []
    
    workflow = create_simple_workflow()
    final_state = await workflow.ainvoke(initial_state)
    
    # Should complete without errors
    assert final_state is not None
    assert len(final_state["labels"]) == 0
    assert final_state["coverage_percentage"] == 0
    assert final_state["needs_review"] is True  # Missing all essentials