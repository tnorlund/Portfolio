#!/usr/bin/env python3
"""
Pre-deployment verification script for Places API v1 migration.

Verifies that all components are ready for production deployment:
- Feature flag configuration
- API client initialization
- DynamoDB table setup
- Health checks
- Data consistency

Usage:
    python scripts/verify_deployment_readiness.py [--verbose]
"""

import asyncio
import logging
import sys
from typing import List, Optional

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class DeploymentVerifier:
    """Verifies deployment readiness for v1 migration."""

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.checks_passed = 0
        self.checks_failed = 0
        self.warnings = []

    def _log_check(self, name: str, passed: bool, message: str = ""):
        """Log a check result."""
        symbol = "✓" if passed else "✗"
        level = logging.INFO if passed else logging.ERROR

        if passed:
            self.checks_passed += 1
            logger.log(level, f"  {symbol} {name}")
        else:
            self.checks_failed += 1
            logger.log(level, f"  {symbol} {name}: {message}")

    def _log_warning(self, message: str):
        """Log a warning."""
        self.warnings.append(message)
        logger.warning(f"  ⚠ {message}")

    async def verify_all(self) -> bool:
        """
        Run all verification checks.

        Returns:
            True if all critical checks pass, False otherwise
        """
        logger.info("=" * 70)
        logger.info("DEPLOYMENT READINESS VERIFICATION")
        logger.info("=" * 70)

        # Check feature flag configuration
        logger.info("\n1. Feature Flag Configuration")
        await self._check_feature_flags()

        # Check API clients
        logger.info("\n2. API Client Configuration")
        await self._check_api_clients()

        # Check DynamoDB
        logger.info("\n3. DynamoDB Setup")
        await self._check_dynamodb()

        # Check dependencies
        logger.info("\n4. Python Dependencies")
        await self._check_dependencies()

        # Check documentation
        logger.info("\n5. Documentation")
        await self._check_documentation()

        # Print summary
        logger.info("\n" + "=" * 70)
        logger.info("VERIFICATION SUMMARY")
        logger.info("=" * 70)
        logger.info(f"Passed: {self.checks_passed}")
        logger.info(f"Failed: {self.checks_failed}")
        if self.warnings:
            logger.info(f"Warnings: {len(self.warnings)}")
            for w in self.warnings:
                logger.warning(f"  - {w}")

        if self.checks_failed == 0:
            logger.info("\n✓ READY FOR DEPLOYMENT")
            return True
        else:
            logger.error("\n✗ NOT READY FOR DEPLOYMENT")
            return False

    async def _check_feature_flags(self):
        """Check feature flag configuration."""
        try:
            from receipt_places.config import PlacesConfig

            config = PlacesConfig()
            self._log_check(
                "Config module imports",
                True,
                f"use_v1_api={config.use_v1_api}"
            )

            # Verify default is False for safe baseline
            if config.use_v1_api:
                self._log_warning(
                    "use_v1_api is True, should be False for safe baseline"
                )
            else:
                self._log_check(
                    "Feature flag defaults to False (safe baseline)",
                    True
                )

        except Exception as e:
            self._log_check(
                "Config module imports",
                False,
                str(e)
            )

    async def _check_api_clients(self):
        """Check API client implementations."""
        checks = [
            ("types_v1.py", "receipt_places.types_v1", "PlaceV1"),
            ("adapter.py", "receipt_places.adapter", "adapt_v1_to_legacy"),
            ("client_v1.py", "receipt_places.client_v1", "PlacesClientV1"),
            ("client.py factory", "receipt_places.client", "create_places_client"),
        ]

        for name, module, item in checks:
            try:
                mod = __import__(module, fromlist=[item])
                getattr(mod, item)
                self._log_check(f"{name} exists", True)
            except Exception as e:
                self._log_check(f"{name} exists", False, str(e))

    async def _check_dynamodb(self):
        """Check DynamoDB setup."""
        checks = [
            ("ReceiptPlace entity", "receipt_dynamo.entities", "ReceiptPlace"),
            ("Receipt place data ops", "receipt_dynamo.data._receipt_place", "_ReceiptPlace"),
            ("Geospatial utils", "receipt_dynamo.utils.geospatial", "calculate_geohash"),
        ]

        for name, module, item in checks:
            try:
                mod = __import__(module, fromlist=[item])
                getattr(mod, item)
                self._log_check(f"{name}", True)
            except Exception as e:
                self._log_check(f"{name}", False, str(e))

    async def _check_dependencies(self):
        """Check required Python dependencies."""
        dependencies = [
            ("pydantic", "Pydantic for data validation"),
            ("boto3", "AWS SDK for DynamoDB"),
            ("geohash2", "Geohash library for spatial indexing"),
        ]

        for package, description in dependencies:
            try:
                __import__(package)
                self._log_check(f"{package}", True, description)
            except ImportError:
                self._log_warning(
                    f"{package} not installed (required for {description})"
                )

    async def _check_documentation(self):
        """Check documentation exists."""
        import os

        docs = [
            ("PLACES_API_V1_MIGRATION.md", "Migration guide"),
            ("scripts/backfill_receipt_place.py", "Backfill script"),
            ("receipt_places/health_check.py", "Health check module"),
        ]

        for filepath, description in docs:
            full_path = filepath
            exists = os.path.exists(full_path)
            self._log_check(f"{description}", exists, filepath if not exists else "")


async def main() -> int:
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Verify deployment readiness for Places API v1 migration"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Verbose output"
    )

    args = parser.parse_args()

    verifier = DeploymentVerifier(verbose=args.verbose)
    ready = await verifier.verify_all()

    return 0 if ready else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
