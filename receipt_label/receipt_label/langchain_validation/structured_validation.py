"""
Structured validation using Pydantic models for type safety
===========================================================

This demonstrates how to use LangChain's structured output
capabilities to ensure type-safe, validated responses.
"""

from typing import List, Optional, Literal
from pydantic import BaseModel, Field
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_ollama import ChatOllama
import os


# ============================================================================
# Pydantic Models for Structured Output
# ============================================================================

class ValidationResult(BaseModel):
    """Single validation result with strong typing"""
    id: str = Field(description="The exact ID from the target")
    is_valid: bool = Field(description="Whether the label is correct")
    correct_label: Optional[str] = Field(
        default=None,
        description="The correct label if is_valid is false"
    )
    confidence: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Confidence score between 0 and 1"
    )


class ValidationResponse(BaseModel):
    """Complete validation response with all results"""
    results: List[ValidationResult] = Field(
        description="List of validation results for each target"
    )
    processing_notes: Optional[str] = Field(
        default=None,
        description="Any notes about the validation process"
    )


# ============================================================================
# Structured Validation with LangChain
# ============================================================================

def get_structured_ollama_llm() -> ChatOllama:
    """Get Ollama configured for structured output"""
    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    api_key = os.getenv("OLLAMA_API_KEY")
    model = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
    
    llm = ChatOllama(
        model=model,
        base_url=base_url,
        temperature=0,
        # Note: We don't set format="json" here when using structured output
    )
    
    if api_key:
        llm.auth = {"api_key": api_key}
    
    return llm


async def validate_with_structured_output(
    formatted_prompt: str,
    validation_targets: List[dict]
) -> ValidationResponse:
    """
    Validate using structured output with Pydantic models
    
    This approach ensures:
    1. Type-safe responses
    2. Automatic validation
    3. Better error handling
    4. No manual JSON parsing
    """
    llm = get_structured_ollama_llm()
    
    # Method 1: Using with_structured_output (if supported by Ollama)
    try:
        structured_llm = llm.with_structured_output(ValidationResponse)
        response = await structured_llm.ainvoke(formatted_prompt)
        return response  # Already a ValidationResponse object!
    except AttributeError:
        # Fallback if Ollama doesn't support with_structured_output
        pass
    
    # Method 2: Using output parser
    parser = PydanticOutputParser(pydantic_object=ValidationResponse)
    
    # Create prompt with format instructions
    prompt = PromptTemplate(
        template="{query}\n\n{format_instructions}",
        input_variables=["query"],
        partial_variables={"format_instructions": parser.get_format_instructions()}
    )
    
    # Create chain
    chain = prompt | llm | parser
    
    # Execute
    response = await chain.ainvoke({"query": formatted_prompt})
    return response  # Returns ValidationResponse object


# ============================================================================
# Alternative: Using JSON Schema directly
# ============================================================================

def get_validation_json_schema():
    """Get JSON schema for validation response"""
    return {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "is_valid": {"type": "boolean"},
                        "correct_label": {"type": "string"},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1}
                    },
                    "required": ["id", "is_valid"]
                }
            }
        },
        "required": ["results"]
    }


async def validate_with_json_schema(
    formatted_prompt: str,
    validation_targets: List[dict]
) -> dict:
    """
    Validate using JSON schema approach
    
    Some LLMs support JSON schema constraints directly
    """
    llm = get_structured_ollama_llm()
    
    # If Ollama supports JSON schema (depends on version)
    try:
        # This would ensure the output conforms to our schema
        response = await llm.ainvoke(
            formatted_prompt,
            config={"response_format": {"json_schema": get_validation_json_schema()}}
        )
        return response
    except:
        # Fallback to regular JSON mode
        llm.format = "json"
        response = await llm.ainvoke(formatted_prompt)
        import json
        return json.loads(response.content)


# ============================================================================
# Integration with existing graph
# ============================================================================

async def validate_with_ollama_structured(state: dict) -> dict:
    """
    Enhanced validation node using structured output
    
    This replaces the manual JSON parsing with Pydantic validation
    """
    try:
        # Use structured output
        response = await validate_with_structured_output(
            state["formatted_prompt"],
            state["validation_targets"]
        )
        
        # Convert Pydantic model to dict for state
        state["validation_results"] = [
            result.dict() for result in response.results
        ]
        state["completed"] = True
        
        # Store confidence scores if available
        if any(r.confidence is not None for r in response.results):
            state["confidence_scores"] = [
                r.confidence for r in response.results
            ]
        
    except Exception as e:
        state["error"] = f"Structured validation failed: {e}"
        state["validation_results"] = []
        state["completed"] = False
    
    return state


# ============================================================================
# Demo
# ============================================================================

if __name__ == "__main__":
    import asyncio
    
    async def demo():
        print("Testing structured validation...")
        
        # Sample prompt
        test_prompt = """
        Validate these receipt labels:
        
        TARGETS:
        [
            {"id": "TEST_001", "text": "Walmart", "proposed_label": "MERCHANT_NAME"},
            {"id": "TEST_002", "text": "$12.99", "proposed_label": "TOTAL"}
        ]
        
        Are the proposed labels correct?
        """
        
        test_targets = [
            {"id": "TEST_001", "text": "Walmart", "proposed_label": "MERCHANT_NAME"},
            {"id": "TEST_002", "text": "$12.99", "proposed_label": "TOTAL"}
        ]
        
        try:
            # Test structured output
            result = await validate_with_structured_output(
                test_prompt,
                test_targets
            )
            
            print(f"Result type: {type(result)}")
            print(f"Results: {result}")
            
            # Access fields with type safety
            for validation in result.results:
                print(f"  {validation.id}: valid={validation.is_valid}")
                if validation.confidence:
                    print(f"    Confidence: {validation.confidence}")
                    
        except Exception as e:
            print(f"Error: {e}")
    
    asyncio.run(demo())