# Receipt Label

A Python package for labeling and validating receipt data using the Google Places API. This package provides functionality to enrich receipt data with business information, validate matches, and handle various data quality scenarios.

## Features

- Batch processing of receipts through the Google Places API
- Intelligent validation strategies based on available data (address, phone, URL, date)
- Confidence scoring and manual review flags
- Robust error handling and logging
- Comprehensive test coverage

## Installation

```bash
pip install receipt-label
```

## Usage

```python
from receipt_label import BatchPlacesProcessor

# Initialize the processor with your Google Places API key
processor = BatchPlacesProcessor(api_key="your_api_key")

# Process a batch of receipts
receipts = [
    {
        "receipt_id": "receipt_1",
        "words": [
            {
                "text": "WALMART",
                "extracted_data": {
                    "type": "name",
                    "value": "WALMART"
                }
            },
            {
                "text": "123 Main St",
                "extracted_data": {
                    "type": "address",
                    "value": "123 Main St"
                }
            },
            {
                "text": "(555) 123-4567",
                "extracted_data": {
                    "type": "phone",
                    "value": "(555) 123-4567"
                }
            }
        ]
    }
]

# Process the receipts
enriched_receipts = processor.process_receipt_batch(receipts)

# Access the results
for receipt in enriched_receipts:
    print(f"Receipt ID: {receipt['receipt_id']}")
    print(f"Confidence Level: {receipt['confidence_level']}")
    print(f"Validation Score: {receipt['validation_score']}")
    print(f"Requires Manual Review: {receipt['requires_manual_review']}")
    print(f"Places API Match: {receipt['places_api_match']}")
    print("---")
```

## Validation Strategies

The package implements different validation strategies based on the available data:

1. High Priority (3+ data points):
   - Address + Phone + Name validation
   - Fallback strategies for partial matches

2. Medium Priority (2 data points):
   - Address + Phone
   - Address + URL
   - Phone + Date
   - Address + Date

3. Low Priority (1 data point):
   - Basic validation with manual review required

4. No Data:
   - Marked for manual review

## Development

### Setup

1. Clone the repository
2. Create a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```
3. Install development dependencies:
   ```bash
   pip install -e ".[dev]"
   ```

### Running Tests

```bash
pytest
```

### Code Style

The package uses:
- Black for code formatting
- isort for import sorting
- mypy for type checking

Run the formatters:
```bash
black .
isort .
mypy .
```

## License

MIT License - see LICENSE file for details. 