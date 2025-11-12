"""LangGraph workflow for validating PENDING labels using CoVe.

This workflow validates existing PENDING labels without re-analyzing the entire receipt.
It groups labels by type and uses CoVe to verify each group.
Only validates CORE_LABELS.
"""

import os
import logging
from typing import List, Optional, Any, Dict
from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph
from langchain_core.tracers.langchain import LangChainTracer
from langchain_ollama import ChatOllama

from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel
from receipt_label.constants import CORE_LABELS
from receipt_label.langchain.state.currency_validation import CurrencyAnalysisState
from receipt_label.langchain.utils.cove import apply_chain_of_verification
from receipt_label.langchain.models.currency_validation import (
    PhaseContextResponse,
    TransactionLabel,
    TransactionLabelType,
)
from receipt_label.langchain.utils.retry import retry_with_backoff

logger = logging.getLogger(__name__)


async def validate_labels_with_cove(
    state: CurrencyAnalysisState,
    ollama_api_key: str,
) -> CurrencyAnalysisState:
    """
    Validate PENDING labels using CoVe.

    Groups labels by type and validates each group using CoVe.
    """
    pending_labels = []

    # Get PENDING labels from state
    labels_to_add = state.receipt_word_labels_to_add or []
    labels_to_update = state.receipt_word_labels_to_update or []

    for label in labels_to_add + labels_to_update:
        status = str(getattr(label, "validation_status", "") or "").upper()
        if status == "PENDING":
            pending_labels.append(label)

    if not pending_labels:
        logger.info("   ✅ No PENDING labels to validate with CoVe")
        return {}

    logger.info(f"   🔍 Validating {len(pending_labels)} PENDING labels with CoVe...")

    # Filter to only CORE_LABELS
    core_label_keys = set(CORE_LABELS.keys())
    filtered_labels = [
        label for label in pending_labels
        if str(label.label).upper() in core_label_keys
    ]

    if len(filtered_labels) < len(pending_labels):
        skipped = len(pending_labels) - len(filtered_labels)
        logger.info(f"   ⏭️  Skipping {skipped} non-CORE_LABELS labels (only validating CORE_LABELS)")

    if not filtered_labels:
        logger.info("   ✅ No CORE_LABELS to validate with CoVe")
        return {}

    # Group labels by type for efficient CoVe validation
    labels_by_type: Dict[str, List[ReceiptWordLabel]] = {}
    for label in filtered_labels:
        label_type = str(label.label).upper()
        if label_type not in labels_by_type:
            labels_by_type[label_type] = []
        labels_by_type[label_type].append(label)

    # Create word text lookup with debug logging
    words_list = state.words or []
    word_text_lookup = {
        (word.line_id, word.word_id): word.text
        for word in words_list
    }

    logger.info(f"   📊 Word lookup created: {len(word_text_lookup)} words from {len(words_list)} word objects")

    # Debug: Check for missing words
    missing_words = []
    for label in filtered_labels:
        key = (label.line_id, label.word_id)
        if key not in word_text_lookup:
            missing_words.append({
                "label": label.label,
                "line_id": label.line_id,
                "word_id": label.word_id,
                "image_id": label.image_id,
                "receipt_id": label.receipt_id,
            })

    if missing_words:
        logger.warning(f"   ⚠️  Found {len(missing_words)} labels with missing words (out of {len(filtered_labels)} total):")
        for missing in missing_words[:10]:  # Show first 10
            logger.warning(f"      - {missing['label']} at line_id={missing['line_id']}, word_id={missing['word_id']} (image_id={missing['image_id']}, receipt_id={missing['receipt_id']})")
        if len(missing_words) > 10:
            logger.warning(f"      ... and {len(missing_words) - 10} more")

        # Log available line_ids and word_ids for debugging
        if words_list:
            available_line_ids = sorted(set(w.line_id for w in words_list))
            logger.info(f"   📋 Available line_ids in words: {available_line_ids[:20]}{'...' if len(available_line_ids) > 20 else ''}")
            missing_line_ids = sorted(set(m['line_id'] for m in missing_words))
            logger.info(f"   📋 Missing line_ids from labels: {missing_line_ids[:20]}{'...' if len(missing_line_ids) > 20 else ''}")

    # Initialize LLM for CoVe
    llm = ChatOllama(
        model="gpt-oss:120b",
        base_url="https://ollama.com",
        client_kwargs={
            "headers": {"Authorization": f"Bearer {ollama_api_key}"}
        },
        format="json",
        temperature=0.3,
        timeout=120,
    )

    validated_count = 0
    invalidated_count = 0
    needs_review_count = 0

    # Validate each label type group
    for label_type, labels in labels_by_type.items():
        logger.info(f"   🔍 Validating {len(labels)} {label_type} labels with CoVe...")

        # Build receipt text snippet for context (lines containing these labels)
        label_line_ids = {label.line_id for label in labels}
        context_lines = []
        for line in (state.lines or []):
            if line.line_id in label_line_ids:
                context_lines.append(f"Line {line.line_id}: {line.text}")

        receipt_text_snippet = "\n".join(context_lines)
        if not receipt_text_snippet:
            receipt_text_snippet = state.formatted_text or ""

        # Create TransactionLabel objects for CoVe validation
        transaction_labels_to_validate = []
        missing_word_details = []
        for label in labels:
            key = (label.line_id, label.word_id)
            word_text = word_text_lookup.get(key, "")
            if not word_text:
                missing_word_details.append(f"line_id={label.line_id}, word_id={label.word_id}")
                continue

            try:
                # Try to get the TransactionLabelType enum value
                label_type_enum = getattr(TransactionLabelType, label_type, None)
                if not label_type_enum:
                    logger.warning(f"   ⚠️  Label type {label_type} not in TransactionLabelType enum, skipping")
                    continue

                # Create TransactionLabel with proper enum type
                transaction_label = TransactionLabel(
                    word_text=word_text,
                    label_type=label_type_enum,  # Use enum, not string
                    confidence=0.9,  # Assume high confidence for existing labels
                    reasoning=f"Existing label on line {label.line_id}, word {label.word_id}",
                    cove_verified=False,  # Will be set by CoVe
                )
                transaction_labels_to_validate.append(transaction_label)
            except Exception as e:
                logger.warning(f"   ⚠️  Failed to create TransactionLabel for {label_type}: {e}")
                continue

        if not transaction_labels_to_validate:
            # No word text found - can't validate, mark as INVALID
            if missing_word_details:
                logger.warning(
                    f"   ⚠️  No word text found for {len(labels)} {label_type} labels "
                    f"(missing words: {', '.join(missing_word_details[:3])}"
                    f"{'...' if len(missing_word_details) > 3 else ''}), marking as INVALID"
                )
            else:
                logger.warning(f"   ⚠️  No valid {label_type} labels to validate (all failed enum check), marking as INVALID")
            for label in labels:
                label.validation_status = "INVALID"
                invalidated_count += len(labels)
            continue

        # Build CORE_LABELS definitions for context
        # Get relevant label definitions (only TransactionLabelType labels)
        transaction_label_types = [
            TransactionLabelType.DATE.value,
            TransactionLabelType.TIME.value,
            TransactionLabelType.PAYMENT_METHOD.value,
            TransactionLabelType.COUPON.value,
            TransactionLabelType.DISCOUNT.value,
            TransactionLabelType.LOYALTY_ID.value,
            TransactionLabelType.MERCHANT_NAME.value,
            TransactionLabelType.PHONE_NUMBER.value,
            TransactionLabelType.ADDRESS_LINE.value,
            TransactionLabelType.STORE_HOURS.value,
            TransactionLabelType.WEBSITE.value,
        ]
        core_labels_definitions = "\n".join(
            f"- {label}: {CORE_LABELS.get(label, 'N/A')}"
            for label in transaction_label_types
            if label in CORE_LABELS
        )

        # Create enhanced task description with CORE_LABELS context
        task_description = f"""Validating {label_type} labels on receipt.

VALID CORE_LABELS (you can ONLY use these label types):
{core_labels_definitions}

The label type '{label_type}' must match one of the CORE_LABELS above. Verify that each word is correctly labeled according to these definitions."""

        # Create initial PhaseContextResponse for CoVe
        # PhaseContextResponse requires transaction_labels, reasoning, and confidence
        try:
            initial_response = PhaseContextResponse(
                transaction_labels=transaction_labels_to_validate,
                reasoning=f"Validating {len(transaction_labels_to_validate)} {label_type} labels from existing receipt labels",
                confidence=0.9,  # Assume high confidence for existing labels
            )
        except Exception as e:
            logger.warning(f"   ⚠️  Failed to create PhaseContextResponse for {label_type}: {e}, marking as INVALID")
            for label in labels:
                label.validation_status = "INVALID"
                invalidated_count += len(labels)
            continue

        # Run CoVe to verify these labels
        try:
            response, cove_verified = await apply_chain_of_verification(
                initial_answer=initial_response,
                receipt_text=receipt_text_snippet,
                task_description=task_description,
                response_model=PhaseContextResponse,
                llm=llm,
                enable_cove=True,
            )

            if cove_verified:
                # CoVe verified the labels - check if they match
                # Create lookup of validated labels
                # Use normalized word_text for matching (case-insensitive, whitespace-stripped)
                validated_word_texts = {}
                for item in response.transaction_labels:
                    normalized_text = item.word_text.strip().upper()
                    validated_word_texts[normalized_text] = item.label_type

                # Check each pending label
                for label in labels:
                    word_text = word_text_lookup.get((label.line_id, label.word_id), "")
                    if not word_text:
                        # No word text found - can't validate, mark as INVALID
                        label.validation_status = "INVALID"
                        invalidated_count += 1
                        continue

                    # Normalize word_text for comparison
                    normalized_word_text = word_text.strip().upper()

                    if normalized_word_text in validated_word_texts:
                        validated_type = validated_word_texts[normalized_word_text]
                        # Convert enum to string for comparison
                        validated_type_str = str(validated_type).upper()
                        if validated_type_str == label_type:
                            # CoVe confirmed this label is correct
                            label.validation_status = "VALID"
                            validated_count += 1
                        else:
                            # CoVe says this word has a different label type
                            label.validation_status = "INVALID"
                            invalidated_count += 1
                    else:
                        # CoVe didn't find this word in the validated labels
                        # Since CoVe verified but didn't include this word, it's likely invalid
                        # Mark as INVALID instead of NEEDS_REVIEW
                        label.validation_status = "INVALID"
                        invalidated_count += 1
            else:
                # CoVe failed or couldn't verify
                # Since we can't verify these labels, mark them as INVALID
                logger.warning(f"   ⚠️  CoVe failed to verify {label_type} labels, marking as INVALID")
                for label in labels:
                    label.validation_status = "INVALID"
                    invalidated_count += len(labels)

        except Exception as e:
            # CoVe validation exception - mark as INVALID since we can't verify
            logger.warning(f"   ⚠️  CoVe validation failed for {label_type}: {e}, marking as INVALID")
            for label in labels:
                label.validation_status = "INVALID"
                invalidated_count += len(labels)

    logger.info(
        f"   📊 CoVe Validation Results: "
        f"✅ {validated_count} validated, "
        f"❌ {invalidated_count} invalidated, "
        f"🔍 {needs_review_count} needs review"
    )

    return {
        "receipt_word_labels_to_add": labels_to_add,
        "receipt_word_labels_to_update": labels_to_update,
        "cove_validation_stats": {
            "validated": validated_count,
            "invalidated": invalidated_count,
            "needs_review": needs_review_count,
        },
    }


def create_validation_cove_graph(
    ollama_api_key: str,
) -> CompiledStateGraph:
    """
    Create a validation-only LangGraph workflow for CoVe.

    This graph validates existing PENDING labels without re-analyzing the receipt.
    """
    workflow = StateGraph(CurrencyAnalysisState)

    # Create node with API key from closure
    async def validate_with_key(state: CurrencyAnalysisState):
        return await validate_labels_with_cove(state, ollama_api_key)

    workflow.add_node("validate_labels_cove", validate_with_key)
    workflow.add_edge(START, "validate_labels_cove")
    workflow.add_edge("validate_labels_cove", END)

    return workflow.compile()


async def validate_pending_labels_cove_simple(
    pending_labels: List[ReceiptWordLabel],
    lines: List,
    words: List,
    receipt_text: str,
    ollama_api_key: str,
    langsmith_api_key: Optional[str] = None,
) -> List[ReceiptWordLabel]:
    """
    Simple validation function for PENDING labels using CoVe.

    This validates labels without creating a full LangGraph workflow.
    """
    # Setup LangSmith if provided
    if langsmith_api_key:
        os.environ["LANGCHAIN_API_KEY"] = langsmith_api_key
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        os.environ["LANGCHAIN_PROJECT"] = "validate-pending-labels-cove"

    # Create state
    state = CurrencyAnalysisState(
        image_id=pending_labels[0].image_id if pending_labels else "",
        receipt_id=str(pending_labels[0].receipt_id) if pending_labels else "",
        receipt_word_labels_to_update=pending_labels,
        receipt_word_labels_to_add=[],
        lines=lines,
        words=words,
        formatted_text=receipt_text,
    )

    # Run validation
    result = await validate_labels_with_cove(state, ollama_api_key)

    # Return updated labels
    updated_labels = result.get("receipt_word_labels_to_update", pending_labels)
    return updated_labels

