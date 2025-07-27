"""
AI Usage Metric entity for tracking costs and usage of AI services.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from .base import DynamoDBEntity
from .util import _repr_str, assert_type


@dataclass(eq=True, unsafe_hash=False)
class AIUsageMetric(DynamoDBEntity):
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
    environment: Optional[str] = (
        None  # "production", "staging", "cicd", "development"
    )
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
        if self.total_tokens is None and (
            self.input_tokens or self.output_tokens
        ):
            self.total_tokens = (self.input_tokens or 0) + (
                self.output_tokens or 0
            )

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
        elif self.user_id:
            return f"USER#{self.user_id}"
        elif self.batch_id:
            return f"BATCH#{self.batch_id}"
        elif self.environment:
            return f"ENV#{self.environment}"
        return None

    @property
    def gsi3sk(self) -> Optional[str]:
        """GSI3 SK for temporal ordering."""
        if self.job_id or self.user_id or self.batch_id or self.environment:
            return f"AI_USAGE#{self.timestamp.isoformat()}"
        return None

    @property
    def item_type(self) -> str:
        return "AIUsageMetric"

    def __repr__(self) -> str:
        tokens_str = (
            f"{self.total_tokens} tokens"
            if self.total_tokens
            else "unknown tokens"
        )
        cost_str = (
            f"${self.cost_usd:.4f}"
            if self.cost_usd is not None
            else "unknown cost"
        )
        return (
            f"<AIUsageMetric {self.service}/{self.model} {self.operation} "
            f"{tokens_str} {cost_str}>"
        )

    def to_dynamodb_item(self) -> Dict:
        """Convert to DynamoDB item format."""
        item = {
            "PK": {"S": self.pk},
            "SK": {"S": self.sk},
            "GSI1PK": {"S": self.gsi1pk},
            "GSI1SK": {"S": self.gsi1sk},
            "GSI2PK": {"S": self.gsi2pk},
            "GSI2SK": {"S": self.gsi2sk},
            "TYPE": {"S": self.item_type},
            "service": {"S": self.service},
            "model": {"S": self.model},
            "operation": {"S": self.operation},
            "timestamp": {"S": self.timestamp.isoformat()},
            "requestId": {"S": self.request_id},
            "apiCalls": {"N": str(self.api_calls)},
            "date": {"S": self.date},
            "month": {"S": self.month},
            "hour": {"S": self.hour},
        }

        # Optional fields
        if self.gsi3pk and self.gsi3sk:
            item["GSI3PK"] = {"S": self.gsi3pk}
            item["GSI3SK"] = {"S": self.gsi3sk}

        if self.input_tokens is not None:
            item["inputTokens"] = {"N": str(self.input_tokens)}
        if self.output_tokens is not None:
            item["outputTokens"] = {"N": str(self.output_tokens)}
        if self.total_tokens is not None:
            item["totalTokens"] = {"N": str(self.total_tokens)}
        if self.cost_usd is not None:
            item["costUSD"] = {"N": str(Decimal(str(self.cost_usd)))}
        if self.latency_ms is not None:
            item["latencyMs"] = {"N": str(self.latency_ms)}

        if self.user_id:
            item["userId"] = {"S": self.user_id}
        if self.job_id:
            item["jobId"] = {"S": self.job_id}
        if self.batch_id:
            item["batchId"] = {"S": self.batch_id}
        if self.github_pr is not None:
            item["githubPR"] = {"N": str(self.github_pr)}
        if self.environment:
            item["environment"] = {"S": self.environment}
        if self.error:
            item["error"] = {"S": self.error}
        if self.metadata:
            item["metadata"] = self._to_dynamodb_value(self.metadata)

        return item

    def _to_dynamodb_value(self, value):
        """Convert a Python value to DynamoDB format."""
        if value is None:
            return {"NULL": True}
        elif isinstance(value, bool):
            return {"BOOL": value}
        elif isinstance(value, int) or isinstance(value, float):
            return {"N": str(value)}
        elif isinstance(value, str):
            return {"S": value}
        elif isinstance(value, dict):
            return {
                "M": {k: self._to_dynamodb_value(v) for k, v in value.items()}
            }
        elif isinstance(value, list):
            return {"L": [self._to_dynamodb_value(v) for v in value]}
        else:
            return {"S": str(value)}

    @classmethod
    def _from_dynamodb_value(cls, value):
        """Convert a DynamoDB value to Python format."""
        if "NULL" in value:
            return None
        elif "BOOL" in value:
            return value["BOOL"]
        elif "N" in value:
            num_str = value["N"]
            if "." in num_str:
                return float(num_str)
            else:
                return int(num_str)
        elif "S" in value:
            return value["S"]
        elif "M" in value:
            return {
                k: cls._from_dynamodb_value(v) for k, v in value["M"].items()
            }
        elif "L" in value:
            return [cls._from_dynamodb_value(v) for v in value["L"]]
        else:
            raise ValueError(f"Unknown DynamoDB value type: {value}")

    @classmethod
    def from_dynamodb_item(cls, item: Dict) -> "AIUsageMetric":
        """Create instance from DynamoDB item."""
        return cls(
            service=item["service"]["S"],
            model=item["model"]["S"],
            operation=item["operation"]["S"],
            timestamp=datetime.fromisoformat(item["timestamp"]["S"]),
            request_id=item["requestId"]["S"],
            input_tokens=(
                int(item["inputTokens"]["N"])
                if "inputTokens" in item
                else None
            ),
            output_tokens=(
                int(item["outputTokens"]["N"])
                if "outputTokens" in item
                else None
            ),
            total_tokens=(
                int(item["totalTokens"]["N"])
                if "totalTokens" in item
                else None
            ),
            api_calls=int(item["apiCalls"]["N"]),
            cost_usd=(
                float(item["costUSD"]["N"]) if "costUSD" in item else None
            ),
            latency_ms=(
                int(item["latencyMs"]["N"]) if "latencyMs" in item else None
            ),
            user_id=item.get("userId", {}).get("S"),
            job_id=item.get("jobId", {}).get("S"),
            batch_id=item.get("batchId", {}).get("S"),
            github_pr=(
                int(item["githubPR"]["N"]) if "githubPR" in item else None
            ),
            environment=item.get("environment", {}).get("S"),
            error=item.get("error", {}).get("S"),
            metadata=(
                cls._from_dynamodb_value(item["metadata"])
                if "metadata" in item
                else {}
            ),
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
            ":end": {
                "S": f"DATE#{end_date}" if end_date else f"DATE#{start_date}"
            },
        }

        response = dynamo_client.query(
            TableName=dynamo_client.table_name,
            IndexName="GSI1",
            KeyConditionExpression=key_condition,
            ExpressionAttributeValues=expression_values,
        )

        return [
            cls.from_dynamodb_item(item) for item in response.get("Items", [])
        ]

    @classmethod
    def get_total_cost_by_date(
        cls, dynamo_client, date: str
    ) -> Dict[str, float]:
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
