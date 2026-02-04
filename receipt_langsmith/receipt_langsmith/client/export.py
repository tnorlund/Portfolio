"""Bulk export manager for LangSmith.

This module provides utilities for managing LangSmith bulk exports,
including triggering exports, checking status, and waiting for completion.
"""

# pylint: disable=import-outside-toplevel,protected-access

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional

from pydantic import BaseModel

if TYPE_CHECKING:
    from receipt_langsmith.client.api import LangSmithClient

logger = logging.getLogger(__name__)


class ExportStatus(str, Enum):
    """Bulk export job status."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ExportJob(BaseModel):
    """Bulk export job status and metadata."""

    id: str
    """Export job UUID."""

    bulk_export_destination_id: str
    """Destination ID."""

    session_id: Optional[str] = None
    """Project/session ID if filtered."""

    status: ExportStatus
    """Current job status."""

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


class BulkExportManager:
    """Manager for LangSmith bulk export operations.

    This class provides methods for triggering bulk exports, checking
    status, and waiting for completion.

    Args:
        client: LangSmith API client.
        destination_id: Default bulk export destination ID.

    Example:
        ```python
        client = LangSmithClient()
        export_mgr = BulkExportManager(client, destination_id="...")

        # Trigger export
        job = export_mgr.trigger_export(project_id="...", days_back=7)

        # Wait for completion
        job = export_mgr.wait_for_completion(job.id)
        print(f"Exported {job.runs_exported} runs")
        ```
    """

    def __init__(
        self,
        client: LangSmithClient,
        destination_id: str,
    ):
        self.client = client
        self.destination_id = destination_id

    def _parse_export_job(self, data: dict[str, Any]) -> ExportJob:
        """Parse API response into ExportJob."""
        return ExportJob(
            id=data["id"],
            bulk_export_destination_id=data["bulk_export_destination_id"],
            session_id=data.get("session_id"),
            status=ExportStatus(data["status"]),
            start_time=datetime.fromisoformat(
                data["start_time"].replace("Z", "+00:00")
            ),
            end_time=datetime.fromisoformat(
                data["end_time"].replace("Z", "+00:00")
            ),
            created_at=datetime.fromisoformat(
                data["created_at"].replace("Z", "+00:00")
            ),
            completed_at=(
                datetime.fromisoformat(
                    data["completed_at"].replace("Z", "+00:00")
                )
                if data.get("completed_at")
                else None
            ),
            error_message=data.get("error_message"),
            runs_exported=data.get("runs_exported"),
        )

    async def atrigger_export(
        self,
        project_id: Optional[str] = None,
        days_back: int = 7,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> ExportJob:
        """Trigger a new bulk export job.

        Args:
            project_id: Project/session ID to export (None = all projects).
            days_back: Number of days back to export (ignored if start_time
                set).
            start_time: Export runs starting from this time.
            end_time: Export runs until this time (default: now).

        Returns:
            ExportJob with initial status.
        """
        if end_time is None:
            end_time = datetime.now(timezone.utc)

        if start_time is None:
            start_time = end_time - timedelta(days=days_back)

        request_body: dict[str, Any] = {
            "bulk_export_destination_id": self.destination_id,
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
        }

        if project_id:
            request_body["session_id"] = project_id

        logger.info(
            "Triggering bulk export: project=%s, start=%s, end=%s",
            project_id or "all",
            start_time.isoformat(),
            end_time.isoformat(),
        )

        http_client = await self.client._get_async_client()
        response = await http_client.post(
            "/api/v1/bulk-exports",
            json=request_body,
        )

        if response.status_code >= 400:
            from receipt_langsmith.client.api import LangSmithAPIError

            raise LangSmithAPIError(response.status_code, response.text)

        data = response.json()
        job = self._parse_export_job(data)

        logger.info("Export job created: %s (status=%s)", job.id, job.status)
        return job

    async def aget_export_status(self, export_id: str) -> ExportJob:
        """Get the current status of an export job.

        Args:
            export_id: Export job UUID.

        Returns:
            ExportJob with current status.
        """
        http_client = await self.client._get_async_client()
        response = await http_client.get(f"/api/v1/bulk-exports/{export_id}")

        if response.status_code >= 400:
            from receipt_langsmith.client.api import LangSmithAPIError

            raise LangSmithAPIError(response.status_code, response.text)

        data = response.json()
        return self._parse_export_job(data)

    async def await_completion(
        self,
        export_id: str,
        poll_interval: int = 30,
        timeout: int = 3600,
    ) -> ExportJob:
        """Wait for an export job to complete.

        Args:
            export_id: Export job UUID.
            poll_interval: Seconds between status checks.
            timeout: Maximum seconds to wait.

        Returns:
            ExportJob with final status.

        Raises:
            TimeoutError: If export doesn't complete within timeout.
        """
        start = time.monotonic()
        while (time.monotonic() - start) < timeout:
            job = await self.aget_export_status(export_id)

            if job.status == ExportStatus.COMPLETED:
                logger.info(
                    "Export completed: %s (%d runs exported)",
                    job.id,
                    job.runs_exported or 0,
                )
                return job

            if job.status == ExportStatus.FAILED:
                logger.error(
                    "Export failed: %s - %s", job.id, job.error_message
                )
                return job

            elapsed = int(time.monotonic() - start)
            logger.debug(
                "Export %s status: %s (elapsed=%ds)",
                job.id,
                job.status,
                elapsed,
            )
            await asyncio.sleep(poll_interval)

        raise TimeoutError(
            f"Export {export_id} did not complete within {timeout}s"
        )

    async def alist_exports(self, limit: int = 20) -> list[ExportJob]:
        """List recent bulk exports.

        Args:
            limit: Maximum number of exports to return.

        Returns:
            List of ExportJob objects.
        """
        http_client = await self.client._get_async_client()
        response = await http_client.get(
            "/api/v1/bulk-exports",
            params={"limit": limit},
        )

        if response.status_code >= 400:
            from receipt_langsmith.client.api import LangSmithAPIError

            raise LangSmithAPIError(response.status_code, response.text)

        return [self._parse_export_job(data) for data in response.json()]

    # Sync wrappers

    def trigger_export(self, **kwargs: Any) -> ExportJob:
        """Trigger a bulk export (sync wrapper)."""
        return asyncio.run(self.atrigger_export(**kwargs))

    def get_export_status(self, export_id: str) -> ExportJob:
        """Get export status (sync wrapper)."""
        return asyncio.run(self.aget_export_status(export_id))

    def wait_for_completion(
        self,
        export_id: str,
        poll_interval: int = 30,
        timeout: int = 3600,
    ) -> ExportJob:
        """Wait for export completion (sync wrapper)."""
        return asyncio.run(
            self.await_completion(export_id, poll_interval, timeout)
        )

    def list_exports(self, limit: int = 20) -> list[ExportJob]:
        """List recent exports (sync wrapper)."""
        return asyncio.run(self.alist_exports(limit))
