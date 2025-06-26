import json
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

# Import the ReceiptValidationSummary class
from receipt_dynamo import (ReceiptValidationCategory, ReceiptValidationResult,
                            ReceiptValidationSummary)

from .metadata import MetadataMixin


class ValidationResultType(str, Enum):
    """Types of validation results."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    SUCCESS = "success"


class ValidationStatus(str, Enum):
    """Overall validation status."""

    VALID = "valid"
    INVALID = "invalid"
    NEEDS_REVIEW = "needs_review"
    INCOMPLETE = "incomplete"


@dataclass
class ValidationResult:
    """
    Represents a single validation check result.

    Instead of using a confidence score, this class includes detailed reasoning
    explaining why the validation passed or failed.
    """

    type: ValidationResultType
    message: str
    reasoning: str
    field: Optional[str] = None
    expected_value: Optional[Any] = None
    actual_value: Optional[Any] = None
    metadata: Dict = dataclass_field(default_factory=dict)


@dataclass
class FieldValidation:
    """
    Represents validation results for a specific field category.

    Field categories include business identity, address, phone number, etc.
    """

    field_category: str
    results: List[ValidationResult]
    status: ValidationStatus = ValidationStatus.VALID
    reasoning: str = ""
    metadata: Dict = dataclass_field(default_factory=dict)

    def __post_init__(self) -> None:
        # Determine overall status based on the validation results
        if not self.results:
            self.status = ValidationStatus.INCOMPLETE
        elif any(r.type == ValidationResultType.ERROR for r in self.results):
            self.status = ValidationStatus.INVALID
        elif any(r.type == ValidationResultType.WARNING for r in self.results):
            self.status = ValidationStatus.NEEDS_REVIEW
        else:
            self.status = ValidationStatus.VALID

        # Generate reasoning if not provided
        if not self.reasoning:
            self.reasoning = self._generate_reasoning()

    def _generate_reasoning(self) -> str:
        """Generate reasoning based on validation results."""
        if not self.results:
            return f"No validation performed for {self.field_category}"

        error_count = sum(
            1 for r in self.results if r.type == ValidationResultType.ERROR
        )
        warning_count = sum(
            1 for r in self.results if r.type == ValidationResultType.WARNING
        )
        info_count = sum(1 for r in self.results if r.type == ValidationResultType.INFO)
        success_count = sum(
            1 for r in self.results if r.type == ValidationResultType.SUCCESS
        )

        result_parts = []
        if error_count:
            result_parts.append(f"{error_count} errors")
        if warning_count:
            result_parts.append(f"{warning_count} warnings")
        if info_count:
            result_parts.append(f"{info_count} informational items")
        if success_count:
            result_parts.append(f"{success_count} successful checks")

        status_text = {
            ValidationStatus.VALID: "PASSED",
            ValidationStatus.INVALID: "FAILED",
            ValidationStatus.NEEDS_REVIEW: "NEEDS REVIEW",
            ValidationStatus.INCOMPLETE: "INCOMPLETE",
        }

        # Include some specific reasoning from the results
        specific_reasons = []
        for r in self.results:
            if r.type in [
                ValidationResultType.ERROR,
                ValidationResultType.WARNING,
            ]:
                specific_reasons.append(r.reasoning)

        reasoning = f"{self.field_category} validation {status_text[self.status]} with {', '.join(result_parts)}"

        if specific_reasons:
            reasoning += f". Issues found: {'; '.join(specific_reasons[:3])}"
            if len(specific_reasons) > 3:
                reasoning += f" and {len(specific_reasons) - 3} more"

        return reasoning

    def add_result(self, result: ValidationResult) -> None:
        """Add a validation result and update the status."""
        self.results.append(result)

        # Update status based on the new result
        if result.type == ValidationResultType.ERROR:
            self.status = ValidationStatus.INVALID
        elif (
            result.type == ValidationResultType.WARNING
            and self.status != ValidationStatus.INVALID
        ):
            self.status = ValidationStatus.NEEDS_REVIEW

        # Update reasoning
        self.reasoning = self._generate_reasoning()


@dataclass
class ValidationAnalysis(MetadataMixin):
    """
    Comprehensive analysis of receipt validation results.

    This class encapsulates all validation checks performed on a receipt, organized by
    validation category. Instead of confidence scores, it provides detailed reasoning
    about the validation process and results.
    """

    business_identity: FieldValidation = dataclass_field(
        default_factory=lambda: FieldValidation(
            field_category="Business Identity", results=[]
        )
    )
    address_verification: FieldValidation = dataclass_field(
        default_factory=lambda: FieldValidation(
            field_category="Address Verification", results=[]
        )
    )
    phone_validation: FieldValidation = dataclass_field(
        default_factory=lambda: FieldValidation(
            field_category="Phone Validation", results=[]
        )
    )
    hours_verification: FieldValidation = dataclass_field(
        default_factory=lambda: FieldValidation(
            field_category="Hours Verification", results=[]
        )
    )
    cross_field_consistency: FieldValidation = dataclass_field(
        default_factory=lambda: FieldValidation(
            field_category="Cross-Field Consistency", results=[]
        )
    )
    line_item_validation: FieldValidation = dataclass_field(
        default_factory=lambda: FieldValidation(
            field_category="Line Item Validation", results=[]
        )
    )

    overall_status: ValidationStatus = ValidationStatus.VALID
    overall_reasoning: str = ""
    validation_timestamp: datetime = dataclass_field(default_factory=datetime.now)
    prompt_template: Optional[str] = None
    response_template: Optional[str] = None
    metadata: Dict = dataclass_field(default_factory=dict)
    timestamp_added: Optional[str] = None
    timestamp_updated: Optional[str] = None
    receipt_id: Optional[int] = None
    image_id: Optional[str] = None

    def __post_init__(self) -> None:
        # Update overall_status based on individual field validations
        self._update_overall_status()

        # Generate overall reasoning if not provided
        if not self.overall_reasoning:
            self.overall_reasoning = self._generate_overall_reasoning()

        # Initialize metadata
        self.initialize_metadata()

        # Add validation-specific metrics
        self.add_processing_metric("validation_status", self.overall_status)

        field_counts = {
            "business_identity": len(self.business_identity.results),
            "address_verification": len(self.address_verification.results),
            "phone_validation": len(self.phone_validation.results),
            "hours_verification": len(self.hours_verification.results),
            "cross_field_consistency": len(self.cross_field_consistency.results),
            "line_item_validation": len(self.line_item_validation.results),
        }
        self.add_processing_metric("validation_counts", field_counts)

        result_types = {
            "error": sum(
                sum(1 for r in v.results if r.type == ValidationResultType.ERROR)
                for v in self._get_field_validations()
            ),
            "warning": sum(
                sum(1 for r in v.results if r.type == ValidationResultType.WARNING)
                for v in self._get_field_validations()
            ),
            "info": sum(
                sum(1 for r in v.results if r.type == ValidationResultType.INFO)
                for v in self._get_field_validations()
            ),
            "success": sum(
                sum(1 for r in v.results if r.type == ValidationResultType.SUCCESS)
                for v in self._get_field_validations()
            ),
        }
        self.add_processing_metric("result_types", result_types)

        # Add to history based on validation status
        if self.overall_status != ValidationStatus.VALID:
            self.add_history_event(
                f"validation_{self.overall_status.lower()}",
                {
                    "error_count": result_types["error"],
                    "warning_count": result_types["warning"],
                },
            )

    def _get_field_validations(self) -> List[FieldValidation]:
        """Get all field validations as a list."""
        return [
            self.business_identity,
            self.address_verification,
            self.phone_validation,
            self.hours_verification,
            self.cross_field_consistency,
            self.line_item_validation,
        ]

    def _update_overall_status(self) -> None:
        """Update the overall validation status based on field validations."""
        field_validations = [
            self.business_identity,
            self.address_verification,
            self.phone_validation,
            self.hours_verification,
            self.cross_field_consistency,
            self.line_item_validation,
        ]

        if any(v.status == ValidationStatus.INVALID for v in field_validations):
            self.overall_status = ValidationStatus.INVALID
        elif any(v.status == ValidationStatus.NEEDS_REVIEW for v in field_validations):
            self.overall_status = ValidationStatus.NEEDS_REVIEW
        elif all(v.status == ValidationStatus.INCOMPLETE for v in field_validations):
            self.overall_status = ValidationStatus.INCOMPLETE
        else:
            self.overall_status = ValidationStatus.VALID

    def _generate_overall_reasoning(self) -> str:
        """Generate overall reasoning based on field validations."""
        field_validations = [
            self.business_identity,
            self.address_verification,
            self.phone_validation,
            self.hours_verification,
            self.cross_field_consistency,
            self.line_item_validation,
        ]

        active_validations = [v for v in field_validations if v.results]
        if not active_validations:
            return "No validation checks performed"

        # Count validation results by type
        error_count = sum(
            sum(1 for r in v.results if r.type == ValidationResultType.ERROR)
            for v in active_validations
        )
        warning_count = sum(
            sum(1 for r in v.results if r.type == ValidationResultType.WARNING)
            for v in active_validations
        )

        # Generate overall status description
        status_text = {
            ValidationStatus.VALID: "valid",
            ValidationStatus.INVALID: "invalid",
            ValidationStatus.NEEDS_REVIEW: "needs review",
            ValidationStatus.INCOMPLETE: "incomplete validation",
        }

        reasoning = f"Receipt validation determined the receipt is {status_text[self.overall_status]}"

        if error_count or warning_count:
            reasoning += f" with {error_count} errors and {warning_count} warnings"

        # Add field-specific reasoning for problem areas
        problem_fields = [
            v
            for v in active_validations
            if v.status in [ValidationStatus.INVALID, ValidationStatus.NEEDS_REVIEW]
        ]
        if problem_fields:
            field_problems = [
                f"{v.field_category}: {v.reasoning}" for v in problem_fields
            ]
            reasoning += f". Problem areas include: {'; '.join(field_problems[:3])}"
            if len(field_problems) > 3:
                reasoning += f" and {len(field_problems) - 3} more"

        return reasoning

    def add_result(self, category: str, result: ValidationResult) -> None:
        """
        Add a validation result to the appropriate category.

        Args:
            category (str): The validation category (e.g., "business_identity")
            result (ValidationResult): The validation result to add
        """
        field_map = {
            "business_identity": self.business_identity,
            "address_verification": self.address_verification,
            "phone_validation": self.phone_validation,
            "hours_verification": self.hours_verification,
            "cross_field_consistency": self.cross_field_consistency,
            "line_item_validation": self.line_item_validation,
        }

        if category in field_map:
            field_map[category].add_result(result)
            self._update_overall_status()
            self.overall_reasoning = self._generate_overall_reasoning()

    def get_validation_summary(self) -> Dict:
        """
        Get a summary of the validation results.

        Returns:
            Dict: Summary of validation results by category and overall status
        """
        return {
            "overall_status": self.overall_status,
            "overall_reasoning": self.overall_reasoning,
            "categories": {
                "business_identity": {
                    "status": self.business_identity.status,
                    "results_count": len(self.business_identity.results),
                },
                "address_verification": {
                    "status": self.address_verification.status,
                    "results_count": len(self.address_verification.results),
                },
                "phone_validation": {
                    "status": self.phone_validation.status,
                    "results_count": len(self.phone_validation.results),
                },
                "hours_verification": {
                    "status": self.hours_verification.status,
                    "results_count": len(self.hours_verification.results),
                },
                "cross_field_consistency": {
                    "status": self.cross_field_consistency.status,
                    "results_count": len(self.cross_field_consistency.results),
                },
                "line_item_validation": {
                    "status": self.line_item_validation.status,
                    "results_count": len(self.line_item_validation.results),
                },
            },
            "timestamp": self.validation_timestamp.isoformat(),
        }

    def to_dynamo(self) -> Dict:
        """
        Convert the ValidationAnalysis to a DynamoDB-compatible dictionary.

        Returns:
            Dict: A dictionary representation for DynamoDB
        """
        # Get base metadata fields
        result = super().to_dict()

        # Add class-specific fields
        field_validations = {}
        for field_name, field_validation in [
            ("business_identity", self.business_identity),
            ("address_verification", self.address_verification),
            ("phone_validation", self.phone_validation),
            ("hours_verification", self.hours_verification),
            ("cross_field_consistency", self.cross_field_consistency),
            ("line_item_validation", self.line_item_validation),
        ]:
            field_validations[field_name] = {
                "status": field_validation.status,
                "reasoning": field_validation.reasoning,
                "results": [
                    {
                        "type": r.type,
                        "message": r.message,
                        "reasoning": r.reasoning,
                        "field": r.field,
                        "expected_value": (
                            str(r.expected_value)
                            if r.expected_value is not None
                            else None
                        ),
                        "actual_value": (
                            str(r.actual_value) if r.actual_value is not None else None
                        ),
                        "metadata": r.metadata,
                    }
                    for r in field_validation.results
                ],
            }

        result.update(
            {
                **field_validations,
                "overall_status": self.overall_status,
                "overall_reasoning": self.overall_reasoning,
                "validation_timestamp": self.validation_timestamp.isoformat(),
                "prompt_template": self.prompt_template,
                "response_template": self.response_template,
            }
        )

        return result

    def to_dynamo_validation_summary(self) -> ReceiptValidationSummary:
        """
        Convert ValidationAnalysis to a ReceiptValidationSummary.

        This method creates a ReceiptValidationSummary object which contains the overall
        validation status and a summary of field validations.

        Returns:
            ReceiptValidationSummary: A ReceiptValidationSummary instance
        """
        if self.receipt_id is None or self.image_id is None:
            raise ValueError(
                "receipt_id and image_id must be set on the ValidationAnalysis instance"
            )

        # Generate field summary with counts and statuses for each field
        field_summary = {}

        for field_name, field_validation in [
            ("business_identity", self.business_identity),
            ("address_verification", self.address_verification),
            ("phone_validation", self.phone_validation),
            ("hours_verification", self.hours_verification),
            ("cross_field_consistency", self.cross_field_consistency),
            ("line_item_validation", self.line_item_validation),
        ]:
            # Count results by type
            error_count = sum(
                1
                for r in field_validation.results
                if r.type == ValidationResultType.ERROR
            )
            warning_count = sum(
                1
                for r in field_validation.results
                if r.type == ValidationResultType.WARNING
            )
            info_count = sum(
                1
                for r in field_validation.results
                if r.type == ValidationResultType.INFO
            )
            success_count = sum(
                1
                for r in field_validation.results
                if r.type == ValidationResultType.SUCCESS
            )

            # Determine if there are errors or warnings - convert to integers for DynamoDB compatibility
            has_errors = 1 if error_count > 0 else 0
            has_warnings = 1 if warning_count > 0 else 0

            field_summary[field_name] = {
                "status": field_validation.status.value,
                "count": len(field_validation.results),
                "has_errors": has_errors,  # Now an integer (1 or 0) instead of boolean
                "has_warnings": has_warnings,  # Now an integer (1 or 0) instead of boolean
                "error_count": error_count,
                "warning_count": warning_count,
                "info_count": info_count,
                "success_count": success_count,
            }

        # Prepare timestamp values
        timestamp_added = None
        if self.timestamp_added:
            if isinstance(self.timestamp_added, str):
                timestamp_added = datetime.fromisoformat(self.timestamp_added)
            else:
                timestamp_added = self.timestamp_added
        else:
            timestamp_added = datetime.now()

        timestamp_updated = None
        if self.timestamp_updated:
            if isinstance(self.timestamp_updated, str):
                timestamp_updated = datetime.fromisoformat(self.timestamp_updated)
            else:
                timestamp_updated = self.timestamp_updated

        # Extract metadata
        metadata = self.metadata.copy() if self.metadata else {}

        # Create a ReceiptValidationSummary instance
        validation_summary = ReceiptValidationSummary(
            receipt_id=self.receipt_id,
            image_id=self.image_id,
            overall_status=self.overall_status.value,
            overall_reasoning=self.overall_reasoning,
            field_summary=field_summary,
            validation_timestamp=self.validation_timestamp.isoformat(),
            version=metadata.get("version", "1.0.0"),
            metadata=metadata,
            timestamp_added=timestamp_added,
            timestamp_updated=timestamp_updated,
        )

        # Return the summary object
        return validation_summary

    def to_dynamo_validation_categories(
        self,
    ) -> List[ReceiptValidationCategory]:
        """
        Convert ValidationAnalysis to a list of ReceiptValidationCategory instances.

        This method creates category items containing detailed validation results for each field.

        Returns:
            List[ReceiptValidationCategory]: A list of ReceiptValidationCategory instances
        """
        if self.receipt_id is None or self.image_id is None:
            raise ValueError(
                "receipt_id and image_id must be set on the ValidationAnalysis instance"
            )

        # Extract metadata and prepare timestamps
        metadata = self.metadata.copy() if self.metadata else {}
        validation_timestamp = self.validation_timestamp.isoformat()

        # List to store all category items
        categories = []

        # Create a category item for each field validation
        for field_name, field_validation in [
            ("business_identity", self.business_identity),
            ("address_verification", self.address_verification),
            ("phone_validation", self.phone_validation),
            ("hours_verification", self.hours_verification),
            ("cross_field_consistency", self.cross_field_consistency),
            ("line_item_validation", self.line_item_validation),
        ]:
            # Count results by type
            error_count = sum(
                1
                for r in field_validation.results
                if r.type == ValidationResultType.ERROR
            )
            warning_count = sum(
                1
                for r in field_validation.results
                if r.type == ValidationResultType.WARNING
            )
            info_count = sum(
                1
                for r in field_validation.results
                if r.type == ValidationResultType.INFO
            )
            success_count = sum(
                1
                for r in field_validation.results
                if r.type == ValidationResultType.SUCCESS
            )

            # Create result summary
            result_summary = {
                "error_count": error_count,
                "warning_count": warning_count,
                "info_count": info_count,
                "success_count": success_count,
                "total_count": len(field_validation.results),
            }

            # Create a ReceiptValidationCategory instance
            category = ReceiptValidationCategory(
                receipt_id=self.receipt_id,
                image_id=self.image_id,
                field_name=field_name,
                field_category=field_validation.field_category,
                status=field_validation.status.value,
                reasoning=field_validation.reasoning,
                result_summary=result_summary,
                validation_timestamp=validation_timestamp,
                metadata=metadata,
            )

            # Add the category to the list
            categories.append(category)

        return categories

    def to_dynamo_validation_results(self) -> List[ReceiptValidationResult]:
        """
        Convert ValidationAnalysis to a list of ReceiptValidationResult instances.

        This method creates detailed result items for each validation check in each category.

        Returns:
            List[ReceiptValidationResult]: A list of ReceiptValidationResult instances
        """
        if self.receipt_id is None or self.image_id is None:
            raise ValueError(
                "receipt_id and image_id must be set on the ValidationAnalysis instance"
            )

        # Extract metadata and prepare timestamp
        metadata = self.metadata.copy() if self.metadata else {}
        validation_timestamp = self.validation_timestamp.isoformat()

        # List to store all result items
        results = []

        # Process each field validation
        for field_name, field_validation in [
            ("business_identity", self.business_identity),
            ("address_verification", self.address_verification),
            ("phone_validation", self.phone_validation),
            ("hours_verification", self.hours_verification),
            ("cross_field_consistency", self.cross_field_consistency),
            ("line_item_validation", self.line_item_validation),
        ]:
            # Convert each validation result in this field
            for result_index, validation_result in enumerate(field_validation.results):
                # Create the ReceiptValidationResult instance
                result_item = ReceiptValidationResult(
                    receipt_id=self.receipt_id,
                    image_id=self.image_id,
                    field_name=field_name,
                    result_index=result_index,
                    type=validation_result.type.value,
                    message=validation_result.message,
                    reasoning=validation_result.reasoning,
                    field=validation_result.field,
                    expected_value=(
                        str(validation_result.expected_value)
                        if validation_result.expected_value is not None
                        else None
                    ),
                    actual_value=(
                        str(validation_result.actual_value)
                        if validation_result.actual_value is not None
                        else None
                    ),
                    validation_timestamp=validation_timestamp,
                    metadata=validation_result.metadata,
                )

                # Add the result to the list
                results.append(result_item)

        return results

    @classmethod
    def from_dynamo(cls, data: Dict) -> "ValidationAnalysis":
        """
        Create a ValidationAnalysis instance from DynamoDB data.

        Args:
            data (Dict): Data from DynamoDB

        Returns:
            ValidationAnalysis: A new instance populated with the DynamoDB data
        """
        # Extract metadata fields
        metadata_fields = MetadataMixin.from_dict(data)

        # Process field validations
        field_validations = {}
        for category in [
            "business_identity",
            "address_verification",
            "phone_validation",
            "hours_verification",
            "cross_field_consistency",
            "line_item_validation",
        ]:
            if category in data:
                category_data = data[category]
                results = []

                for result_data in category_data.get("results", []):
                    results.append(
                        ValidationResult(
                            type=result_data.get("type", ValidationResultType.INFO),
                            message=result_data.get("message", ""),
                            reasoning=result_data.get("reasoning", ""),
                            field=result_data.get("field"),
                            expected_value=result_data.get("expected_value"),
                            actual_value=result_data.get("actual_value"),
                            metadata=result_data.get("metadata", {}),
                        )
                    )

                field_validations[category] = FieldValidation(
                    field_category=category.replace("_", " ").title(),
                    results=results,
                    status=category_data.get("status", ValidationStatus.VALID),
                    reasoning=category_data.get("reasoning", ""),
                )

        # Create validation analysis
        result = cls(
            **field_validations,
            overall_status=data.get("overall_status", ValidationStatus.VALID),
            overall_reasoning=data.get("overall_reasoning", ""),
            prompt_template=data.get("prompt_template"),
            response_template=data.get("response_template"),
            **metadata_fields,
        )

        # Parse validation timestamp
        if "validation_timestamp" in data:
            try:
                result.validation_timestamp = datetime.fromisoformat(
                    data["validation_timestamp"]
                )
            except (ValueError, TypeError):
                pass

        return result

    @classmethod
    def from_dynamo_items(
        cls,
        summary: ReceiptValidationSummary,
        categories: List[ReceiptValidationCategory],
        results: List[ReceiptValidationResult],
    ) -> "ValidationAnalysis":
        """
        Reconstruct a ValidationAnalysis from separate DynamoDB items.

        This method combines data from ReceiptValidationSummary, ReceiptValidationCategory,
        and ReceiptValidationResult objects to create a complete ValidationAnalysis.

        Args:
            summary: The ReceiptValidationSummary object
            categories: List of ReceiptValidationCategory objects
            results: List of ReceiptValidationResult objects

        Returns:
            ValidationAnalysis: A new instance populated with data from the DynamoDB items
        """
        # Initialize the field validations dict
        field_validations = {}

        # Extract common information from summary
        receipt_id = summary.receipt_id
        image_id = summary.image_id
        overall_status = summary.overall_status
        overall_reasoning = summary.overall_reasoning
        validation_timestamp = summary.validation_timestamp
        metadata = summary.metadata.copy() if summary.metadata else {}
        version = summary.version

        # Process categories and organize results by field name
        results_by_field = {}
        for result in results:
            if result.field_name not in results_by_field:
                results_by_field[result.field_name] = []
            results_by_field[result.field_name].append(result)

        # Process each field category
        for category in categories:
            field_name = category.field_name
            field_category = category.field_category
            status = category.status
            reasoning = category.reasoning

            # Get results for this field
            field_results = results_by_field.get(field_name, [])

            # Sort results by index to maintain original order
            field_results.sort(key=lambda r: r.result_index)

            # Convert ReceiptValidationResult objects to ValidationResult objects
            validation_results = []
            for result in field_results:
                # Convert type string to ValidationResultType enum
                try:
                    result_type = ValidationResultType(result.type)
                except ValueError:
                    result_type = ValidationResultType.INFO

                # Create ValidationResult
                validation_result = ValidationResult(
                    type=result_type,
                    message=result.message,
                    reasoning=result.reasoning,
                    field=result.field,
                    expected_value=result.expected_value,
                    actual_value=result.actual_value,
                    metadata=(result.metadata if hasattr(result, "metadata") else {}),
                )
                validation_results.append(validation_result)

            # Create FieldValidation
            try:
                field_status = ValidationStatus(status)
            except ValueError:
                field_status = ValidationStatus.VALID

            field_validations[field_name] = FieldValidation(
                field_category=field_category,
                results=validation_results,
                status=field_status,
                reasoning=reasoning,
            )

        # Create validation analysis with all the gathered information
        analysis = cls(
            receipt_id=receipt_id,
            image_id=image_id,
            overall_status=(
                ValidationStatus(overall_status)
                if overall_status
                else ValidationStatus.VALID
            ),
            overall_reasoning=overall_reasoning,
            **field_validations,
            metadata=metadata,
        )

        # Set timestamp fields
        if validation_timestamp:
            try:
                analysis.validation_timestamp = datetime.fromisoformat(
                    validation_timestamp
                )
            except (ValueError, TypeError):
                pass

        # Set timestamp_added and timestamp_updated if available
        if hasattr(summary, "timestamp_added") and summary.timestamp_added:
            analysis.timestamp_added = summary.timestamp_added

        if hasattr(summary, "timestamp_updated") and summary.timestamp_updated:
            analysis.timestamp_updated = summary.timestamp_updated

        # Copy any other relevant fields from the summary
        if hasattr(summary, "prompt_template") and summary.prompt_template:
            analysis.prompt_template = summary.prompt_template

        if hasattr(summary, "response_template") and summary.response_template:
            analysis.response_template = summary.response_template

        return analysis
