"""AI Usage Metric data access mixin for DynamoDB operations."""

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from receipt_dynamo.data.base_operations import (
    FlattenedStandardMixin,
    PutRequestTypeDef,
    WriteRequestTypeDef,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import EntityValidationError
from receipt_dynamo.entities.ai_usage_metric import AIUsageMetric

if TYPE_CHECKING:
    pass


class _AIUsageMetric(FlattenedStandardMixin):
    """Mixin for AI usage metric operations in DynamoDB."""

    @handle_dynamodb_errors("add_ai_usage_metric")
    def put_ai_usage_metric(self, metric: AIUsageMetric) -> None:
        """
        Store a single AI usage metric in DynamoDB.

        Args:
            metric: The AI usage metric to store
        """
        self._validate_entity(metric, AIUsageMetric, "metric")
        # Convert metric to entity format that works with _add_entity
        temp_metric = type(
            "TempMetric", (), {"to_item": lambda: metric.to_dynamodb_item()}
        )()
        self._add_entity(temp_metric)

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

        # Convert metrics to DynamoDB items and create mapping for failure
        # tracking
        items = [metric.to_dynamodb_item() for metric in metrics]
        failed_metrics = []

        # Convert metrics to WriteRequestTypeDef format
        request_items = [
            WriteRequestTypeDef(PutRequest=PutRequestTypeDef(Item=item))
            for item in items
        ]

        try:
            self._batch_write_with_retry(request_items)
        except Exception:
            # If batch write fails, assume all metrics failed
            # This simplifies error handling compared to the original
            # complex logic
            failed_metrics = metrics.copy()

        return failed_metrics

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
            raise EntityValidationError(
                "Service parameter is required for date-based queries"
            )

        key_condition = "GSI1PK = :gsi1pk AND GSI1SK = :gsi1sk"
        expression_values = {
            ":gsi1pk": {"S": f"AI_USAGE#{service}"},
            ":gsi1sk": {"S": f"DATE#{date}"},
        }

        # Use _query_entities but return raw items
        items, _ = self._query_entities(
            index_name="GSI1",
            key_condition_expression=key_condition,
            expression_attribute_names=None,
            expression_attribute_values=expression_values,
            converter_func=lambda x: x,  # Return raw items
            limit=limit,
            last_evaluated_key=None,
        )

        return items

    @handle_dynamodb_errors("get_ai_usage_metric")
    def get_ai_usage_metric(
        self, service: str, model: str, timestamp: str, request_id: str
    ) -> Optional[AIUsageMetric]:
        """
        Get a specific AI usage metric.

        Args:
            service: Service name (e.g., "openai", "anthropic")
            model: Model name (e.g., "gpt-4", "claude-3")
            timestamp: ISO format timestamp
            request_id: Unique request identifier

        Returns:
            The metric if found, None otherwise
        """
        return self._get_entity(
            primary_key=f"AI_USAGE#{service}#{model}",
            sort_key=f"USAGE#{timestamp}#{request_id}",
            entity_class=AIUsageMetric,
            converter_func=lambda item: AIUsageMetric.from_dynamodb_item(item),
        )
