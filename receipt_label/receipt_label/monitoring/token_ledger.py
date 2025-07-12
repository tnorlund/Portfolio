"""
Token usage ledger for comprehensive GPT cost tracking.

This module provides detailed logging of prompt_tokens, completion_tokens,
model tiers, and decisions for precise cost analysis and optimization.
"""

import logging
import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from enum import Enum

logger = logging.getLogger(__name__)


class ModelTier(Enum):
    """GPT model tiers for cost calculation."""
    GPT_4O_MINI = "gpt-4o-mini"
    GPT_4O = "gpt-4o"
    GPT_4_TURBO = "gpt-4-turbo"
    GPT_3_5_TURBO = "gpt-3.5-turbo"


class TokenUsageType(Enum):
    """Types of token usage for categorization."""
    IMMEDIATE_GPT = "immediate_gpt"      # REQUIRED decision
    BATCH_GPT = "batch_gpt"              # BATCH decision processing
    SHADOW_TEST = "shadow_test"          # Shadow testing validation
    RETRY_ATTEMPT = "retry_attempt"      # Failed request retry


@dataclass
class TokenUsageRecord:
    """Detailed record of token usage for a single GPT call."""
    
    # Identifiers
    record_id: str
    session_id: str
    receipt_id: str
    image_id: str
    
    # Request details
    model_tier: ModelTier
    usage_type: TokenUsageType
    agent_decision: str
    batch_id: Optional[str] = None
    
    # Token breakdown
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    
    # Cost calculation
    prompt_cost_usd: float = 0.0
    completion_cost_usd: float = 0.0
    total_cost_usd: float = 0.0
    
    # Context information
    missing_fields: List[str] = None
    merchant_name: Optional[str] = None
    merchant_category: Optional[str] = None
    
    # Performance metrics
    processing_time_ms: float = 0.0
    request_timestamp: datetime = None
    response_timestamp: Optional[datetime] = None
    
    # Quality metrics
    labels_extracted: int = 0
    essential_labels_found: int = 0
    accuracy_score: Optional[float] = None
    
    # Error tracking
    retry_count: int = 0
    error_message: Optional[str] = None
    success: bool = True
    
    def __post_init__(self):
        if self.missing_fields is None:
            self.missing_fields = []
        if self.request_timestamp is None:
            self.request_timestamp = datetime.now(timezone.utc)
        if self.total_tokens == 0:
            self.total_tokens = self.prompt_tokens + self.completion_tokens
        if self.total_cost_usd == 0.0:
            self.total_cost_usd = self.prompt_cost_usd + self.completion_cost_usd
    
    def mark_completed(self, response_timestamp: Optional[datetime] = None):
        """Mark the token usage record as completed."""
        self.response_timestamp = response_timestamp or datetime.now(timezone.utc)
        if self.request_timestamp:
            self.processing_time_ms = (
                self.response_timestamp - self.request_timestamp
            ).total_seconds() * 1000
    
    def mark_failed(self, error_message: str, retry_count: int = 0):
        """Mark the token usage record as failed."""
        self.success = False
        self.error_message = error_message
        self.retry_count = retry_count
        self.mark_completed()


class TokenLedger:
    """
    Comprehensive token usage ledger for GPT cost tracking.
    
    This system logs every GPT call with detailed token breakdown for
    precise cost analysis and optimization insights.
    """
    
    # Cost per 1K tokens for different models (as of 2024)
    MODEL_COSTS = {
        ModelTier.GPT_4O_MINI: {"prompt": 0.00015, "completion": 0.0006},
        ModelTier.GPT_4O: {"prompt": 0.005, "completion": 0.015},
        ModelTier.GPT_4_TURBO: {"prompt": 0.01, "completion": 0.03},
        ModelTier.GPT_3_5_TURBO: {"prompt": 0.0005, "completion": 0.0015}
    }
    
    def __init__(self, storage_backend: Optional[Any] = None):
        """
        Initialize token ledger.
        
        Args:
            storage_backend: Backend for persistent storage (DynamoDB, etc.)
        """
        self.storage_backend = storage_backend
        self.usage_records: List[TokenUsageRecord] = []
        
        # Statistics tracking
        self.stats = {
            "total_records": 0,
            "total_tokens": 0,
            "total_cost_usd": 0.0,
            "successful_requests": 0,
            "failed_requests": 0,
            "retry_attempts": 0,
            "models_used": set(),
            "usage_types": {}
        }
        
        # Per-model statistics
        self.model_stats = {
            model.value: {
                "requests": 0,
                "tokens": 0,
                "cost_usd": 0.0,
                "avg_tokens_per_request": 0.0,
                "avg_cost_per_request": 0.0
            }
            for model in ModelTier
        }
    
    def calculate_cost(
        self,
        model_tier: ModelTier,
        prompt_tokens: int,
        completion_tokens: int
    ) -> Dict[str, float]:
        """
        Calculate cost breakdown for token usage.
        
        Args:
            model_tier: GPT model used
            prompt_tokens: Number of prompt tokens
            completion_tokens: Number of completion tokens
            
        Returns:
            Dictionary with cost breakdown
        """
        if model_tier not in self.MODEL_COSTS:
            logger.warning(f"Unknown model tier: {model_tier}")
            return {"prompt_cost": 0.0, "completion_cost": 0.0, "total_cost": 0.0}
        
        costs = self.MODEL_COSTS[model_tier]
        
        # Cost per 1K tokens
        prompt_cost = (prompt_tokens / 1000) * costs["prompt"]
        completion_cost = (completion_tokens / 1000) * costs["completion"]
        total_cost = prompt_cost + completion_cost
        
        return {
            "prompt_cost": prompt_cost,
            "completion_cost": completion_cost,
            "total_cost": total_cost
        }
    
    def log_token_usage(
        self,
        session_id: str,
        receipt_id: str,
        image_id: str,
        model_tier: ModelTier,
        usage_type: TokenUsageType,
        agent_decision: str,
        prompt_tokens: int,
        completion_tokens: int,
        missing_fields: Optional[List[str]] = None,
        merchant_name: Optional[str] = None,
        merchant_category: Optional[str] = None,
        batch_id: Optional[str] = None,
        labels_extracted: int = 0,
        essential_labels_found: int = 0,
        accuracy_score: Optional[float] = None,
        retry_count: int = 0,
        error_message: Optional[str] = None
    ) -> str:
        """
        Log detailed token usage for a GPT call.
        
        Args:
            session_id: Monitoring session ID
            receipt_id: Receipt identifier
            image_id: Image identifier
            model_tier: GPT model used
            usage_type: Type of usage (immediate, batch, shadow test)
            agent_decision: Original agent decision
            prompt_tokens: Number of prompt tokens
            completion_tokens: Number of completion tokens
            missing_fields: Fields that were missing and requested
            merchant_name: Merchant name
            merchant_category: Merchant category
            batch_id: Batch identifier for batch processing
            labels_extracted: Number of labels extracted by GPT
            essential_labels_found: Number of essential labels found
            accuracy_score: Quality score if available
            retry_count: Number of retries for this request
            error_message: Error message if request failed
            
        Returns:
            Record ID for tracking
        """
        import uuid
        
        # Calculate costs
        cost_breakdown = self.calculate_cost(model_tier, prompt_tokens, completion_tokens)
        
        # Create usage record
        record = TokenUsageRecord(
            record_id=str(uuid.uuid4()),
            session_id=session_id,
            receipt_id=receipt_id,
            image_id=image_id,
            model_tier=model_tier,
            usage_type=usage_type,
            agent_decision=agent_decision,
            batch_id=batch_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            prompt_cost_usd=cost_breakdown["prompt_cost"],
            completion_cost_usd=cost_breakdown["completion_cost"],
            total_cost_usd=cost_breakdown["total_cost"],
            missing_fields=missing_fields or [],
            merchant_name=merchant_name,
            merchant_category=merchant_category,
            labels_extracted=labels_extracted,
            essential_labels_found=essential_labels_found,
            accuracy_score=accuracy_score,
            retry_count=retry_count,
            error_message=error_message,
            success=error_message is None
        )
        
        # Mark as completed
        record.mark_completed()
        
        # Store record
        self.usage_records.append(record)
        
        # Update statistics
        self._update_statistics(record)
        
        # Persist to storage backend if available
        if self.storage_backend:
            try:
                self._persist_record(record)
            except Exception as e:
                logger.error(f"Failed to persist token usage record: {e}")
        
        logger.debug(
            f"Logged token usage: {record.total_tokens} tokens, "
            f"${record.total_cost_usd:.4f}, model={model_tier.value}"
        )
        
        return record.record_id
    
    def _update_statistics(self, record: TokenUsageRecord):
        """Update internal statistics with new record."""
        
        # Global statistics
        self.stats["total_records"] += 1
        self.stats["total_tokens"] += record.total_tokens
        self.stats["total_cost_usd"] += record.total_cost_usd
        
        if record.success:
            self.stats["successful_requests"] += 1
        else:
            self.stats["failed_requests"] += 1
        
        if record.retry_count > 0:
            self.stats["retry_attempts"] += record.retry_count
        
        self.stats["models_used"].add(record.model_tier.value)
        
        # Usage type statistics
        usage_type = record.usage_type.value
        if usage_type not in self.stats["usage_types"]:
            self.stats["usage_types"][usage_type] = {
                "count": 0, "tokens": 0, "cost_usd": 0.0
            }
        
        self.stats["usage_types"][usage_type]["count"] += 1
        self.stats["usage_types"][usage_type]["tokens"] += record.total_tokens
        self.stats["usage_types"][usage_type]["cost_usd"] += record.total_cost_usd
        
        # Model-specific statistics
        model_key = record.model_tier.value
        model_stat = self.model_stats[model_key]
        
        model_stat["requests"] += 1
        model_stat["tokens"] += record.total_tokens
        model_stat["cost_usd"] += record.total_cost_usd
        
        # Calculate averages
        if model_stat["requests"] > 0:
            model_stat["avg_tokens_per_request"] = model_stat["tokens"] / model_stat["requests"]
            model_stat["avg_cost_per_request"] = model_stat["cost_usd"] / model_stat["requests"]
    
    def _persist_record(self, record: TokenUsageRecord):
        """Persist token usage record to storage backend."""
        if not self.storage_backend:
            return
        
        # Convert record to DynamoDB item format
        item = {
            "PK": f"TOKEN_USAGE#{record.session_id}",
            "SK": f"RECORD#{record.record_id}",
            "GSI1PK": f"RECEIPT#{record.receipt_id}",
            "GSI1SK": f"TOKEN_USAGE#{record.request_timestamp.isoformat()}",
            "GSI2PK": f"MODEL#{record.model_tier.value}",
            "GSI2SK": f"COST#{record.total_cost_usd:010.4f}#{record.request_timestamp.isoformat()}",
            "record_id": record.record_id,
            "session_id": record.session_id,
            "receipt_id": record.receipt_id,
            "image_id": record.image_id,
            "model_tier": record.model_tier.value,
            "usage_type": record.usage_type.value,
            "agent_decision": record.agent_decision,
            "prompt_tokens": record.prompt_tokens,
            "completion_tokens": record.completion_tokens,
            "total_tokens": record.total_tokens,
            "prompt_cost_usd": record.prompt_cost_usd,
            "completion_cost_usd": record.completion_cost_usd,
            "total_cost_usd": record.total_cost_usd,
            "missing_fields": json.dumps(record.missing_fields),
            "merchant_name": record.merchant_name,
            "merchant_category": record.merchant_category,
            "batch_id": record.batch_id,
            "processing_time_ms": record.processing_time_ms,
            "request_timestamp": record.request_timestamp.isoformat(),
            "response_timestamp": record.response_timestamp.isoformat() if record.response_timestamp else None,
            "labels_extracted": record.labels_extracted,
            "essential_labels_found": record.essential_labels_found,
            "accuracy_score": record.accuracy_score,
            "retry_count": record.retry_count,
            "error_message": record.error_message,
            "success": record.success,
            "TYPE": "TOKEN_USAGE",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "ttl": int((datetime.now(timezone.utc).timestamp() + 90 * 24 * 3600))  # 90 days TTL
        }
        
        # Store in DynamoDB
        self.storage_backend.put_item(Item=item)
    
    def get_usage_summary(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        usage_type: Optional[TokenUsageType] = None,
        model_tier: Optional[ModelTier] = None
    ) -> Dict[str, Any]:
        """
        Get token usage summary with optional filtering.
        
        Args:
            start_date: Start date for filtering
            end_date: End date for filtering
            usage_type: Filter by usage type
            model_tier: Filter by model tier
            
        Returns:
            Summary statistics
        """
        # Filter records
        filtered_records = self.usage_records.copy()
        
        if start_date:
            filtered_records = [
                r for r in filtered_records 
                if r.request_timestamp >= start_date
            ]
        
        if end_date:
            filtered_records = [
                r for r in filtered_records 
                if r.request_timestamp <= end_date
            ]
        
        if usage_type:
            filtered_records = [
                r for r in filtered_records 
                if r.usage_type == usage_type
            ]
        
        if model_tier:
            filtered_records = [
                r for r in filtered_records 
                if r.model_tier == model_tier
            ]
        
        if not filtered_records:
            return {"message": "No records found for the specified criteria"}
        
        # Calculate summary statistics
        total_records = len(filtered_records)
        total_tokens = sum(r.total_tokens for r in filtered_records)
        total_cost = sum(r.total_cost_usd for r in filtered_records)
        successful_records = [r for r in filtered_records if r.success]
        failed_records = [r for r in filtered_records if not r.success]
        
        avg_tokens_per_request = total_tokens / total_records if total_records > 0 else 0
        avg_cost_per_request = total_cost / total_records if total_records > 0 else 0
        success_rate = len(successful_records) / total_records if total_records > 0 else 0
        
        # Usage type breakdown
        usage_breakdown = {}
        for record in filtered_records:
            usage_key = record.usage_type.value
            if usage_key not in usage_breakdown:
                usage_breakdown[usage_key] = {"count": 0, "tokens": 0, "cost": 0.0}
            usage_breakdown[usage_key]["count"] += 1
            usage_breakdown[usage_key]["tokens"] += record.total_tokens
            usage_breakdown[usage_key]["cost"] += record.total_cost_usd
        
        # Model breakdown
        model_breakdown = {}
        for record in filtered_records:
            model_key = record.model_tier.value
            if model_key not in model_breakdown:
                model_breakdown[model_key] = {"count": 0, "tokens": 0, "cost": 0.0}
            model_breakdown[model_key]["count"] += 1
            model_breakdown[model_key]["tokens"] += record.total_tokens
            model_breakdown[model_key]["cost"] += record.total_cost_usd
        
        return {
            "summary": {
                "total_records": total_records,
                "total_tokens": total_tokens,
                "total_cost_usd": total_cost,
                "avg_tokens_per_request": avg_tokens_per_request,
                "avg_cost_per_request": avg_cost_per_request,
                "success_rate": success_rate,
                "successful_requests": len(successful_records),
                "failed_requests": len(failed_records)
            },
            "usage_type_breakdown": usage_breakdown,
            "model_breakdown": model_breakdown,
            "date_range": {
                "start": start_date.isoformat() if start_date else None,
                "end": end_date.isoformat() if end_date else None
            }
        }
    
    def generate_cost_report(self, days: int = 30) -> str:
        """
        Generate human-readable cost report for the last N days.
        
        Args:
            days: Number of days to include in report
            
        Returns:
            Formatted cost report
        """
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=days)
        
        summary = self.get_usage_summary(start_date=start_date, end_date=end_date)
        
        if "message" in summary:
            return f"No token usage data available for the last {days} days."
        
        report = f"""
Token Usage Cost Report ({days} days)
=====================================

Overall Summary:
- Total GPT Requests: {summary['summary']['total_records']:,}
- Total Tokens Used: {summary['summary']['total_tokens']:,}
- Total Cost: ${summary['summary']['total_cost_usd']:.4f}
- Average Cost per Request: ${summary['summary']['avg_cost_per_request']:.4f}
- Success Rate: {summary['summary']['success_rate']:.1%}

Usage Type Breakdown:
"""
        
        for usage_type, data in summary['usage_type_breakdown'].items():
            percentage = (data['cost'] / summary['summary']['total_cost_usd']) * 100
            report += f"- {usage_type.replace('_', ' ').title()}: {data['count']} requests, "
            report += f"{data['tokens']:,} tokens, ${data['cost']:.4f} ({percentage:.1f}%)\n"
        
        report += "\nModel Breakdown:\n"
        for model, data in summary['model_breakdown'].items():
            percentage = (data['cost'] / summary['summary']['total_cost_usd']) * 100
            report += f"- {model}: {data['count']} requests, "
            report += f"{data['tokens']:,} tokens, ${data['cost']:.4f} ({percentage:.1f}%)\n"
        
        # Cost optimization insights
        report += "\nCost Optimization Insights:\n"
        
        if 'immediate_gpt' in summary['usage_type_breakdown']:
            immediate_cost = summary['usage_type_breakdown']['immediate_gpt']['cost']
            total_cost = summary['summary']['total_cost_usd']
            immediate_percentage = (immediate_cost / total_cost) * 100
            
            if immediate_percentage > 50:
                report += f"⚠️  Immediate GPT usage is {immediate_percentage:.1f}% of total cost\n"
                report += "   Consider optimizing agent decision thresholds\n"
        
        if 'shadow_test' in summary['usage_type_breakdown']:
            shadow_cost = summary['usage_type_breakdown']['shadow_test']['cost']
            total_cost = summary['summary']['total_cost_usd']
            shadow_percentage = (shadow_cost / total_cost) * 100
            
            if shadow_percentage > 10:
                report += f"⚠️  Shadow testing is {shadow_percentage:.1f}% of total cost\n"
                report += "   Consider reducing shadow testing sampling rate\n"
        
        if summary['summary']['success_rate'] < 0.95:
            report += f"⚠️  Success rate is {summary['summary']['success_rate']:.1%}\n"
            report += "   Review error handling and retry logic\n"
        
        return report
    
    def flush_to_storage(self):
        """Flush all buffered token usage records to storage."""
        if not self.storage_backend:
            logger.warning("No storage backend configured for token ledger")
            return
        
        flushed_count = 0
        for record in self.usage_records:
            try:
                self._persist_record(record)
                flushed_count += 1
            except Exception as e:
                logger.error(f"Failed to flush record {record.record_id}: {e}")
        
        logger.info(f"Flushed {flushed_count} token usage records to storage")
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get current ledger statistics."""
        return {
            **self.stats.copy(),
            "models_used": list(self.stats["models_used"]),
            "model_stats": self.model_stats.copy()
        }