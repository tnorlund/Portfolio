#!/usr/bin/env python3
"""Analyze step function execution to find failure details."""

import json
import subprocess
import sys
from typing import Dict, List, Optional

EXECUTION_ID = "a7bfaf55-8500-4e63-a4d8-bdea190d0c4a"
SF_NAME = "line-ingest-sf-dev-1554303"


def run_aws_command(cmd: List[str]) -> Dict:
    """Run an AWS CLI command and return JSON output."""
    try:
        result = subprocess.run(
            ["aws"] + cmd + ["--output", "json"],
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(result.stdout)
    except subprocess.CalledProcessError as e:
        print(f"Error running command: {' '.join(cmd)}", file=sys.stderr)
        print(f"Error: {e.stderr}", file=sys.stderr)
        return {}
    except json.JSONDecodeError as e:
        print(f"Error parsing JSON: {e}", file=sys.stderr)
        return {}


def get_step_function_arn(sf_name: str) -> Optional[str]:
    """Get the ARN for a step function by name."""
    result = run_aws_command(["stepfunctions", "list-state-machines"])
    for sf in result.get("stateMachines", []):
        if sf_name in sf.get("name", ""):
            return sf.get("stateMachineArn")
    return None


def get_execution_arn(sf_arn: str, execution_id: str) -> Optional[str]:
    """Get execution ARN from execution ID."""
    result = run_aws_command(
        [
            "stepfunctions",
            "list-executions",
            "--state-machine-arn",
            sf_arn,
            "--max-results",
            "1000",
        ]
    )
    for execution in result.get("executions", []):
        if execution_id in execution.get("executionArn", ""):
            return execution.get("executionArn")
    return None


def main():
    """Analyze execution failure."""
    print(f"Analyzing execution: {EXECUTION_ID}")
    print(f"Step Function: {SF_NAME}\n")

    # Get step function ARN
    sf_arn = get_step_function_arn(SF_NAME)
    if not sf_arn:
        print(f"ERROR: Could not find step function: {SF_NAME}")
        return

    # Get execution ARN
    exec_arn = get_execution_arn(sf_arn, EXECUTION_ID)
    if not exec_arn:
        print(f"ERROR: Could not find execution: {EXECUTION_ID}")
        return

    print(f"Execution ARN: {exec_arn}\n")

    # Get execution details
    exec_details = run_aws_command(
        ["stepfunctions", "describe-execution", "--execution-arn", exec_arn]
    )
    print("Execution Details:")
    print(f"  Status: {exec_details.get('status')}")
    print(f"  Start: {exec_details.get('startDate')}")
    print(f"  Stop: {exec_details.get('stopDate')}")
    print(f"  Input: {json.dumps(exec_details.get('input', {}), indent=2)}")
    print(f"  Output: {json.dumps(exec_details.get('output', {}), indent=2)}")
    print()

    # Get execution history
    history = run_aws_command(
        [
            "stepfunctions",
            "get-execution-history",
            "--execution-arn",
            exec_arn,
            "--max-results",
            "1000",
        ]
    )

    events = history.get("events", [])
    print(f"Total events: {len(events)}\n")

    # Find failure events
    print("=" * 80)
    print("FAILURE EVENTS")
    print("=" * 80)
    for event in events:
        event_type = event.get("type")
        if "Failed" in event_type or "Aborted" in event_type or "TimedOut" in event_type:
            print(f"\n{event_type}:")
            print(json.dumps(event, indent=2))

    # Find error events
    print("\n" + "=" * 80)
    print("ERROR EVENTS")
    print("=" * 80)
    for event in events:
        event_type = event.get("type")
        if event_type == "ExecutionFailed":
            details = event.get("executionFailedEventDetails", {})
            print(f"\nExecution Failed:")
            print(f"  Error: {details.get('error')}")
            print(f"  Cause: {details.get('cause')}")
        elif event_type == "TaskFailed":
            details = event.get("taskFailedEventDetails", {})
            print(f"\nTask Failed:")
            print(f"  Resource: {details.get('resource')}")
            print(f"  Error: {details.get('error')}")
            print(f"  Cause: {details.get('cause')}")

    # Show first 20 events chronologically
    print("\n" + "=" * 80)
    print("FIRST 20 EVENTS (chronological)")
    print("=" * 80)
    for i, event in enumerate(events[:20]):
        event_type = event.get("type")
        timestamp = event.get("timestamp", "")
        print(f"\n[{i+1}] {timestamp} - {event_type}")
        if event_type in ["TaskStateEntered", "TaskScheduled", "TaskStarted"]:
            details = (
                event.get("taskStateEnteredEventDetails")
                or event.get("taskScheduledEventDetails")
                or event.get("taskStartedEventDetails")
                or {}
            )
            print(f"    Resource: {details.get('resource')}")
        elif event_type in ["TaskFailed", "TaskTimedOut"]:
            details = event.get("taskFailedEventDetails") or event.get("taskTimedOutEventDetails") or {}
            print(f"    Resource: {details.get('resource')}")
            print(f"    Error: {details.get('error')}")
            print(f"    Cause: {details.get('cause')}")


if __name__ == "__main__":
    main()

