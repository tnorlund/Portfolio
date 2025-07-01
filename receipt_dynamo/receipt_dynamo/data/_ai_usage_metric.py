"""AI Usage Metric data access mixin for DynamoDB operations."""

import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from receipt_dynamo.data._base import DynamoClientProtocol

if TYPE_CHECKING:
    from receipt_dynamo.data._base import (
        PutRequestTypeDef,
        WriteRequestTypeDef,
    )

# These are used at runtime, not just for type checking
from receipt_dynamo.data._base import (
    PutRequestTypeDef,
    WriteRequestTypeDef,
)
from receipt_dynamo.entities.ai_usage_metric import AIUsageMetric


class _AIUsageMetric(DynamoClientProtocol):
    """Mixin for AI usage metric operations in DynamoDB."""

    def put_ai_usage_metric(self, metric: AIUsageMetric) -> None:
        """
        Store a single AI usage metric in DynamoDB.

        Args:
            metric: The AI usage metric to store
        """
        item = metric.to_dynamodb_item()
        self._client.put_item(TableName=self.table_name, Item=item)

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

        # Convert metrics to DynamoDB items
        items = [metric.to_dynamodb_item() for metric in metrics]

        # Process in batches of 25 (DynamoDB limit)
        failed_metrics = []

        for i in range(0, len(items), 25):
            batch = items[i : i + 25]
            request_items = {
                self.table_name: [
                    WriteRequestTypeDef(PutRequest=PutRequestTypeDef(Item=item))
                    for item in batch
                ]
            }

            response = self._client.batch_write_item(RequestItems=request_items)

            # Handle unprocessed items
            unprocessed = response.get("UnprocessedItems", {})
            if unprocessed and self.table_name in unprocessed:
                # Map unprocessed items back to metrics
                unprocessed_requests = unprocessed[self.table_name]
                for request in unprocessed_requests:
                    if "PutRequest" in request:
                        # Find the corresponding metric
                        item = request["PutRequest"]["Item"]
                        # Match by requestId (camelCase as per DynamoDB item format)
                        for j, metric in enumerate(metrics[i : i + 25]):
                            if metric.request_id == item.get("requestId", {}).get("S"):
                                failed_metrics.append(metric)
                                break

        return failed_metrics

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
            raise ValueError("Service parameter is required for date-based queries")

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
