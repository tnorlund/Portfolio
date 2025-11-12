# Chain of Verification (CoVe) Implementation

## Overview

Chain of Verification (CoVe) is a technique that improves LLM accuracy by having the model verify its own outputs. This implementation applies CoVe to receipt analysis tasks in Phase 1 (currency classification) and Phase 2 (line item extraction).

## How It Works

CoVe follows a 3-step process:

1. **Generate Initial Answer**: The LLM produces an initial structured answer (e.g., currency labels or line item labels)
2. **Generate Verification Questions**: The LLM generates specific questions to verify claims in the initial answer
3. **Answer Questions & Revise**: The LLM answers the verification questions by checking the receipt text, then revises the initial answer if discrepancies are found

## Architecture

### Models (`receipt_label/langchain/models/cove.py`)

- `VerificationQuestion`: A question targeting a specific claim
- `VerificationAnswer`: Answer to a verification question with evidence
- `VerificationQuestionsResponse`: Collection of verification questions
- `VerificationAnswersResponse`: Collection of answers with revision recommendations

### Utility Functions (`receipt_label/langchain/utils/cove.py`)

- `generate_verification_questions()`: Generates questions to verify an initial answer
- `answer_verification_questions()`: Answers questions by checking receipt text
- `revise_answer_with_verification()`: Revises the initial answer based on verification results
- `apply_chain_of_verification()`: Main entry point that orchestrates all three steps

## Integration

### Phase 1: Currency Analysis

CoVe is integrated into `phase1_currency_analysis()`:

```python
# After generating initial response
if enable_cove:
    response = await apply_chain_of_verification(
        initial_answer=initial_response,
        receipt_text=state.formatted_text,
        task_description="Currency amount classification...",
        response_model=Phase1Response,
        llm=llm,
        enable_cove=True,
    )
```

**What gets verified:**
- Currency amounts match what's on the receipt
- Label types (GRAND_TOTAL, TAX, etc.) are correctly assigned
- Line IDs point to correct locations
- Extracted text matches receipt content

### Phase 2: Line Item Analysis

CoVe is integrated into `phase2_line_analysis()`:

```python
# After generating initial response
if enable_cove:
    response = await apply_chain_of_verification(
        initial_answer=initial_response,
        receipt_text=verification_context,  # Target snippet + full receipt
        task_description="Line item component extraction...",
        response_model=Phase2Response,
        llm=llm,
        enable_cove=True,
    )
```

**What gets verified:**
- Product names match the receipt text
- Quantities are correctly identified
- Unit prices are accurate
- Words are correctly classified

## Usage

### Enabling/Disabling CoVe

CoVe is enabled by default but can be disabled:

```python
# Disable CoVe for Phase 1
await phase1_currency_analysis(state, ollama_api_key, enable_cove=False)

# Disable CoVe for Phase 2 (via send_data or function parameter)
# Note: Currently defaults to True, can be modified in the function signature
```

### Configuration

CoVe uses the same LLM instance as the main analysis, so it respects:
- Model selection (120b for Phase 1, 20b for Phase 2)
- Temperature settings
- Timeout settings
- API key configuration

## Benefits

1. **Improved Accuracy**: Catches errors by verifying claims against source material
2. **Self-Correction**: Automatically revises answers when discrepancies are found
3. **Transparency**: Provides evidence for verification decisions
4. **Flexibility**: Can be enabled/disabled per phase or globally

## Performance Considerations

- **Latency**: Adds 2-3 additional LLM calls per phase (question generation, answering, revision)
- **Cost**: Increases token usage by ~3x for each phase
- **Reliability**: Falls back to initial answer if CoVe fails

## Example Flow

```
Phase 1: Currency Analysis
├─ Initial Answer: "GRAND_TOTAL: 24.01 at line 29"
├─ Verification Questions:
│  ├─ "Is 24.01 actually at the bottom of the receipt?"
│  ├─ "Does line 29 contain the amount 24.01?"
│  └─ "Is there a larger amount that might be the grand total?"
├─ Verification Answers:
│  ├─ "Yes, line 29 is at bottom and contains 24.01"
│  ├─ "Confirmed: line 29 has 24.01"
│  └─ "No larger amounts found"
└─ Final Answer: (revised if needed, or original if verified)
```

## Error Handling

CoVe is designed to be non-blocking:
- If question generation fails → uses initial answer
- If verification fails → uses initial answer
- If revision fails → uses initial answer

All errors are logged but don't stop the workflow.

## Future Enhancements

Potential improvements:
1. **Selective Verification**: Only verify high-confidence or critical claims
2. **Parallel Verification**: Answer multiple questions in parallel
3. **Confidence Thresholds**: Only revise if confidence drops below threshold
4. **Verification Caching**: Cache verification results for similar receipts
5. **Multi-Model Verification**: Use different models for verification vs. generation

## References

- Original CoVe paper: "Chain-of-Verification Reduces Hallucination in Large Language Models"
- Implementation inspired by the CoVe pattern for structured outputs

