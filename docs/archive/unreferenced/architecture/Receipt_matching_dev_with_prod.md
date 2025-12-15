# How are we going to see which PROD images are in DEV?

## Dynamo DB

### Download all images and receipts from both PROD AND DEV and store as local JSON

```python
import os
import json
from dataclasses import asdict
from receipt_dynamo import DynamoClient

def download_all_images_and_receipts(table_name: str, output_dir: str):
    """
    Download all images and receipts from DynamoDB and save them as JSON files.
    
    Args:
        table_name: The DynamoDB table name (e.g., 'prod-table' or 'dev-table')
        output_dir: Directory where JSON files will be saved
    """
    # Initialize DynamoDB client
    dynamo_client = DynamoClient(table_name)
    
    # Create output directory
    os.makedirs(output_dir, exist_ok=True)
    
    # List all images
    all_images = []
    last_evaluated_key = None
    
    print(f"Downloading images from {table_name}...")
    while True:
        images, last_evaluated_key = dynamo_client.list_images(
            last_evaluated_key=last_evaluated_key
        )
        all_images.extend(images)
        
        print(f"Downloaded {len(all_images)} images so far...")
        
        if last_evaluated_key is None:
            break
    
    # Save images to JSON
    images_output_file = os.path.join(output_dir, f"{table_name}_images.json")
    with open(images_output_file, 'w') as f:
        json.dump(
            [asdict(image) for image in all_images],
            f,
            indent=2,
            default=str
        )
    print(f"Saved {len(all_images)} images to {images_output_file}")
    
    # List all receipts
    all_receipts = []
    last_evaluated_key = None
    
    print(f"Downloading receipts from {table_name}...")
    while True:
        receipts, last_evaluated_key = dynamo_client.list_receipts(
            last_evaluated_key=last_evaluated_key
        )
        all_receipts.extend(receipts)
        
        print(f"Downloaded {len(all_receipts)} receipts so far...")
        
        if last_evaluated_key is None:
            break
    
    # Save receipts to JSON
    receipts_output_file = os.path.join(output_dir, f"{table_name}_receipts.json")
    with open(receipts_output_file, 'w') as f:
        json.dump(
            [asdict(receipt) for receipt in all_receipts],
            f,
            indent=2,
            default=str
        )
    print(f"Saved {len(all_receipts)} receipts to {receipts_output_file}")

# Usage example:
# Download from PROD
download_all_images_and_receipts("prod-receipts-table", "./prod_data")

# Download from DEV
download_all_images_and_receipts("dev-receipts-table", "./dev_data")
```

### Download detailed data (lines and words) for comparison

```python
import os
import json
from dataclasses import asdict
from receipt_dynamo import DynamoClient

def download_detailed_data_for_comparison(table_name: str, output_dir: str):
    """
    Download all images and receipts with their full details (lines, words, letters, labels).
    
    Args:
        table_name: The DynamoDB table name
        output_dir: Directory where JSON files will be saved
    """
    # Initialize DynamoDB client
    dynamo_client = DynamoClient(table_name)
    
    # Create output directory
    os.makedirs(output_dir, exist_ok=True)
    
    # Get all images first
    all_images = []
    last_evaluated_key = None
    while True:
        images, last_evaluated_key = dynamo_client.list_images(
            last_evaluated_key=last_evaluated_key
        )
        all_images.extend(images)
        if last_evaluated_key is None:
            break
    
    print(f"Processing {len(all_images)} images from {table_name}...")
    
    # Process each image and get all its details
    detailed_data = []
    
    for i, image in enumerate(all_images):
        print(f"Processing image {i+1}/{len(all_images)}: {image.image_id}")
        
        # Get all details for this image
        details = dynamo_client.get_image_details(image.image_id)
        
        # Store everything as a dict
        image_data = {
            "image": asdict(image),
            "lines": [asdict(line) for line in details.lines],
            "words": [asdict(word) for word in details.words],
            "letters": [asdict(letter) for letter in details.letters],
            "receipts": [asdict(receipt) for receipt in details.receipts],
            "receipt_lines": [asdict(rl) for rl in details.receipt_lines],
            "receipt_words": [asdict(rw) for rw in details.receipt_words],
            "receipt_letters": [asdict(rl) for rl in details.receipt_letters],
            "receipt_word_labels": [asdict(rwl) for rwl in details.receipt_word_labels],
        }
        
        detailed_data.append(image_data)
    
    # Save to JSON
    output_file = os.path.join(output_dir, f"{table_name}_detailed_data.json")
    with open(output_file, 'w') as f:
        json.dump(detailed_data, f, indent=2, default=str)
    
    print(f"Saved detailed data to {output_file}")
    print(f"Total images: {len(all_images)}")
```

### Compare images and receipts by words and lines

```python
import json
from typing import Dict, List, Tuple
from collections import defaultdict

def load_json(file_path: str) -> List[Dict]:
    """Load JSON data from a file."""
    with open(file_path, 'r') as f:
        return json.load(f)

def extract_text_from_words(words: List[Dict], receipt_id: int = None) -> str:
    """
    Extract text from receipt words, optionally filtering by receipt_id.
    
    Args:
        words: List of word dictionaries
        receipt_id: Optional receipt ID to filter words
        
    Returns:
        Concatenated text from words
    """
    text_parts = []
    for word in words:
        # Only include receipt words if receipt_id matches or is None
        if receipt_id is None or word.get('receipt_id') == receipt_id:
            word_text = word.get('text', '')
            if word_text:
                text_parts.append(word_text)
    
    return ' '.join(text_parts)

def compare_images_and_receipts(
    prod_data_file: str,
    dev_data_file: str,
    output_file: str = "comparison_report.txt"
):
    """
    Compare images and receipts between PROD and DEV environments.
    
    Args:
        prod_data_file: Path to PROD detailed data JSON
        dev_data_file: Path to DEV detailed data JSON
        output_file: Path to save comparison report
    """
    # Load data
    prod_data = load_json(prod_data_file)
    dev_data = load_json(dev_data_file)
    
    # Build index of DEV images by image_id
    dev_images_by_id = {img['image']['image_id']: img for img in dev_data}
    
    # Track comparisons
    matching_images = []
    only_in_prod = []
    only_in_dev = []
    text_differences = []
    
    print(f"Comparing PROD ({len(prod_data)} images) with DEV ({len(dev_data)} images)...")
    
    for prod_img in prod_data:
        image_id = prod_img['image']['image_id']
        
        if image_id in dev_images_by_id:
            # Both environments have this image - compare in detail
            dev_img = dev_images_by_id[image_id]
            
            # Extract text from PROD and DEV receipt words
            prod_text = extract_text_from_words(prod_img['receipt_words'])
            dev_text = extract_text_from_words(dev_img['receipt_words'])
            
            # Compare by text
            if prod_text == dev_text:
                matching_images.append({
                    'image_id': image_id,
                    'status': 'identical_text',
                    'prod_receipt_count': len(prod_img['receipts']),
                    'dev_receipt_count': len(dev_img['receipts']),
                })
            else:
                text_differences.append({
                    'image_id': image_id,
                    'prod_text': prod_text[:200] + '...' if len(prod_text) > 200 else prod_text,
                    'dev_text': dev_text[:200] + '...' if len(dev_text) > 200 else dev_text,
                    'prod_word_count': len(prod_img['receipt_words']),
                    'dev_word_count': len(dev_img['receipt_words']),
                    'prod_line_count': len(prod_img['receipt_lines']),
                    'dev_line_count': len(dev_img['receipt_lines']),
                })
        else:
            # Only in PROD
            only_in_prod.append({
                'image_id': image_id,
                'receipt_count': len(prod_img['receipts']),
                'word_count': len(prod_img['receipt_words']),
            })
    
    # Find images only in DEV
    prod_images_by_id = {img['image']['image_id']: img for img in prod_data}
    for dev_img in dev_data:
        image_id = dev_img['image']['image_id']
        if image_id not in prod_images_by_id:
            only_in_dev.append({
                'image_id': image_id,
                'receipt_count': len(dev_img['receipts']),
                'word_count': len(dev_img['receipt_words']),
            })
    
    # Generate report
    report = []
    report.append("=" * 80)
    report.append("COMPARISON REPORT: PROD vs DEV")
    report.append("=" * 80)
    report.append("")
    
    report.append(f"Total PROD images: {len(prod_data)}")
    report.append(f"Total DEV images: {len(dev_data)}")
    report.append("")
    
    report.append("-" * 80)
    report.append(f"MATCHING IMAGES (identical text): {len(matching_images)}")
    report.append("-" * 80)
    for img in matching_images[:10]:  # Show first 10
        report.append(f"  Image ID: {img['image_id']}")
        report.append(f"    PROD receipts: {img['prod_receipt_count']}, DEV receipts: {img['dev_receipt_count']}")
    if len(matching_images) > 10:
        report.append(f"  ... and {len(matching_images) - 10} more")
    report.append("")
    
    report.append("-" * 80)
    report.append(f"TEXT DIFFERENCES: {len(text_differences)}")
    report.append("-" * 80)
    for diff in text_differences[:10]:  # Show first 10
        report.append(f"  Image ID: {diff['image_id']}")
        report.append(f"    PROD words: {diff['prod_word_count']}, lines: {diff['prod_line_count']}")
        report.append(f"    DEV words: {diff['dev_word_count']}, lines: {diff['dev_line_count']}")
        report.append(f"    PROD text: {diff['prod_text']}")
        report.append(f"    DEV text: {diff['dev_text']}")
        report.append("")
    if len(text_differences) > 10:
        report.append(f"  ... and {len(text_differences) - 10} more")
    report.append("")
    
    report.append("-" * 80)
    report.append(f"ONLY IN PROD: {len(only_in_prod)}")
    report.append("-" * 80)
    for img in only_in_prod[:10]:  # Show first 10
        report.append(f"  Image ID: {img['image_id']}")
        report.append(f"    Receipts: {img['receipt_count']}, Words: {img['word_count']}")
    if len(only_in_prod) > 10:
        report.append(f"  ... and {len(only_in_prod) - 10} more")
    report.append("")
    
    report.append("-" * 80)
    report.append(f"ONLY IN DEV: {len(only_in_dev)}")
    report.append("-" * 80)
    for img in only_in_dev[:10]:  # Show first 10
        report.append(f"  Image ID: {img['image_id']}")
        report.append(f"    Receipts: {img['receipt_count']}, Words: {img['word_count']}")
    if len(only_in_dev) > 10:
        report.append(f"  ... and {len(only_in_dev) - 10} more")
    report.append("")
    
    # Save report
    report_text = "\n".join(report)
    with open(output_file, 'w') as f:
        f.write(report_text)
    
    print(f"Comparison report saved to {output_file}")
    
    # Print summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"Identical images: {len(matching_images)}")
    print(f"Images with text differences: {len(text_differences)}")
    print(f"Images only in PROD: {len(only_in_prod)}")
    print(f"Images only in DEV: {len(only_in_dev)}")

# Usage:
# First download the data
# download_detailed_data_for_comparison("prod-table", "./prod_detailed")
# download_detailed_data_for_comparison("dev-table", "./dev_detailed")
#
# Then compare
# compare_images_and_receipts(
#     "prod_detailed/prod-table_detailed_data.json",
#     "dev_detailed/dev-table_detailed_data.json"
# )
```

## Chroma

### Attempt to download from S3 and update EFS

