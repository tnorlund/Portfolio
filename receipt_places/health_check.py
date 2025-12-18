"""
Health check endpoints for Places API v1 migration.

Provides health checks for:
- API connectivity (both legacy and v1)
- DynamoDB access
- Cache functionality
- Overall system health
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class HealthCheckResult:
    """Result of a health check."""
    status: str  # "healthy", "degraded", "unhealthy"
    message: str
    details: Dict[str, Any]
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for JSON serialization."""
        return {
            "status": self.status,
            "message": self.message,
            "details": self.details,
            "errors": self.errors,
        }


class PlacesAPIHealthChecker:
    """
    Health checker for Places API and related systems.

    Checks:
    - Google Places API v1 connectivity
    - Legacy API connectivity (for rollback)
    - DynamoDB access for ReceiptPlace writes
    - Cache functionality
    - Dual-write success rates
    """

    def __init__(
        self,
        places_client: Optional[Any] = None,
        dynamo_client: Optional[Any] = None,
        cache_manager: Optional[Any] = None,
    ):
        """
        Initialize health checker.

        Args:
            places_client: Places API client (v1 or legacy)
            dynamo_client: DynamoDB client
            cache_manager: Cache manager for Places API
        """
        self.places = places_client
        self.dynamo = dynamo_client
        self.cache = cache_manager

    def check_all(self) -> HealthCheckResult:
        """
        Run all health checks.

        Returns:
            Overall health check result
        """
        results = {}
        errors = []
        unhealthy_count = 0

        # Check Places API
        try:
            results["places_api"] = self.check_places_api()
            if results["places_api"].status != "healthy":
                unhealthy_count += 1
                errors.extend(results["places_api"].errors)
        except Exception as e:
            logger.exception("Places API check failed")
            results["places_api"] = HealthCheckResult(
                status="unhealthy",
                message="Places API health check failed",
                details={},
                errors=[str(e)],
            )
            unhealthy_count += 1
            errors.append(f"Places API check: {e!s}")

        # Check DynamoDB
        try:
            results["dynamodb"] = self.check_dynamodb()
            if results["dynamodb"].status != "healthy":
                unhealthy_count += 1
                errors.extend(results["dynamodb"].errors)
        except Exception as e:
            logger.exception("DynamoDB check failed")
            results["dynamodb"] = HealthCheckResult(
                status="unhealthy",
                message="DynamoDB health check failed",
                details={},
                errors=[str(e)],
            )
            unhealthy_count += 1
            errors.append(f"DynamoDB check: {e!s}")

        # Check cache
        if self.cache:
            try:
                results["cache"] = self.check_cache()
                if results["cache"].status != "healthy":
                    unhealthy_count += 1
                    errors.extend(results["cache"].errors)
            except Exception as e:
                logger.exception("Cache check failed")
                results["cache"] = HealthCheckResult(
                    status="unhealthy",
                    message="Cache health check failed",
                    details={},
                    errors=[str(e)],
                )
                unhealthy_count += 1
                errors.append(f"Cache check: {e!s}")

        # Determine overall status
        if unhealthy_count == 0:
            overall_status = "healthy"
            message = "All systems operational"
        elif unhealthy_count <= 1:
            overall_status = "degraded"
            message = f"{unhealthy_count} system degraded, investigating"
        else:
            overall_status = "unhealthy"
            message = f"{unhealthy_count} systems unhealthy, immediate attention needed"

        return HealthCheckResult(
            status=overall_status,
            message=message,
            details=results,
            errors=errors,
        )

    def check_places_api(self) -> HealthCheckResult:
        """Check Places API connectivity."""
        if not self.places:
            return HealthCheckResult(
                status="unhealthy",
                message="Places API client not configured",
                details={},
                errors=["No Places API client"],
            )

        try:
            # Try a simple lookup with a known place_id
            # Using a well-known location (Google Sydney office)
            test_place_id = "ChIJ68p4rXBCFzMRKx_QBcT3WYQ"

            result = self.places.get_place_details(test_place_id)

            if result:
                return HealthCheckResult(
                    status="healthy",
                    message="Places API responding normally",
                    details={
                        "api_version": getattr(
                            self.places, "api_version", "unknown"
                        ),
                        "test_place": test_place_id,
                        "response_status": "success",
                    },
                )
            else:
                return HealthCheckResult(
                    status="degraded",
                    message="Places API returned no data",
                    details={
                        "api_version": getattr(
                            self.places, "api_version", "unknown"
                        ),
                        "test_place": test_place_id,
                        "response_status": "empty",
                    },
                    errors=["API returned no data for valid place_id"],
                )

        except Exception as e:
            logger.exception("Places API check failed")
            return HealthCheckResult(
                status="unhealthy",
                message="Places API error",
                details={
                    "api_version": getattr(
                        self.places, "api_version", "unknown"
                    ),
                    "error_type": type(e).__name__,
                },
                errors=[str(e)],
            )

    def check_dynamodb(self) -> HealthCheckResult:
        """Check DynamoDB connectivity and ReceiptPlace operations."""
        if not self.dynamo:
            return HealthCheckResult(
                status="unhealthy",
                message="DynamoDB client not configured",
                details={},
                errors=["No DynamoDB client"],
            )

        try:
            # Check table exists and is accessible
            # Use public method to get table description
            import boto3
            client = boto3.client("dynamodb")
            table_status = client.describe_table(
                TableName=self.dynamo.table_name
            )

            table_info = table_status["Table"]
            return HealthCheckResult(
                status="healthy",
                message="DynamoDB accessible and operational",
                details={
                    "table_name": self.dynamo.table_name,
                    "table_status": table_info["TableStatus"],
                    "item_count": table_info.get("ItemCount", 0),
                    "gsi_count": len(table_info.get("GlobalSecondaryIndexes", [])),
                },
            )

        except Exception as e:
            logger.exception("DynamoDB check failed")
            return HealthCheckResult(
                status="unhealthy",
                message="DynamoDB error",
                details={"table_name": self.dynamo.table_name},
                errors=[str(e)],
            )

    def check_cache(self) -> HealthCheckResult:
        """Check cache functionality."""
        if not self.cache:
            return HealthCheckResult(
                status="degraded",
                message="Cache not configured",
                details={},
                errors=["No cache manager"],
            )

        try:
            # Try to get cache stats
            stats = getattr(self.cache, "get_stats", lambda: {})()

            return HealthCheckResult(
                status="healthy",
                message="Cache operational",
                details={
                    "cache_type": type(self.cache).__name__,
                    "stats": stats,
                },
            )

        except Exception as e:
            logger.exception("Cache check failed")
            return HealthCheckResult(
                status="degraded",
                message="Cache check failed",
                details={"cache_type": type(self.cache).__name__},
                errors=[str(e)],
            )

    def check_dual_write_status(self) -> Dict[str, Any]:
        """
        Check dual-write status and success rates.

        Returns:
            Statistics about dual-write operations
        """
        # This would be populated from metrics collected during operation
        return {
            "dual_write_enabled": True,
            "metadata_writes": 0,
            "receipt_place_writes": 0,
            "receipt_place_success_rate": 0.0,  # Will be populated at runtime
            "last_error": None,
        }
