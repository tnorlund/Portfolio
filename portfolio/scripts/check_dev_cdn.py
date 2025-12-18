#!/usr/bin/env python3
"""
Check DEV CDN configuration and compare with existing structure.
"""

import logging
import os
import sys

# Add parent directories to path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
portfolio_root = os.path.dirname(script_dir)
parent_dir = os.path.dirname(portfolio_root)

sys.path.insert(0, parent_dir)

from pulumi import automation as auto

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    """Check DEV configuration."""
    work_dir = os.path.join(parent_dir, "infra")

    logger.info("Getting DEV configuration...")
    dev_stack = auto.create_or_select_stack(
        stack_name="tnorlund/portfolio/dev",
        work_dir=work_dir,
    )
    dev_outputs = dev_stack.outputs()
    dev_table = dev_outputs["dynamodb_table_name"].value

    try:
        cdn_bucket = dev_outputs["cdn_bucket_name"].value
        logger.info(f"DEV table: {dev_table}")
        logger.info(f"DEV CDN bucket: {cdn_bucket}")
    except KeyError:
        logger.info(f"DEV table: {dev_table}")
        logger.info("DEV CDN bucket: Not configured")


if __name__ == "__main__":
    main()
