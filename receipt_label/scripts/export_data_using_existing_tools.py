#!/usr/bin/env python3
"""
Export receipt data using existing receipt_dynamo export tools.

This script uses the existing export_image function from receipt_dynamo
to download receipt data for local testing of pattern detection enhancements.
"""

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import List

import boto3

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Import existing export functionality
from receipt_dynamo.data.export_image import export_image


def get_sample_image_ids(table_name: str, limit: int = 20) -> List[str]:
    """Get a sample of image IDs from the DynamoDB table."""
    dynamodb = boto3.client('dynamodb')
    
    try:
        # Query the GSITYPE index for IMAGE type
        response = dynamodb.query(
            TableName=table_name,
            IndexName='GSITYPE',
            KeyConditionExpression='#type = :image_type',
            ExpressionAttributeNames={
                '#type': 'TYPE'
            },
            ExpressionAttributeValues={
                ':image_type': {'S': 'IMAGE'}
            },
            Limit=limit
        )
        
        image_ids = []
        for item in response.get('Items', []):
            pk = item.get('PK', {}).get('S', '')
            if pk.startswith('IMAGE#'):
                image_id = pk.replace('IMAGE#', '')
                image_ids.append(image_id)
        
        logging.info(f"Found {len(image_ids)} image IDs")
        return image_ids
        
    except Exception as e:
        logging.error(f"Error getting image IDs: {e}")
        return []


def export_sample_data(table_name: str, output_dir: str, size: int = 20) -> int:
    """Export sample receipt data using existing export tools."""
    # Create output directory
    Path(output_dir).mkdir(exist_ok=True, parents=True)
    
    # Get sample image IDs
    image_ids = get_sample_image_ids(table_name, limit=size * 2)  # Get extra in case some fail
    
    if not image_ids:
        logging.warning("No image IDs found to export")
        return 0
    
    exported_count = 0
    for i, image_id in enumerate(image_ids[:size]):
        try:
            logging.info(f"Exporting image {i+1}/{size}: {image_id}")
            export_image(table_name, image_id, output_dir)
            exported_count += 1
            
            if exported_count % 5 == 0:
                logging.info(f"Exported {exported_count}/{size} images")
                
        except Exception as e:
            logging.error(f"Failed to export image {image_id}: {e}")
            continue
    
    logging.info(f"Successfully exported {exported_count} images to {output_dir}")
    return exported_count


def export_specific_image(table_name: str, image_id: str, output_dir: str) -> bool:
    """Export a specific image using existing export tools."""
    Path(output_dir).mkdir(exist_ok=True, parents=True)
    
    try:
        logging.info(f"Exporting specific image: {image_id}")
        export_image(table_name, image_id, output_dir)
        logging.info(f"Successfully exported image {image_id}")
        return True
    except Exception as e:
        logging.error(f"Failed to export image {image_id}: {e}")
        return False


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Export receipt data using existing tools")
    parser.add_argument("command", choices=["sample", "single"], 
                       help="Export command to run")
    
    # Common arguments
    parser.add_argument("--table-name", default="ReceiptsTable-d7ff76a",
                       help="DynamoDB table name")
    parser.add_argument("--output-dir", default="./receipt_data_existing",
                       help="Output directory")
    parser.add_argument("--verbose", "-v", action="store_true", 
                       help="Verbose logging")
    
    # Sample command arguments
    parser.add_argument("--size", type=int, default=20,
                       help="Number of images to export (sample)")
    
    # Single image arguments
    parser.add_argument("--image-id", help="Image ID (single)")
    
    args = parser.parse_args()
    
    # Configure logging
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    
    try:
        if args.command == "sample":
            count = export_sample_data(
                table_name=args.table_name,
                output_dir=args.output_dir,
                size=args.size
            )
            print(f"Exported {count} images to {args.output_dir}")
            
        elif args.command == "single":
            if not args.image_id:
                print("Error: --image-id is required for single command")
                sys.exit(1)
            
            success = export_specific_image(
                table_name=args.table_name,
                image_id=args.image_id,
                output_dir=args.output_dir
            )
            if success:
                print(f"Exported image {args.image_id}")
            else:
                print("Export failed")
                sys.exit(1)
                
    except KeyboardInterrupt:
        print("\nExport cancelled by user")
        sys.exit(1)
    except Exception as e:
        logging.error(f"Export failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()