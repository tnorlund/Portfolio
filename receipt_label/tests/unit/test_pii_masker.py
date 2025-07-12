"""
Unit tests for PII masking functionality.
"""

import pytest
from receipt_label.utils.pii_masker import (
    PIIMasker, PIIType, create_receipt_masker, mask_before_api_call
)


class TestPIIMasker:
    """Test PII masking functionality."""
    
    def test_credit_card_masking(self):
        """Test credit card number detection and masking."""
        masker = PIIMasker()
        
        # Test various credit card formats
        test_cases = [
            ("Card: 4111111111111111", "Card: ****-****-****-1111"),
            ("4111-1111-1111-1111", "****-****-****-1111"),
            ("4111 1111 1111 1111", "****-****-****-1111"),
            ("Payment: 5500000000000004", "Payment: ****-****-****-0004"),
        ]
        
        for original, expected in test_cases:
            masked, matches = masker.mask_text(original)
            assert expected in masked
            assert len(matches) == 1
            assert matches[0].pii_type == PIIType.CREDIT_CARD
    
    def test_ssn_masking(self):
        """Test SSN detection and masking."""
        masker = PIIMasker()
        
        test_cases = [
            ("SSN: 123-45-6789", "SSN: ***-**-****"),
            ("ID: 123456789", "ID: ***-**-****"),
        ]
        
        for original, expected in test_cases:
            masked, matches = masker.mask_text(original)
            assert expected in masked
            assert len(matches) == 1
            assert matches[0].pii_type == PIIType.SSN
    
    def test_email_masking(self):
        """Test email masking with domain preservation."""
        masker = PIIMasker(mask_emails=True)
        
        test_cases = [
            ("Contact: john.doe@example.com", "Contact: ****@example.com"),
            ("Email: test@gmail.com", "Email: ****@gmail.com"),
        ]
        
        for original, expected in test_cases:
            masked, matches = masker.mask_text(original)
            assert expected in masked
            assert len(matches) == 1
            assert matches[0].pii_type == PIIType.EMAIL
    
    def test_phone_masking(self):
        """Test phone number masking with area code preservation."""
        masker = PIIMasker(mask_phones=True)
        
        test_cases = [
            ("Call: (555) 123-4567", "Call: (555) ***-****"),
            ("Phone: 555-123-4567", "Phone: (555) ***-****"),
            ("+1 555 123 4567", "+1 (555) ***-****"),
        ]
        
        for original, expected in test_cases:
            masked, matches = masker.mask_text(original)
            assert "(555) ***-****" in masked
            assert len(matches) == 1
            assert matches[0].pii_type == PIIType.PHONE
    
    def test_receipt_masker_configuration(self):
        """Test receipt-specific masker configuration."""
        masker = create_receipt_masker()
        
        # Should mask emails
        text = "Email: customer@example.com"
        masked, matches = masker.mask_text(text)
        assert "****@example.com" in masked
        
        # Should NOT mask phones (business phones needed for Places API)
        text = "Store: (555) 123-4567"
        masked, matches = masker.mask_text(text)
        assert masked == text  # No masking
        assert len(matches) == 0
    
    def test_mask_receipt_words(self):
        """Test masking receipt words while preserving structure."""
        masker = PIIMasker()
        
        words = [
            {"word_id": 1, "text": "WALMART", "line_id": 0},
            {"word_id": 2, "text": "Card:", "line_id": 5},
            {"word_id": 3, "text": "4111111111111111", "line_id": 5},
            {"word_id": 4, "text": "Total:", "line_id": 6},
        ]
        
        masked_words = masker.mask_receipt_words(words)
        
        # Merchant name should be preserved
        assert masked_words[0]["text"] == "WALMART"
        
        # Credit card should be masked
        assert masked_words[2]["text"] == "****-****-****-1111"
        assert masked_words[2].get("pii_masked") is True
        assert PIIType.CREDIT_CARD.value in masked_words[2].get("pii_types", [])
    
    def test_business_context_preservation(self):
        """Test that business information is preserved."""
        masker = PIIMasker()
        
        # Business names should not be masked even if they contain patterns
        business_contexts = [
            {"text": "Walmart", "is_merchant_area": True},
            {"text": "CVS Pharmacy", "is_merchant_area": True},
            {"text": "Target Store", "is_merchant_area": True},
        ]
        
        for context in business_contexts:
            text = context["text"]
            masked, matches = masker.mask_text(text, context)
            assert masked == text  # No masking
            assert len(matches) == 0
    
    def test_mask_api_request_places(self):
        """Test masking Places API requests."""
        masker = PIIMasker()
        
        request_data = {
            "input": "123 Main St with card 4111111111111111",
            "key": "api_key_here"
        }
        
        masked_data, matches = masker.mask_api_request(request_data, api_type="places")
        
        # Should mask credit card in input
        assert "****-****-****-1111" in masked_data["input"]
        assert len(matches) == 1
        
        # API key should not be touched
        assert masked_data["key"] == "api_key_here"
    
    def test_mask_api_request_gpt(self):
        """Test masking GPT API requests."""
        masker = PIIMasker(mask_emails=True)
        
        request_data = {
            "messages": [
                {
                    "role": "user",
                    "content": "Process receipt with email john@example.com and card 4111111111111111"
                }
            ]
        }
        
        masked_data, matches = masker.mask_api_request(request_data, api_type="gpt")
        
        # Should mask both email and credit card
        content = masked_data["messages"][0]["content"]
        assert "****@example.com" in content
        assert "****-****-****-1111" in content
        assert len(matches) == 2
    
    def test_mask_before_api_call_convenience(self):
        """Test convenience function for API masking."""
        # Test with string
        text = "Call store at 555-123-4567"
        masked_text, matches = mask_before_api_call(text)
        assert masked_text == text  # Phones not masked by default
        
        # Test with dict (API request)
        request = {"input": "Payment: 4111111111111111"}
        masked_request, matches = mask_before_api_call(request, api_type="places")
        assert "****-****-****-1111" in masked_request["input"]
        
        # Test with list (receipt words)
        words = [{"text": "john@example.com", "word_id": 1}]
        masked_words, matches = mask_before_api_call(words)
        assert "****@example.com" in masked_words[0]["text"]
    
    def test_statistics(self):
        """Test PII match statistics."""
        masker = PIIMasker(mask_emails=True, mask_phones=True)
        
        text = """
        Customer: John Doe
        Email: john@example.com
        Phone: 555-123-4567
        Card: 4111111111111111
        SSN: 123-45-6789
        """
        
        masked, matches = masker.mask_text(text)
        stats = masker.get_statistics(matches)
        
        assert stats["total_matches"] == 4
        assert stats["by_type"][PIIType.EMAIL.value] == 1
        assert stats["by_type"][PIIType.PHONE.value] == 1
        assert stats["by_type"][PIIType.CREDIT_CARD.value] == 1
        assert stats["by_type"][PIIType.SSN.value] == 1
        assert stats["high_confidence"] == 4  # All should be high confidence
    
    def test_no_false_positives(self):
        """Test that common receipt text doesn't trigger false positives."""
        masker = PIIMasker()
        
        safe_texts = [
            "Order #1234567890",  # Order numbers
            "SKU: 4111222233334444",  # Product codes  
            "Date: 01/23/2024",  # Dates
            "Time: 12:34:56",  # Times
            "Subtotal: $123.45",  # Prices
            "ZIP: 12345",  # ZIP codes
        ]
        
        for text in safe_texts:
            masked, matches = masker.mask_text(text)
            assert masked == text  # No masking
            assert len(matches) == 0