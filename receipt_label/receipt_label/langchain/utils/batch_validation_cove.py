"""Batch validation functions that reuse CoVe verification templates."""

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

from langchain_ollama import ChatOllama
from receipt_dynamo import DynamoClient
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel
from receipt_dynamo.entities.receipt_word_label_cove_verification import (
    ReceiptWordLabelCoVeVerification,
)
from receipt_label.langchain.models.cove import (
    VerificationAnswer,
    VerificationQuestion,
    VerificationAnswersResponse,
    VerificationQuestionsResponse,
)
from receipt_label.langchain.utils.cove import answer_verification_questions

logger = logging.getLogger(__name__)


async def validate_labels_using_cove_templates(
    pending_labels: List[ReceiptWordLabel],
    receipt_text: str,
    llm: ChatOllama,
    dynamo_client: DynamoClient,
    word_text_lookup: Dict[Tuple[int, int], str],
    merchant_name: Optional[str] = None,
) -> List[ReceiptWordLabel]:
    """
    Validate PENDING labels by reusing CoVe verification questions from similar labels.

    Strategy:
    1. Group pending labels by label_type
    2. For each label_type, find successful CoVe verifications (templates)
    3. Reuse verification questions from successful verifications
    4. Answer questions for new labels
    5. Validate based on answers

    Args:
        pending_labels: List of PENDING labels to validate
        receipt_text: Full receipt text for context
        llm: LLM instance for answering questions
        dynamo_client: DynamoDB client for querying CoVe records
        word_text_lookup: Dictionary mapping (line_id, word_id) to word text
        merchant_name: Optional merchant name for merchant-specific templates

    Returns:
        List of validated labels with updated validation_status
    """

    # Group by label type
    labels_by_type: Dict[str, List[ReceiptWordLabel]] = {}
    for label in pending_labels:
        label_type = label.label.upper()
        if label_type not in labels_by_type:
            labels_by_type[label_type] = []
        labels_by_type[label_type].append(label)

    validated_labels = []

    for label_type, labels in labels_by_type.items():
        logger.info(f"   🔍 Validating {len(labels)} {label_type} labels using CoVe templates...")

        # Find successful CoVe verifications for this label type
        templates: List[ReceiptWordLabelCoVeVerification] = []

        # Try merchant-specific templates first
        if merchant_name:
            try:
                merchant_templates, _ = dynamo_client.query_cove_verifications_by_merchant(
                    merchant_name=merchant_name,
                    label=label_type,
                    can_be_reused_as_template=True,
                    limit=10,
                )
                templates.extend(merchant_templates)
            except Exception as e:
                logger.debug(f"   ⚠️  Could not query merchant templates: {e}")

        # Fall back to general templates if no merchant-specific ones found
        if not templates:
            try:
                general_templates, _ = dynamo_client.query_cove_verifications_by_label(
                    label=label_type,
                    cove_verified=True,
                    limit=10,
                )
                # Filter to only reusable templates
                templates = [
                    t for t in general_templates
                    if t.can_be_reused_as_template and t.final_label == t.initial_label
                ]
            except Exception as e:
                logger.debug(f"   ⚠️  Could not query general templates: {e}")

        if not templates:
            logger.info(f"   ⏭️  No CoVe templates found for {label_type}, skipping template validation")
            validated_labels.extend(labels)
            continue

        # Use the most recent template with highest applicability score
        best_template = max(
            templates,
            key=lambda t: (t.template_applicability_score, t.verification_timestamp)
        )

        logger.info(f"   📋 Using template from {best_template.verification_timestamp} (score: {best_template.template_applicability_score:.2f})")

        # Convert stored questions back to VerificationQuestion objects
        verification_questions = []
        for q_dict in best_template.verification_questions:
            try:
                question = VerificationQuestion(
                    question=q_dict.get("question", ""),
                    claim_to_verify=q_dict.get("claim_to_verify", ""),
                    importance=q_dict.get("importance", 0.5),
                )
                verification_questions.append(question)
            except Exception as e:
                logger.debug(f"   ⚠️  Could not parse verification question: {e}")
                continue

        if not verification_questions:
            logger.info(f"   ⏭️  No valid verification questions in template, skipping")
            validated_labels.extend(labels)
            continue

        # For each pending label, answer the template questions
        # Process sequentially to avoid rate limiting (with small delay between labels)
        for i, label in enumerate(labels):
            # Add small delay between labels to avoid burst requests to Ollama API
            # Skip delay for first label
            if i > 0:
                await asyncio.sleep(0.5)  # 500ms delay between label validations

            word_text = word_text_lookup.get((label.line_id, label.word_id), "")
            if not word_text:
                label.validation_status = "INVALID"
                validated_labels.append(label)
                continue

            try:
                # Create a questions response for this label
                questions_response = VerificationQuestionsResponse(
                    questions=verification_questions,
                    reasoning=f"Reusing questions from template verification at {best_template.verification_timestamp}",
                )

                # Create a minimal initial answer for this label
                # This is a simplified approach - in practice you'd create a proper response model
                initial_answer_dict = {
                    "word_text": word_text,
                    "label_type": label_type,
                    "line_id": label.line_id,
                    "word_id": label.word_id,
                }

                # Answer verification questions for this specific label
                answers_response = await answer_verification_questions(
                    questions=verification_questions,
                    receipt_text=receipt_text,
                    initial_answer=initial_answer_dict,
                    llm=llm,
                )

                # Determine validation status based on answers
                if all(
                    a.confidence >= 0.8 and not a.requires_revision
                    for a in answers_response.answers
                ):
                    # High confidence, no revisions needed
                    label.validation_status = "VALID"
                elif any(a.requires_revision for a in answers_response.answers):
                    # Revisions needed - likely invalid
                    label.validation_status = "INVALID"
                elif all(a.confidence >= 0.6 for a in answers_response.answers):
                    # Medium confidence - keep as PENDING for manual review
                    label.validation_status = "PENDING"
                else:
                    # Low confidence - mark as INVALID
                    label.validation_status = "INVALID"

            except Exception as e:
                logger.warning(f"   ⚠️  Failed to validate label {label.label} using template: {e}")
                label.validation_status = "PENDING"

            validated_labels.append(label)

    return validated_labels


async def validate_labels_by_merchant_template(
    pending_labels: List[ReceiptWordLabel],
    merchant_name: str,
    receipt_text: str,
    llm: ChatOllama,
    dynamo_client: DynamoClient,
    word_text_lookup: Dict[Tuple[int, int], str],
) -> List[ReceiptWordLabel]:
    """
    Use CoVe verification templates from other receipts by the same merchant.

    This leverages merchant-specific patterns (e.g., Costco always puts
    GRAND_TOTAL at bottom, Trader Joe's formats dates differently).

    Args:
        pending_labels: Labels to validate
        merchant_name: Merchant name for template lookup
        receipt_text: Receipt text for context
        llm: LLM instance
        dynamo_client: DynamoDB client
        word_text_lookup: Word text lookup dictionary

    Returns:
        List of validated labels
    """
    return await validate_labels_using_cove_templates(
        pending_labels=pending_labels,
        receipt_text=receipt_text,
        llm=llm,
        dynamo_client=dynamo_client,
        word_text_lookup=word_text_lookup,
        merchant_name=merchant_name,
    )


async def validate_using_cove_patterns(
    pending_labels: List[ReceiptWordLabel],
    receipt_words: List[Any],
    dynamo_client: DynamoClient,
    similarity_threshold: float = 0.7,
) -> List[ReceiptWordLabel]:
    """
    Validate labels by matching patterns from successful CoVe verifications.

    Example:
    - If CoVe verified "24.01" as GRAND_TOTAL with questions about position
    - And we have "24.01" as PENDING GRAND_TOTAL in another receipt
    - We can apply similar verification logic

    Args:
        pending_labels: Labels to validate
        receipt_words: List of ReceiptWord objects
        dynamo_client: DynamoDB client
        similarity_threshold: Minimum similarity score (0-1)

    Returns:
        List of validated labels
    """

    # Build word text lookup
    word_lookup = {
        (w.line_id, w.word_id): w.text
        for w in receipt_words
    }

    validated = []

    for label in pending_labels:
        word_text = word_lookup.get((label.line_id, label.word_id), "")
        if not word_text:
            label.validation_status = "INVALID"
            validated.append(label)
            continue

        # Find similar successful CoVe verifications
        try:
            # Query by label type
            similar_cove, _ = dynamo_client.query_cove_verifications_by_label(
                label=label.label,
                cove_verified=True,
                limit=50,
            )

            # Filter to successful verifications with similar word text
            # Simple text similarity (in production, use embeddings)
            similar_verifications = []
            for cove_record in similar_cove:
                if cove_record.word_text:
                    # Simple similarity: check if word texts are similar
                    if cove_record.word_text.lower().strip() == word_text.lower().strip():
                        similar_verifications.append(cove_record)

            if similar_verifications:
                # Check success rate
                success_count = sum(
                    1 for v in similar_verifications
                    if v.cove_verified and v.final_label == v.initial_label
                )
                success_rate = success_count / len(similar_verifications)

                if success_rate >= 0.8:
                    # High success rate - likely valid
                    label.validation_status = "VALID"
                elif success_rate <= 0.2:
                    # Low success rate - likely invalid
                    label.validation_status = "INVALID"
                else:
                    # Medium success rate - keep as PENDING
                    label.validation_status = "PENDING"
            else:
                # No similar verifications found - keep as PENDING
                label.validation_status = "PENDING"

        except Exception as e:
            logger.warning(f"   ⚠️  Failed to validate label using patterns: {e}")
            label.validation_status = "PENDING"

        validated.append(label)

    return validated

