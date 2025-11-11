"""Extract merchant information from receipt text."""

from typing import Dict, Any
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from receipt_label.langchain.state.metadata_creation import MetadataCreationState
from receipt_label.langchain.utils.retry import retry_with_backoff


class ExtractedMerchantInfo(BaseModel):
    """Extracted merchant information from receipt."""

    merchant_name: str = Field(default="", description="Business/merchant name")
    address: str = Field(default="", description="Business address")
    phone_number: str = Field(default="", description="Phone number")
    merchant_words: list[str] = Field(default_factory=list, description="Words that might be merchant name")


async def extract_merchant_info(
    state: MetadataCreationState,
    ollama_api_key: str,
    thinking_strength: str = "medium",
) -> Dict[str, Any]:
    """Extract merchant information from receipt text using Ollama Cloud LLM.

    Args:
        state: Current workflow state
        ollama_api_key: Ollama Cloud API key (required)
        thinking_strength: Ollama thinking strength - "low", "medium", or "high" (default: "medium")

    Returns:
        Dictionary with extracted merchant information
    """
    print(f"🔍 Extracting merchant information from receipt text...")

    # Initialize LLM with structured outputs - using Ollama Cloud only
    # Use cloud model with -cloud suffix (latest Ollama Cloud feature, v0.12+)
    model_name = "gpt-oss:120b-cloud"  # Cloud model for better extraction

    # For Ollama Cloud structured outputs, pass the JSON schema directly to format parameter
    # This is the correct way per Ollama documentation: format=Model.model_json_schema()
    json_schema = ExtractedMerchantInfo.model_json_schema()

    llm = ChatOllama(
        model=model_name,
        base_url="https://ollama.com",
        client_kwargs={
            "headers": {"Authorization": f"Bearer {ollama_api_key}"}
        },
        format=json_schema,  # Pass JSON schema directly (not "json" string)
        temperature=0.3,
    )

    # Use with_structured_output for type-safe parsing
    # This will parse the JSON response into the Pydantic model
    llm_structured = llm.with_structured_output(ExtractedMerchantInfo)

    messages = [
        SystemMessage(
            content="""You are extracting merchant information from receipt text that comes from OCR (Optical Character Recognition).

Your task is to carefully read the receipt text and extract:
1. **merchant_name**: The business/merchant name (usually at the top of the receipt)
   - Extract what appears to be the merchant name
   - Be aware that OCR may have errors, but extract as-is for now
   - The search phase will handle OCR corrections
2. **address**: The full business address (street address, city, state, zip code)
   - Look for lines that contain street numbers, street names, city names, state abbreviations, and zip codes
   - Extract what you see, even if it might contain OCR errors
3. **phone_number**: The phone number (usually formatted as (XXX) XXX-XXXX or XXX-XXX-XXXX)
   - Look for patterns like phone numbers
   - Phone numbers are usually more reliable (less OCR errors)
4. **merchant_words**: A list of words/phrases that might be part of the merchant name
   - Include individual words and phrases from the merchant name
   - Include location words if they appear with the merchant name (e.g., if you see "IN N OUT MESTLAKE VILLAGE", include both "IN N OUT" and "MESTLAKE VILLAGE")
   - These will be used for fuzzy matching and variation attempts

**Important:**
- Look carefully through the entire receipt text for address and phone information
- Addresses often appear near the merchant name or at the bottom of receipts
- Phone numbers may be formatted in various ways: (555) 123-4567, 555-123-4567, 555.123.4567, etc.
- If you find partial information (e.g., just city/state but no street), include what you find
- Extract what you see - don't try to correct OCR errors here (that will happen in the search phase)
- Only return empty strings if you truly cannot find the information anywhere in the receipt

The output must match the JSON schema exactly (which is provided to the model).
"""
        ),
        HumanMessage(
            content=f"""Extract merchant information from this receipt text:

{state.formatted_text}

Extract:
- Merchant name (as it appears in the text)
- Address (if present)
- Phone number (if present)
- Merchant words (individual words/phrases that might be part of the merchant name or location)"""
        ),
    ]

    # Define invocation function for retry logic
    async def invoke_llm():
        return await llm_structured.ainvoke(
            messages,
            config={
                "metadata": {
                    "receipt_id": state.receipt_id,
                    "phase": "extract_merchant_info",
                },
                "tags": ["metadata_creation", "extract_merchant"],
            },
        )

    try:
        # Get structured response with retry logic
        response = await retry_with_backoff(
            invoke_llm,
            max_retries=3,
            initial_delay=1.0,
        )

        print(f"   ✅ Extracted merchant info:")
        print(f"      Name: {response.merchant_name}")
        print(f"      Address: {response.address}")
        print(f"      Phone: {response.phone_number}")

        return {
            "extracted_merchant_name": response.merchant_name,
            "extracted_address": response.address,
            "extracted_phone": response.phone_number,
            "extracted_merchant_words": response.merchant_words,
        }

    except Exception as e:
        print(f"   ⚠️ Error extracting merchant info: {e}")
        # Return empty values on error
        return {
            "extracted_merchant_name": "",
            "extracted_address": "",
            "extracted_phone": "",
            "extracted_merchant_words": [],
            "error_count": state.error_count + 1,
            "last_error": str(e),
        }

