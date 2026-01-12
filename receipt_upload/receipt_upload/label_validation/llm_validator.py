"""LLM-based label validation using Ollama with OpenRouter fallback.

This module provides receipt-aware batch validation of pending labels using
the same LLM infrastructure as the label evaluator (gpt-oss:120b-cloud).

The LLM sees:
1. Full receipt text with pending labels highlighted
2. Similar validated words from ChromaDB
3. Merchant context
4. Mathematical relationships between currency amounts

Uses LANGCHAIN_PROJECT env var for LangSmith project configuration.
Traces are sent to the project specified by LANGCHAIN_PROJECT environment variable.
"""

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.messages import HumanMessage

from receipt_agent.constants import CORE_LABELS
from receipt_agent.utils.llm_factory import create_resilient_llm

logger = logging.getLogger(__name__)

# Enable Langsmith tracing if API key is set
if os.environ.get("LANGCHAIN_API_KEY") and not os.environ.get("LANGCHAIN_TRACING_V2"):
    os.environ["LANGCHAIN_TRACING_V2"] = "true"


def _get_traceable():
    """Get the traceable decorator if langsmith is available."""
    try:
        from langsmith.run_helpers import traceable
        return traceable
    except ImportError:
        # Return a no-op decorator if langsmith not installed
        def noop_decorator(*args, **kwargs):
            def wrapper(fn):
                return fn
            return wrapper
        return noop_decorator


def _get_label_validation_project() -> str:
    """Get the Langsmith project name for label validation from env var."""
    return os.environ.get("LANGCHAIN_PROJECT", "receipt-label-validation")


@dataclass
class LLMValidationResult:
    """Result of LLM validation for a single label."""

    word_id: str
    decision: str  # "VALID", "CORRECTED", or "NEEDS_REVIEW"
    label: str  # Final label (original if VALID, corrected if CORRECTED)
    confidence: str  # "high", "medium", "low"
    reasoning: str


def _build_core_labels_prompt() -> str:
    """Build the label definitions section."""
    lines = []
    for label, description in CORE_LABELS.items():
        lines.append(f"- **{label}**: {description}")
    return "\n".join(lines)


def _format_similar_evidence(
    similar_words: List[Dict[str, Any]],
    max_words: int = 5,
) -> str:
    """Format similar word evidence for prompt."""
    if not similar_words:
        return "No similar validated words found."

    lines = []
    for word in similar_words[:max_words]:
        text = word.get("text", word.get("word_text", ""))
        similarity = word.get("similarity", word.get("similarity_score", 0))
        valid_labels = word.get("valid_labels", [])
        merchant = word.get("merchant_name", "Unknown")
        is_same = word.get("is_same_merchant", False)

        merchant_note = "(SAME MERCHANT)" if is_same else ""
        labels_str = ", ".join(valid_labels) if valid_labels else "none"

        lines.append(
            f'  - "{text}" → {labels_str} ({similarity:.0%} similar) '
            f"{merchant} {merchant_note}"
        )

    return "\n".join(lines)


def _format_receipt_text(
    words: List[Dict[str, Any]],
    pending_labels: List[Dict[str, Any]],
) -> Tuple[str, Dict[str, int]]:
    """
    Format receipt text with pending labels highlighted.

    Returns:
        Tuple of (formatted text, word_id to issue_index mapping)
    """
    # Create a set of pending word IDs for quick lookup
    pending_word_ids = {}
    for idx, label in enumerate(pending_labels):
        word_id = f"{label['line_id']}_{label['word_id']}"
        pending_word_ids[word_id] = (idx, label["label"])

    # Group words by line
    lines_dict: Dict[int, List[Dict]] = {}
    for word in words:
        line_id = word.get("line_id", 0)
        if line_id not in lines_dict:
            lines_dict[line_id] = []
        lines_dict[line_id].append(word)

    # Sort lines by y position (top to bottom)
    sorted_lines = sorted(lines_dict.items(), key=lambda x: x[0])

    # Format each line
    output_lines = []
    word_to_index = {}

    for line_id, line_words in sorted_lines:
        # Sort words by x position (left to right)
        line_words.sort(key=lambda w: w.get("x", 0))

        formatted_words = []
        for word in line_words:
            text = word.get("text", "")
            w_id = f"{word.get('line_id', 0)}_{word.get('word_id', 0)}"

            if w_id in pending_word_ids:
                idx, pred_label = pending_word_ids[w_id]
                word_to_index[w_id] = idx
                # Highlight pending labels with brackets and index
                formatted_words.append(f"[{idx}:{text}]({pred_label}?)")
            else:
                formatted_words.append(text)

        output_lines.append(" ".join(formatted_words))

    return "\n".join(output_lines), word_to_index


def _compute_currency_context(words: List[Dict[str, Any]]) -> str:
    """Extract currency amounts and compute mathematical hints."""
    # Simple currency pattern
    currency_pattern = re.compile(r"^\$?\d+\.\d{2}$")

    amounts = []
    for word in words:
        text = word.get("text", "").replace(",", "")
        if currency_pattern.match(text):
            try:
                value = float(text.replace("$", ""))
                amounts.append((text, value, word.get("line_id", 0)))
            except ValueError:
                pass

    if not amounts:
        return "No currency amounts detected."

    # Sort by value descending
    amounts.sort(key=lambda x: -x[1])

    lines = ["Currency amounts found (largest first):"]
    for text, value, line_id in amounts[:10]:
        lines.append(f"  - {text} (line {line_id})")

    # Add sum hint
    total = sum(a[1] for a in amounts)
    lines.append(f"\nSum of all amounts: ${total:.2f}")

    return "\n".join(lines)


def build_validation_prompt(
    pending_labels: List[Dict[str, Any]],
    words: List[Dict[str, Any]],
    similar_evidence: Dict[str, List[Dict[str, Any]]],
    merchant_name: Optional[str],
) -> str:
    """
    Build the receipt-aware batch validation prompt.

    Args:
        pending_labels: List of pending labels to validate
        words: All words in the receipt (with text, line_id, word_id, x, y)
        similar_evidence: Dict mapping word_id to list of similar validated words
        merchant_name: Merchant name for context

    Returns:
        Formatted prompt string
    """
    # Format receipt text with highlights
    receipt_text, word_to_index = _format_receipt_text(words, pending_labels)

    # Build prompt sections
    prompt = f"""# Receipt Label Validation

You are validating predicted labels for a receipt from **{merchant_name or "Unknown Merchant"}**.

## Label Definitions

{_build_core_labels_prompt()}

## Receipt Text

Words marked with [N:text](LABEL?) are pending validation.
N is the issue index, text is the word, LABEL is the predicted label.

```
{receipt_text}
```

## Similar Word Evidence

For each pending label, here are similar words that have been validated:

"""

    # Add evidence for each pending label
    for idx, label in enumerate(pending_labels):
        word_id = f"{label['line_id']}_{label['word_id']}"
        word_text = label.get("word_text", "")
        predicted = label["label"]
        evidence = similar_evidence.get(word_id, [])

        prompt += f"""
### [{idx}] "{word_text}" - Predicted: {predicted}
{_format_similar_evidence(evidence)}
"""

    # Add currency context
    prompt += f"""
## Currency Analysis

{_compute_currency_context(words)}

## Your Task

For each pending label [0] through [{len(pending_labels) - 1}], decide:
- **VALID**: The predicted label is correct
- **CORRECT**: The predicted label is wrong, provide the correct label

Consider:
1. Position on receipt (header labels at top, totals at bottom)
2. Similar word evidence (how were similar words labeled?)
3. Mathematical consistency (do amounts add up correctly?)
4. Merchant patterns (same merchant evidence is most reliable)

Respond with a JSON array:
```json
[
  {{"index": 0, "decision": "VALID", "label": "MERCHANT_NAME", "confidence": "high", "reasoning": "..."}},
  {{"index": 1, "decision": "CORRECT", "label": "LINE_TOTAL", "confidence": "medium", "reasoning": "..."}}
]
```

IMPORTANT:
- Use ONLY labels from the definitions above
- Every pending label MUST have a decision (no PENDING/UNCERTAIN)
- Base reasoning on the evidence provided
"""

    return prompt


def parse_validation_response(
    response_text: str,
    pending_labels: List[Dict[str, Any]],
) -> List[LLMValidationResult]:
    """
    Parse the LLM response into validation results.

    Args:
        response_text: Raw LLM response
        pending_labels: Original pending labels for fallback

    Returns:
        List of LLMValidationResult objects

    Note:
        - Missing labels are marked as NEEDS_REVIEW (not auto-VALID)
        - Out-of-range and duplicate indexes are dropped
        - CORRECT and CORRECTED are normalized to CORRECTED
    """
    results = []
    num_labels = len(pending_labels)

    # Try to extract JSON from response
    json_match = re.search(r"\[[\s\S]*\]", response_text)
    if not json_match:
        logger.warning("No JSON array found in LLM response")
        # Fallback: mark all as NEEDS_REVIEW (not auto-VALID)
        for label in pending_labels:
            word_id = f"{label['line_id']}_{label['word_id']}"
            results.append(
                LLMValidationResult(
                    word_id=word_id,
                    decision="NEEDS_REVIEW",
                    label=label["label"],
                    confidence="low",
                    reasoning="LLM response parsing failed - no JSON found",
                )
            )
        return results

    try:
        parsed = json.loads(json_match.group())
    except json.JSONDecodeError as e:
        logger.warning("Failed to parse LLM JSON: %s", e)
        # Fallback: mark all as NEEDS_REVIEW
        for label in pending_labels:
            word_id = f"{label['line_id']}_{label['word_id']}"
            results.append(
                LLMValidationResult(
                    word_id=word_id,
                    decision="NEEDS_REVIEW",
                    label=label["label"],
                    confidence="low",
                    reasoning=f"LLM response parsing failed: {str(e)[:50]}",
                )
            )
        return results

    # Build result lookup with robust index handling
    # - Coerce index to int
    # - Drop out-of-range indexes
    # - Drop duplicate indexes (keep first)
    result_by_index: Dict[int, Dict[str, Any]] = {}
    seen_indexes: set = set()

    for item in parsed:
        if not isinstance(item, dict):
            continue

        # Coerce index to int
        raw_index = item.get("index")
        try:
            idx = int(raw_index) if raw_index is not None else -1
        except (ValueError, TypeError):
            logger.warning("Invalid index value: %r, skipping", raw_index)
            continue

        # Check range
        if idx < 0 or idx >= num_labels:
            logger.warning(
                "Index %d out of range [0, %d), skipping",
                idx,
                num_labels,
            )
            continue

        # Check duplicates
        if idx in seen_indexes:
            logger.warning("Duplicate index %d, keeping first", idx)
            continue

        seen_indexes.add(idx)
        result_by_index[idx] = item

    # Process each pending label
    for idx, label in enumerate(pending_labels):
        word_id = f"{label['line_id']}_{label['word_id']}"
        llm_result = result_by_index.get(idx)

        if llm_result is None:
            # Missing result - mark as NEEDS_REVIEW, not auto-VALID
            logger.warning(
                "No LLM result for index %d (word_id=%s), marking NEEDS_REVIEW",
                idx,
                word_id,
            )
            results.append(
                LLMValidationResult(
                    word_id=word_id,
                    decision="NEEDS_REVIEW",
                    label=label["label"],
                    confidence="low",
                    reasoning="LLM did not return a decision for this word",
                )
            )
            continue

        # Normalize decision: CORRECT -> CORRECTED
        decision = llm_result.get("decision", "").upper()
        if decision == "CORRECT":
            decision = "CORRECTED"
        elif decision not in ("VALID", "CORRECTED", "NEEDS_REVIEW"):
            logger.warning(
                "Unknown decision '%s' for index %d, treating as NEEDS_REVIEW",
                decision,
                idx,
            )
            decision = "NEEDS_REVIEW"

        final_label = llm_result.get("label", label["label"])
        confidence = llm_result.get("confidence", "medium")
        reasoning = llm_result.get("reasoning", "")

        # Validate label is in CORE_LABELS
        if final_label not in CORE_LABELS:
            logger.warning(
                "LLM returned invalid label '%s', keeping original '%s'",
                final_label,
                label["label"],
            )
            final_label = label["label"]
            # If decision was CORRECTED but label is invalid, mark as NEEDS_REVIEW
            if decision == "CORRECTED":
                decision = "NEEDS_REVIEW"
                reasoning = f"Invalid corrected label '{llm_result.get('label')}'. {reasoning}"

        results.append(
            LLMValidationResult(
                word_id=word_id,
                decision=decision,
                label=final_label,
                confidence=confidence,
                reasoning=reasoning,
            )
        )

    return results


class LLMBatchValidator:
    """
    Validates all pending labels for a receipt using LLM.

    Uses the same Ollama + OpenRouter fallback pattern as the label evaluator.
    """

    def __init__(
        self,
        temperature: float = 0.0,
        timeout: int = 120,
    ):
        """
        Initialize the LLM validator.

        Args:
            temperature: LLM temperature (0.0 for deterministic)
            timeout: Request timeout in seconds
        """
        self.llm = create_resilient_llm(
            temperature=temperature,
            timeout=timeout,
        )
        self._call_count = 0
        self._success_count = 0

    def _call_llm_with_tracing(
        self,
        prompt: str,
        pending_labels: List[Dict[str, Any]],
        merchant_name: Optional[str] = None,
    ) -> str:
        """Call LLM with Langsmith tracing.

        This method is traced to capture the full prompt and response
        in Langsmith for debugging and improvement.
        """
        traceable = _get_traceable()

        @traceable(
            project_name=_get_label_validation_project(),
            name="llm_batch_validation",
        )
        def _traced_llm_call(
            prompt: str,
            label_count: int,
            merchant: Optional[str],
        ) -> dict:
            """Traced LLM call - captures prompt and response."""
            response = self.llm.invoke([HumanMessage(content=prompt)])
            response_text = response.content.strip()
            return {
                "prompt": prompt,
                "response": response_text,
                "label_count": label_count,
                "merchant": merchant,
            }

        result = _traced_llm_call(
            prompt=prompt,
            label_count=len(pending_labels),
            merchant=merchant_name,
        )
        return result["response"]

    def validate_receipt_labels(
        self,
        pending_labels: List[Dict[str, Any]],
        words: List[Dict[str, Any]],
        similar_evidence: Dict[str, List[Dict[str, Any]]],
        merchant_name: Optional[str] = None,
    ) -> List[LLMValidationResult]:
        """
        Validate all pending labels for a receipt in one LLM call.

        Args:
            pending_labels: List of pending labels with:
                - line_id, word_id, label, word_text
            words: All words in the receipt with:
                - text, line_id, word_id, x, y
            similar_evidence: Dict mapping word_id to list of similar words
            merchant_name: Merchant name for context

        Returns:
            List of LLMValidationResult objects
        """
        if not pending_labels:
            return []

        self._call_count += 1

        # Build prompt
        prompt = build_validation_prompt(
            pending_labels=pending_labels,
            words=words,
            similar_evidence=similar_evidence,
            merchant_name=merchant_name,
        )

        logger.info(
            "Validating %d pending labels for %s",
            len(pending_labels),
            merchant_name or "unknown merchant",
        )

        try:
            # Call LLM with tracing
            response_text = self._call_llm_with_tracing(
                prompt=prompt,
                pending_labels=pending_labels,
                merchant_name=merchant_name,
            )

            # Parse response
            results = parse_validation_response(response_text, pending_labels)
            self._success_count += 1

            # Log summary
            valid_count = sum(1 for r in results if r.decision == "VALID")
            correct_count = sum(1 for r in results if r.decision == "CORRECT")
            logger.info(
                "LLM validation complete: %d VALID, %d CORRECTED",
                valid_count,
                correct_count,
            )

            return results

        except Exception as e:
            logger.error("LLM validation failed: %s", e)
            # Return fallback results - mark all as valid with low confidence
            return [
                LLMValidationResult(
                    word_id=f"{label['line_id']}_{label['word_id']}",
                    decision="VALID",
                    label=label["label"],
                    confidence="low",
                    reasoning=f"LLM call failed: {str(e)[:100]}",
                )
                for label in pending_labels
            ]

    def get_stats(self) -> Dict[str, Any]:
        """Get LLM call statistics."""
        return {
            "call_count": self._call_count,
            "success_count": self._success_count,
            "llm_stats": self.llm.get_stats(),
        }
