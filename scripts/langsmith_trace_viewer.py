#!/usr/bin/env python3
"""
LangSmith Trace Viewer

Fetches and displays trace information from LangSmith API.

Usage:
    python scripts/langsmith_trace_viewer.py <trace_id>
    python scripts/langsmith_trace_viewer.py 019ac629-184b-749d-99ba-08c29ec6f64b
    python scripts/langsmith_trace_viewer.py <trace_id> --tree  # Show tree view
    python scripts/langsmith_trace_viewer.py <trace_id> --json  # Output raw JSON
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

# Repo root for finding Pulumi config
REPO_ROOT = Path(__file__).parent.parent


def load_secrets(env: str = "dev", working_dir: str = None) -> Dict[str, Any]:
    """Retrieves Pulumi stack secrets for the specified environment.

    Copied from receipt_dynamo to avoid import chain issues.
    """
    try:
        # Find the directory containing Pulumi.yaml
        if working_dir:
            pulumi_dir = Path(working_dir)
        else:
            pulumi_dir = REPO_ROOT / "infra"

        if not pulumi_dir.exists() or not (pulumi_dir / "Pulumi.yaml").exists():
            return {}

        result = subprocess.run(
            [
                "pulumi",
                "config",
                "--show-secrets",
                "--stack",
                f"tnorlund/portfolio/{env}",
                "--json",
            ],
            check=True,
            capture_output=True,
            text=True,
            cwd=str(pulumi_dir),
        )
        result_data = json.loads(result.stdout)

        # Extract the actual values from the Pulumi config format
        if isinstance(result_data, dict):
            extracted = {}
            for key, config_entry in result_data.items():
                if isinstance(config_entry, dict) and "value" in config_entry:
                    extracted[key] = config_entry["value"]
                else:
                    extracted[key] = config_entry
            return extracted

        return result_data if isinstance(result_data, dict) else {}
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return {}


def get_langsmith_api_key() -> str:
    """Get LangSmith API key from Pulumi secrets or environment."""
    # First try environment variable
    api_key = os.environ.get("LANGCHAIN_API_KEY") or os.environ.get("LANGSMITH_API_KEY")
    if api_key:
        return api_key

    # Try Pulumi secrets
    secrets = load_secrets("dev", str(REPO_ROOT / "infra"))

    # The key might be prefixed with "portfolio:" in config
    api_key = secrets.get("LANGCHAIN_API_KEY") or secrets.get("portfolio:LANGCHAIN_API_KEY")

    if not api_key:
        raise ValueError(
            "LangSmith API key not found. Set LANGCHAIN_API_KEY environment variable "
            "or ensure it's configured in Pulumi secrets."
        )

    return api_key


class LangSmithClient:
    """Client using LangSmith Python SDK."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        os.environ["LANGCHAIN_API_KEY"] = api_key

        try:
            from langsmith import Client
            self.client = Client()
        except ImportError:
            raise ImportError("langsmith package not installed. Run: pip install langsmith")

    def get_run(self, run_id: str) -> Dict[str, Any]:
        """Fetch a single run by ID."""
        run = self.client.read_run(run_id)
        return self._run_to_dict(run)

    def _run_to_dict(self, run) -> Dict[str, Any]:
        """Convert Run object to dict."""
        return {
            "id": str(run.id),
            "name": run.name,
            "run_type": run.run_type,
            "status": run.status,
            "start_time": run.start_time.isoformat() if run.start_time else None,
            "end_time": run.end_time.isoformat() if run.end_time else None,
            "inputs": run.inputs,
            "outputs": run.outputs,
            "error": run.error,
            "parent_run_id": str(run.parent_run_id) if run.parent_run_id else None,
            "trace_id": str(run.trace_id) if run.trace_id else None,
            "session_id": str(run.session_id) if hasattr(run, 'session_id') and run.session_id else None,
            "extra": run.extra if hasattr(run, 'extra') else {},
        }

    def get_child_runs(self, parent_run_id: str) -> List[Dict[str, Any]]:
        """Fetch all child runs for a parent run."""
        # List runs that have this parent
        runs = list(self.client.list_runs(
            parent_run_id=parent_run_id,
            limit=100,
        ))
        return [self._run_to_dict(r) for r in runs]

    def get_runs_in_trace(self, trace_id: str) -> List[Dict[str, Any]]:
        """Get all runs in a trace."""
        runs = list(self.client.list_runs(
            trace_id=trace_id,
            limit=100,
        ))
        return [self._run_to_dict(r) for r in runs]

    def get_trace_runs_flat(self, trace_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Get all runs in a trace as a flat list (faster than tree)."""
        runs = list(self.client.list_runs(
            trace_id=trace_id,
            limit=limit,
        ))
        return [self._run_to_dict(r) for r in runs]

    def get_recent_runs(self, project_name: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Get recent runs from a project."""
        runs = list(self.client.list_runs(
            project_name=project_name,
            limit=limit,
        ))
        return [self._run_to_dict(r) for r in runs]

    def get_run_tree(self, run_id: str, max_depth: int = 10) -> Dict[str, Any]:
        """Recursively fetch run and all descendants."""
        run = self.get_run(run_id)

        # Also get all runs in this trace to check for orphans
        trace_id = run.get("trace_id") or run_id
        all_runs_in_trace = self.get_runs_in_trace(trace_id)

        # Build tree
        run["_all_runs_in_trace"] = len(all_runs_in_trace)

        if max_depth > 0:
            try:
                children = self.get_child_runs(run_id)
                if children:
                    run["_children"] = []
                    for child in children:
                        child_tree = self.get_run_tree(child["id"], max_depth - 1)
                        run["_children"].append(child_tree)
            except Exception as e:
                print(f"   Warning: Could not fetch children: {e}")

        return run


def format_duration(start_time: str, end_time: Optional[str]) -> str:
    """Format duration between start and end times."""
    if not end_time:
        return "running..."

    try:
        start = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        end = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
        duration = (end - start).total_seconds()

        if duration < 1:
            return f"{duration * 1000:.0f}ms"
        elif duration < 60:
            return f"{duration:.1f}s"
        else:
            mins = int(duration // 60)
            secs = duration % 60
            return f"{mins}m {secs:.0f}s"
    except Exception:
        return "?"


def format_status(run: Dict[str, Any]) -> str:
    """Format run status with emoji."""
    status = run.get("status", "unknown")
    error = run.get("error")

    if error:
        return f"❌ error: {error[:50]}..." if len(str(error)) > 50 else f"❌ error: {error}"
    elif status == "success":
        return "✅ success"
    elif status == "pending":
        return "⏳ pending"
    elif status == "running":
        return "🔄 running"
    else:
        return f"❓ {status}"


def print_run_summary(run: Dict[str, Any], indent: int = 0, show_prompts: bool = False) -> None:
    """Print a summary of a single run."""
    prefix = "  " * indent

    name = run.get("name", "unnamed")
    run_type = run.get("run_type", "unknown")
    duration = format_duration(run.get("start_time", ""), run.get("end_time"))
    status = format_status(run)

    # Truncate long names
    if len(name) > 60:
        name = name[:57] + "..."

    print(f"{prefix}├── {name}")
    print(f"{prefix}│   Type: {run_type} | Duration: {duration} | {status}")

    # Show inputs/outputs summary if available
    inputs = run.get("inputs", {})
    outputs = run.get("outputs", {})

    if show_prompts and run_type in ["llm", "chain", "chat"]:
        # Show full prompts and responses for LLM runs
        if inputs:
            print(f"{prefix}│")
            print(f"{prefix}│   📥 INPUT/PROMPT:")
            input_str = json.dumps(inputs, indent=2, default=str)
            for line in input_str.split('\n'):
                print(f"{prefix}│   {line}")

        if outputs:
            print(f"{prefix}│")
            print(f"{prefix}│   📤 OUTPUT/RESPONSE:")
            output_str = json.dumps(outputs, indent=2, default=str)
            for line in output_str.split('\n'):
                print(f"{prefix}│   {line}")
    else:
        # Show summary
        if inputs:
            input_keys = list(inputs.keys())[:3]
            print(f"{prefix}│   Inputs: {input_keys}")

        if outputs:
            output_keys = list(outputs.keys())[:3]
            print(f"{prefix}│   Outputs: {output_keys}")

    # Show metadata if interesting
    metadata = run.get("extra", {}).get("metadata", {})
    if metadata:
        interesting_keys = ["merchant", "label_type", "word", "prompt_version"]
        shown = {k: v for k, v in metadata.items() if k in interesting_keys}
        if shown:
            print(f"{prefix}│   Metadata: {shown}")


def print_run_tree(run: Dict[str, Any], indent: int = 0, is_last: bool = True, show_prompts: bool = False) -> None:
    """Print run tree recursively."""
    print_run_summary(run, indent, show_prompts)

    children = run.get("_children", [])
    for i, child in enumerate(children):
        is_child_last = (i == len(children) - 1)
        print_run_tree(child, indent + 1, is_child_last, show_prompts)


def analyze_trace_nesting(run: Dict[str, Any], depth: int = 0) -> Dict[str, Any]:
    """Analyze trace nesting structure."""
    analysis = {
        "total_runs": 1,
        "max_depth": depth,
        "runs_by_type": {},
        "runs_by_depth": {depth: 1},
        "errors": [],
        "orphan_runs": [],  # Runs that should have been nested but weren't
    }

    run_type = run.get("run_type", "unknown")
    analysis["runs_by_type"][run_type] = analysis["runs_by_type"].get(run_type, 0) + 1

    if run.get("error"):
        analysis["errors"].append({
            "name": run.get("name"),
            "error": run.get("error"),
            "depth": depth,
        })

    children = run.get("_children", [])
    for child in children:
        child_analysis = analyze_trace_nesting(child, depth + 1)

        analysis["total_runs"] += child_analysis["total_runs"]
        analysis["max_depth"] = max(analysis["max_depth"], child_analysis["max_depth"])

        for rtype, count in child_analysis["runs_by_type"].items():
            analysis["runs_by_type"][rtype] = analysis["runs_by_type"].get(rtype, 0) + count

        for d, count in child_analysis["runs_by_depth"].items():
            analysis["runs_by_depth"][d] = analysis["runs_by_depth"].get(d, 0) + count

        analysis["errors"].extend(child_analysis["errors"])

    return analysis


def print_analysis(analysis: Dict[str, Any]) -> None:
    """Print trace analysis summary."""
    print("\n" + "=" * 60)
    print("TRACE ANALYSIS")
    print("=" * 60)

    print(f"\n📊 Overview:")
    print(f"   Total runs: {analysis['total_runs']}")
    print(f"   Max depth: {analysis['max_depth']}")

    print(f"\n📦 Runs by type:")
    for rtype, count in sorted(analysis["runs_by_type"].items(), key=lambda x: -x[1]):
        print(f"   {rtype}: {count}")

    print(f"\n📐 Runs by depth:")
    for depth in sorted(analysis["runs_by_depth"].keys()):
        count = analysis["runs_by_depth"][depth]
        bar = "█" * min(count, 40)
        print(f"   Depth {depth}: {bar} ({count})")

    if analysis["errors"]:
        print(f"\n❌ Errors ({len(analysis['errors'])}):")
        for err in analysis["errors"][:5]:
            print(f"   - {err['name']}: {err['error'][:80]}...")
        if len(analysis["errors"]) > 5:
            print(f"   ... and {len(analysis['errors']) - 5} more errors")
    else:
        print(f"\n✅ No errors found")

    # Check if nesting is working properly
    if analysis["max_depth"] == 0:
        print(f"\n⚠️  WARNING: No nested runs found!")
        print(f"   This trace has no children. If you expected nested traces,")
        print(f"   check that langsmith_extra={{\"parent\": run_tree}} is being passed.")
    elif analysis["max_depth"] == 1:
        print(f"\n⚠️  NOTE: Shallow nesting (max depth = 1)")
        print(f"   Consider if deeper nesting is expected.")
    else:
        print(f"\n✅ Trace nesting looks good (depth = {analysis['max_depth']})")


def main():
    parser = argparse.ArgumentParser(
        description="Fetch and display LangSmith trace information",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("trace_id", help="LangSmith trace/run ID")
    parser.add_argument("--tree", action="store_true", help="Show full tree view")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    parser.add_argument("--prompts", action="store_true", help="Show detailed prompts and responses")
    parser.add_argument("--depth", type=int, default=3, help="Max depth to fetch (default: 3)")

    args = parser.parse_args()

    try:
        api_key = get_langsmith_api_key()
        print(f"🔑 API key loaded (ends with ...{api_key[-4:]})")
    except ValueError as e:
        print(f"❌ {e}")
        sys.exit(1)

    client = LangSmithClient(api_key)

    print(f"\n🔍 Fetching trace: {args.trace_id}")
    print("-" * 60)

    try:
        if args.prompts and not args.tree:
            # Fast mode: just get all runs in trace flat
            print("🔍 Fetching all runs in trace (fast mode)...")
            runs = client.get_trace_runs_flat(args.trace_id, limit=100)
            print(f"\n📋 Found {len(runs)} runs in trace\n")

            for i, run in enumerate(runs, 1):
                print("=" * 80)
                print(f"Run #{i}: {run.get('name', 'unnamed')}")
                print("=" * 80)
                print(f"Type: {run.get('run_type')} | Status: {format_status(run)}")
                print(f"Duration: {format_duration(run.get('start_time', ''), run.get('end_time'))}")

                if run.get('run_type') in ['llm', 'chain', 'chat']:
                    inputs = run.get("inputs", {})
                    outputs = run.get("outputs", {})

                    if inputs:
                        print("\n📥 INPUT/PROMPT:")
                        print(json.dumps(inputs, indent=2, default=str))

                    if outputs:
                        print("\n📤 OUTPUT/RESPONSE:")
                        print(json.dumps(outputs, indent=2, default=str))
                else:
                    if run.get("inputs"):
                        print(f"\nInputs: {list(run.get('inputs', {}).keys())}")
                    if run.get("outputs"):
                        print(f"Outputs: {list(run.get('outputs', {}).keys())}")

                metadata = run.get("extra", {}).get("metadata", {})
                if metadata:
                    print(f"\nMetadata: {metadata}")

                print()
        else:
            # Fetch the run tree
            run_tree = client.get_run_tree(args.trace_id, max_depth=args.depth)

            if args.json:
                print(json.dumps(run_tree, indent=2, default=str))
            else:
                # Print header info
                print(f"\n📋 Trace: {run_tree.get('name', 'unnamed')}")
                print(f"   ID: {run_tree.get('id')}")
                print(f"   Session ID: {run_tree.get('session_id', 'unknown')}")
                print(f"   Status: {format_status(run_tree)}")
                print(f"   Duration: {format_duration(run_tree.get('start_time', ''), run_tree.get('end_time'))}")

                if args.tree:
                    print("\n" + "=" * 60)
                    print("TRACE TREE")
                    print("=" * 60 + "\n")
                    print_run_tree(run_tree, show_prompts=args.prompts)

                # Analyze and print summary
                analysis = analyze_trace_nesting(run_tree)
                print_analysis(analysis)

    except Exception as e:
        print(f"❌ Error: {e}")
        if "404" in str(e):
            print(f"   Trace not found. Check the ID is correct.")
        elif "401" in str(e):
            print(f"   Authentication failed. Check your API key.")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

