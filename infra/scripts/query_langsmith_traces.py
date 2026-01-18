#!/usr/bin/env python3
"""Query LangSmith traces for debugging."""

import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

# Compute infra directory relative to this script
SCRIPT_DIR = Path(__file__).resolve().parent
INFRA_DIR = SCRIPT_DIR.parent  # scripts/ is inside infra/


def get_pulumi_secret(key: str, stack: str = "dev") -> str:
    """Get a secret from Pulumi config."""
    result = subprocess.run(
        ["pulumi", "config", "get", key, "--stack", stack],
        capture_output=True,
        text=True,
        cwd=str(INFRA_DIR),
    )
    if result.returncode != 0:
        raise ValueError(f"Failed to get {key}: {result.stderr}")
    return result.stdout.strip()


def query_langsmith(
    endpoint: str,
    api_key: str,
    tenant_id: str,
    method: str = "GET",
    data: Optional[dict] = None,
) -> dict:
    """Query the LangSmith API."""
    headers = {
        "X-API-Key": api_key,
        "X-Tenant-ID": tenant_id,
        "Content-Type": "application/json",
    }
    url = f"https://api.smith.langchain.com/api/v1/{endpoint}"

    try:
        if method == "GET":
            resp = requests.get(url, headers=headers, timeout=(3, 10))
        else:
            resp = requests.post(
                url, headers=headers, json=data, timeout=(3, 10)
            )
        resp.raise_for_status()
        return resp.json() if resp.text else {}
    except requests.exceptions.Timeout:
        print(f"Request to {endpoint} timed out")
        return {}
    except requests.exceptions.HTTPError as e:
        print(f"HTTP error querying {endpoint}: {e}")
        return {}
    except requests.exceptions.RequestException as e:
        print(f"Request error querying {endpoint}: {e}")
        return {}


def query_trace(trace_id: str, api_key: str, tenant_id: str):
    """Query a specific trace and its children."""
    print(f"\n🔍 Querying trace: {trace_id}")
    run = query_langsmith(f"runs/{trace_id}", api_key, tenant_id)
    print(
        json.dumps(
            {
                "id": run.get("id"),
                "name": run.get("name"),
                "status": run.get("status"),
                "error": run.get("error"),
                "start_time": run.get("start_time"),
                "end_time": run.get("end_time"),
                "outputs": run.get("outputs"),
                "session_id": run.get("session_id"),
                "session_name": run.get("session_name"),
            },
            indent=2,
        )
    )

    # Get child runs
    print(f"\n👶 Child runs for trace {trace_id}:")
    children = query_langsmith(
        "runs/query",
        api_key,
        tenant_id,
        method="POST",
        data={"trace": trace_id, "limit": 30},
    )
    for child in children.get("runs", []):
        status_icon = (
            "✅"
            if child.get("status") == "success"
            else "❌" if child.get("error") else "⏳"
        )
        print(
            f"  {status_icon} {child.get('name')} - {child.get('status')} (id={child.get('id')[:8]}...)"
        )


def query_project_traces(
    project_name: str,
    api_key: str,
    tenant_id: str,
    start_date: Optional[str] = None,
    limit: int = 10,
):
    """Query traces from a specific project."""
    # Get all projects
    projects = query_langsmith("sessions?limit=50", api_key, tenant_id)

    # Validate response is a list
    if not isinstance(projects, list):
        print(f"Error: Expected list of projects, got: {type(projects)}")
        return []

    project_id = None
    for p in projects:
        if p["name"] == project_name:
            project_id = p["id"]
            break

    if not project_id:
        print(f"Project '{project_name}' not found")
        return []

    print(f"\n📊 Traces in {project_name} (id={project_id}):")

    query_data = {
        "session": project_id,
        "limit": limit,
    }

    if start_date:
        query_data["start_time"] = start_date

    runs = query_langsmith(
        "runs/query", api_key, tenant_id, method="POST", data=query_data
    )

    traces = runs.get("runs", [])
    for run in traces:
        status_icon = (
            "✅"
            if run.get("status") == "success"
            else "❌" if run.get("error") else "⏳"
        )
        start_time = run.get("start_time", "N/A")
        end_time = run.get("end_time", "N/A")
        print(
            f"  {status_icon} {run.get('name')} - status={run.get('status')}"
        )
        print(
            f"      start={start_time[:19] if start_time != 'N/A' else 'N/A'}, end={end_time[:19] if end_time and end_time != 'N/A' else 'N/A'}"
        )
        print(f"      id={run.get('id')}")
        if run.get("error"):
            print(f"      Error: {run.get('error')[:100]}...")

    return traces


def main():
    api_key = get_pulumi_secret("LANGSMITH_SERVICE_API_KEY")
    tenant_id = get_pulumi_secret("LANGSMITH_TENANT_ID")

    print("=" * 60)
    print("LangSmith Trace Query Tool")
    print("=" * 60)

    # List all projects
    print("\n📁 All Projects:")
    projects = query_langsmith("sessions?limit=50", api_key, tenant_id)

    # Validate response is a list
    if not isinstance(projects, list):
        print(f"Error: Expected list of projects, got: {type(projects)}")
        print(f"Response: {projects}")
        return

    for p in projects:
        print(
            f"  - {p['name']} (created: {p.get('start_time', 'N/A')[:10] if p.get('start_time') else 'N/A'})"
        )

    # Handle command line args
    if len(sys.argv) > 1:
        arg = sys.argv[1]

        # Check if it's a date (YYYY-MM-DD) or trace ID
        if arg.startswith("project:"):
            # Query specific project
            project_name = arg.replace("project:", "")
            start_date = sys.argv[2] if len(sys.argv) > 2 else None
            traces = query_project_traces(
                project_name, api_key, tenant_id, start_date
            )

            # If traces found, query first one's children
            if traces:
                print("\n" + "=" * 40)
                print("First trace details:")
                query_trace(traces[0]["id"], api_key, tenant_id)

        elif arg.startswith("date:"):
            # Query traces from a specific date across all projects
            date_str = arg.replace("date:", "")
            print(f"\n📅 Looking for traces from {date_str}")

            # Look for projects that might have traces from that date
            for p in projects:
                project_start = p.get("start_time", "")
                if project_start and date_str in project_start[:10]:
                    print(f"\n  Found project from that date: {p['name']}")
                    query_project_traces(
                        p["name"], api_key, tenant_id, limit=5
                    )

        else:
            # Assume it's a trace ID
            query_trace(arg, api_key, tenant_id)


if __name__ == "__main__":
    main()
