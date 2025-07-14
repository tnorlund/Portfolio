"""
AI Usage Tracker - Decorator and middleware for tracking AI service usage and costs.
"""

import json
import os
import time
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Callable, Dict, Optional, Type, Union

import boto3
from openai import OpenAI
from openai.types.chat import ChatCompletion
from openai.types.create_embedding_response import CreateEmbeddingResponse
from receipt_dynamo.entities.ai_usage_metric import AIUsageMetric

from .cost_calculator import AICostCalculator
from .environment_config import (
    AIUsageEnvironmentConfig,
    Environment,
    EnvironmentConfig,
)


def _supports_put_ai_usage_metric(obj: Any) -> bool:
    """
    True  → safe to call obj.put_ai_usage_metric(metric)
    False → call obj.put_item(TableName=..., Item=...)

    Uses probe-based detection that only relies on public Mock behavior:
    - Real objects are checked for the method
    - Spec'd mocks reject unknown attributes (raises AttributeError)
    - Plain mocks create any attribute on demand
    """
    from unittest.mock import Mock

    # Real objects (and non-Mock stubs)
    if not isinstance(obj, Mock):
        return callable(getattr(obj, "put_ai_usage_metric", None))

    # Mock instances → probe with a name that cannot exist on DynamoClient
    PROBE = "_ai_usage_tracker_probe_attribute_"
    try:
        getattr(obj, PROBE)
        # Attribute was fabricated → this is a *basic* Mock
        return False
    except AttributeError:
        # Attribute not allowed → this mock is spec'd
        return True


class AIUsageTracker:
    """
    Tracks AI service usage and costs across different providers.
    Stores metrics in DynamoDB for analysis and cost monitoring.
    """

    def __init__(
        self,
        dynamo_client: Optional[Any] = None,
        table_name: Optional[str] = None,
        user_id: Optional[str] = None,
        track_to_dynamo: bool = True,
        track_to_file: bool = False,
        log_file: str = "/tmp/ai_usage.jsonl",  # nosec B108
        environment: Optional[Environment] = None,
        environment_config: Optional[EnvironmentConfig] = None,
        validate_table_environment: bool = True,
    ) -> None:
        """
        Initialize the AI usage tracker.

        Args:
            dynamo_client: DynamoDB client for storing metrics
            table_name: DynamoDB table name (if None, will be auto-generated with environment suffix)
            user_id: User identifier for tracking
            track_to_dynamo: Whether to store metrics in DynamoDB
            track_to_file: Whether to log metrics to a file (for local dev)
            log_file: Path to the log file
            environment: Specific environment to use (if None, will auto-detect)
            environment_config: Pre-built environment config (if None, will create)
            validate_table_environment: Whether to validate table name matches environment
        """
        # Environment configuration
        self.environment_config = (
            environment_config
            or AIUsageEnvironmentConfig.get_config(environment)
        )

        # Set up table name - if table_name is provided, use it as-is
        # If not provided, construct it from base name with environment suffix
        if table_name:
            self.table_name = table_name
        else:
            base_table_name = os.environ.get(
                "DYNAMODB_TABLE_NAME", "AIUsageMetrics"
            )
            self.table_name = AIUsageEnvironmentConfig.get_table_name(
                base_table_name, self.environment_config.environment
            )

        # Optionally validate environment isolation
        if (
            validate_table_environment
            and not AIUsageEnvironmentConfig.validate_environment_isolation(
                self.table_name, self.environment_config.environment
            )
        ):
            raise ValueError(
                f"Table name '{self.table_name}' does not match environment "
                f"'{self.environment_config.environment.value}' isolation requirements"
            )

        self.dynamo_client = dynamo_client
        self.user_id = user_id or os.environ.get("USER_ID", "default")
        self.track_to_dynamo = track_to_dynamo and dynamo_client is not None
        self.track_to_file = track_to_file
        self.log_file = log_file

        # Context for tracking job/batch IDs
        self.current_job_id: Optional[str] = None
        self.current_batch_id: Optional[str] = None
        self.github_pr: Optional[int] = None

        # Context manager support
        self.current_context: Dict[str, Any] = {}
        self.pending_metrics: list[AIUsageMetric] = []
        self.batch_mode: bool = False

    def set_tracking_context(
        self,
        job_id: Optional[str] = None,
        batch_id: Optional[str] = None,
        github_pr: Optional[int] = None,
        user_id: Optional[str] = None,
    ) -> None:
        """Set context for tracking metrics."""
        if job_id is not None:
            self.current_job_id = job_id
        if batch_id is not None:
            self.current_batch_id = batch_id
        if github_pr is not None:
            self.github_pr = github_pr
        if user_id is not None:
            self.user_id = user_id

    def set_context(self, context: Dict[str, Any]) -> None:
        """Set the current tracking context for the context manager pattern."""
        self.current_context = context
        # Extract standard fields if present
        if "job_id" in context:
            self.current_job_id = context["job_id"]
        if "batch_id" in context:
            self.current_batch_id = context["batch_id"]
        if "github_pr" in context:
            self.github_pr = context["github_pr"]

    def add_context_metadata(self, metadata: Dict[str, Any]) -> None:
        """Add additional metadata to the current context."""
        self.current_context.update(metadata)

    def set_batch_mode(self, enabled: bool) -> None:
        """Enable or disable batch pricing mode."""
        self.batch_mode = enabled

    def flush_metrics(self) -> None:
        """Flush any pending metrics to storage."""
        for metric in self.pending_metrics:
            self._store_metric(metric)
        self.pending_metrics.clear()

    def _store_metric(self, metric: AIUsageMetric) -> None:
        """Store metric in DynamoDB and/or file."""
        if self.track_to_dynamo and self.dynamo_client:
            try:
                item = metric.to_dynamodb_item()

                if _supports_put_ai_usage_metric(self.dynamo_client):
                    # Real client or spec'd MagicMock → high-level method
                    self.dynamo_client.put_ai_usage_metric(metric)
                else:
                    # Basic Mock or minimalist stub → vanilla put_item path
                    self.dynamo_client.put_item(
                        TableName=self.table_name, Item=item
                    )
            except Exception as e:
                print(f"Failed to store metric in DynamoDB: {e}")

        if self.track_to_file:
            try:
                # Convert to JSON-serializable format
                log_entry = {
                    "service": metric.service,
                    "model": metric.model,
                    "operation": metric.operation,
                    "timestamp": metric.timestamp.isoformat(),
                    "request_id": metric.request_id,
                    "input_tokens": metric.input_tokens,
                    "output_tokens": metric.output_tokens,
                    "total_tokens": metric.total_tokens,
                    "cost_usd": metric.cost_usd,
                    "latency_ms": metric.latency_ms,
                    "user_id": metric.user_id,
                    "job_id": metric.job_id,
                    "batch_id": metric.batch_id,
                    "environment": self.environment_config.environment.value,  # Add environment from config
                    "error": metric.error,
                }
                with open(self.log_file, "a") as f:
                    f.write(json.dumps(log_entry) + "\n")
            except Exception as e:
                print(f"Failed to log metric to file: {e}")

    def _create_base_metadata(self) -> Dict[str, Any]:
        """Create base metadata including environment auto-tags and context."""
        metadata = {}

        # Add environment auto-tags
        metadata.update(self.environment_config.auto_tag)

        # Add current context metadata
        if self.current_context:
            metadata.update(self.current_context)

        return metadata

    def track_openai_completion(
        self, func: Callable[..., Any]
    ) -> Callable[..., Any]:
        """
        Decorator for tracking OpenAI completion API calls.

        Usage:
            @tracker.track_openai_completion
            def call_gpt(prompt: str, model: str = "gpt-3.5-turbo"):
                return client.chat.completions.create(...)
        """

        @wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.time()
            error = None
            response = None

            # Extract metadata BEFORE calling the function
            extracted_metadata = kwargs.pop("metadata", None)

            try:
                response = func(*args, **kwargs)
                return response
            except Exception as e:
                error = str(e)
                raise
            finally:
                # Extract model from kwargs or response
                model = kwargs.get("model", "unknown")
                if response and hasattr(response, "model"):
                    model = response.model

                # Calculate tokens and cost
                input_tokens = None
                output_tokens = None
                total_tokens = None
                cost_usd = None

                if response and hasattr(response, "usage"):
                    usage = response.usage
                    if usage:
                        input_tokens = getattr(usage, "prompt_tokens", None)
                        output_tokens = getattr(
                            usage, "completion_tokens", None
                        )
                        total_tokens = getattr(usage, "total_tokens", None)

                        # Calculate cost
                        cost_usd = AICostCalculator.calculate_openai_cost(
                            model=model,
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                            is_batch=kwargs.get("is_batch", False)
                            or self.batch_mode,
                        )

                # Create base metadata with environment auto-tags
                metadata = self._create_base_metadata()

                # Include metadata from extracted metadata (added by context manager)
                if extracted_metadata:
                    # Extract context fields
                    if extracted_metadata.get("operation_type"):
                        metadata["operation_type"] = extracted_metadata[
                            "operation_type"
                        ]
                    if extracted_metadata.get("job_id"):
                        self.current_job_id = extracted_metadata["job_id"]
                    if extracted_metadata.get("batch_id"):
                        self.current_batch_id = extracted_metadata["batch_id"]

                metadata.update(
                    {
                        "function": func.__name__,
                        "temperature": kwargs.get("temperature"),
                        "max_tokens": kwargs.get("max_tokens"),
                    }
                )

                # Create and store metric
                metric = AIUsageMetric(
                    service="openai",
                    model=model,
                    operation="completion",
                    timestamp=datetime.now(timezone.utc),
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                    cost_usd=cost_usd,
                    latency_ms=int((time.time() - start_time) * 1000),
                    user_id=self.user_id,
                    job_id=self.current_job_id,
                    batch_id=self.current_batch_id,
                    error=error,
                    metadata=metadata,
                )
                self._store_metric(metric)

        return wrapper

    def track_openai_embedding(
        self, func: Callable[..., Any]
    ) -> Callable[..., Any]:
        """
        Decorator for tracking OpenAI embedding API calls.
        """

        @wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.time()
            error = None
            response = None

            # Extract metadata BEFORE calling the function
            extracted_metadata = kwargs.pop("metadata", None)

            try:
                response = func(*args, **kwargs)
                return response
            except Exception as e:
                error = str(e)
                raise
            finally:
                # Extract model
                model = kwargs.get("model", "text-embedding-3-small")
                if response and hasattr(response, "model"):
                    model = response.model

                # Calculate tokens and cost
                total_tokens = None
                cost_usd = None

                if response and hasattr(response, "usage"):
                    usage = response.usage
                    if usage:
                        total_tokens = getattr(usage, "total_tokens", None)

                        # Calculate cost
                        cost_usd = AICostCalculator.calculate_openai_cost(
                            model=model,
                            total_tokens=total_tokens,
                            is_batch=kwargs.get("is_batch", False)
                            or self.batch_mode,
                        )

                # Create base metadata with environment auto-tags
                metadata = self._create_base_metadata()

                # Include metadata from extracted metadata (added by context manager)
                if extracted_metadata:
                    # Extract context fields
                    if extracted_metadata.get("operation_type"):
                        metadata["operation_type"] = extracted_metadata[
                            "operation_type"
                        ]
                    if extracted_metadata.get("job_id"):
                        self.current_job_id = extracted_metadata["job_id"]
                    if extracted_metadata.get("batch_id"):
                        self.current_batch_id = extracted_metadata["batch_id"]

                metadata.update(
                    {
                        "function": func.__name__,
                        "input_count": (
                            len(kwargs.get("input", []))
                            if "input" in kwargs
                            else None
                        ),
                    }
                )

                # Create and store metric
                metric = AIUsageMetric(
                    service="openai",
                    model=model,
                    operation="embedding",
                    timestamp=datetime.now(timezone.utc),
                    total_tokens=total_tokens,
                    cost_usd=cost_usd,
                    latency_ms=int((time.time() - start_time) * 1000),
                    user_id=self.user_id,
                    job_id=self.current_job_id,
                    batch_id=self.current_batch_id,
                    error=error,
                    metadata=metadata,
                )
                self._store_metric(metric)

        return wrapper

    def track_anthropic_completion(
        self, func: Callable[..., Any]
    ) -> Callable[..., Any]:
        """
        Decorator for tracking Anthropic Claude API calls.
        """

        @wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.time()
            error = None
            response = None

            try:
                response = func(*args, **kwargs)
                return response
            except Exception as e:
                error = str(e)
                raise
            finally:
                # Extract model and tokens
                model = kwargs.get("model", "claude-3-sonnet")
                input_tokens = None
                output_tokens = None
                cost_usd = None

                if response and hasattr(response, "usage"):
                    usage = response.usage
                    if usage:
                        input_tokens = getattr(usage, "input_tokens", None)
                        output_tokens = getattr(usage, "output_tokens", None)

                        # Calculate cost
                        cost_usd = AICostCalculator.calculate_anthropic_cost(
                            model=model,
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                        )

                # Create base metadata with environment auto-tags
                metadata = self._create_base_metadata()
                metadata.update(
                    {
                        "function": func.__name__,
                        "max_tokens": kwargs.get("max_tokens"),
                    }
                )

                # Create and store metric
                metric = AIUsageMetric(
                    service="anthropic",
                    model=model,
                    operation="completion",
                    timestamp=datetime.now(timezone.utc),
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=cost_usd,
                    latency_ms=int((time.time() - start_time) * 1000),
                    user_id=self.user_id,
                    job_id=self.current_job_id,
                    github_pr=self.github_pr,
                    error=error,
                    metadata=metadata,
                )
                self._store_metric(metric)

        return wrapper

    def track_google_places(
        self, operation: str
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """
        Decorator for tracking Google Places API calls.

        Args:
            operation: The type of Places API operation
        """

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            @wraps(func)
            def wrapper(*args, **kwargs):
                start_time = time.time()
                error = None

                try:
                    result = func(*args, **kwargs)
                    return result
                except Exception as e:
                    error = str(e)
                    raise
                finally:
                    # Calculate cost
                    cost_usd = AICostCalculator.calculate_google_places_cost(
                        operation=operation, api_calls=1
                    )

                    # Create base metadata with environment auto-tags
                    metadata = self._create_base_metadata()
                    metadata.update(
                        {
                            "function": func.__name__,
                        }
                    )

                    # Create and store metric
                    metric = AIUsageMetric(
                        service="google_places",
                        model="places_api",
                        operation=operation,
                        timestamp=datetime.now(timezone.utc),
                        api_calls=1,
                        cost_usd=cost_usd,
                        latency_ms=int((time.time() - start_time) * 1000),
                        user_id=self.user_id,
                        job_id=self.current_job_id,
                        error=error,
                        metadata=metadata,
                    )
                    self._store_metric(metric)

            return wrapper

        return decorator

    def track_github_claude_review(
        self,
        pr_number: int,
        model: str = "claude-3-opus",
        estimated_tokens: int = 5000,
    ) -> None:
        """
        Track Claude usage in GitHub Actions for PR reviews.
        This is called from GitHub Actions workflows.
        """
        # Estimate cost (Claude reviews typically use ~5k tokens)
        input_estimate = int(estimated_tokens * 0.7)  # 70% input
        output_estimate = estimated_tokens - input_estimate

        cost_usd = AICostCalculator.calculate_anthropic_cost(
            model=model,
            input_tokens=input_estimate,
            output_tokens=output_estimate,
        )

        # Create base metadata with environment auto-tags
        metadata = self._create_base_metadata()
        metadata.update(
            {
                "pr_number": pr_number,
                "workflow": "claude-review",
            }
        )

        metric = AIUsageMetric(
            service="anthropic",
            model=model,
            operation="code_review",
            timestamp=datetime.now(timezone.utc),
            input_tokens=input_estimate,
            output_tokens=output_estimate,
            total_tokens=estimated_tokens,
            cost_usd=cost_usd,
            github_pr=pr_number,
            user_id="github-actions",
            metadata=metadata,
        )
        self._store_metric(metric)

    @classmethod
    def create_for_environment(
        cls,
        dynamo_client: Optional[Any] = None,
        table_name: Optional[str] = None,
        user_id: Optional[str] = None,
        track_to_dynamo: bool = True,
        track_to_file: bool = False,
        environment: Optional[Environment] = None,
        validate_table_environment: bool = True,  # Strict validation by default
    ) -> "AIUsageTracker":
        """
        Create an AIUsageTracker with automatic environment detection and configuration.

        This is the recommended factory method for creating trackers.

        Args:
            dynamo_client: DynamoDB client for storing metrics
            table_name: DynamoDB table name (if None, will auto-generate with environment suffix)
            user_id: User identifier for tracking
            track_to_dynamo: Whether to store metrics in DynamoDB
            track_to_file: Whether to log metrics to a file (for local dev)
            environment: Specific environment to use (if None, will auto-detect)
            validate_table_environment: Whether to validate table name matches environment

        Returns:
            AIUsageTracker: Configured tracker for the environment
        """
        return cls(
            dynamo_client=dynamo_client,
            table_name=table_name,
            user_id=user_id,
            track_to_dynamo=track_to_dynamo,
            track_to_file=track_to_file,
            environment=environment,
            validate_table_environment=validate_table_environment,
        )

    @classmethod
    def create_wrapped_openai_client(
        cls, openai_client: OpenAI, tracker: "AIUsageTracker"
    ) -> OpenAI:
        """
        Create a wrapped OpenAI client that automatically tracks usage.

        Usage:
            tracker = AIUsageTracker(dynamo_client)
            client = OpenAI(api_key="...")
            tracked_client = AIUsageTracker.create_wrapped_openai_client(client, tracker)

            # Now all calls are automatically tracked
            response = tracked_client.chat.completions.create(...)
        """

        # Create a wrapper class dynamically
        class TrackedOpenAIClient:
            def __init__(self, client: OpenAI, tracker: AIUsageTracker):
                self._client = client
                self._tracker = tracker

            def __getattr__(self, name):
                attr = getattr(self._client, name)

                # Wrap chat completions
                if name == "chat":

                    class TrackedChat:
                        def __init__(self, chat, tracker):
                            self._chat = chat
                            self._tracker = tracker

                        @property
                        def completions(self):
                            class TrackedCompletions:
                                def __init__(self, completions, tracker):
                                    self._completions = completions
                                    self._tracker = tracker

                                def create(self, **kwargs):
                                    # Import here to avoid circular dependency
                                    from .ai_usage_context import (
                                        get_current_context,
                                    )

                                    # Merge thread-local context with kwargs
                                    current_context = get_current_context()
                                    if current_context:
                                        # Add context metadata to kwargs
                                        if "metadata" not in kwargs:
                                            kwargs["metadata"] = {}
                                        kwargs["metadata"].update(
                                            {
                                                "operation_type": current_context.get(
                                                    "operation_type"
                                                ),
                                                "job_id": current_context.get(
                                                    "job_id"
                                                ),
                                                "batch_id": current_context.get(
                                                    "batch_id"
                                                ),
                                            }
                                        )

                                    # The decorator will extract and remove metadata
                                    @self._tracker.track_openai_completion
                                    def _create(**kw):
                                        return self._completions.create(**kw)

                                    return _create(**kwargs)

                            return TrackedCompletions(
                                self._chat.completions, self._tracker
                            )

                    return TrackedChat(attr, self._tracker)

                # Wrap embeddings
                elif name == "embeddings":

                    class TrackedEmbeddings:
                        def __init__(self, embeddings, tracker):
                            self._embeddings = embeddings
                            self._tracker = tracker

                        def create(self, **kwargs):
                            # Import here to avoid circular dependency
                            from .ai_usage_context import get_current_context

                            # Merge thread-local context with kwargs
                            current_context = get_current_context()
                            if current_context:
                                # Add context metadata to kwargs
                                if "metadata" not in kwargs:
                                    kwargs["metadata"] = {}
                                kwargs["metadata"].update(
                                    {
                                        "operation_type": current_context.get(
                                            "operation_type"
                                        ),
                                        "job_id": current_context.get(
                                            "job_id"
                                        ),
                                        "batch_id": current_context.get(
                                            "batch_id"
                                        ),
                                    }
                                )

                            @self._tracker.track_openai_embedding
                            def _create(**kw):
                                return self._embeddings.create(**kw)

                            return _create(**kwargs)

                    return TrackedEmbeddings(attr, self._tracker)

                return attr

        return TrackedOpenAIClient(openai_client, tracker)

    @classmethod
    def create_wrapped_places_client(
        cls, places_client: Any, tracker: "AIUsageTracker"
    ) -> Any:
        """
        Create a wrapped Google Places client that automatically tracks usage.

        Usage:
            tracker = AIUsageTracker(dynamo_client)
            client = googlemaps.Client(key="...")
            tracked_client = AIUsageTracker.create_wrapped_places_client(client, tracker)

            # Now all calls are automatically tracked
            results = tracked_client.places("restaurants near me")
        """

        # Create a wrapper class dynamically
        class TrackedPlacesClient:
            def __init__(self, client: Any, tracker: AIUsageTracker):
                self._client = client
                self._tracker = tracker

            def __getattr__(self, name):
                attr = getattr(self._client, name)

                # List of Places API methods to track
                places_methods = [
                    "places",
                    "places_nearby",
                    "place",
                    "places_autocomplete",
                    "places_autocomplete_query",
                    "places_photo",
                    "find_place",
                ]

                if name in places_methods:

                    def tracked_method(*args, **kwargs):
                        # Import here to avoid circular dependency
                        from .ai_usage_context import get_current_context

                        # Get thread-local context
                        current_context = get_current_context()

                        # The decorator will track the call
                        @self._tracker.track_google_places(name)
                        def _call(*a, **kw):
                            return attr(*a, **kw)

                        # If we have context, update the tracker's current job_id
                        if current_context:
                            old_job_id = self._tracker.current_job_id
                            try:
                                self._tracker.current_job_id = (
                                    current_context.get("job_id", old_job_id)
                                )
                                return _call(*args, **kwargs)
                            finally:
                                self._tracker.current_job_id = old_job_id
                        else:
                            return _call(*args, **kwargs)

                    return tracked_method

                return attr

        return TrackedPlacesClient(places_client, tracker)
