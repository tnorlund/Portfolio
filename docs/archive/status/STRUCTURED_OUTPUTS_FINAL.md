# Structured Outputs with Pydantic Models: Final Approach

## Summary

After the user correctly pointed out that hardcoding JSON schemas in prompts defeats the purpose of using Pydantic models, we implemented a hybrid approach that maintains **Pydantic as the single source of truth** while dynamically injecting the schema into prompts.

## The Problem

The original approach was:
1. ✅ Define Pydantic models (`Phase1Response`, `Phase2Response`)
2. ✅ Use `with_structured_output()` to bind models to LLM
3. ❌ Hardcode JSON schemas in prompts (defeats purpose!)

This was redundant because we were maintaining schema information in both Pydantic models AND hardcoded prompts.

## The Solution

### Single Source of Truth: Pydantic Models

The Pydantic models are now the **single source of truth** for the expected output structure:

```python
# receipt_label/receipt_label/langchain/models/currency_validation.py

class CurrencyLabel(BaseModel):
    """A discovered currency label with LLM reasoning."""
    
    line_text: str = Field(..., description="...")
    amount: float = Field(..., description="...")
    label_type: CurrencyLabelType = Field(..., description="...")
    line_ids: List[int] = Field(default_factory=list, description="...")
    confidence: float = Field(..., ge=0.0, le=1.0, description="...")
    reasoning: str = Field(..., description="...")
```

### Dynamic Schema Injection

We dynamically extract the JSON schema from the Pydantic model and inject it into the prompt:

```python
# receipt_label/receipt_label/langchain/nodes/phase1.py

import json

# Get JSON schema from the Pydantic model (single source of truth!)
schema = Phase1Response.model_json_schema()
schema_json = json.dumps(schema, indent=2)

messages = [{
    "role": "user",
    "content": f"""...instructions...

CRITICAL INSTRUCTIONS:
1. You MUST respond with valid JSON ONLY
2. Output must match this EXACT JSON structure:

{schema_json}"""
}]
```

## Benefits

### 1. **Single Source of Truth**
- Schema is defined once in the Pydantic model
- No duplication between model and prompts
- Changes to models automatically propagate to prompts

### 2. **Type Safety**
- Pydantic validates all responses
- Field types are enforced
- Clear error messages if output doesn't match

### 3. **Self-Documenting**
- Improved Field descriptions guide the LLM
- Examples in descriptions help with accuracy
- Docstrings explain what each model represents

### 4. **Maintainability**
- Want to add a field? Update the Pydantic model
- The schema in prompts updates automatically
- No need to modify multiple places

## How It Works

1. **Pydantic Model** defines structure, types, and descriptions
2. **`with_structured_output()`** binds the model to the LLM
3. **Dynamic injection** extracts JSON schema and adds it to the prompt
4. **LLM** sees the exact schema and produces matching JSON
5. **Pydantic** validates and parses the response

## File Changes

### Models (receipt_label/receipt_label/langchain/models/currency_validation.py)
- Enhanced Field descriptions with examples
- Added `...` for required fields (explicit)
- Improved docstrings

### Nodes (receipt_label/receipt_label/langchain/nodes/phase1.py, phase2.py)
- Removed hardcoded JSON schemas
- Added dynamic schema extraction using `model_json_schema()`
- Prompts now reference the Pydantic model's schema

## Why This Approach?

- **Before**: Schema defined in 2 places (Pydantic + hardcoded prompt)
- **After**: Schema defined in 1 place (Pydantic), dynamically referenced

The user's insight was correct: we should NOT hardcode schemas when we have Pydantic models that can generate them dynamically!

## Testing

```bash
python dev.test_simple_currency_validation.py
```

✅ Successfully extracts currency labels with correct structure
✅ Validates against Pydantic models
✅ Returns properly typed response objects

