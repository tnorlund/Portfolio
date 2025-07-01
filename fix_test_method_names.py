#!/usr/bin/env python3
"""
Fix method naming convention in integration tests from camelCase to snake_case.
"""

import os
import re
from pathlib import Path

# Map of old camelCase method names to new snake_case names
METHOD_MAPPINGS = {
    # Job methods
    "addJob": "add_job",
    "addJobs": "add_jobs",
    "updateJob": "update_job",
    "deleteJob": "delete_job",
    "getJob": "get_job",
    "listJobs": "list_jobs",
    "getJobWithStatus": "get_job_with_status",
    "listJobsByStatus": "list_jobs_by_status",
    "listJobsByUser": "list_jobs_by_user",
    # JobMetric methods
    "addJobMetric": "add_job_metric",
    "getJobMetric": "get_job_metric",
    "listJobMetrics": "list_job_metrics",
    "getMetricsByName": "get_metrics_by_name",
    "getMetricsByNameAcrossJobs": "get_metrics_by_name_across_jobs",
    # JobStatus methods
    "addJobStatus": "add_job_status",
    "getLatestJobStatus": "get_latest_job_status",
    "listJobStatuses": "list_job_statuses",
    # JobCheckpoint methods
    "addJobCheckpoint": "add_job_checkpoint",
    "getJobCheckpoint": "get_job_checkpoint",
    "deleteJobCheckpoint": "delete_job_checkpoint",
    "listJobCheckpoints": "list_job_checkpoints",
    "listAllJobCheckpoints": "list_all_job_checkpoints",
    "getBestCheckpoint": "get_best_checkpoint",
    "updateBestCheckpoint": "update_best_checkpoint",
    # JobDependency methods
    "addJobDependency": "add_job_dependency",
    "getJobDependency": "get_job_dependency",
    "deleteJobDependency": "delete_job_dependency",
    "listDependencies": "list_dependencies",
    "listDependents": "list_dependents",
    "deleteAllDependencies": "delete_all_dependencies",
    # JobLog methods
    "addJobLog": "add_job_log",
    "addJobLogs": "add_job_logs",
    "getJobLog": "get_job_log",
    "deleteJobLog": "delete_job_log",
    "listJobLogs": "list_job_logs",
    # JobResource methods
    "addJobResource": "add_job_resource",
    "getJobResource": "get_job_resource",
    "listJobResources": "list_job_resources",
    "getResourceById": "get_resource_by_id",
    "listResourcesByType": "list_resources_by_type",
    "updateJobResourceStatus": "update_job_resource_status",
    # Instance methods
    "addInstance": "add_instance",
    "addInstances": "add_instances",
    "updateInstance": "update_instance",
    "deleteInstance": "delete_instance",
    "getInstance": "get_instance",
    "getInstanceWithJobs": "get_instance_with_jobs",
    "listInstances": "list_instances",
    "listInstancesByStatus": "list_instances_by_status",
    "listInstancesForJob": "list_instances_for_job",
    "addInstanceJob": "add_instance_job",
    "getInstanceJob": "get_instance_job",
    "listInstanceJobs": "list_instance_jobs",
    # Queue methods
    "addQueue": "add_queue",
    "addQueues": "add_queues",
    "updateQueue": "update_queue",
    "deleteQueue": "delete_queue",
    "getQueue": "get_queue",
    "listQueues": "list_queues",
    "addJobToQueue": "add_job_to_queue",
    "removeJobFromQueue": "remove_job_from_queue",
    "listJobsInQueue": "list_jobs_in_queue",
    "findQueuesForJob": "find_queues_for_job",
    # PlacesCache methods
    "addPlacesCache": "add_places_cache",
    "getPlacesCache": "get_places_cache",
    "getPlacesCacheByPlaceId": "get_places_cache_by_place_id",
    "updatePlacesCache": "update_places_cache",
    "deletePlacesCache": "delete_places_cache",
    "listPlacesCaches": "list_places_caches",
    # Receipt methods
    "addReceipt": "add_receipt",
    "addReceipts": "add_receipts",
    "updateReceipt": "update_receipt",
    "updateReceipts": "update_receipts",
    "deleteReceipt": "delete_receipt",
    "deleteReceipts": "delete_receipts",
    "getReceipt": "get_receipt",
    "getReceiptDetails": "get_receipt_details",
    "listReceipts": "list_receipts",
    "listReceiptDetails": "list_receipt_details",
    # ReceiptLine methods
    "addReceiptLine": "add_receipt_line",
    "addReceiptLines": "add_receipt_lines",
    "updateReceiptLine": "update_receipt_line",
    "updateReceiptLines": "update_receipt_lines",
    "deleteReceiptLine": "delete_receipt_line",
    "deleteReceiptLines": "delete_receipt_lines",
    "getReceiptLine": "get_receipt_line",
    "listReceiptLines": "list_receipt_lines",
    "listReceiptLinesFromReceipt": "list_receipt_lines_from_receipt",
    # ReceiptLetter methods
    "deleteReceiptLetter": "delete_receipt_letter",
    # Line methods
    "addLine": "add_line",
    "updateLine": "update_line",
    "deleteLine": "delete_line",
    "getLine": "get_line",
    "listLines": "list_lines",
}


def fix_method_names_in_file(file_path):
    """Fix method names in a single file."""
    with open(file_path, "r") as f:
        content = f.read()

    original_content = content

    # Replace method calls
    for old_name, new_name in METHOD_MAPPINGS.items():
        # Match method calls like dynamo_client.oldMethod(
        pattern = rf"\.{old_name}\("
        replacement = f".{new_name}("
        content = re.sub(pattern, replacement, content)

    # Only write if content changed
    if content != original_content:
        with open(file_path, "w") as f:
            f.write(content)
        print(f"Fixed method names in: {file_path}")
        return True
    return False


def main():
    # Get all test files in the integration directory
    test_dir = Path("/Users/tnorlund/GitHub/issue-121/receipt_dynamo/tests/integration")
    test_files = list(test_dir.glob("test_*.py"))

    fixed_count = 0
    for test_file in test_files:
        if fix_method_names_in_file(test_file):
            fixed_count += 1

    print(f"\nFixed {fixed_count} files out of {len(test_files)} total test files.")


if __name__ == "__main__":
    main()
