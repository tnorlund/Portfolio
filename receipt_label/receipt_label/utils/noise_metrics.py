"""Metrics collection and monitoring for noise word detection."""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from receipt_dynamo.entities import ReceiptWord

logger = logging.getLogger(__name__)


@dataclass
class NoiseDetectionMetrics:
    """Metrics for noise word detection performance and cost savings."""

    timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    total_words: int = 0
    noise_words: int = 0
    meaningful_words: int = 0
    noise_percentage: float = 0.0

    # Cost metrics (based on OpenAI embedding pricing)
    embedding_cost_per_token: float = 0.00002  # $0.00002 per token
    tokens_saved: int = 0
    cost_saved: float = 0.0

    # Performance metrics
    processing_time_ms: float = 0.0
    words_per_second: float = 0.0

    # Receipt-level metrics
    receipt_id: Optional[int] = None
    image_id: Optional[str] = None
    merchant_type: Optional[str] = None

    def calculate_derived_metrics(self) -> None:
        """Calculate derived metrics from raw counts."""
        if self.total_words > 0:
            self.noise_percentage = (self.noise_words / self.total_words) * 100
            self.meaningful_words = self.total_words - self.noise_words

            # Estimate tokens saved (average 1.5 tokens per word)
            self.tokens_saved = int(self.noise_words * 1.5)
            self.cost_saved = self.tokens_saved * self.embedding_cost_per_token

            if self.processing_time_ms > 0:
                self.words_per_second = (
                    self.total_words / self.processing_time_ms
                ) * 1000

    def to_dict(self) -> Dict:
        """Convert metrics to dictionary for storage/logging."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "total_words": self.total_words,
            "noise_words": self.noise_words,
            "meaningful_words": self.meaningful_words,
            "noise_percentage": round(self.noise_percentage, 2),
            "tokens_saved": self.tokens_saved,
            "cost_saved": round(self.cost_saved, 6),
            "processing_time_ms": round(self.processing_time_ms, 2),
            "words_per_second": round(self.words_per_second, 2),
            "receipt_id": self.receipt_id,
            "image_id": self.image_id,
            "merchant_type": self.merchant_type,
        }

    def to_cloudwatch_metrics(self) -> List[Dict]:
        """Format metrics for CloudWatch."""
        base_dimensions = [
            {"Name": "Environment", "Value": "production"},
            {"Name": "Service", "Value": "receipt-processing"},
        ]

        if self.merchant_type:
            base_dimensions.append(
                {"Name": "MerchantType", "Value": self.merchant_type}
            )

        return [
            {
                "MetricName": "NoiseWordPercentage",
                "Value": self.noise_percentage,
                "Unit": "Percent",
                "Dimensions": base_dimensions,
            },
            {
                "MetricName": "EmbeddingTokensSaved",
                "Value": self.tokens_saved,
                "Unit": "Count",
                "Dimensions": base_dimensions,
            },
            {
                "MetricName": "EmbeddingCostSaved",
                "Value": self.cost_saved,
                "Unit": "None",  # Dollar amount
                "Dimensions": base_dimensions,
            },
            {
                "MetricName": "NoiseDetectionProcessingTime",
                "Value": self.processing_time_ms,
                "Unit": "Milliseconds",
                "Dimensions": base_dimensions,
            },
        ]


class NoiseMetricsCollector:
    """Collector for aggregating noise detection metrics."""

    def __init__(self):
        self.batch_metrics: List[NoiseDetectionMetrics] = []
        self.aggregate_metrics = NoiseDetectionMetrics()

    def collect_receipt_metrics(
        self,
        words: List[ReceiptWord],
        processing_time_ms: float,
        receipt_id: Optional[int] = None,
        image_id: Optional[str] = None,
        merchant_type: Optional[str] = None,
    ) -> NoiseDetectionMetrics:
        """Collect metrics for a single receipt."""
        metrics = NoiseDetectionMetrics(
            receipt_id=receipt_id,
            image_id=image_id,
            merchant_type=merchant_type,
            processing_time_ms=processing_time_ms,
        )

        # Count noise vs meaningful words
        for word in words:
            metrics.total_words += 1
            if hasattr(word, "is_noise") and word.is_noise:
                metrics.noise_words += 1

        # Calculate derived metrics
        metrics.calculate_derived_metrics()

        # Add to batch
        self.batch_metrics.append(metrics)

        # Update aggregates
        self._update_aggregates(metrics)

        # Log metrics
        logger.info(
            f"Noise detection metrics: {json.dumps(metrics.to_dict())}"
        )

        return metrics

    def _update_aggregates(self, metrics: NoiseDetectionMetrics) -> None:
        """Update aggregate metrics."""
        self.aggregate_metrics.total_words += metrics.total_words
        self.aggregate_metrics.noise_words += metrics.noise_words
        self.aggregate_metrics.tokens_saved += metrics.tokens_saved
        self.aggregate_metrics.cost_saved += metrics.cost_saved
        self.aggregate_metrics.processing_time_ms += metrics.processing_time_ms

        # Recalculate aggregate percentages
        self.aggregate_metrics.calculate_derived_metrics()

    def get_batch_summary(self) -> Dict:
        """Get summary of all collected metrics."""
        if not self.batch_metrics:
            return {"message": "No metrics collected"}

        summary = {
            "total_receipts": len(self.batch_metrics),
            "aggregate_metrics": self.aggregate_metrics.to_dict(),
            "average_noise_percentage": round(
                sum(m.noise_percentage for m in self.batch_metrics)
                / len(self.batch_metrics),
                2,
            ),
            "total_cost_saved": round(self.aggregate_metrics.cost_saved, 4),
            "receipts_by_noise_range": self._get_noise_distribution(),
        }

        return summary

    def _get_noise_distribution(self) -> Dict[str, int]:
        """Get distribution of receipts by noise percentage ranges."""
        ranges = {
            "0-20%": 0,
            "20-30%": 0,
            "30-40%": 0,
            "40-50%": 0,
            "50%+": 0,
        }

        for metrics in self.batch_metrics:
            if metrics.noise_percentage < 20:
                ranges["0-20%"] += 1
            elif metrics.noise_percentage < 30:
                ranges["20-30%"] += 1
            elif metrics.noise_percentage < 40:
                ranges["30-40%"] += 1
            elif metrics.noise_percentage < 50:
                ranges["40-50%"] += 1
            else:
                ranges["50%+"] += 1

        return ranges

    def export_cloudwatch_metrics(self) -> List[Dict]:
        """Export all metrics in CloudWatch format."""
        all_metrics = []

        # Add individual receipt metrics
        for metrics in self.batch_metrics:
            all_metrics.extend(metrics.to_cloudwatch_metrics())

        # Add aggregate metrics
        if self.aggregate_metrics.total_words > 0:
            all_metrics.extend(self.aggregate_metrics.to_cloudwatch_metrics())

        return all_metrics


def log_noise_detection_event(
    event_type: str,
    details: Dict,
    receipt_id: Optional[int] = None,
    image_id: Optional[str] = None,
) -> None:
    """Log noise detection events for monitoring."""
    log_entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "receipt_id": receipt_id,
        "image_id": image_id,
        "details": details,
    }

    logger.info(f"NoiseDetectionEvent: {json.dumps(log_entry)}")


def calculate_monthly_savings_projection(
    daily_receipts: int,
    average_words_per_receipt: int,
    average_noise_percentage: float,
) -> Dict:
    """Calculate projected monthly cost savings from noise filtering."""
    # Constants
    tokens_per_word = 1.5
    cost_per_token = 0.00002  # $0.00002 per token
    days_per_month = 30

    # Calculations
    monthly_receipts = daily_receipts * days_per_month
    total_words = monthly_receipts * average_words_per_receipt
    noise_words = total_words * (average_noise_percentage / 100)
    tokens_saved = noise_words * tokens_per_word
    monthly_savings = tokens_saved * cost_per_token

    return {
        "daily_receipts": daily_receipts,
        "monthly_receipts": monthly_receipts,
        "total_words_processed": total_words,
        "noise_words_filtered": int(noise_words),
        "tokens_saved": int(tokens_saved),
        "monthly_savings_usd": round(monthly_savings, 2),
        "annual_savings_usd": round(monthly_savings * 12, 2),
        "assumptions": {
            "average_words_per_receipt": average_words_per_receipt,
            "average_noise_percentage": average_noise_percentage,
            "tokens_per_word": tokens_per_word,
            "cost_per_token_usd": cost_per_token,
        },
    }
