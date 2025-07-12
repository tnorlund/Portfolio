#!/usr/bin/env python3
"""
Script to check and fix Receipt SK formatting in DynamoDB.

This script:
1. Lists all Receipt entities
2. Checks if their SK is properly formatted as RECEIPT#00001 (5-digit zero-padded)
3. Updates any incorrectly formatted receipts
"""

import argparse
import logging
import os
import sys
from typing import List, Dict, Any, Tuple

# Add parent directories to path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
portfolio_root = os.path.dirname(script_dir)
parent_dir = os.path.dirname(portfolio_root)

sys.path.insert(0, parent_dir)
sys.path.insert(0, os.path.join(parent_dir, "receipt_dynamo"))

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities import Receipt

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class ReceiptSKFixer:
    """Handles checking and fixing Receipt SK formatting."""

    def __init__(self, dynamo_table_name: str, dry_run: bool = True):
        self.dynamo_client = DynamoClient(dynamo_table_name)
        self.dry_run = dry_run
        self.stats = {
            "total_scanned": 0,
            "correctly_formatted": 0,
            "needs_fix": 0,
            "fixed": 0,
            "errors": 0,
        }
        self.problematic_receipts: List[Tuple[Receipt, str, str]] = []

    def check_sk_format(self, receipt: Receipt) -> Tuple[bool, str]:
        """
        Check if a receipt's SK is correctly formatted.
        
        Returns:
            Tuple of (is_correct, actual_sk)
        """
        expected_sk = f"RECEIPT#{receipt.receipt_id:05d}"
        actual_sk = receipt.key["SK"]["S"]
        
        return actual_sk == expected_sk, actual_sk

    def scan_and_fix_receipts(self):
        """Scan all receipts and fix any with incorrect SK formatting."""
        logger.info("Scanning for receipts with incorrect SK formatting...")
        
        last_evaluated_key = None
        
        while True:
            # Get a batch of receipts
            receipts, last_evaluated_key = self.dynamo_client.list_receipts(
                limit=100,
                last_evaluated_key=last_evaluated_key,
            )
            
            self.stats["total_scanned"] += len(receipts)
            
            # Check each receipt
            for receipt in receipts:
                is_correct, actual_sk = self.check_sk_format(receipt)
                
                if is_correct:
                    self.stats["correctly_formatted"] += 1
                else:
                    self.stats["needs_fix"] += 1
                    expected_sk = f"RECEIPT#{receipt.receipt_id:05d}"
                    self.problematic_receipts.append((receipt, actual_sk, expected_sk))
                    logger.warning(
                        f"Found incorrectly formatted receipt: "
                        f"Image={receipt.image_id}, Receipt={receipt.receipt_id}, "
                        f"Current SK='{actual_sk}', Expected SK='{expected_sk}'"
                    )
                    
                    if not self.dry_run:
                        try:
                            # Update the receipt to trigger SK regeneration
                            self.dynamo_client.update_receipt(receipt)
                            self.stats["fixed"] += 1
                            logger.info(f"Fixed receipt {receipt.image_id}:{receipt.receipt_id}")
                        except Exception as e:
                            self.stats["errors"] += 1
                            logger.error(
                                f"Failed to fix receipt {receipt.image_id}:{receipt.receipt_id}: {e}"
                            )
            
            # If no more items, break
            if not last_evaluated_key:
                break
        
        # Print summary
        logger.info("\n" + "=" * 50)
        logger.info("RECEIPT SK FORMAT CHECK SUMMARY")
        logger.info("=" * 50)
        logger.info(f"Total receipts scanned: {self.stats['total_scanned']}")
        logger.info(f"Correctly formatted: {self.stats['correctly_formatted']}")
        logger.info(f"Needs fix: {self.stats['needs_fix']}")
        
        if not self.dry_run:
            logger.info(f"Fixed: {self.stats['fixed']}")
            logger.info(f"Errors: {self.stats['errors']}")
        
        if self.problematic_receipts:
            logger.info("\nProblematic receipts found:")
            for receipt, actual_sk, expected_sk in self.problematic_receipts[:10]:  # Show first 10
                logger.info(
                    f"  - Image: {receipt.image_id}, Receipt: {receipt.receipt_id}, "
                    f"SK: '{actual_sk}' -> '{expected_sk}'"
                )
            if len(self.problematic_receipts) > 10:
                logger.info(f"  ... and {len(self.problematic_receipts) - 10} more")
        
        if self.dry_run and self.stats["needs_fix"] > 0:
            logger.info("\nThis was a DRY RUN - no changes were made")
            logger.info(f"Run with --no-dry-run to fix {self.stats['needs_fix']} receipts")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Check and fix Receipt SK formatting in DynamoDB"
    )
    parser.add_argument(
        "--stack",
        required=True,
        choices=["dev", "prod"],
        help="Pulumi stack to use (dev or prod)",
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        help="Actually fix the receipts (default is dry run)",
    )
    
    args = parser.parse_args()
    
    # Get configuration from Pulumi
    from pulumi import automation as auto
    
    # Set up the stack
    stack_name = f"tnorlund/portfolio/{args.stack}"
    work_dir = os.path.join(parent_dir, "infra")
    
    logger.info(f"Using stack: {stack_name}")
    
    # Create a stack reference to get outputs
    stack = auto.create_or_select_stack(
        stack_name=stack_name,
        work_dir=work_dir,
    )
    
    # Get the outputs
    outputs = stack.outputs()
    
    # Extract configuration
    dynamo_table_name = outputs["dynamodb_table_name"].value
    
    logger.info(f"DynamoDB table: {dynamo_table_name}")
    logger.info(f"Mode: {'LIVE UPDATE' if args.no_dry_run else 'DRY RUN'}")
    
    # Create and run fixer
    fixer = ReceiptSKFixer(
        dynamo_table_name=dynamo_table_name,
        dry_run=not args.no_dry_run,
    )
    
    fixer.scan_and_fix_receipts()


if __name__ == "__main__":
    main()