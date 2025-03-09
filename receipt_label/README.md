# Receipt Label

A Python package for labeling and validating receipt data using various processing strategies including GPT and pattern matching.

## Features

- **Receipt Structure Analysis**: Identify sections and layout of receipts
- **Field Labeling**: Label individual words and fields in receipts
- **Data Validation**: Validate receipt data against external sources
- **Places API Integration**: Enrich receipt data with business information
- **Flexible Processing**: Support for both GPT and pattern-based processing
- **Comprehensive Validation**: Validate business names, addresses, phone numbers, dates, and amounts

## Installation

```bash
pip install receipt_label
```

For development installation:

```bash
git clone https://github.com/yourusername/receipt_label.git
cd receipt_label
pip install -e ".[dev]"
```

## Usage

### Basic Usage

```python
from receipt_label import ReceiptLabeler, Receipt, ReceiptWord

# Initialize the labeler
labeler = ReceiptLabeler(
    places_api_key="your_google_places_api_key",
    dynamodb_table_name="your_dynamodb_table",
    gpt_api_key="your_gpt_api_key"  # Optional
)

# Create a receipt object
receipt = Receipt(
    receipt_id="123",
    image_id="456",
    words=[
        ReceiptWord(
            text="Example",
            line_id=0,
            word_id=0,
            confidence=1.0
        ),
        # ... more words
    ]
)

# Label the receipt
result = await labeler.label_receipt(
    receipt=receipt,
    receipt_words=receipt.words,
    receipt_lines=[{"text": "Example line", "words": [...]}]
)

# Access results
print(f"Structure confidence: {result.structure_analysis['overall_confidence']}")
print(f"Field confidence: {result.field_analysis['metadata']['average_confidence']}")
print(f"Validation results: {result.validation_results}")
```

### Advanced Usage

#### Custom Processors

```python
from receipt_label import StructureProcessor, FieldProcessor

# Use custom processors
structure_processor = StructureProcessor()
field_processor = FieldProcessor()

# Configure processors
structure_processor.section_patterns = {
    "custom_section": {
        "keywords": ["custom", "keywords"],
        "position": "top",
        "max_lines": 5,
    }
}

field_processor.field_patterns = {
    "custom_field": {
        "patterns": [r"custom\s+pattern"],
        "position": "top",
        "max_lines": 5,
        "confidence": 0.9,
    }
}
```

#### Validation

```python
from receipt_label import ReceiptValidator
from receipt_label.utils import validate_business_name, validate_address

# Validate specific fields
is_valid, message, confidence = validate_business_name(
    receipt_name="Example Store",
    api_name="Example Store Inc"
)

# Validate address
is_valid, message, confidence = validate_address(
    receipt_address="123 Main St",
    api_address="123 Main Street"
)

# Use the validator class
validator = ReceiptValidator()
validation_results = validator.validate_receipt_data(
    field_analysis=result.field_analysis,
    places_api_data=result.places_api_data,
    receipt_words=receipt.words,
    batch_processor=labeler.places_processor
)
```

## API Access Patterns

The package leverages two primary external APIs to enhance receipt processing capabilities:

### Google Places API Integration

The Google Places API is used to validate and enrich business information from receipts. Key functionalities include:

- **Business Verification**: Validates business names and addresses against Google's extensive database
- **Location Enrichment**: Retrieves additional business details like full address, phone numbers, and business hours
- **Geocoding**: Converts addresses to coordinates for spatial analysis
- **Place Details**: Fetches rich metadata about businesses including ratings, website URLs, and business categories

The API is called automatically during the receipt validation process, comparing extracted receipt data against Places API results to ensure accuracy.

### ChatGPT API Integration

The ChatGPT API provides advanced natural language processing capabilities for receipt analysis:

- **Layout Understanding**: Analyzes receipt structure and identifies distinct sections
- **Field Extraction**: Intelligently extracts and labels fields like totals, tax amounts, and line items
- **Context Awareness**: Understands context-dependent information like special offers or discounts
- **Ambiguity Resolution**: Helps resolve unclear or non-standard receipt formats
- **Multi-language Support**: Processes receipts in various languages and formats

The GPT processor can be used as a standalone strategy or in combination with traditional pattern matching for enhanced accuracy.

### Access Patterns

#### Google Places API

The package uses the Google Places API for business validation and enrichment. Here are the supported access patterns:

```python
# 1. Direct API key initialization
labeler = ReceiptLabeler(places_api_key="your_google_places_api_key")

# 2. Environment variable
# Set GOOGLE_PLACES_API_KEY in your environment
import os
os.environ["GOOGLE_PLACES_API_KEY"] = "your_google_places_api_key"
labeler = ReceiptLabeler()  # Will automatically use environment variable

# 3. Configuration file
# Create a config.json file:
# {
#     "places_api_key": "your_google_places_api_key"
# }
labeler = ReceiptLabeler(config_file="path/to/config.json")

# 4. Custom Places API client
from receipt_label.data import PlacesAPIClient

class CustomPlacesClient(PlacesAPIClient):
    def __init__(self):
        # Custom initialization
        pass

    async def search_place(self, query: str):
        # Custom implementation
        pass

labeler = ReceiptLabeler(places_client=CustomPlacesClient())
```

### ChatGPT API

The package supports GPT-based processing for enhanced receipt analysis. Here are the supported access patterns:

```python
# 1. Direct API key initialization
labeler = ReceiptLabeler(gpt_api_key="your_gpt_api_key")

# 2. Environment variable
# Set OPENAI_API_KEY in your environment
import os
os.environ["OPENAI_API_KEY"] = "your_gpt_api_key"
labeler = ReceiptLabeler()  # Will automatically use environment variable

# 3. Configuration file
# Create a config.json file:
# {
#     "gpt_api_key": "your_gpt_api_key"
# }
labeler = ReceiptLabeler(config_file="path/to/config.json")

# 4. Custom GPT processor
from receipt_label.processors import GPTProcessor

class CustomGPTProcessor(GPTProcessor):
    def __init__(self):
        # Custom initialization
        pass

    async def process_receipt(self, receipt_text: str):
        # Custom implementation
        pass

labeler = ReceiptLabeler(gpt_processor=CustomGPTProcessor())

# 5. GPT Model Configuration
labeler = ReceiptLabeler(
    gpt_api_key="your_gpt_api_key",
    gpt_model="gpt-4",  # Default is "gpt-3.5-turbo"
    gpt_temperature=0.3,  # Default is 0.0
    gpt_max_tokens=2000  # Default is 1000
)
```

Both APIs support automatic retries with exponential backoff and rate limiting to ensure reliable operation and compliance with API quotas.

## Receipt Validation

The `ReceiptValidator` performs comprehensive validation of receipt data across multiple dimensions:

1. **Business Identity Validation**
   - Compares business names from receipt against Google Places API data
   - Validates business existence and name accuracy
   - Flags potential mismatches or discrepancies

2. **Address Verification**
   - Normalizes and compares receipt addresses with Places API data
   - Handles different address formats and variations
   - Validates address completeness and accuracy

3. **Phone Number Validation**
   - Compares phone numbers from receipt against Places API data
   - Normalizes phone numbers by removing non-numeric characters
   - Flags mismatches between receipt and official business phone numbers

4. **Hours Verification**
   - Validates receipt date and time formats
   - Supports multiple date/time format patterns
   - Prepared for future business hours verification against Places API data

5. **Cross-field Consistency**
   - Validates mathematical consistency of monetary amounts
   - Verifies subtotal + tax = total (with small rounding tolerance)
   - Handles currency symbols and number formatting
   - Flags mathematical discrepancies in calculations

Each validation category produces detailed results including:
- Warning and error messages for identified issues
- Confidence scores where applicable
- Detailed comparison information
- Overall validity status

## Package Structure

```
receipt_label/
├── core/              # Core functionality
│   ├── labeler.py     # Main labeling logic
│   └── validator.py   # Validation logic
├── data/              # Data handling
│   └── places_api.py  # Places API integration
├── models/            # Data models
│   └── receipt.py     # Receipt data models
├── processors/        # Processing strategies
│   ├── gpt.py        # GPT-based processing
│   ├── structure.py  # Structure analysis
│   └── field.py      # Field labeling
└── utils/            # Utility functions
    ├── address.py    # Address handling
    ├── date.py      # Date handling
    └── validation.py # Validation utilities
```

## Development

### Running Tests

```bash
pytest
```

### Code Style

```bash
# Format code
black .
isort .

# Type checking
mypy .

# Linting
flake8
```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Commit your changes
4. Push to the branch
5. Create a Pull Request

## License

This project is licensed under the MIT License - see the LICENSE file for details. 