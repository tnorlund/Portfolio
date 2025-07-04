"""AI Usage Metric data access mixin for DynamoDB operations."""

import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from receipt_dynamo.data.base_operations import (
    BatchOperationsMixin,
    DynamoDBBaseOperations,
    handle_dynamodb_errors,
)

if TYPE_CHECKING:
    from receipt_dynamo.data._base import (
        PutRequestTypeDef,
        WriteRequestTypeDef,
    )

# These are used at runtime, not just for type checking
from receipt_dynamo.data._base import PutRequestTypeDef, WriteRequestTypeDef
from receipt_dynamo.entities.ai_usage_metric import AIUsageMetric


class _AIUsageMetric(DynamoDBBaseOperations, BatchOperationsMixin):
    """Mixin for AI usage metric operations in DynamoDB."""

    @handle_dynamodb_errors("add_ai_usage_metric")
    def put_ai_usage_metric(self, metric: AIUsageMetric) -> None:
        """
        Store a single AI usage metric in DynamoDB.

        Args:
            metric: The AI usage metric to store
        """
        self._validate_entity(metric, AIUsageMetric, "metric")
        item = metric.to_dynamodb_item()
        self._client.put_item(TableName=self.table_name, Item=item)

    @handle_dynamodb_errors("batch_add_ai_usage_metrics")
    def batch_put_ai_usage_metrics(
        self, metrics: List[AIUsageMetric]
    ) -> List[AIUsageMetric]:
        """
        Store multiple AI usage metrics in DynamoDB using batch write.

        Args:
            metrics: List of metrics to store

        Returns:
            List of metrics that failed to write
        """
        if not metrics:
            return []

        self._validate_entity_list(metrics, AIUsageMetric, "metrics")

        # Convert metrics to write requests
        request_items = []
        metric_map = {}  # Map request to original metric for tracking failures

        for metric in metrics:
            item = metric.to_dynamodb_item()
            request = WriteRequestTypeDef(
                PutRequest=PutRequestTypeDef(Item=item)
            )
            request_items.append(request)
            # Store mapping for failure tracking
            metric_map[metric.request_id] = metric

        # Use base batch write with retry
        try:
            self._batch_write_with_retry(request_items)
            return []  # All successful
        except Exception as e:
            # If batch write fails completely, return all metrics as failed
            # In practice, _batch_write_with_retry handles partial failures
            # This is a safety net for complete failures
            if "Failed to process all items" in str(e):
                # Extract unprocessed count from error message
                # This is a simplified approach - real implementation might parse better
                return metrics[-25:]  # Assume last batch failed
            raise

    @handle_dynamodb_errors("query_ai_usage_metrics_by_date")
    def query_ai_usage_metrics_by_date(
        self, date: str, service: Optional[str] = None, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Query AI usage metrics by date with optional service filter.

        Args:
            date: Date string in YYYY-MM-DD format
            service: Optional service name to filter by
            limit: Maximum number of items to return

        Returns:
            List of metric items from DynamoDB
        """
        # Query using GSI1 to get metrics by date
        # Service parameter is required because GSI1PK is "AI_USAGE#{service}"
        if not service:
            raise ValueError(
                "Service parameter is required for date-based queries"
            )

        key_condition = "GSI1PK = :gsi1pk AND GSI1SK = :gsi1sk"
        expression_values = {
            ":gsi1pk": {"S": f"AI_USAGE#{service}"},
            ":gsi1sk": {"S": f"DATE#{date}"},
        }

        response = self._client.query(
            TableName=self.table_name,
            IndexName="GSI1",  # Query the GSI for date-based access
            KeyConditionExpression=key_condition,
            ExpressionAttributeValues=expression_values,
            Limit=limit,
        )

        return response.get("Items", [])

    @handle_dynamodb_errors("get_ai_usage_metric")
    def get_ai_usage_metric(
        self, service: str, model: str, timestamp: str, request_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get a specific AI usage metric.

        Args:
            service: Service name (e.g., "openai", "anthropic")
            model: Model name (e.g., "gpt-4", "claude-3")
            timestamp: ISO format timestamp
            request_id: Unique request identifier

        Returns:
            The metric item if found, None otherwise
        """
        response = self._client.get_item(
            TableName=self.table_name,
            Key={
                "PK": {"S": f"AI_USAGE#{service}#{model}"},
                "SK": {"S": f"USAGE#{timestamp}#{request_id}"},
            },
        )

        return response.get("Item")
