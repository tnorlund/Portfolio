"""Test if imports are working."""

print("Starting imports...")

import os
print("✓ os imported")

from openai import AsyncOpenAI
print("✓ AsyncOpenAI imported")

from receipt_dynamo import DynamoClient
print("✓ DynamoClient imported")

from receipt_label.merchant_validation.handler import MerchantValidationHandler
print("✓ MerchantValidationHandler imported")

print("\nAll imports successful!")