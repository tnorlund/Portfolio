"""
AI Usage Metric entity for tracking costs and usage of AI services.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from .base import DynamoDBEntity
from .entity_mixins import SerializationMixin


@dataclass(eq=True, unsafe_hash=False)
class AIUsageMetric(SerializationMixin, DynamoDBEntity):
    """
    Tracks usage and costs for AI service calls (OpenAI, Anthropic, Google
    Places).

    Uses:
    - PK: "AI_USAGE#{service}#{model}"
    - SK: "USAGE#{timestamp}#{request_id}"
    - GSI1PK: "AI_USAGE#{service}"
    - GSI1SK: "DATE#{date}"
    - GSI2PK: "AI_USAGE_COST"
    - GSI2SK: "COST#{date}#{service}"
    """

    service: str  # "openai", "anthropic", "google_places"
    model: str  # "gpt-3.5-turbo", "claude-3-opus", etc.
    operation: str  # "completion", "embedding", "place_lookup", "code_review"
    timestamp: datetime
    request_id: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    api_calls: int = 1
    cost_usd: Optional[float] = None
    latency_ms: Optional[int] = None
    user_id: Optional[str] = None
    job_id: Optional[str] = None
    batch_id: Optional[str] = None
    github_pr: Optional[int] = None
    environment: Optional[str] = None  # "production", "staging", "cicd", "development"
    error: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = field(default_factory=dict)
    # Computed fields
    date: str = field(init=False)
    month: str = field(init=False)
    hour: str = field(init=False)

    def __post_init__(self) -> None:
        """Validate and normalize initialization arguments."""
        self.service = self.service.lower()
        if not self.request_id:
            self.request_id = str(uuid.uuid4())

        # Calculate total_tokens if not provided
        if self.total_tokens is None and (self.input_tokens or self.output_tokens):
            self.total_tokens = (self.input_tokens or 0) + (self.output_tokens or 0)

        # Ensure metadata is a dict
        if self.metadata is None:
            self.metadata = {}

        # Computed fields
        self.date = self.timestamp.strftime("%Y-%m-%d")
        self.month = self.timestamp.strftime("%Y-%m")
        self.hour = self.timestamp.strftime("%Y-%m-%d-%H")

    @property
    def pk(self) -> str:
        return f"AI_USAGE#{self.service}#{self.model}"

    @property
    def sk(self) -> str:
        return f"USAGE#{self.timestamp.isoformat()}#{self.request_id}"

    @property
    def gsi1pk(self) -> str:
        return f"AI_USAGE#{self.service}"

    @property
    def gsi1sk(self) -> str:
        return f"DATE#{self.date}"

    @property
    def gsi2pk(self) -> str:
        return "AI_USAGE_COST"

    @property
    def gsi2sk(self) -> str:
        return f"COST#{self.date}#{self.service}"

    @property
    def gsi3pk(self) -> Optional[str]:
        """Enhanced GSI3 PK with priority hierarchy for scope-based queries."""
        # Priority: job_id > user_id > batch_id > environment
        if self.job_id:
            return f"JOB#{self.job_id}"
        if self.user_id:
            return f"USER#{self.user_id}"
        if self.batch_id:
            return f"BATCH#{self.batch_id}"
        if self.environment:
            return f"ENV#{self.environment}"
        return None

    @property
    def gsi3sk(self) -> Optional[str]:
        """GSI3 SK for temporal ordering."""
        if self.job_id or self.user_id or self.batch_id or self.environment:
            return f"AI_USAGE#{self.timestamp.isoformat()}"
        return None

    @property
    def key(self) -> Dict[str, Any]:
        """Returns the primary key for DynamoDB."""
        return {"PK": {"S": self.pk}, "SK": {"S": self.sk}}

    @property
    def item_type(self) -> str:
        return "AIUsageMetric"

    def __repr__(self) -> str:
        tokens_str = (
            f"{self.total_tokens} tokens" if self.total_tokens else "unknown tokens"
        )
        cost_str = (
            f"${self.cost_usd:.4f}" if self.cost_usd is not None else "unknown cost"
        )
        return (
            f"<AIUsageMetric {self.service}/{self.model} {self.operation} "
            f"{tokens_str} {cost_str}>"
        )

    def to_item(self) -> Dict[str, Any]:
        """Convert to DynamoDB item format using SerializationMixin."""
        # Build GSI keys
        gsi_keys = {
            "GSI1PK": {"S": self.gsi1pk},
            "GSI1SK": {"S": self.gsi1sk},
            "GSI2PK": {"S": self.gsi2pk},
            "GSI2SK": {"S": self.gsi2sk},
        }

        # Add GSI3 keys if available
        if self.gsi3pk and self.gsi3sk:
            gsi_keys["GSI3PK"] = {"S": self.gsi3pk}
            gsi_keys["GSI3SK"] = {"S": self.gsi3sk}

        # Custom field mappings for DynamoDB naming conventions
        custom_fields = {**gsi_keys}

        # Add required fields
        custom_fields["request_id"] = self._serialize_value(self.request_id)
        custom_fields["api_calls"] = self._serialize_value(self.api_calls)

        # Add optional fields only if they have values
        if self.input_tokens is not None:
            custom_fields["input_tokens"] = self._serialize_value(self.input_tokens)
        if self.output_tokens is not None:
            custom_fields["output_tokens"] = self._serialize_value(self.output_tokens)
        if self.total_tokens is not None:
            custom_fields["total_tokens"] = self._serialize_value(self.total_tokens)
        if self.cost_usd is not None:
            custom_fields["cost_usd"] = self._serialize_value(
                self.cost_usd, serialize_decimal=True
            )
        if self.latency_ms is not None:
            custom_fields["latency_ms"] = self._serialize_value(self.latency_ms)
        if self.user_id is not None:
            custom_fields["user_id"] = self._serialize_value(self.user_id)
        if self.job_id is not None:
            custom_fields["job_id"] = self._serialize_value(self.job_id)
        if self.batch_id is not None:
            custom_fields["batch_id"] = self._serialize_value(self.batch_id)
        if self.github_pr is not None:
            custom_fields["github_pr"] = self._serialize_value(self.github_pr)
        if self.environment is not None:
            custom_fields["environment"] = self._serialize_value(self.environment)
        if self.error is not None:
            custom_fields["error"] = self._serialize_value(self.error)
        if self.metadata:
            custom_fields["metadata"] = self._serialize_value(self.metadata)

        # Exclude computed fields and internal properties from
        # auto-serialization
        exclude_fields = {
            "pk",
            "sk",
            "gsi1pk",
            "gsi1sk",
            "gsi2pk",
            "gsi2sk",
            "gsi3pk",
            "gsi3sk",
            "item_type",
            "request_id",
            "api_calls",
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "cost_usd",
            "latency_ms",
            "user_id",
            "job_id",
            "batch_id",
            "github_pr",
            "environment",
            "error",
            "metadata",
        }

        return self.build_dynamodb_item(
            entity_type=self.item_type,
            custom_fields=custom_fields,
            exclude_fields=exclude_fields,
        )

    def to_dynamodb_item(self) -> Dict[str, Any]:
        """Compatibility method for tests expecting to_dynamodb_item."""
        return self.to_item()

    @classmethod
    def from_dynamodb_item(cls, item: Dict) -> "AIUsageMetric":
        """Create instance from DynamoDB item using type-safe EntityFactory."""
        from receipt_dynamo.entities.entity_factory import EntityFactory

        # Type-safe extractors for all fields
        custom_extractors = {
            "service": EntityFactory.extract_string_field("service"),
            "model": EntityFactory.extract_string_field("model"),
            "operation": EntityFactory.extract_string_field("operation"),
            "timestamp": EntityFactory.extract_datetime_field("timestamp"),
            "request_id": EntityFactory.extract_string_field("request_id"),
            "input_tokens": EntityFactory.extract_int_field("input_tokens"),
            "output_tokens": EntityFactory.extract_int_field("output_tokens"),
            "total_tokens": EntityFactory.extract_int_field("total_tokens"),
            "api_calls": EntityFactory.extract_int_field("api_calls", default=1),
            "cost_usd": EntityFactory.extract_float_field("cost_usd"),
            "latency_ms": EntityFactory.extract_int_field("latency_ms"),
            "user_id": EntityFactory.extract_string_field("user_id"),
            "job_id": EntityFactory.extract_string_field("job_id"),
            "batch_id": EntityFactory.extract_string_field("batch_id"),
            "github_pr": EntityFactory.extract_int_field("github_pr"),
            "environment": EntityFactory.extract_string_field("environment"),
            "error": EntityFactory.extract_string_field("error"),
            "metadata": lambda item: cls.safe_deserialize_field(
                item, "metadata", default={}, field_type=dict
            ),
        }

        required_keys = {
            "service",
            "model",
            "operation",
            "timestamp",
        }

        # No field mappings needed - using snake_case consistently
        field_mappings = {}

        return EntityFactory.create_entity(
            entity_class=cls,
            item=item,
            required_keys=required_keys,
            field_mappings=field_mappings,
            custom_extractors=custom_extractors,
        )

    @classmethod
    def query_by_service_date(
        cls,
        dynamo_client,
        service: str,
        start_date: str,
        end_date: Optional[str] = None,
    ) -> List["AIUsageMetric"]:
        """Query usage metrics by service and date range."""
        key_condition = f"GSI1PK = :pk AND GSI1SK BETWEEN :start AND :end"
        expression_values = {
            ":pk": {"S": f"AI_USAGE#{service}"},
            ":start": {"S": f"DATE#{start_date}"},
            ":end": {"S": f"DATE#{end_date}" if end_date else f"DATE#{start_date}"},
        }

        response = dynamo_client.query(
            TableName=dynamo_client.table_name,
            IndexName="GSI1",
            KeyConditionExpression=key_condition,
            ExpressionAttributeValues=expression_values,
        )

        return [cls.from_dynamodb_item(item) for item in response.get("Items", [])]

    @classmethod
    def get_total_cost_by_date(cls, dynamo_client, date: str) -> Dict[str, float]:
        """Get total cost for all services on a specific date."""
        key_condition = "GSI2PK = :pk AND begins_with(GSI2SK, :date)"
        expression_values = {
            ":pk": {"S": "AI_USAGE_COST"},
            ":date": {"S": f"COST#{date}"},
        }

        response = dynamo_client.query(
            TableName=dynamo_client.table_name,
            IndexName="GSI2",
            KeyConditionExpression=key_condition,
            ExpressionAttributeValues=expression_values,
        )

        costs_by_service: Dict[str, Any] = {}
        for item in response.get("Items", []):
            metric = cls.from_dynamodb_item(item)
            if metric.cost_usd:
                if metric.service not in costs_by_service:
                    costs_by_service[metric.service] = 0.0
                costs_by_service[metric.service] += metric.cost_usd

        return costs_by_service

    def __hash__(self) -> int:
        """Returns the hash value of the AIUsageMetric object."""
        return hash(
            (
                self.service,
                self.model,
                self.operation,
                self.timestamp,
                self.request_id,
                self.input_tokens,
                self.output_tokens,
                self.total_tokens,
                self.api_calls,
                self.cost_usd,
                self.latency_ms,
                self.user_id,
                self.job_id,
                self.batch_id,
                self.github_pr,
                self.environment,
                self.error,
                tuple(self.metadata.items()) if self.metadata else None,
            )
        )


def item_to_ai_usage_metric(item: Dict) -> AIUsageMetric:
    """Convert DynamoDB item to :class:`AIUsageMetric` instance."""
    return AIUsageMetric.from_dynamodb_item(item)
