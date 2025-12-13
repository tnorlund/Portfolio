# LLM-Driven Financial Discovery Sub-Agent

## Overview

The LLM-Driven Financial Discovery Sub-Agent represents a fundamental shift from rule-based to reasoning-based financial value identification on receipts. Instead of relying on hard-coded patterns and position rules, this sub-agent uses Large Language Model (LLM) reasoning to understand receipt structure and identify financial values.

## Key Innovation

**Traditional Approach**: Hard-coded rules like "look for 'TOTAL' keyword at bottom right"
**LLM-Driven Approach**: "Analyze this receipt structure and reason about which numbers represent which financial types based on context, positioning, and business logic"

## Architecture

### Core Components

1. **Structure Analyzer**: Examines receipt text, words, and table structure
2. **Numeric Discovery Engine**: Finds all numeric values with positional context
3. **LLM Reasoning Engine**: Applies sophisticated reasoning to assign financial types
4. **Mathematical Validator**: Verifies that assignments follow receipt mathematics
5. **Context Generator**: Produces rich context for downstream label assignment

### Workflow

```text
Receipt Data → Structure Analysis → Numeric Discovery → LLM Reasoning → Math Validation → Financial Context
```

## Key Features

### 🧠 **Reasoning-First Design**
- Uses LLM's understanding of receipt patterns and business logic
- Adapts to different receipt formats without code changes
- Considers context clues beyond simple keyword matching

### 🔍 **Discovery Without Labels**
- Works with sparse or completely missing labels
- Identifies financial values from raw text and structure
- Provides candidates for label assignment rather than requiring existing labels

### 🧮 **Mathematical Verification**
- Tests mathematical relationships to validate reasoning
- Ensures GRAND_TOTAL = SUBTOTAL + TAX (±$0.01 tolerance)
- Validates SUBTOTAL = sum of LINE_TOTAL values
- Checks line-item math: QUANTITY × UNIT_PRICE = LINE_TOTAL

### 📊 **Rich Context Output**
- Provides detailed reasoning for each assignment
- Includes confidence scores and positioning information
- Generates structured context for label sub-agents

### 🔄 **Self-Validation**
- Agent tests its own conclusions through mathematical verification
- Adjusts confidence based on verification results
- Provides transparent reasoning for each decision

## Input Requirements

### Required Inputs
- **Receipt Text**: Complete receipt text content
- **Words**: Word-level data with line_id, word_id, text, and positions
- **Labels**: Existing labels (can be empty or sparse)

### Optional Inputs
- **Table Structure**: Column/row analysis from table sub-agent (enhances accuracy)

## Output Format

```python
{
    "financial_candidates": {
        "GRAND_TOTAL": [
            {
                "line_id": 25,
                "word_id": 3,
                "value": 45.67,
                "confidence": 0.95,
                "reasoning": "This is the largest value at the bottom of the receipt, appearing after 'TOTAL'"
            }
        ],
        "SUBTOTAL": [
            {
                "line_id": 23,
                "word_id": 2,
                "value": 42.18,
                "confidence": 0.90,
                "reasoning": "This appears before tax with 'SUBTOTAL' text, and is close to sum of line items"
            }
        ],
        "TAX": [
            {
                "line_id": 24,
                "word_id": 2,
                "value": 3.49,
                "confidence": 0.85,
                "reasoning": "This follows subtotal and precedes total, with 'TAX' in context"
            }
        ],
        "LINE_TOTAL": [
            {
                "line_id": 10,
                "word_id": 4,
                "value": 12.99,
                "confidence": 0.80,
                "reasoning": "Right-aligned amount at end of product line"
            },
            {
                "line_id": 12,
                "word_id": 3,
                "value": 8.50,
                "confidence": 0.75,
                "reasoning": "Follows product description in structured line item"
            }
        ]
    },
    "mathematical_validation": {
        "verified": 2,
        "total_tests": 2,
        "all_valid": true,
        "success_rate": 1.0,
        "test_details": [
            {
                "test_name": "GRAND_TOTAL = SUBTOTAL + TAX",
                "passes": true,
                "grand_total": 45.67,
                "subtotal": 42.18,
                "tax": 3.49,
                "calculated_total": 45.67,
                "difference": 0.00
            }
        ]
    },
    "currency": "USD",
    "llm_reasoning": {
        "structure_analysis": "Receipt shows standard grocery format with line items followed by subtotal, tax, and grand total...",
        "final_assessment": "High confidence in financial assignments based on clear structure and mathematical validation",
        "confidence": "high"
    },
    "summary": {
        "total_financial_values": 6,
        "financial_types_identified": ["GRAND_TOTAL", "SUBTOTAL", "TAX", "LINE_TOTAL"],
        "avg_confidence": 0.85,
        "mathematical_validity": "valid"
    }
}
```

## Available Tools

### 1. `analyze_receipt_structure()`
**Purpose**: Get comprehensive view of receipt structure
**Output**: Receipt text, word counts, line previews, table structure summary

### 2. `identify_numeric_candidates()`
**Purpose**: Find all numeric values with positional context
**Output**: Numeric values with line context, positioning, and statistics

### 3. `reason_about_financial_layout(reasoning, candidate_assignments)`
**Purpose**: Apply LLM reasoning to assign financial types
**Input**: Detailed reasoning string + list of proposed assignments
**Output**: Organized assignments by financial type

### 4. `test_mathematical_relationships()`
**Purpose**: Verify mathematical correctness of assignments
**Output**: Validation results for each mathematical test

### 5. `finalize_financial_context(final_reasoning, confidence_assessment, currency)`
**Purpose**: Submit final analysis as structured context
**Output**: Complete financial context for label assignment

## Integration with Label Harmonizer

### Updated Workflow

The financial discovery sub-agent fits into the label harmonizer workflow as follows:

1. **Structure Analysis**: `get_line_id_text_list()` - understand receipt layout
2. **Table Analysis**: `run_table_subagent()` - identify column/row structure
3. **🆕 Financial Discovery**: `validate_financial_consistency()` - **LLM-driven discovery of financial values**
4. **Label Assignment**: `run_label_subagent()` - **use financial context for informed labeling**

### Key Changes

- **Before**: Financial validation required existing labels to function
- **After**: Financial discovery works without labels and provides context for label assignment
- **Before**: Hard-coded rules determined financial value identification
- **After**: LLM reasoning adapts to different receipt formats and structures

## Usage Example

```python
from receipt_agent.subagents.financial_validation.llm_driven_graph import (
    create_llm_driven_financial_graph,
    run_llm_driven_financial_discovery,
)

# Create graph
graph, state_holder = create_llm_driven_financial_graph()

# Run discovery
result = await run_llm_driven_financial_discovery(
    graph=graph,
    state_holder=state_holder,
    receipt_text=receipt_text,
    labels=existing_labels,  # Can be empty
    words=word_data,
    table_structure=table_analysis,  # Optional
)

# Use financial context for label assignment
financial_candidates = result["financial_candidates"]
math_validation = result["mathematical_validation"]
llm_reasoning = result["llm_reasoning"]
```

## Benefits

### 🎯 **Improved Accuracy**
- Considers full context rather than isolated patterns
- Adapts to receipt format variations
- Self-validates through mathematical verification

### 🚀 **Reduced Maintenance**
- No hard-coded rules to update for new receipt formats
- LLM reasoning adapts to new patterns automatically
- Extensible to new financial types without code changes

### 🔍 **Better Label Assignment**
- Provides rich context for downstream label sub-agents
- Reduces guesswork in financial label assignment
- Enables confident labeling even with sparse existing labels

### 🧪 **Explainable AI**
- Detailed reasoning provided for each assignment
- Mathematical validation results available for review
- Transparent decision-making process

## Technical Implementation

### State Management
The sub-agent maintains state across tool calls:
- `financial_reasoning`: LLM's structural analysis
- `proposed_assignments`: Candidate assignments with reasoning
- `verification_results`: Mathematical validation outcomes
- `final_result`: Complete context for output

### Error Handling
Robust error handling at multiple levels:
- Tool-level validation of required parameters
- Mathematical validation of assignments
- Fallback to basic analysis if LLM reasoning fails
- Clear error reporting with context

### Performance Considerations
- Reuses sub-agent graph instances for efficiency
- Async execution within sync wrapper for compatibility
- Selective tool usage based on available data
- Optimized for typical receipt sizes (50-200 words)

## Testing

Comprehensive test coverage includes:
- Various receipt formats (grocery, restaurant, retail)
- Different currency types and formatting
- Sparse and missing label scenarios
- Mathematical edge cases and rounding
- Error conditions and recovery

See `test_llm_driven_financial_discovery.py` for detailed testing examples.

## Future Enhancements

### Potential Improvements
1. **Multi-Currency Support**: Enhanced currency detection and handling
2. **Receipt Type Classification**: Specialized reasoning for different business types
3. **Confidence Learning**: Adaptive confidence scoring based on validation success
4. **Batch Processing**: Optimized processing for multiple receipts
5. **Visual Context**: Integration with spatial/visual receipt information

### Extension Points
- Custom reasoning prompts for specific business domains
- Additional mathematical relationship validators
- Integration with external knowledge bases
- Support for non-standard receipt formats

## Conclusion

The LLM-Driven Financial Discovery Sub-Agent represents a significant advancement in receipt processing technology. By leveraging LLM reasoning instead of hard-coded rules, it provides more accurate, adaptable, and maintainable financial value identification that serves as a robust foundation for accurate label assignment.

This approach transforms the financial validation component from a rigid rule-based system into an intelligent reasoning engine that can adapt to the diverse and evolving landscape of receipt formats while maintaining mathematical rigor and explainable decision-making.
