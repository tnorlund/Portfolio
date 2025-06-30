"""
Budget management system for AI usage cost control.

This module provides budget lifecycle management including
creation, tracking, reset cycles, and rollover functionality.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional

from receipt_dynamo import DynamoClient

logger = logging.getLogger(__name__)


class BudgetPeriod(Enum):
    """Budget period types."""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"

    def to_timedelta(self) -> timedelta:
        """Convert period to timedelta."""
        if self == BudgetPeriod.DAILY:
            return timedelta(days=1)
        elif self == BudgetPeriod.WEEKLY:
            return timedelta(weeks=1)
        elif self == BudgetPeriod.MONTHLY:
            return timedelta(days=30)  # Approximate
        elif self == BudgetPeriod.QUARTERLY:
            return timedelta(days=90)  # Approximate
        elif self == BudgetPeriod.ANNUAL:
            return timedelta(days=365)
        else:
            raise ValueError(f"Unknown period: {self}")


@dataclass
class Budget:
    """Represents a budget configuration."""

    budget_id: str
    scope: str  # e.g., "user:123", "service:openai", "global:all"
    amount: Decimal
    period: BudgetPeriod
    created_at: datetime
    effective_from: datetime
    effective_until: Optional[datetime] = None
    rollover_enabled: bool = False
    alert_thresholds: List[float] = field(default_factory=lambda: [50, 80, 95, 100])
    metadata: Dict[str, Any] = field(default_factory=dict)
    is_active: bool = True

    def to_dict(self) -> Dict[str, Any]:
        """Convert budget to dictionary for storage."""
        return {
            "budget_id": self.budget_id,
            "scope": self.scope,
            "amount": str(self.amount),
            "period": self.period.value,
            "created_at": self.created_at.isoformat(),
            "effective_from": self.effective_from.isoformat(),
            "effective_until": (
                self.effective_until.isoformat() if self.effective_until else None
            ),
            "rollover_enabled": self.rollover_enabled,
            "alert_thresholds": self.alert_thresholds,
            "metadata": self.metadata,
            "is_active": self.is_active,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Budget":
        """Create budget from dictionary."""
        return cls(
            budget_id=data["budget_id"],
            scope=data["scope"],
            amount=Decimal(data["amount"]),
            period=BudgetPeriod(data["period"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            effective_from=datetime.fromisoformat(data["effective_from"]),
            effective_until=(
                datetime.fromisoformat(data["effective_until"])
                if data.get("effective_until")
                else None
            ),
            rollover_enabled=data.get("rollover_enabled", False),
            alert_thresholds=data.get("alert_thresholds", [50, 80, 95, 100]),
            metadata=data.get("metadata", {}),
            is_active=data.get("is_active", True),
        )


class BudgetManager:
    """
    Manages budget lifecycle for AI usage costs.

    This class provides:
    - Budget creation and updates
    - Period-based budget tracking
    - Automatic budget reset and rollover
    - Budget history and analytics
    """

    def __init__(
        self,
        dynamo_client: DynamoClient,
        table_name: Optional[str] = None,
    ):
        """
        Initialize budget manager.

        Args:
            dynamo_client: DynamoDB client for storage
            table_name: Optional table name for budget storage
        """
        self.dynamo_client = dynamo_client
        self.table_name = table_name or dynamo_client.table_name

    def create_budget(
        self,
        scope: str,
        amount: Decimal,
        period: BudgetPeriod,
        effective_from: Optional[datetime] = None,
        effective_until: Optional[datetime] = None,
        rollover_enabled: bool = False,
        alert_thresholds: Optional[List[float]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Budget:
        """
        Create a new budget.

        Args:
            scope: Budget scope (e.g., "user:123", "service:openai")
            amount: Budget amount limit
            period: Budget period (daily, weekly, monthly, etc.)
            effective_from: When budget becomes active (default: now)
            effective_until: When budget expires (optional)
            rollover_enabled: Whether unused budget rolls over
            alert_thresholds: Custom alert thresholds
            metadata: Additional metadata

        Returns:
            Created Budget object
        """
        now = datetime.now(timezone.utc)
        budget_id = f"BUDGET#{scope}#{period.value}#{now.timestamp()}"

        budget = Budget(
            budget_id=budget_id,
            scope=scope,
            amount=amount,
            period=period,
            created_at=now,
            effective_from=effective_from or now,
            effective_until=effective_until,
            rollover_enabled=rollover_enabled,
            alert_thresholds=alert_thresholds or [50, 80, 95, 100],
            metadata=metadata or {},
            is_active=True,
        )

        # Store in DynamoDB
        self._store_budget(budget)

        logger.info(f"Created budget: {budget_id} for {scope} with limit ${amount}")

        return budget

    def get_active_budget(
        self,
        scope: str,
        period: Optional[BudgetPeriod] = None,
    ) -> Optional[Budget]:
        """
        Get the active budget for a scope.

        Args:
            scope: Budget scope to query
            period: Optional period filter

        Returns:
            Active Budget or None if not found
        """
        now = datetime.now(timezone.utc)

        # Query budgets for scope
        budgets = self._query_budgets_by_scope(scope)

        # Filter to active budgets
        active_budgets = [
            b
            for b in budgets
            if b.is_active
            and b.effective_from <= now
            and (b.effective_until is None or b.effective_until > now)
            and (period is None or b.period == period)
        ]

        # Return most recent if multiple found
        if active_budgets:
            return max(active_budgets, key=lambda b: b.created_at)

        return None

    def update_budget(
        self,
        budget_id: str,
        amount: Optional[Decimal] = None,
        alert_thresholds: Optional[List[float]] = None,
        rollover_enabled: Optional[bool] = None,
        effective_until: Optional[datetime] = None,
        metadata_updates: Optional[Dict[str, Any]] = None,
    ) -> Budget:
        """
        Update an existing budget.

        Args:
            budget_id: Budget ID to update
            amount: New amount limit
            alert_thresholds: New alert thresholds
            rollover_enabled: Update rollover setting
            effective_until: Update expiration
            metadata_updates: Metadata updates to apply

        Returns:
            Updated Budget object
        """
        # Get existing budget
        budget = self._get_budget_by_id(budget_id)
        if not budget:
            raise ValueError(f"Budget not found: {budget_id}")

        # Apply updates
        if amount is not None:
            budget.amount = amount
        if alert_thresholds is not None:
            budget.alert_thresholds = alert_thresholds
        if rollover_enabled is not None:
            budget.rollover_enabled = rollover_enabled
        if effective_until is not None:
            budget.effective_until = effective_until
        if metadata_updates:
            budget.metadata.update(metadata_updates)

        # Store updated budget
        self._store_budget(budget)

        logger.info(f"Updated budget: {budget_id}")

        return budget

    def deactivate_budget(self, budget_id: str) -> None:
        """
        Deactivate a budget.

        Args:
            budget_id: Budget ID to deactivate
        """
        budget = self._get_budget_by_id(budget_id)
        if not budget:
            raise ValueError(f"Budget not found: {budget_id}")

        budget.is_active = False
        budget.effective_until = datetime.now(timezone.utc)

        self._store_budget(budget)

        logger.info(f"Deactivated budget: {budget_id}")

    def get_budget_history(
        self,
        scope: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> List[Budget]:
        """
        Get budget history for a scope.

        Args:
            scope: Budget scope to query
            start_date: Start of history period
            end_date: End of history period

        Returns:
            List of historical budgets
        """
        budgets = self._query_budgets_by_scope(scope)

        # Filter by date range if provided
        if start_date or end_date:
            filtered = []
            for budget in budgets:
                if start_date and budget.created_at < start_date:
                    continue
                if end_date and budget.created_at > end_date:
                    continue
                filtered.append(budget)
            budgets = filtered

        # Sort by creation date
        return sorted(budgets, key=lambda b: b.created_at, reverse=True)

    def process_period_rollover(self, budget: Budget) -> Optional[Budget]:
        """
        Process budget rollover for a new period.

        Args:
            budget: Budget to process rollover for

        Returns:
            New budget with rollover applied, or None if not applicable
        """
        if not budget.rollover_enabled:
            return None

        # TODO: Calculate unused amount from previous period
        # This requires integration with CostMonitor

        # For now, create new budget for next period
        now = datetime.now(timezone.utc)

        new_budget = self.create_budget(
            scope=budget.scope,
            amount=budget.amount,  # TODO: Add rollover amount
            period=budget.period,
            effective_from=now,
            effective_until=budget.effective_until,
            rollover_enabled=budget.rollover_enabled,
            alert_thresholds=budget.alert_thresholds,
            metadata={
                **budget.metadata,
                "rolled_over_from": budget.budget_id,
                "rollover_date": now.isoformat(),
            },
        )

        # Deactivate old budget
        self.deactivate_budget(budget.budget_id)

        return new_budget

    def _store_budget(self, budget: Budget) -> None:
        """Store budget in DynamoDB."""
        item = {
            "PK": {"S": f"BUDGET#{budget.scope}"},
            "SK": {"S": budget.budget_id},
            "budget_data": {"S": json.dumps(budget.to_dict())},
            "scope": {"S": budget.scope},
            "period": {"S": budget.period.value},
            "is_active": {"BOOL": budget.is_active},
            "created_at": {"S": budget.created_at.isoformat()},
            "effective_from": {"S": budget.effective_from.isoformat()},
        }

        if budget.effective_until:
            item["effective_until"] = {"S": budget.effective_until.isoformat()}

        self.dynamo_client._client.put_item(
            TableName=self.table_name,
            Item=item,
        )

    def _get_budget_by_id(self, budget_id: str) -> Optional[Budget]:
        """Get budget by ID from DynamoDB."""
        # Extract scope from budget_id
        parts = budget_id.split("#")
        if len(parts) < 3:
            return None

        scope = parts[1]

        response = self.dynamo_client._client.get_item(
            TableName=self.table_name,
            Key={
                "PK": {"S": f"BUDGET#{scope}"},
                "SK": {"S": budget_id},
            },
        )

        if "Item" not in response:
            return None

        budget_data = json.loads(response["Item"]["budget_data"]["S"])
        return Budget.from_dict(budget_data)

    def _query_budgets_by_scope(self, scope: str) -> List[Budget]:
        """Query all budgets for a scope."""
        response = self.dynamo_client._client.query(
            TableName=self.table_name,
            KeyConditionExpression="PK = :pk",
            ExpressionAttributeValues={
                ":pk": {"S": f"BUDGET#{scope}"},
            },
        )

        budgets = []
        for item in response.get("Items", []):
            budget_data = json.loads(item["budget_data"]["S"])
            budgets.append(Budget.from_dict(budget_data))

        return budgets
