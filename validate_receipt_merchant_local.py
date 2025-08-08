"""
Modified merchant validation handler that supports local LLM clients (like Ollama)
and synchronous execution.
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple, Union

from openai import OpenAI, AsyncOpenAI
from receipt_dynamo.constants import ValidationMethod
from receipt_dynamo.entities.receipt_metadata import ReceiptMetadata
from receipt_label.merchant_validation.merchant_validation import (
    extract_candidate_merchant_fields,
)

logger = logging.getLogger(__name__)


class LocalMerchantValidator:
    """Merchant validator that works with local LLM clients."""
    
    def __init__(
        self,
        client: Union[OpenAI, AsyncOpenAI],
        google_places_api_key: Optional[str] = None,
        model: str = "llama3.2",  # Default Ollama model
    ):
        """
        Initialize the validator with a custom client.
        
        Args:
            client: OpenAI-compatible client (can be Ollama client)
            google_places_api_key: Optional Google Places API key
            model: Model name to use
        """
        self.client = client
        self.google_places_api_key = google_places_api_key
        self.model = model
        self.is_async = isinstance(client, AsyncOpenAI)
    
    def _create_validation_prompt(
        self,
        image_id: str,
        receipt_id: int,
        receipt_lines: list,
        receipt_words: list,
    ) -> str:
        """Create a prompt for merchant validation."""
        # Extract candidate fields
        extracted_data = extract_candidate_merchant_fields(receipt_words)
        
        # Build the prompt
        prompt = f"""You are analyzing a receipt to extract merchant information.

Receipt ID: {image_id}#{receipt_id}

Raw Receipt Text (by line):
{chr(10).join([line.text for line in receipt_lines])}

Extracted Candidate Fields:
"""
        for field_type, words in extracted_data.items():
            if words:
                prompt += f"- {field_type}: {', '.join([w.text for w in words])}\n"
        
        prompt += """
Please extract and validate the following merchant information:
1. merchant_name: The name of the business/store
2. merchant_address: The full address if available
3. merchant_phone: The phone number if available
4. merchant_website: The website if available
5. purchase_date: The date of purchase (format: YYYY-MM-DD)
6. purchase_time: The time of purchase if available (format: HH:MM)

Respond with a JSON object containing only the fields you can confidently extract.
Example: {"merchant_name": "Store Name", "purchase_date": "2024-01-15"}

If you cannot extract any merchant information with confidence, respond with: {}
"""
        return prompt
    
    def validate_receipt_merchant_sync(
        self,
        image_id: str,
        receipt_id: int,
        receipt_lines: list,
        receipt_words: list,
        temperature: float = 0.1,
    ) -> Tuple[ReceiptMetadata, Dict[str, Any]]:
        """
        Synchronous validation of merchant information.
        
        Args:
            image_id: Image UUID
            receipt_id: Receipt ID
            receipt_lines: List of receipt line objects
            receipt_words: List of receipt word objects
            temperature: LLM temperature for generation
            
        Returns:
            Tuple of (ReceiptMetadata, status_info)
        """
        if self.is_async:
            # Run async method in sync context
            return asyncio.run(
                self.validate_receipt_merchant_async(
                    image_id, receipt_id, receipt_lines, receipt_words, temperature
                )
            )
        
        try:
            # Create the prompt
            prompt = self._create_validation_prompt(
                image_id, receipt_id, receipt_lines, receipt_words
            )
            
            # Call the LLM
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are a receipt parsing assistant."},
                    {"role": "user", "content": prompt}
                ],
                temperature=temperature,
                response_format={"type": "json_object"},  # Request JSON response
            )
            
            # Parse the response
            content = response.choices[0].message.content
            logger.info(f"LLM Response: {content}")
            
            try:
                extracted_data = json.loads(content)
            except json.JSONDecodeError:
                logger.error(f"Failed to parse JSON response: {content}")
                extracted_data = {}
            
            # Build metadata
            metadata = self._build_metadata(
                image_id, receipt_id, extracted_data
            )
            
            status_info = {
                "status": "processed" if extracted_data else "no_data",
                "merchant_name": extracted_data.get("merchant_name", ""),
                "model": self.model,
                "client_type": "local",
            }
            
            return metadata, status_info
            
        except Exception as e:
            logger.error(f"Validation failed: {e}")
            # Return empty metadata on failure
            metadata = ReceiptMetadata(
                image_id=image_id,
                receipt_id=receipt_id,
                validation_method=ValidationMethod.FAILED,
                validation_timestamp=datetime.now(timezone.utc),
                validation_model=self.model,
            )
            return metadata, {"status": "failed", "error": str(e)}
    
    async def validate_receipt_merchant_async(
        self,
        image_id: str,
        receipt_id: int,
        receipt_lines: list,
        receipt_words: list,
        temperature: float = 0.1,
    ) -> Tuple[ReceiptMetadata, Dict[str, Any]]:
        """
        Asynchronous validation of merchant information.
        
        Args:
            image_id: Image UUID
            receipt_id: Receipt ID
            receipt_lines: List of receipt line objects
            receipt_words: List of receipt word objects
            temperature: LLM temperature for generation
            
        Returns:
            Tuple of (ReceiptMetadata, status_info)
        """
        if not self.is_async:
            # Run sync method if client is not async
            return self.validate_receipt_merchant_sync(
                image_id, receipt_id, receipt_lines, receipt_words, temperature
            )
        
        try:
            # Create the prompt
            prompt = self._create_validation_prompt(
                image_id, receipt_id, receipt_lines, receipt_words
            )
            
            # Call the LLM
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are a receipt parsing assistant."},
                    {"role": "user", "content": prompt}
                ],
                temperature=temperature,
                response_format={"type": "json_object"},  # Request JSON response
            )
            
            # Parse the response
            content = response.choices[0].message.content
            logger.info(f"LLM Response: {content}")
            
            try:
                extracted_data = json.loads(content)
            except json.JSONDecodeError:
                logger.error(f"Failed to parse JSON response: {content}")
                extracted_data = {}
            
            # Build metadata
            metadata = self._build_metadata(
                image_id, receipt_id, extracted_data
            )
            
            status_info = {
                "status": "processed" if extracted_data else "no_data",
                "merchant_name": extracted_data.get("merchant_name", ""),
                "model": self.model,
                "client_type": "local",
            }
            
            return metadata, status_info
            
        except Exception as e:
            logger.error(f"Validation failed: {e}")
            # Return empty metadata on failure
            metadata = ReceiptMetadata(
                image_id=image_id,
                receipt_id=receipt_id,
                validation_method=ValidationMethod.FAILED,
                validation_timestamp=datetime.now(timezone.utc),
                validation_model=self.model,
            )
            return metadata, {"status": "failed", "error": str(e)}
    
    def _build_metadata(
        self,
        image_id: str,
        receipt_id: int,
        extracted_data: Dict[str, Any],
    ) -> ReceiptMetadata:
        """Build ReceiptMetadata from extracted data."""
        # Determine validation method
        if not extracted_data:
            validation_method = ValidationMethod.NO_MATCH
        else:
            validation_method = ValidationMethod.AI_VALIDATED
        
        # Build metadata
        metadata = ReceiptMetadata(
            image_id=image_id,
            receipt_id=receipt_id,
            merchant_name=extracted_data.get("merchant_name", ""),
            merchant_address=extracted_data.get("merchant_address", ""),
            merchant_phone=extracted_data.get("merchant_phone", ""),
            merchant_website=extracted_data.get("merchant_website", ""),
            purchase_date=extracted_data.get("purchase_date", ""),
            purchase_time=extracted_data.get("purchase_time", ""),
            validation_method=validation_method,
            validation_timestamp=datetime.now(timezone.utc),
            validation_model=self.model,
            confidence_score=0.8 if extracted_data else 0.0,
        )
        
        return metadata


# Example usage function
def validate_with_ollama(
    image_id: str,
    receipt_id: int,
    receipt_lines: list,
    receipt_words: list,
    ollama_base_url: str = "http://localhost:11434/v1",
    model: str = "llama3.2",
) -> Tuple[ReceiptMetadata, Dict[str, Any]]:
    """
    Convenience function to validate a receipt using Ollama.
    
    Args:
        image_id: Image UUID
        receipt_id: Receipt ID
        receipt_lines: List of receipt line objects
        receipt_words: List of receipt word objects
        ollama_base_url: Ollama API base URL
        model: Ollama model to use
        
    Returns:
        Tuple of (ReceiptMetadata, status_info)
    """
    # Create Ollama client
    client = OpenAI(
        base_url=ollama_base_url,
        api_key="ollama",  # Ollama doesn't need a real API key
    )
    
    # Create validator
    validator = LocalMerchantValidator(client=client, model=model)
    
    # Run validation synchronously
    return validator.validate_receipt_merchant_sync(
        image_id=image_id,
        receipt_id=receipt_id,
        receipt_lines=receipt_lines,
        receipt_words=receipt_words,
    )


# Example usage with async client
async def validate_with_async_client(
    image_id: str,
    receipt_id: int,
    receipt_lines: list,
    receipt_words: list,
    client: AsyncOpenAI,
    model: str = "gpt-3.5-turbo",
) -> Tuple[ReceiptMetadata, Dict[str, Any]]:
    """
    Example of using the validator with an async OpenAI client.
    
    Args:
        image_id: Image UUID
        receipt_id: Receipt ID
        receipt_lines: List of receipt line objects
        receipt_words: List of receipt word objects
        client: Async OpenAI client
        model: Model to use
        
    Returns:
        Tuple of (ReceiptMetadata, status_info)
    """
    validator = LocalMerchantValidator(client=client, model=model)
    return await validator.validate_receipt_merchant_async(
        image_id=image_id,
        receipt_id=receipt_id,
        receipt_lines=receipt_lines,
        receipt_words=receipt_words,
    )


if __name__ == "__main__":
    # Example usage
    import sys
    from receipt_dynamo import DynamoClient
    from pathlib import Path
    
    logging.basicConfig(level=logging.INFO)
    
    # This would integrate with your existing dev.add_metadatas.py script
    print("Local Merchant Validator ready for use.")
    print("Example usage:")
    print("  from validate_receipt_merchant_local import validate_with_ollama")
    print("  metadata, status = validate_with_ollama(image_id, receipt_id, lines, words)")