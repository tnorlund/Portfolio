"""Rate limit detection and monitoring for LangGraph workflows."""

import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class RateLimitEvent:
    """Record of a rate limit event."""
    
    timestamp: float
    phase: str  # "phase1" or "phase2"
    receipt_id: str
    error_message: str
    retry_count: int


class RateLimitDetector:
    """Detect and track rate limiting patterns."""
    
    def __init__(self, window_size: int = 100, time_window: int = 60):
        """
        Initialize rate limit detector.
        
        Args:
            window_size: Number of requests to track
            time_window: Time window in seconds for rate calculation
        """
        self.window_size = window_size
        self.time_window = time_window
        
        # Track requests by phase
        self.request_times: Dict[str, deque] = defaultdict(lambda: deque(maxlen=window_size))
        self.error_counts: Dict[str, int] = defaultdict(int)
        self.rate_limit_events: List[RateLimitEvent] = []
        
    def record_request(self, phase: str) -> None:
        """Record a request attempt."""
        current_time = time.time()
        self.request_times[phase].append(current_time)
    
    def record_rate_limit(self, phase: str, receipt_id: str, error_message: str, retry_count: int) -> None:
        """Record a rate limit occurrence."""
        event = RateLimitEvent(
            timestamp=time.time(),
            phase=phase,
            receipt_id=receipt_id,
            error_message=error_message,
            retry_count=retry_count,
        )
        self.rate_limit_events.append(event)
        self.error_counts[phase] += 1
        
    def get_rate_stats(self, phase: str) -> Dict[str, float]:
        """
        Get current rate statistics for a phase.
        
        Returns:
            Dict with keys: requests_per_second, errors_per_minute, is_rate_limited
        """
        if phase not in self.request_times or len(self.request_times[phase]) < 2:
            return {
                "requests_per_second": 0.0,
                "errors_per_minute": 0.0,
                "is_rate_limited": False,
            }
        
        times = list(self.request_times[phase])
        recent_times = [t for t in times if time.time() - t < self.time_window]
        
        if len(recent_times) < 2:
            return {
                "requests_per_second": 0.0,
                "errors_per_minute": 0.0,
                "is_rate_limited": False,
            }
        
        # Calculate requests per second
        time_span = recent_times[-1] - recent_times[0]
        requests_per_second = len(recent_times) / max(time_span, 1.0)
        
        # Calculate errors per minute
        recent_errors = [
            event for event in self.rate_limit_events 
            if time.time() - event.timestamp < 60 and event.phase == phase
        ]
        errors_per_minute = len(recent_errors)
        
        # Determine if rate limited (heuristic: high error rate or high request rate)
        is_rate_limited = (
            errors_per_minute > 5 or  # More than 5 errors per minute
            requests_per_second > 10  # More than 10 requests per second
        )
        
        return {
            "requests_per_second": requests_per_second,
            "errors_per_minute": errors_per_minute,
            "is_rate_limited": is_rate_limited,
            "total_errors": self.error_counts.get(phase, 0),
        }
    
    def should_apply_backoff(self, phase: str) -> bool:
        """Determine if we should apply exponential backoff based on rate limiting."""
        stats = self.get_rate_stats(phase)
        return stats["is_rate_limited"]
    
    def get_detection_summary(self) -> str:
        """Get a summary of rate limit detection for logging."""
        summary = []
        for phase in ["phase1", "phase2"]:
            if phase in self.request_times and len(self.request_times[phase]) > 0:
                stats = self.get_rate_stats(phase)
                summary.append(
                    f"{phase}: {stats['requests_per_second']:.2f} req/s, "
                    f"{stats['errors_per_minute']} errors/min"
                )
        
        if not summary:
            return "No rate limit issues detected"
        
        return " | ".join(summary)
    
    def get_rate_limit_events_summary(self, last_n: int = 10) -> List[Dict]:
        """Get summary of recent rate limit events."""
        recent_events = self.rate_limit_events[-last_n:]
        
        return [
            {
                "timestamp": event.timestamp,
                "phase": event.phase,
                "receipt_id": event.receipt_id,
                "retry_count": event.retry_count,
            }
            for event in recent_events
        ]


# Global instance for tracking
_rate_limit_detector = RateLimitDetector()


def get_rate_limit_detector() -> RateLimitDetector:
    """Get the global rate limit detector instance."""
    return _rate_limit_detector

