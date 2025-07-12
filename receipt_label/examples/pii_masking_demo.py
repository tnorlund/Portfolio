#!/usr/bin/env python3
"""
Demonstration of PII masking before external API calls.

This example shows how the PII masker protects sensitive information
while preserving business-relevant data for Places API and GPT calls.
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from receipt_label.utils.pii_masker import PIIMasker, create_receipt_masker, mask_before_api_call


def demo_pii_masking():
    """Demonstrate PII masking functionality."""
    
    print("🔒 PII Masking Demo")
    print("=" * 50)
    
    # Create a masker configured for receipts
    masker = create_receipt_masker()
    
    # Example 1: Credit card in receipt text
    print("\n1. Credit Card Masking:")
    receipt_text = "Paid with Visa ending in 4111111111111111 at Walmart"
    masked_text, matches = masker.mask_text(receipt_text)
    print(f"   Original: {receipt_text}")
    print(f"   Masked:   {masked_text}")
    print(f"   Detected: {len(matches)} PII item(s)")
    
    # Example 2: Address with SSN (should mask SSN only)
    print("\n2. Address with SSN:")
    address_text = "123 Main St, Customer ID: 123-45-6789"
    masked_address, matches = masker.mask_text(address_text)
    print(f"   Original: {address_text}")
    print(f"   Masked:   {masked_address}")
    print(f"   Detected: {len(matches)} PII item(s)")
    
    # Example 3: Business phone (should NOT be masked for receipts)
    print("\n3. Business Phone (Preserved):")
    business_text = "Walmart Store #1234, Call: (555) 123-4567"
    masked_business, matches = masker.mask_text(business_text)
    print(f"   Original: {business_text}")
    print(f"   Masked:   {masked_business}")
    print(f"   Note: Business phones preserved for Places API")
    
    # Example 4: Email address
    print("\n4. Email Masking:")
    email_text = "Receipt sent to john.doe@example.com"
    masked_email, matches = masker.mask_text(email_text)
    print(f"   Original: {email_text}")
    print(f"   Masked:   {masked_email}")
    
    # Example 5: Receipt words with PII
    print("\n5. Receipt Words Processing:")
    receipt_words = [
        {"word_id": 1, "text": "TARGET", "line_id": 0},
        {"word_id": 2, "text": "Store", "line_id": 0},
        {"word_id": 3, "text": "#2345", "line_id": 0},
        {"word_id": 10, "text": "Card:", "line_id": 5},
        {"word_id": 11, "text": "****1234", "line_id": 5},
        {"word_id": 12, "text": "Auth:", "line_id": 6},
        {"word_id": 13, "text": "4111111111111111", "line_id": 6},
        {"word_id": 20, "text": "Email:", "line_id": 8},
        {"word_id": 21, "text": "customer@gmail.com", "line_id": 8},
    ]
    
    masked_words = masker.mask_receipt_words(receipt_words)
    print("   Receipt words with PII:")
    
    # Create lookup for original text
    original_lookup = {w["word_id"]: w["text"] for w in receipt_words}
    
    for word in masked_words:
        if word.get("pii_masked"):
            original_text = original_lookup.get(word['word_id'], "[unknown]")
            print(f"   - Word {word['word_id']}: '{word['text']}' "
                  f"(was: {original_text}) "
                  f"[{', '.join(word['pii_types'])}]")
    
    # Example 6: API request masking
    print("\n6. API Request Masking:")
    
    # Places API request
    places_request = {
        "input": "123 Main St, paid with 4111111111111111",
        "inputtype": "textquery",
        "key": "AIza..."
    }
    
    masked_places, matches = masker.mask_api_request(places_request, api_type="places")
    print("   Places API Request:")
    print(f"   Original input: {places_request['input']}")
    print(f"   Masked input:   {masked_places['input']}")
    
    # GPT API request
    gpt_request = {
        "messages": [
            {
                "role": "user",
                "content": "Extract labels from receipt: Customer SSN 123-45-6789, "
                           "email john@example.com, total $45.99"
            }
        ]
    }
    
    masked_gpt, matches = masker.mask_api_request(gpt_request, api_type="gpt")
    print("\n   GPT API Request:")
    print(f"   Original: {gpt_request['messages'][0]['content']}")
    print(f"   Masked:   {masked_gpt['messages'][0]['content']}")
    
    # Example 7: Statistics
    print("\n7. PII Detection Statistics:")
    test_text = """
    Customer Receipt
    Name: John Doe
    Email: john.doe@company.com
    Phone: (555) 123-4567
    Card: 4111-1111-1111-1111
    SSN: 123-45-6789
    Order #: 98765
    """
    
    full_masker = PIIMasker(mask_emails=True, mask_phones=True)
    masked_full, all_matches = full_masker.mask_text(test_text)
    stats = full_masker.get_statistics(all_matches)
    
    print(f"   Total PII items found: {stats['total_matches']}")
    print("   By type:")
    for pii_type, count in stats['by_type'].items():
        print(f"   - {pii_type}: {count}")
    print(f"   High confidence matches: {stats['high_confidence']}")
    
    # Example 8: Using the convenience function
    print("\n8. Convenience Function:")
    mixed_data = "Store at 456 Oak Ave, customer card 5500000000000004"
    masked_conv, conv_matches = mask_before_api_call(mixed_data)
    print(f"   Original: {mixed_data}")
    print(f"   Masked:   {masked_conv}")
    print(f"   Ready for API call with {len(conv_matches)} items masked")


def demo_places_api_integration():
    """Demonstrate PII masking integrated with Places API."""
    
    print("\n\n🌐 Places API Integration Demo")
    print("=" * 50)
    
    # This would normally use real API key
    from receipt_label.utils.pii_masker import create_receipt_masker
    
    # Create a PII masker as it would be used in Places API
    masker = create_receipt_masker()
    
    print("\n✅ Places API client created with built-in PII masking")
    print("   - Credit cards, SSNs, etc. will be automatically masked")
    print("   - Business phones and addresses preserved for matching")
    print("   - All external API calls protected from PII exposure")
    
    # Example query that would be masked
    example_queries = [
        "Walmart at 123 Main St, card 4111111111111111",
        "Restaurant near SSN 123-45-6789 location",
        "Store with email customer@example.com"
    ]
    
    print("\n   Example queries that would be masked:")
    for query in example_queries:
        masked, _ = masker.mask_text(query)
        print(f"   - '{query}'")
        print(f"     → '{masked}'")


if __name__ == "__main__":
    print("🔐 PII Masking System Demo")
    print("=" * 70)
    print("\nThis demo shows how sensitive information is protected before")
    print("sending data to external APIs like Google Places API and GPT.")
    print()
    
    # Run demonstrations
    demo_pii_masking()
    demo_places_api_integration()
    
    print("\n\n✅ Demo completed successfully!")
    print("\n📝 Key Takeaways:")
    print("   • PII is automatically detected and masked before API calls")
    print("   • Business information (names, phones) preserved for Places API")
    print("   • Credit cards show last 4 digits for reference")
    print("   • Emails show domain for context")
    print("   • All external APIs protected from accidental PII exposure")