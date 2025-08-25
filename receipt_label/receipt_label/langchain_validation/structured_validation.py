"""
LangChain Structured Output for Receipt Validation
=================================================

This module implements proper LangChain structured outputs using PydanticOutputParser
with the authenticated Ollama Turbo client.
"""

from typing import Optional
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_core.messages import HumanMessage

from .validation_tool import ValidationResult
from .ollama_turbo_client import create_ollama_turbo_client
from receipt_label.constants import CORE_LABELS


class StructuredReceiptValidator:
    """
    Receipt validation using LangChain's structured outputs with Pydantic models.
    
    This provides proper structured output parsing without manual JSON handling.
    """
    
    def __init__(
        self, 
        model: str = "gpt-oss:20b",
        base_url: str = "https://ollama.com",
        api_key: Optional[str] = None,
        temperature: float = 0.0
    ):
        # Create authenticated Ollama client
        self.llm = create_ollama_turbo_client(
            model=model,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature
        )
        
        # Create Pydantic output parser
        self.output_parser = PydanticOutputParser(pydantic_object=ValidationResult)
        
        # Create prompt template with format instructions - exact format from assemble_prompt_batch_optimized.py
        self.prompt = PromptTemplate(
            template="""Validate this word label on a receipt:

Word: "{word_text}"
Label: {label}
{label_definition}

{merchant_info}

{context_text}

{semantic_similarity}

📄 OCR Data Context:
This text was extracted from a receipt image using Optical Character Recognition (OCR).
OCR may introduce errors such as:
- Character misrecognition (e.g., 'O' vs '0', 'l' vs '1', 'S' vs '5')
- Spacing issues (e.g., "Grand Total" → "GrandTotal" or "Grand To tal")
- Partial text extraction (e.g., cut-off words at image edges)
- Case sensitivity variations (e.g., "TOTAL" vs "total")

When validating, consider that the word might be a slightly corrupted version of the intended text.

Validation Status Guidelines:
- VALID: The label is completely correct for this word in this context (accounting for potential OCR errors)
- INVALID: The label is definitely wrong for this word, even considering OCR variations
- NEEDS_REVIEW: The label might be correct but requires human review due to ambiguity or OCR uncertainty
- PENDING: Cannot determine without additional information

Analyze the word, its context, potential OCR errors, and provide your validation decision.

{format_instructions}""",
            input_variables=["word_text", "label", "label_definition", "merchant_info", "context_text", "semantic_similarity"],
            partial_variables={"format_instructions": self.output_parser.get_format_instructions()}
        )
        
        # Create the chain
        self.chain = self.prompt | self.llm | self.output_parser
    
    def _format_merchant_info(self, receipt_metadata=None) -> str:
        """Format merchant information exactly like assemble_prompt_batch_optimized.py lines 414-425."""
        if not receipt_metadata:
            return "🏪 Merchant: Unknown Merchant"
        
        lines = [f"🏪 Merchant: {receipt_metadata.merchant_name}"]
        if receipt_metadata.merchant_category:
            lines.append(f"   Category: {receipt_metadata.merchant_category}")
        if receipt_metadata.address:
            lines.append(f"   Address: {receipt_metadata.address}")
        if receipt_metadata.phone_number:
            lines.append(f"   Phone: {receipt_metadata.phone_number}")
        if receipt_metadata.validation_status:
            lines.append(f"   Validation Status: {receipt_metadata.validation_status}")
        if receipt_metadata.matched_fields:
            lines.append(f"   Matched Fields: {', '.join(receipt_metadata.matched_fields)}")
        
        return "\n".join(lines)
    
    def _format_label_definition(self, label: str) -> str:
        """Format label definition exactly like assemble_prompt_batch_optimized.py line 409."""
        definition = CORE_LABELS.get(label, 'Unknown label')
        return f"Label definition: {definition}"
    
    def _format_semantic_similarity(self, similar_valid_words=None, similar_invalid_words=None, label="") -> str:
        """Format semantic similarity exactly like assemble_prompt_batch_optimized.py lines 436-481."""
        if not similar_valid_words:
            similar_valid_words = []
        if not similar_invalid_words:
            similar_invalid_words = []
        
        lines = ["🧠 SEMANTIC SIMILARITY:"]
        lines.append(f"  Similar words where '{label}' was VALID: ({len(similar_valid_words)} found)")
        
        if similar_valid_words:
            for word_info in similar_valid_words[:3]:  # Show top 3
                lines.append(f"    ✓ '{word_info['text']}' (distance: {word_info['distance']:.3f}) - {word_info['merchant']}")
        else:
            lines.append("    None found")
        
        lines.append(f"  Similar words where '{label}' was INVALID: ({len(similar_invalid_words)} found)")
        
        if similar_invalid_words:
            for word_info in similar_invalid_words[:3]:  # Show top 3
                lines.append(f"    ✗ '{word_info['text']}' (distance: {word_info['distance']:.3f}) - {word_info['merchant']}")
        else:
            lines.append("    None found")
        
        # Add suggestion exactly like lines 460-481
        valid_count = len(similar_valid_words)
        invalid_count = len(similar_invalid_words)
        
        if valid_count > invalid_count:
            lines.append(f"\n   → Suggestion: '{label}' is likely VALID for this word")
            lines.append(f"     (Based on {valid_count} valid vs {invalid_count} invalid similar examples)")
        elif invalid_count > valid_count:
            lines.append(f"\n   → Suggestion: '{label}' is likely INVALID for this word")
            lines.append(f"     (Based on {invalid_count} invalid vs {valid_count} valid similar examples)")
        else:
            lines.append(f"\n   → No clear suggestion - needs manual review")
            lines.append(f"     ({valid_count} valid vs {invalid_count} invalid similar examples)")
        
        return "\n".join(lines)
    
    def validate_word_label(
        self, 
        word_text: str, 
        label: str, 
        context_text: str,
        receipt_metadata=None,
        similar_valid_words=None,
        similar_invalid_words=None
    ) -> ValidationResult:
        """
        Validate a single word label using structured output.
        
        Args:
            word_text: The word to validate
            label: The proposed label
            context_text: Receipt context around the word
            receipt_metadata: Receipt metadata for merchant info
            similar_valid_words: List of similar words that were labeled VALID
            similar_invalid_words: List of similar words that were labeled INVALID
            
        Returns:
            ValidationResult with validation_status, confidence, and reasoning
        """
        try:
            # Format all sections exactly like assemble_prompt_batch_optimized.py
            label_definition = self._format_label_definition(label)
            merchant_info = self._format_merchant_info(receipt_metadata)
            semantic_similarity = self._format_semantic_similarity(
                similar_valid_words, similar_invalid_words, label
            )
            
            result = self.chain.invoke({
                "word_text": word_text,
                "label": label,
                "label_definition": label_definition,
                "merchant_info": merchant_info,
                "context_text": context_text,
                "semantic_similarity": semantic_similarity
            })
            return result
            
        except Exception as e:
            # Fallback validation result if parsing fails
            return ValidationResult(
                validation_status="NEEDS_REVIEW",
                reasoning=f"Validation failed due to parsing error: {e}"
            )
    
    async def validate_async(
        self, 
        word_text: str, 
        label: str, 
        context,  # Can be ValidationContext object or formatted string
        formatted_context: str = None,  # Optional pre-formatted context
        receipt_metadata=None,
        similar_valid_words=None,
        similar_invalid_words=None
    ) -> ValidationResult:
        """
        Async version of validate_word_label for parallel processing.
        
        Args:
            word_text: The word to validate
            label: The proposed label
            context: ValidationContext object or formatted string
            formatted_context: Optional pre-formatted context string
            receipt_metadata: Receipt metadata for merchant info (legacy)
            similar_valid_words: List of similar words that were labeled VALID (legacy)
            similar_invalid_words: List of similar words that were labeled INVALID (legacy)
            
        Returns:
            ValidationResult with validation_status and reasoning
        """
        try:
            # Extract data from ValidationContext if provided
            if hasattr(context, 'receipt_context') and hasattr(context, 'semantic_context'):
                # Extract receipt metadata
                if context.receipt_context.receipt_metadata:
                    receipt_metadata = context.receipt_context.receipt_metadata
                
                # Extract semantic similarity data
                if context.semantic_context:
                    similar_valid_words = context.semantic_context.similar_valid_words
                    similar_invalid_words = context.semantic_context.similar_invalid_words
                
                # Use pre-formatted context or format it ourselves
                context_text = formatted_context or str(context)
            else:
                # Legacy: context is already formatted string
                context_text = context
            
            # Format sections with actual data
            result = await self.chain.ainvoke({
                "word_text": word_text,
                "label": label,
                "label_definition": self._format_label_definition(label),
                "merchant_info": self._format_merchant_info(receipt_metadata),
                "context_text": context_text,
                "semantic_similarity": self._format_semantic_similarity(
                    similar_valid_words, similar_invalid_words, label
                )
            })
            return result
            
        except Exception as e:
            # Fallback validation result if parsing fails
            return ValidationResult(
                validation_status="NEEDS_REVIEW",
                reasoning=f"Async validation failed due to parsing error: {e}"
            )
    
    def get_format_instructions(self) -> str:
        """Get the format instructions for debugging."""
        return self.output_parser.get_format_instructions()


# For testing, use the dedicated test file at:
# receipt_label/tests/test_langchain_validation.py