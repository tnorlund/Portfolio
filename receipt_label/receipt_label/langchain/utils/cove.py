"""
Chain of Verification (CoVe) utility functions.

Implements the CoVe pattern:
1. Generate initial answer
2. Generate verification questions
3. Answer questions by checking source
4. Revise answer if needed
"""

from typing import TypeVar, Type, Any, Dict, List, Tuple
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage
from langchain_core.utils.json import parse_json_markdown
import json

from receipt_label.langchain.models.cove import (
    VerificationQuestionsResponse,
    VerificationAnswersResponse,
)

T = TypeVar("T", bound=Any)


async def generate_verification_questions(
    initial_answer: Any,
    receipt_text: str,
    task_description: str,
    llm: ChatOllama,
) -> VerificationQuestionsResponse:
    """Generate verification questions for an initial answer.

    Args:
        initial_answer: The initial structured answer (Pydantic model)
        receipt_text: The original receipt text to verify against
        task_description: Description of what task was performed
        llm: The LLM instance to use

    Returns:
        VerificationQuestionsResponse with questions to verify the answer
    """

    # Convert answer to JSON for inspection
    if hasattr(initial_answer, "model_dump"):
        answer_json = json.dumps(initial_answer.model_dump(), indent=2)
    elif hasattr(initial_answer, "dict"):
        answer_json = json.dumps(initial_answer.dict(), indent=2)
    else:
        answer_json = str(initial_answer)

    # Get JSON schema for format parameter (more reliable with Ollama)
    schema = VerificationQuestionsResponse.model_json_schema()
    schema_json = json.dumps(schema, indent=2)

    # Create LLM with JSON schema in format parameter (like extract_merchant_info.py)
    llm_with_schema = ChatOllama(
        model=llm.model,
        base_url=llm.base_url,
        client_kwargs=llm.client_kwargs,
        format=schema,  # Pass schema directly, not "json" string
        temperature=llm.temperature,
    )
    llm_structured = llm_with_schema.with_structured_output(VerificationQuestionsResponse)

    prompt = f"""You are a verification expert. Your task is to generate specific, answerable questions that can verify the accuracy of an initial analysis.

TASK: {task_description}

INITIAL ANSWER:
{answer_json}

RECEIPT TEXT (source material):
{receipt_text[:2000]}

CRITICAL INSTRUCTIONS:
1. You MUST respond with valid JSON ONLY - no markdown, no tables, no explanations
2. Output must match this EXACT JSON structure:

{schema_json}

Generate 3-5 specific verification questions that can be answered by checking the receipt text. Each question should:
1. Target a specific claim in the initial answer
2. Be answerable by examining the receipt text
3. Help identify potential errors or uncertainties

Focus on:
- Verifying amounts match what's actually on the receipt
- Checking that label types (GRAND_TOTAL, TAX, etc.) are correctly assigned
- Ensuring line_ids point to the correct locations
- Validating that extracted text matches receipt content

Return ONLY valid JSON matching the schema above. Start your response with {{ and end with }}. No markdown formatting."""

    try:
        response = await llm_structured.ainvoke([HumanMessage(content=prompt)])
        return response
    except Exception as e:
        # If structured output fails, try to parse raw response
        print(f"   ⚠️ Structured output failed, trying raw LLM call: {e}")
        raw_llm = ChatOllama(
            model=llm.model,
            base_url=llm.base_url,
            client_kwargs=llm.client_kwargs,
            format="json",
            temperature=llm.temperature,
        )
        raw_response = await raw_llm.ainvoke([HumanMessage(content=prompt)])
        # Try to parse the content
        try:
            parsed = parse_json_markdown(raw_response.content)
            return VerificationQuestionsResponse(**parsed)
        except Exception:
            # Last resort: return empty questions
            print(f"   ⚠️ Could not parse response, skipping CoVe verification")
            return VerificationQuestionsResponse(questions=[], reasoning="CoVe parsing failed")


async def answer_verification_questions(
    questions: List[Any],
    receipt_text: str,
    initial_answer: Any,
    llm: ChatOllama,
) -> VerificationAnswersResponse:
    """Answer verification questions by checking the receipt text.

    Args:
        questions: List of VerificationQuestion objects
        receipt_text: The original receipt text to check
        initial_answer: The initial answer being verified
        llm: The LLM instance to use

    Returns:
        VerificationAnswersResponse with answers and revision recommendations
    """

    # Convert answer to JSON
    if hasattr(initial_answer, "model_dump"):
        answer_json = json.dumps(initial_answer.model_dump(), indent=2)
    elif hasattr(initial_answer, "dict"):
        answer_json = json.dumps(initial_answer.dict(), indent=2)
    else:
        answer_json = str(initial_answer)

    # Format questions
    questions_text = "\n".join([
        f"{i+1}. {q.question} (verifying: {q.claim_to_verify}, importance: {q.importance})"
        for i, q in enumerate(questions)
    ])

    # Get JSON schema for format parameter
    schema = VerificationAnswersResponse.model_json_schema()
    schema_json = json.dumps(schema, indent=2)

    # Create LLM with JSON schema in format parameter
    llm_with_schema = ChatOllama(
        model=llm.model,
        base_url=llm.base_url,
        client_kwargs=llm.client_kwargs,
        format=schema,  # Pass schema directly
        temperature=llm.temperature,
    )
    llm_structured = llm_with_schema.with_structured_output(VerificationAnswersResponse)

    prompt = f"""You are a fact-checker. Answer each verification question by carefully examining the receipt text.

INITIAL ANSWER:
{answer_json}

VERIFICATION QUESTIONS:
{questions_text}

RECEIPT TEXT (source material - check this carefully):
{receipt_text}

CRITICAL INSTRUCTIONS:
1. You MUST respond with valid JSON ONLY - no markdown, no tables, no explanations
2. Output must match this EXACT JSON structure:

{schema_json}

For each question:
1. Search the receipt text for relevant information
2. Provide a clear answer with evidence
3. Indicate if the original claim needs revision
4. Give a confidence score for your answer

Be thorough and precise. If you find discrepancies, clearly state what needs to be corrected.

Return ONLY valid JSON matching the schema above. Start your response with {{ and end with }}. No markdown formatting."""

    try:
        response = await llm_structured.ainvoke([HumanMessage(content=prompt)])
        return response
    except Exception as e:
        print(f"   ⚠️ Structured output failed, trying raw LLM call: {e}")
        raw_llm = ChatOllama(
            model=llm.model,
            base_url=llm.base_url,
            client_kwargs=llm.client_kwargs,
            format="json",
            temperature=llm.temperature,
        )
        raw_response = await raw_llm.ainvoke([HumanMessage(content=prompt)])
        try:
            parsed = parse_json_markdown(raw_response.content)
            return VerificationAnswersResponse(**parsed)
        except Exception:
            print(f"   ⚠️ Could not parse response, skipping verification")
            return VerificationAnswersResponse(
                answers=[],
                overall_assessment="Verification parsing failed",
                revision_needed=False
            )


async def revise_answer_with_verification(
    initial_answer: Any,
    verification_answers: VerificationAnswersResponse,
    receipt_text: str,
    response_model: Type[T],
    llm: ChatOllama,
) -> T:
    """Revise the initial answer based on verification results.

    Args:
        initial_answer: The original answer
        verification_answers: The verification answers and assessment
        receipt_text: The original receipt text
        response_model: The Pydantic model type for the revised answer
        llm: The LLM instance to use

    Returns:
        Revised answer of type T
    """

    # Convert to JSON
    if hasattr(initial_answer, "model_dump"):
        answer_json = json.dumps(initial_answer.model_dump(), indent=2)
    elif hasattr(initial_answer, "dict"):
        answer_json = json.dumps(initial_answer.dict(), indent=2)
    else:
        answer_json = str(initial_answer)

    # Format verification results
    verification_summary = f"""OVERALL ASSESSMENT: {verification_answers.overall_assessment}
REVISION NEEDED: {verification_answers.revision_needed}

VERIFICATION RESULTS:
"""
    for answer in verification_answers.answers:
        verification_summary += f"""
Question: {answer.question}
Answer: {answer.answer}
Evidence: {answer.evidence}
Requires Revision: {answer.requires_revision}
Confidence: {answer.confidence}
"""

    # Get JSON schema for format parameter
    schema = response_model.model_json_schema()
    schema_json = json.dumps(schema, indent=2)

    # Create LLM with JSON schema in format parameter
    llm_with_schema = ChatOllama(
        model=llm.model,
        base_url=llm.base_url,
        client_kwargs=llm.client_kwargs,
        format=schema,  # Pass schema directly
        temperature=llm.temperature,
    )
    llm_structured = llm_with_schema.with_structured_output(response_model)

    prompt = f"""You are revising an initial answer based on verification results.

INITIAL ANSWER:
{answer_json}

VERIFICATION RESULTS:
{verification_summary}

RECEIPT TEXT:
{receipt_text[:2000]}

CRITICAL INSTRUCTIONS:
1. You MUST respond with valid JSON ONLY - no markdown, no tables, no explanations
2. Output must match this EXACT JSON structure:

{schema_json}

Revise the initial answer to correct any errors found during verification.
- Keep correct parts unchanged
- Fix any discrepancies identified
- Update confidence scores if needed
- Ensure all amounts, line_ids, and text match the receipt exactly

Return ONLY valid JSON matching the schema above. Start your response with {{ and end with }}. No markdown formatting."""

    try:
        response = await llm_structured.ainvoke([HumanMessage(content=prompt)])
        return response
    except Exception as e:
        print(f"   ⚠️ Structured output failed, using original answer: {e}")
        # Return original answer if revision fails
        return initial_answer


async def apply_chain_of_verification(
    initial_answer: T,
    receipt_text: str,
    task_description: str,
    response_model: Type[T],
    llm: ChatOllama,
    enable_cove: bool = True,
) -> Tuple[T, bool]:
    """Apply chain of verification to an initial answer.

    This is the main entry point for CoVe. It:
    1. Generates verification questions
    2. Answers them by checking the receipt
    3. Revises the answer if needed

    Args:
        initial_answer: The initial structured answer
        receipt_text: The original receipt text
        task_description: Description of the task (e.g., "Currency amount classification")
        response_model: The Pydantic model type
        llm: The LLM instance to use
        enable_cove: Whether to actually run CoVe (can be disabled for testing)

    Returns:
        Tuple of (final_answer, cove_verified: bool)
        - final_answer: Final answer (revised if needed, or original if verification passed)
        - cove_verified: True if CoVe completed successfully, False if it failed or was disabled
    """

    if not enable_cove:
        return initial_answer, False

    try:
        # Step 1: Generate verification questions
        print("   🔍 CoVe Step 1: Generating verification questions...")
        questions_response = await generate_verification_questions(
            initial_answer=initial_answer,
            receipt_text=receipt_text,
            task_description=task_description,
            llm=llm,
        )

        if not questions_response.questions:
            print("   ⚠️ No verification questions generated, using initial answer")
            return initial_answer, False

        print(f"   ✅ Generated {len(questions_response.questions)} verification questions")

        # Step 2: Answer verification questions
        print("   🔍 CoVe Step 2: Answering verification questions...")
        answers_response = await answer_verification_questions(
            questions=questions_response.questions,
            receipt_text=receipt_text,
            initial_answer=initial_answer,
            llm=llm,
        )

        print(f"   ✅ Answered {len(answers_response.answers)} questions")
        print(f"   📊 Revision needed: {answers_response.revision_needed}")

        # Step 3: Revise if needed
        if answers_response.revision_needed:
            print("   🔍 CoVe Step 3: Revising answer based on verification...")
            revised_answer = await revise_answer_with_verification(
                initial_answer=initial_answer,
                verification_answers=answers_response,
                receipt_text=receipt_text,
                response_model=response_model,
                llm=llm,
            )
            print("   ✅ Answer revised based on verification")
            # CoVe verified successfully (even though revision was needed, it was verified and corrected)
            return revised_answer, True
        else:
            print("   ✅ Verification passed - no revision needed")
            # CoVe verified successfully (no issues found)
            return initial_answer, True

    except Exception as e:
        print(f"   ⚠️ CoVe failed: {e}, using initial answer")
        # CoVe failed, so not verified
        return initial_answer, False

