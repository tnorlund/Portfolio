# Receipt Label

A Python package for labeling and validating receipt data using GPT and progressive processing strategies.

## Features

- **Receipt Structure Analysis**: Identify sections and layout of receipts using GPT
- **Field Labeling**: Label individual words and fields in receipts using GPT
- **Progressive Line Item Processing**: Advanced line item extraction with confidence scoring
- **Data Validation**: Validate receipt data against external sources
- **Places API Integration**: Enrich receipt data with business information
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
from receipt_label import ReceiptLabeler, Receipt, ReceiptWord, ReceiptLine

# Initialize the labeler
labeler = ReceiptLabeler(
    places_api_key="your_google_places_api_key",
    dynamodb_table_name="your_dynamodb_table",
    gpt_api_key="your_gpt_api_key"
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

# Create receipt lines
receipt_lines = [
    ReceiptLine(
        text="Example line",
        line_id=0,
        bounding_box={"x": 0, "y": 0, "width": 100, "height": 20}
    )
]

# Label the receipt
result = await labeler.label_receipt(
    receipt=receipt,
    receipt_words=receipt.words,
    receipt_lines=receipt_lines
)

# Access results
print(f"Structure confidence: {result.structure_analysis['overall_confidence']}")
print(f"Field confidence: {result.field_analysis['metadata']['average_confidence']}")
print(f"Line items found: {result.line_item_analysis['total_found']}")
print(f"Validation results: {result.validation_results}")
```

### Validation

```python
from receipt_label import ReceiptValidator
from receipt_label.utils import validate_business_name, validate_address

# Use the validator class
validator = ReceiptValidator()
validation_results = validator.validate_receipt_data(
    field_analysis=result.field_analysis,
    places_api_data=result.places_api_data,
    receipt_words=receipt.words,
    line_item_analysis=result.line_item_analysis,
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

### ChatGPT API Integration

The ChatGPT API provides advanced natural language processing capabilities for receipt analysis:

- **Layout Understanding**: Analyzes receipt structure and identifies distinct sections
- **Field Extraction**: Intelligently extracts and labels fields like totals, tax amounts, and line items
- **Context Awareness**: Understands context-dependent information like special offers or discounts
- **Ambiguity Resolution**: Helps resolve unclear or non-standard receipt formats
- **Multi-language Support**: Processes receipts in various languages and formats

### Access Patterns

#### Google Places API

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
```

### ChatGPT API

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

# 4. GPT Model Configuration
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
│   ├── receipt.py     # Receipt data models
│   └── line_item.py   # Line item models
├── processors/        # Processing strategies
│   ├── gpt.py        # GPT-based processing
│   └── progressive_processor.py  # Progressive line item processing
└── utils/            # Utility functions
    ├── address.py    # Address handling
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

### Versioning

This package follows semantic versioning (SemVer) and uses a single source of truth for version management:

1. The canonical version is defined in `receipt_label/version.py` as `__version__`
2. All package components reference this single version definition
3. Packaging files (setup.py and pyproject.toml) read from this source

To update the version:

```bash
# 1. Edit receipt_label/version.py to change the version number
# 2. Run the update script to sync all version references
python update_version.py
```

This ensures version consistency across all parts of the codebase and packaging.

## Contributing

1. Fork the repository
2. Create a feature branch
3. Commit your changes
4. Push to the branch
5. Create a Pull Request

## License

This project is licensed under the MIT License - see the LICENSE file for details.
