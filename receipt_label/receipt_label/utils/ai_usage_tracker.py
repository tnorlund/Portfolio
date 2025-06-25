"""
AI Usage Tracker - Decorator and middleware for tracking AI service usage and costs.
"""
import json
import os
import time
from datetime import datetime
from functools import wraps
from typing import Any, Callable, Dict, Optional, Union

import boto3
from openai import OpenAI
from openai.types.chat import ChatCompletion
from openai.types.create_embedding_response import CreateEmbeddingResponse

from receipt_dynamo.entities.ai_usage_metric import AIUsageMetric

from .cost_calculator import AICostCalculator


class AIUsageTracker:
    """
    Tracks AI service usage and costs across different providers.
    Stores metrics in DynamoDB for analysis and cost monitoring.
    """
    
    def __init__(
        self,
        dynamo_client=None,
        table_name: Optional[str] = None,
        user_id: Optional[str] = None,
        track_to_dynamo: bool = True,
        track_to_file: bool = False,
        log_file: str = "/tmp/ai_usage.jsonl"
    ):
        """
        Initialize the AI usage tracker.
        
        Args:
            dynamo_client: DynamoDB client for storing metrics
            table_name: DynamoDB table name
            user_id: User identifier for tracking
            track_to_dynamo: Whether to store metrics in DynamoDB
            track_to_file: Whether to log metrics to a file (for local dev)
            log_file: Path to the log file
        """
        self.dynamo_client = dynamo_client
        self.table_name = table_name or os.environ.get("DYNAMODB_TABLE_NAME")
        self.user_id = user_id or os.environ.get("USER_ID", "default")
        self.track_to_dynamo = track_to_dynamo and dynamo_client is not None
        self.track_to_file = track_to_file
        self.log_file = log_file
        
        # Context for tracking job/batch IDs
        self.current_job_id: Optional[str] = None
        self.current_batch_id: Optional[str] = None
        self.github_pr: Optional[int] = None
    
    def set_context(
        self,
        job_id: Optional[str] = None,
        batch_id: Optional[str] = None,
        github_pr: Optional[int] = None,
        user_id: Optional[str] = None
    ):
        """Set context for tracking metrics."""
        if job_id is not None:
            self.current_job_id = job_id
        if batch_id is not None:
            self.current_batch_id = batch_id
        if github_pr is not None:
            self.github_pr = github_pr
        if user_id is not None:
            self.user_id = user_id
    
    def _store_metric(self, metric: AIUsageMetric):
        """Store metric in DynamoDB and/or file."""
        if self.track_to_dynamo and self.dynamo_client:
            try:
                item = metric.to_dynamodb_item()
                self.dynamo_client.put_item(
                    TableName=self.table_name,
                    Item=item
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
                    "error": metric.error,
                }
                with open(self.log_file, "a") as f:
                    f.write(json.dumps(log_entry) + "\n")
            except Exception as e:
                print(f"Failed to log metric to file: {e}")
    
    def track_openai_completion(self, func: Callable) -> Callable:
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
                        output_tokens = getattr(usage, "completion_tokens", None)
                        total_tokens = getattr(usage, "total_tokens", None)
                        
                        # Calculate cost
                        cost_usd = AICostCalculator.calculate_openai_cost(
                            model=model,
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                            is_batch=kwargs.get("is_batch", False)
                        )
                
                # Create and store metric
                metric = AIUsageMetric(
                    service="openai",
                    model=model,
                    operation="completion",
                    timestamp=datetime.utcnow(),
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                    cost_usd=cost_usd,
                    latency_ms=int((time.time() - start_time) * 1000),
                    user_id=self.user_id,
                    job_id=self.current_job_id,
                    batch_id=self.current_batch_id,
                    error=error,
                    metadata={
                        "function": func.__name__,
                        "temperature": kwargs.get("temperature"),
                        "max_tokens": kwargs.get("max_tokens"),
                    }
                )
                self._store_metric(metric)
        
        return wrapper
    
    def track_openai_embedding(self, func: Callable) -> Callable:
        """
        Decorator for tracking OpenAI embedding API calls.
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
                        )
                
                # Create and store metric
                metric = AIUsageMetric(
                    service="openai",
                    model=model,
                    operation="embedding",
                    timestamp=datetime.utcnow(),
                    total_tokens=total_tokens,
                    cost_usd=cost_usd,
                    latency_ms=int((time.time() - start_time) * 1000),
                    user_id=self.user_id,
                    job_id=self.current_job_id,
                    batch_id=self.current_batch_id,
                    error=error,
                    metadata={
                        "function": func.__name__,
                        "input_count": len(kwargs.get("input", [])) if "input" in kwargs else None,
                    }
                )
                self._store_metric(metric)
        
        return wrapper
    
    def track_anthropic_completion(self, func: Callable) -> Callable:
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
                            output_tokens=output_tokens
                        )
                
                # Create and store metric
                metric = AIUsageMetric(
                    service="anthropic",
                    model=model,
                    operation="completion",
                    timestamp=datetime.utcnow(),
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=cost_usd,
                    latency_ms=int((time.time() - start_time) * 1000),
                    user_id=self.user_id,
                    job_id=self.current_job_id,
                    github_pr=self.github_pr,
                    error=error,
                    metadata={
                        "function": func.__name__,
                        "max_tokens": kwargs.get("max_tokens"),
                    }
                )
                self._store_metric(metric)
        
        return wrapper
    
    def track_google_places(self, operation: str):
        """
        Decorator for tracking Google Places API calls.
        
        Args:
            operation: The type of Places API operation
        """
        def decorator(func: Callable) -> Callable:
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
                        operation=operation,
                        api_calls=1
                    )
                    
                    # Create and store metric
                    metric = AIUsageMetric(
                        service="google_places",
                        model="places_api",
                        operation=operation,
                        timestamp=datetime.utcnow(),
                        api_calls=1,
                        cost_usd=cost_usd,
                        latency_ms=int((time.time() - start_time) * 1000),
                        user_id=self.user_id,
                        job_id=self.current_job_id,
                        error=error,
                        metadata={
                            "function": func.__name__,
                        }
                    )
                    self._store_metric(metric)
            
            return wrapper
        return decorator
    
    def track_github_claude_review(
        self,
        pr_number: int,
        model: str = "claude-3-opus",
        estimated_tokens: int = 5000
    ):
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
            output_tokens=output_estimate
        )
        
        metric = AIUsageMetric(
            service="anthropic",
            model=model,
            operation="code_review",
            timestamp=datetime.utcnow(),
            input_tokens=input_estimate,
            output_tokens=output_estimate,
            total_tokens=estimated_tokens,
            cost_usd=cost_usd,
            github_pr=pr_number,
            user_id="github-actions",
            metadata={
                "pr_number": pr_number,
                "workflow": "claude-code-review",
            }
        )
        self._store_metric(metric)
    
    @classmethod
    def create_wrapped_openai_client(cls, openai_client: OpenAI, tracker: "AIUsageTracker") -> OpenAI:
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
                                    @self._tracker.track_openai_completion
                                    def _create(**kw):
                                        return self._completions.create(**kw)
                                    return _create(**kwargs)
                            
                            return TrackedCompletions(self._chat.completions, self._tracker)
                    
                    return TrackedChat(attr, self._tracker)
                
                # Wrap embeddings
                elif name == "embeddings":
                    class TrackedEmbeddings:
                        def __init__(self, embeddings, tracker):
                            self._embeddings = embeddings
                            self._tracker = tracker
                        
                        def create(self, **kwargs):
                            @self._tracker.track_openai_embedding
                            def _create(**kw):
                                return self._embeddings.create(**kw)
                            return _create(**kwargs)
                    
                    return TrackedEmbeddings(attr, self._tracker)
                
                return attr
        
        return TrackedOpenAIClient(openai_client, tracker)