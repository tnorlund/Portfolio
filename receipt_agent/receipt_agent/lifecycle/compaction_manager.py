"""
Compaction Management for Receipt Lifecycle

Handles waiting for and checking the status of ChromaDB compaction runs.
"""

import time
from typing import Optional, Tuple

from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import CompactionState


def wait_for_compaction(
    client: DynamoClient,
    image_id: str,
    receipt_id: int,
    max_wait_seconds: int = 300,
    poll_interval: int = 5,
    initial_wait_seconds: int = 10,
) -> str:
    """
    Wait for CompactionRun to complete for a receipt.

    First waits for CompactionRun to appear (NDJSON worker creates it),
    then polls until both lines and words collections are COMPLETED.

    Args:
        client: DynamoDB client
        image_id: Image ID
        receipt_id: Receipt ID
        max_wait_seconds: Maximum total wait time
        poll_interval: Seconds between polls
        initial_wait_seconds: Initial wait before checking for CompactionRun

    Returns:
        run_id of the completed compaction run

    Raises:
        TimeoutError: If compaction doesn't complete within max_wait_seconds
        RuntimeError: If compaction fails
    """
    start_time = time.time()
    run_id = None
    last_state = None
    compaction_run_found = False

    print(f"⏳ Waiting for compaction to complete (max {max_wait_seconds}s)...")

    # First, wait a bit for NDJSON worker to create CompactionRun
    if initial_wait_seconds > 0:
        print(f"   Waiting {initial_wait_seconds}s for CompactionRun to be created...")
        time.sleep(initial_wait_seconds)

    while time.time() - start_time < max_wait_seconds:
        # Get most recent CompactionRun for this receipt
        runs, _ = client.list_compaction_runs_for_receipt(image_id, receipt_id, limit=1)

        if runs:
            if not compaction_run_found:
                print(f"   ✓ CompactionRun found, waiting for completion...")
                compaction_run_found = True

            run = runs[0]
            run_id = run.run_id

            # Check current state
            current_state = f"lines={run.lines_state}, words={run.words_state}"
            if current_state != last_state:
                print(f"   Compaction state: {current_state}")
                last_state = current_state

            # Check if both collections are completed
            if (
                run.lines_state == CompactionState.COMPLETED.value
                and run.words_state == CompactionState.COMPLETED.value
            ):
                print(f"✅ Compaction completed for run {run_id}")
                return run_id

            # Check for failures
            if (
                run.lines_state == CompactionState.FAILED.value
                or run.words_state == CompactionState.FAILED.value
            ):
                error_msg = f"Compaction failed: lines={run.lines_state}, words={run.words_state}"
                if run.lines_error:
                    error_msg += f", lines_error={run.lines_error}"
                if run.words_error:
                    error_msg += f", words_error={run.words_error}"
                raise RuntimeError(error_msg)
        else:
            # CompactionRun not created yet
            if not compaction_run_found:
                elapsed = int(time.time() - start_time)
                if elapsed % 10 == 0:  # Print every 10 seconds
                    print(f"   Waiting for CompactionRun to be created... ({elapsed}s)")

        time.sleep(poll_interval)

    # Timeout
    if not run_id:
        raise TimeoutError(
            f"CompactionRun not found for receipt {receipt_id} after {max_wait_seconds}s. "
            f"NDJSON worker may not have processed the queue yet."
        )
    else:
        raise TimeoutError(
            f"Compaction did not complete for run {run_id} after {max_wait_seconds}s. "
            f"Current state: {last_state}"
        )


def check_compaction_status(
    client: DynamoClient,
    image_id: str,
    receipt_id: int,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Check the current compaction status for a receipt.

    Args:
        client: DynamoDB client
        image_id: Image ID
        receipt_id: Receipt ID

    Returns:
        Tuple of (run_id, lines_state, words_state) or (None, None, None) if no run found
    """
    runs, _ = client.list_compaction_runs_for_receipt(image_id, receipt_id, limit=1)
    if not runs:
        return (None, None, None)

    run = runs[0]
    return (run.run_id, run.lines_state, run.words_state)
