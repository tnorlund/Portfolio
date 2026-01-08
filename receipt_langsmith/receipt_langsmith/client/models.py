"""API request/response models for LangSmith client."""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class Project(BaseModel):
    """LangSmith project (session) metadata."""

    id: str
    """Project UUID."""

    name: str
    """Project name."""

    description: Optional[str] = None
    """Project description."""

    created_at: Optional[datetime] = None
    """When the project was created."""

    modified_at: Optional[datetime] = None
    """When the project was last modified."""

    extra: dict[str, Any] = Field(default_factory=dict)
    """Additional metadata."""


class BulkExportDestination(BaseModel):
    """Bulk export destination configuration."""

    id: str
    """Destination UUID."""

    destination_type: str
    """Type: 's3', 'gcs', etc."""

    display_name: str
    """Human-readable name."""

    config: dict[str, Any] = Field(default_factory=dict)
    """Destination-specific config (bucket, prefix, etc.)."""

    created_at: Optional[datetime] = None
    modified_at: Optional[datetime] = None


class BulkExportRequest(BaseModel):
    """Request to trigger a bulk export."""

    bulk_export_destination_id: str
    """Destination ID to export to."""

    session_id: Optional[str] = None
    """Project/session ID to export (None = all)."""

    start_time: datetime
    """Export runs starting from this time."""

    end_time: datetime
    """Export runs until this time."""

    export_fields: Optional[list[str]] = None
    """Specific fields to export (None = all)."""


class BulkExportResponse(BaseModel):
    """Response from triggering a bulk export."""

    id: str
    """Export job UUID."""

    bulk_export_destination_id: str
    """Destination ID."""

    session_id: Optional[str] = None
    """Project/session ID if filtered."""

    status: str
    """Job status: 'pending', 'running', 'completed', 'failed'."""

    start_time: datetime
    """Export time range start."""

    end_time: datetime
    """Export time range end."""

    created_at: datetime
    """When the export was triggered."""

    completed_at: Optional[datetime] = None
    """When the export completed."""

    error_message: Optional[str] = None
    """Error message if failed."""

    runs_exported: Optional[int] = None
    """Number of runs exported (if completed)."""
