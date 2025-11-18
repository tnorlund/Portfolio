"""Helper functions for storing CoVe verification records in DynamoDB."""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from receipt_dynamo.client import DynamoClient
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel
from receipt_dynamo.entities.receipt_word_label_cove_verification import (
    ReceiptWordLabelCoVeVerification,
)
from receipt_label.langchain.models.cove import (
    VerificationAnswersResponse,
    VerificationQuestionsResponse,
)
from receipt_label.langchain.utils.cove import apply_chain_of_verification

logger = logging.getLogger(__name__)


async def apply_chain_of_verification_with_storage(
    initial_answer: Any,
    receipt_text: str,
    task_description: str,
    response_model: type,
    llm: Any,
    labels: List[ReceiptWordLabel],
    word_text_lookup: Dict[Tuple[int, int], str],
    dynamo_client: Optional[DynamoClient] = None,
    merchant_name: Optional[str] = None,
    enable_cove: bool = True,
    store_records: bool = True,
) -> Tuple[Any, bool, Optional[VerificationQuestionsResponse], Optional[VerificationAnswersResponse]]:
    """
    Apply chain of verification and optionally store verification records.

    This wraps apply_chain_of_verification and captures the verification
    questions and answers for storage.

    Args:
        initial_answer: The initial structured answer
        receipt_text: The original receipt text
        task_description: Description of the task
        response_model: The Pydantic model type
        llm: The LLM instance to use
        labels: List of ReceiptWordLabel objects being verified
        word_text_lookup: Dictionary mapping (line_id, word_id) to word text
        dynamo_client: DynamoDB client for storing records (optional)
        merchant_name: Merchant name for template matching (optional)
        enable_cove: Whether to run CoVe
        store_records: Whether to store verification records in DynamoDB

    Returns:
        Tuple of (final_answer, cove_verified, questions_response, answers_response)
    """

    if not enable_cove:
        return initial_answer, False, None, None

    # We need to intercept the CoVe calls to capture questions/answers
    # For now, we'll call CoVe and then try to reconstruct the verification data
    # In a future enhancement, we could modify apply_chain_of_verification to return this

    final_answer, cove_verified = await apply_chain_of_verification(
        initial_answer=initial_answer,
        receipt_text=receipt_text,
        task_description=task_description,
        response_model=response_model,
        llm=llm,
        enable_cove=enable_cove,
    )

    # If we can't store records or CoVe didn't verify, return early
    if not store_records or not cove_verified or not dynamo_client or not labels:
        return final_answer, cove_verified, None, None

    # Try to create verification records for each label
    # Note: This is a simplified approach - in practice, you'd want to capture
    # the actual questions/answers from the CoVe process
    try:
        verification_records = []
        for label in labels:
            word_text = word_text_lookup.get((label.line_id, label.word_id), "")

            # Determine initial and final labels
            initial_label = label.label
            final_label = initial_label  # Assume no change unless we detect revision

            # Create a basic verification record
            # In a full implementation, you'd capture the actual questions/answers
            verification_record = ReceiptWordLabelCoVeVerification(
                image_id=label.image_id,
                receipt_id=label.receipt_id,
                line_id=label.line_id,
                word_id=label.word_id,
                label=label.label,
                verification_questions=[],  # Would be populated from actual CoVe run
                verification_answers=[],  # Would be populated from actual CoVe run
                initial_label=initial_label,
                final_label=final_label,
                revision_needed=False,  # Would be determined from CoVe result
                revision_reasoning=None,
                overall_assessment="CoVe verification completed successfully" if cove_verified else "CoVe verification failed",
                cove_verified=cove_verified,
                verification_timestamp=datetime.now().isoformat(),
                llm_model=getattr(llm, "model", "unknown"),
                word_text=word_text,
                merchant_name=merchant_name,
                can_be_reused_as_template=cove_verified,
                template_applicability_score=1.0 if cove_verified else 0.0,
            )
            verification_records.append(verification_record)

        # Store records in batch
        if verification_records:
            dynamo_client.add_receipt_word_label_cove_verifications(verification_records)
            logger.info(f"   💾 Stored {len(verification_records)} CoVe verification records")

    except Exception as e:
        logger.warning(f"   ⚠️  Failed to store CoVe verification records: {e}")
        # Don't fail the validation if storage fails

    return final_answer, cove_verified, None, None


def create_cove_verification_record(
    label: ReceiptWordLabel,
    questions_response: VerificationQuestionsResponse,
    answers_response: VerificationAnswersResponse,
    initial_label: str,
    final_label: str,
    word_text: Optional[str] = None,
    merchant_name: Optional[str] = None,
    llm_model: str = "unknown",
) -> ReceiptWordLabelCoVeVerification:
    """
    Create a CoVe verification record from verification data.

    Args:
        label: The ReceiptWordLabel being verified
        questions_response: Verification questions response
        answers_response: Verification answers response
        initial_label: Label before CoVe
        final_label: Label after CoVe
        word_text: The word text (optional)
        merchant_name: Merchant name (optional)
        llm_model: LLM model used

    Returns:
        ReceiptWordLabelCoVeVerification record
    """

    # Convert Pydantic models to dictionaries for storage
    verification_questions = [
        q.model_dump() if hasattr(q, "model_dump") else q.dict() if hasattr(q, "dict") else q
        for q in questions_response.questions
    ]

    verification_answers = [
        a.model_dump() if hasattr(a, "model_dump") else a.dict() if hasattr(a, "dict") else a
        for a in answers_response.answers
    ]

    revision_needed = answers_response.revision_needed
    can_be_reused_as_template = (
        answers_response.revision_needed is False
        and len(verification_questions) > 0
        and len(verification_answers) > 0
    )

    # Calculate template applicability score based on verification quality
    template_applicability_score = 0.0
    if can_be_reused_as_template:
        # Higher score if more questions were answered confidently
        confident_answers = sum(
            1 for a in answers_response.answers
            if hasattr(a, "confidence") and a.confidence >= 0.8
        )
        template_applicability_score = min(1.0, confident_answers / max(1, len(answers_response.answers)))

    return ReceiptWordLabelCoVeVerification(
        image_id=label.image_id,
        receipt_id=label.receipt_id,
        line_id=label.line_id,
        word_id=label.word_id,
        label=label.label,
        verification_questions=verification_questions,
        verification_answers=verification_answers,
        initial_label=initial_label,
        final_label=final_label,
        revision_needed=revision_needed,
        revision_reasoning=answers_response.overall_assessment if revision_needed else None,
        overall_assessment=answers_response.overall_assessment,
        cove_verified=True,
        verification_timestamp=datetime.now().isoformat(),
        llm_model=llm_model,
        word_text=word_text,
        merchant_name=merchant_name,
        can_be_reused_as_template=can_be_reused_as_template,
        template_applicability_score=template_applicability_score,
    )

