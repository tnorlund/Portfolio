"""DynamoDB entity serialization following Portfolio's key/item convention."""

from planner.entities.record import PlannerRecord, item_to_planner_record

__all__ = ["PlannerRecord", "item_to_planner_record"]
