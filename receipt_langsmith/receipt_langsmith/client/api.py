"""LangSmith API client with async/sync support and retry logic.

This module provides a client for interacting with the LangSmith API,
supporting both async and sync operations with automatic retry logic.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from receipt_langsmith.client.models import Project

logger = logging.getLogger(__name__)


class LangSmithAPIError(Exception):
    """Error from LangSmith API."""

    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"LangSmith API error {status_code}: {message}")


class LangSmithClient:
    """Async/sync LangSmith API client with retry logic.

    This client provides methods for interacting with the LangSmith API,
    including listing projects, listing runs, and managing bulk exports.

    Args:
        api_key: LangSmith API key. If not provided, uses LANGCHAIN_API_KEY env var.
        tenant_id: LangSmith tenant/workspace ID. If not provided, uses
            LANGSMITH_TENANT_ID env var.
        timeout: Request timeout in seconds.
        base_url: LangSmith API base URL.

    Example:
        ```python
        # Sync usage
        client = LangSmithClient()
        projects = client.list_projects()

        # Async usage
        async with LangSmithClient() as client:
            projects = await client.alist_projects()
        ```
    """

    BASE_URL = "https://api.smith.langchain.com"

    def __init__(
        self,
        api_key: Optional[str] = None,
        tenant_id: Optional[str] = None,
        timeout: float = 30.0,
        base_url: Optional[str] = None,
    ):
        self.api_key = api_key or os.environ.get("LANGCHAIN_API_KEY", "")
        self.tenant_id = tenant_id or os.environ.get("LANGSMITH_TENANT_ID")
        self.timeout = timeout
        self.base_url = base_url or self.BASE_URL

        if not self.api_key:
            raise ValueError(
                "LangSmith API key required. Set LANGCHAIN_API_KEY env var "
                "or pass api_key parameter."
            )

        self._async_client: Optional[httpx.AsyncClient] = None
        self._sync_client: Optional[httpx.Client] = None

    @property
    def headers(self) -> dict[str, str]:
        """HTTP headers for API requests."""
        headers = {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
        }
        if self.tenant_id:
            headers["X-Tenant-Id"] = self.tenant_id
        return headers

    # Async client management

    async def _get_async_client(self) -> httpx.AsyncClient:
        """Get or create async HTTP client."""
        if self._async_client is None:
            self._async_client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=self.headers,
                timeout=self.timeout,
            )
        return self._async_client

    async def aclose(self) -> None:
        """Close async client."""
        if self._async_client:
            await self._async_client.aclose()
            self._async_client = None

    async def __aenter__(self) -> LangSmithClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.aclose()

    # Sync client management

    def _get_sync_client(self) -> httpx.Client:
        """Get or create sync HTTP client."""
        if self._sync_client is None:
            self._sync_client = httpx.Client(
                base_url=self.base_url,
                headers=self.headers,
                timeout=self.timeout,
            )
        return self._sync_client

    def close(self) -> None:
        """Close sync client."""
        if self._sync_client:
            self._sync_client.close()
            self._sync_client = None

    def __enter__(self) -> LangSmithClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    # Async API methods

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(httpx.HTTPError),
    )
    async def _arequest(
        self,
        method: str,
        path: str,
        params: Optional[dict[str, Any]] = None,
        json: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any] | list[Any]:
        """Make an async API request with retry logic."""
        client = await self._get_async_client()
        response = await client.request(method, path, params=params, json=json)

        if response.status_code >= 400:
            raise LangSmithAPIError(response.status_code, response.text)

        return response.json()

    async def alist_projects(self) -> list[Project]:
        """List all projects (sessions) in the workspace.

        Returns:
            List of Project objects.
        """
        data = await self._arequest("GET", "/api/v1/sessions")
        if isinstance(data, list):
            return [Project(**p) for p in data]
        return []

    async def aget_project(self, project_name: str) -> Optional[Project]:
        """Get a project by name.

        Args:
            project_name: Name of the project to find.

        Returns:
            Project if found, None otherwise.
        """
        projects = await self.alist_projects()
        for project in projects:
            if project.name == project_name:
                return project
        return None

    async def alist_runs(
        self,
        project_name: Optional[str] = None,
        project_id: Optional[str] = None,
        run_type: Optional[str] = None,
        name_filter: Optional[str] = None,
        start_time: Optional[datetime] = None,
        parent_run_id: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List runs with optional filters.

        Args:
            project_name: Filter by project name.
            project_id: Filter by project ID.
            run_type: Filter by run type ('chain', 'llm', 'tool').
            name_filter: Filter by exact run name.
            start_time: Only runs after this time.
            parent_run_id: Filter by parent run ID.
            limit: Maximum number of runs to return.

        Returns:
            List of run dictionaries.
        """
        params: dict[str, Any] = {"limit": limit}

        if project_name:
            params["project_name"] = project_name
        if project_id:
            params["session"] = project_id
        if run_type:
            params["run_type"] = run_type
        if name_filter:
            # Sanitize name_filter to prevent filter injection
            # Escape backslashes first, then quotes (order matters)
            sanitized = name_filter.replace("\\", "\\\\").replace('"', '\\"')
            params["filter"] = f'eq(name, "{sanitized}")'
        if start_time:
            params["start_time"] = start_time.isoformat()
        if parent_run_id:
            params["parent_run_id"] = parent_run_id

        data = await self._arequest("GET", "/api/v1/runs", params=params)
        return data if isinstance(data, list) else []

    async def alist_recent_traces(
        self,
        project_name: str,
        trace_name: str = "ReceiptEvaluation",
        hours_back: int = 168,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List recent root traces by name.

        Args:
            project_name: LangSmith project name.
            trace_name: Name of root traces to find.
            hours_back: How many hours back to search.
            limit: Maximum number of traces.

        Returns:
            List of trace dictionaries with outputs.
        """
        start_time = datetime.now(timezone.utc) - timedelta(hours=hours_back)

        runs = await self.alist_runs(
            project_name=project_name,
            run_type="chain",
            name_filter=trace_name,
            start_time=start_time,
            limit=limit,
        )

        # Filter to only runs with outputs
        return [run for run in runs if run.get("outputs")]

    async def aget_child_traces(
        self, parent_run_id: str
    ) -> dict[str, dict[str, Any]]:
        """Get child traces for a parent run.

        Args:
            parent_run_id: Parent run ID.

        Returns:
            Dict mapping child trace name to its outputs.
        """
        children = await self.alist_runs(
            parent_run_id=parent_run_id, limit=100
        )
        return {
            run["name"]: run.get("outputs", {})
            for run in children
            if run.get("outputs")
        }

    # Sync API methods (wrappers around async methods)

    def _run_sync(self, coro: Any) -> Any:
        """Run async coroutine synchronously with proper cleanup.

        Ensures the async client is closed after each sync call to prevent
        leaking connections across event loops.
        """

        async def wrapper() -> Any:
            try:
                return await coro
            finally:
                await self.aclose()

        return asyncio.run(wrapper())

    def list_projects(self) -> list[Project]:
        """List all projects (sync wrapper)."""
        return self._run_sync(self.alist_projects())

    def get_project(self, project_name: str) -> Optional[Project]:
        """Get a project by name (sync wrapper)."""
        return self._run_sync(self.aget_project(project_name))

    def list_runs(self, **kwargs: Any) -> list[dict[str, Any]]:
        """List runs with filters (sync wrapper)."""
        return self._run_sync(self.alist_runs(**kwargs))

    def list_recent_traces(self, **kwargs: Any) -> list[dict[str, Any]]:
        """List recent traces (sync wrapper)."""
        return self._run_sync(self.alist_recent_traces(**kwargs))

    def get_child_traces(
        self, parent_run_id: str
    ) -> dict[str, dict[str, Any]]:
        """Get child traces (sync wrapper)."""
        return self._run_sync(self.aget_child_traces(parent_run_id))
