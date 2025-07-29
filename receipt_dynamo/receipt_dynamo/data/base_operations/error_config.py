"""
Error message configuration for DynamoDB operations.

This module centralizes all error message templates and patterns,
making it easy to maintain consistency across all operations.
"""

from typing import Any, Dict


class ErrorMessageConfig:
    """
    Configuration for entity-specific error messages.

    This centralizes all error message templates and patterns,
    making it easy to maintain consistency and add new entities.
    """

    # Operation-specific error message templates
    OPERATION_MESSAGES: Dict[str, str] = {
        "add": "Could not add {entity_type} to DynamoDB",
        "update": "Could not update {entity_type} in DynamoDB",
        "delete": "Could not delete {entity_type} from DynamoDB",
        "get": "Could not get {entity_type}",
        "list": "Could not list {entity_type} from DynamoDB",
    }

    # Validation error patterns
    VALIDATION_PATTERNS: Dict[str, Any] = {
        "with_operation": "Validation error in {operation}: {error}",
        "simple": "Validation error",
        "raw_with_transform": True,  # Apply parameter transformations
    }

    # Parameter validation messages
    PARAM_VALIDATION: Dict[str, str] = {
        "required": "{param} cannot be None",
        "type_mismatch": (
            "{param} must be an instance of the {class_name} class."
        ),
        "list_required": "{param} must be a list of {class_name} instances.",
        "list_type_mismatch": (
            "All {param} must be instances of the {class_name} class."
        ),
    }

    # Special parameter name mappings for backward compatibility
    PARAM_NAME_MAPPINGS: Dict[str, str] = {
        # Note: job_checkpoint mapping removed - handled in special cases
        # for different operations
        "ReceiptLabelAnalysis": "receipt_label_analysis",
        "ReceiptField": "ReceiptField",
        # Note: ReceiptWordLabel mapping removed - handled in special
        # cases for different operations
        "receiptField": "receiptField",
        # Note: receipt_field mapping removed - handled separately for
        # required vs type mismatch
        # Note: receipt_label_analyses mapping removed - use snake_case
        # consistently
        "ReceiptFields": "ReceiptFields",
        # Note: receipt_word_labels mapping removed - handled in special
        # cases for different operations
        "receiptFields": "ReceiptFields",
    }

    # Required parameter special messages for backward compatibility
    REQUIRED_PARAM_MESSAGES: Dict[str, str] = {
        "job_checkpoint": (
            "JobCheckpoint parameter is required and cannot be None."
        ),
        "receipt_label_analysis": "receipt_label_analysis cannot be None",
        "ReceiptLabelAnalysis": "ReceiptLabelAnalysis cannot be None",
        "receipt_field": "receipt_field cannot be None",
        "receipt_word_label": "receipt_word_label cannot be None",
        "analysis": "analysis cannot be None",
        "analyses": "analyses cannot be None",
        "categories": "categories cannot be None",
        "category": "category cannot be None",
        "checkpoint": "checkpoint cannot be None",
        "checkpoints": "checkpoints cannot be None",
        "dependency": "dependency cannot be None",
        "dependencies": "dependencies cannot be None",
        "export_job": "export_job cannot be None",
        "field": "field cannot be None",
        "fields": "fields cannot be None",
        "image": "image cannot be None",
        "images": "images cannot be None",
        "instance": "instance cannot be None",
        "instances": "instances cannot be None",
        "instance_job": "instance_job cannot be None",
        "item": "item cannot be None",
        "job": "job cannot be None",
        "jobs": "jobs cannot be None",
        "jobstatus": "jobstatus cannot be None",
        "job_resource": "job_resource cannot be None",
        "label": "label cannot be None",
        "labels": "labels cannot be None",
        "letter": "letter cannot be None",
        "letters": "letters cannot be None",
        "line": "line cannot be None",
        "lines": "lines cannot be None",
        "log": "log cannot be None",
        "logs": "logs cannot be None",
        "metric": "metric cannot be None",
        "metrics": "metrics cannot be None",
        "ocr_job": "ocr_job cannot be None",
        "queue": "queue cannot be None",
        "queues": "queues cannot be None",
        "queue_job": "queue_job cannot be None",
        "receipt": "receipt cannot be None",
        "receipts": "receipts cannot be None",
        "resource": "resource cannot be None",
        "resources": "resources cannot be None",
        "result": "result cannot be None",
        "results": "results cannot be None",
        "status": "status cannot be None",
        "summary": "summary cannot be None",
        "validation": "validation cannot be None",
        "validations": "validations cannot be None",
        "word": "word cannot be None",
        "words": "words cannot be None",
    }

    # List required special messages (when parameter is not a list)
    LIST_REQUIRED_MESSAGES: Dict[str, str] = {
        "images": "images must be a list of Image instances.",
        "jobs": "jobs must be a list of Job instances.",
        "receipts": "receipts must be a list of Receipt instances.",
        "fields": "fields must be a list of ReceiptField instances.",
        "labels": "labels must be a list of ReceiptWordLabel instances.",
        "checkpoints": (
            "checkpoints must be a list of JobCheckpoint instances."
        ),
        "analyses": (
            "analyses must be a list of ReceiptLineItemAnalysis instances."
        ),
        "instances": "instances must be a list of Instance instances.",
        "items": "items must be a list of PlacesCache instances.",
        "letters": "letters must be a list of ReceiptLetter instances.",
        "lines": "lines must be a list of Line instances.",
        "logs": "logs must be a list of JobLog instances.",
        "metrics": "metrics must be a list of JobMetric instances.",
        "queues": "queues must be a list of Queue instances.",
        "resources": "resources must be a list of JobResource instances.",
        "results": (
            "results must be a list of ReceiptValidationResult instances."
        ),
        "validations": (
            "validations must be a list of ReceiptChatGPTValidation "
            "instances."
        ),
        "words": "words must be a list of ReceiptWord instances.",
        "categories": (
            "categories must be a list of ReceiptValidationCategory "
            "instances."
        ),
        "dependencies": (
            "dependencies must be a list of JobDependency instances."
        ),
    }

    # Type mismatch special messages for backward compatibility
    TYPE_MISMATCH_MESSAGES: Dict[str, str] = {
        "job_checkpoint": (
            "job_checkpoint must be an instance of the JobCheckpoint class."
        ),
        "receipt_label_analysis": (
            "receipt_label_analysis must be an instance of the "
            "ReceiptLabelAnalysis class."
        ),
        "receipt_field": (
            "receiptField must be an instance of the ReceiptField class."
        ),
        "receipt_word_label": (
            "receipt_word_label must be an instance of the "
            "ReceiptWordLabel class."
        ),
        "ReceiptLabelAnalysis": (
            "ReceiptLabelAnalysis must be an instance of the "
            "ReceiptLabelAnalysis class."
        ),
        "ReceiptField": (
            "receiptField must be an instance of the ReceiptField class."
        ),
        "ReceiptWordLabel": (
            "ReceiptWordLabel must be an instance of the ReceiptWordLabel "
            "class."
        ),
        "jobstatus": "job_status must be an instance of the JobStatus class.",
        "job_resource": (
            "job_resource must be an instance of the JobResource class."
        ),
        "analyses": (
            "analyses must be a list of ReceiptLineItemAnalysis instances."
        ),
        "categories": (
            "categories must be a list of ReceiptValidationCategory "
            "instances."
        ),
        "checkpoints": (
            "checkpoints must be a list of JobCheckpoint instances."
        ),
        "dependencies": (
            "dependencies must be a list of JobDependency instances."
        ),
        "fields": "fields must be a list of ReceiptField instances.",
        "images": "images must be a list of Image instances.",
        "instances": "instances must be a list of Instance instances.",
        "items": "items must be a list of PlacesCache instances.",
        "jobs": "jobs must be a list of Job instances.",
        "labels": "labels must be a list of ReceiptWordLabel instances.",
        "letters": "letters must be a list of ReceiptLetter instances.",
        "lines": "lines must be a list of Line instances.",
        "logs": "logs must be a list of JobLog instances.",
        "metrics": "metrics must be a list of JobMetric instances.",
        "queues": "queues must be a list of Queue instances.",
        "receipts": "receipts must be a list of Receipt instances.",
        "resources": "resources must be a list of JobResource instances.",
        "results": (
            "results must be a list of ReceiptValidationResult instances."
        ),
        "validations": (
            "validations must be a list of ReceiptChatGPTValidation "
            "instances"
        ),
        "words": "words must be a list of ReceiptWord instances.",
        "receiptFields": (
            "ReceiptFields must be a list of ReceiptField instances."
        ),
        "receipt_word_labels": (
            "receipt_word_labels must be a list of ReceiptWordLabel "
            "instances."
        ),
    }

    # Entity not found message patterns
    ENTITY_NOT_FOUND_PATTERNS: Dict[str, str] = {
        "default": "Entity does not exist: {entity_type}",
        "job": "Job with job id {job_id} does not exist",
        "instance": "Instance with instance id {instance_id} does not exist",
        "queue": "Queue {queue_name} not found",
        "receipt": "Receipt with receipt_id {receipt_id} does not exist",
        "image": "Image with image_id {image_id} does not exist",
        "receipt_field": "does not exist",
        "receipt_word_label": "Entity does not exist: ReceiptWordLabel",
        "queue_job": "Entity does not exist: QueueJob",
    }

    # Entity already exists message patterns
    ENTITY_EXISTS_PATTERNS: Dict[str, str] = {
        "default": "already exists",
        "job": "Entity already exists: Job",
        "instance": "Entity already exists: Instance",
        "queue": "Queue {queue_name} already exists",
        "receipt": "Entity already exists: Receipt",
        "image": "Entity already exists: Image",
        "ocr_job": "Entity already exists: OCRJob",
        "job_checkpoint": (
            "Entity already exists: JobCheckpoint with job_id={job_id}"
        ),
        "job_resource": (
            "JobResource with resource ID {resource_id} for job {job_id} already exists"
        ),
        "receipt_chatgpt_validation": (
            "Entity already exists: ReceiptChatGPTValidation with "
            "receipt_id={receipt_id}"
        ),
        "receipt_field": "already exists",
        "receipt_word_label": "already exists",
    }

    # Batch operation error messages
    BATCH_ERROR_MESSAGES: Dict[str, str] = {
        "update_receipt_line_item_analyses": (
            "One or more receipt line item analyses do not exist"
        ),
        "update_receipt_label_analyses": (
            "One or more receipt label analyses do not exist"
        ),
        "delete_receipt_label_analyses": (
            "One or more receipt label analyses do not exist"
        ),
        "update_receipt_word_labels": (
            "One or more receipt word labels do not exist"
        ),
        "delete_receipt_word_labels": (
            "One or more receipt word labels do not exist"
        ),
        "update_receipt_chatgpt_validations": (
            "One or more ReceiptChatGPTValidations do not exist"
        ),
        "update_receipt_fields": "One or more items do not exist",
        "delete_receipt_fields": "One or more items do not exist",
    }
