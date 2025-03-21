"""
DynamoDB operations for Receipt Analysis models.

This module provides functions for saving, retrieving, and querying analysis
results in DynamoDB, leveraging the to_dynamo/from_dynamo methods in the model
classes.

Each function takes a DynamoClient instance to ensure consistent use of the
existing wrapper around boto3.
"""

import logging
from typing import List, Dict, Any, Optional, Union, Type, Tuple
from ..models.label import LabelAnalysis, WordLabel
from ..models.structure import StructureAnalysis
from ..models.line_item import LineItemAnalysis
from ..models.validation import ValidationAnalysis
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities.receipt_analysis import ReceiptAnalysis

# Configure logging
logger = logging.getLogger(__name__)

# ======================== Label Analysis Operations ========================


def save_label_analysis(label_analysis: LabelAnalysis, client: DynamoClient) -> bool:
    """Save a LabelAnalysis object to DynamoDB.

    Args:
        label_analysis: The LabelAnalysis object to save
        client: DynamoClient instance

    Returns:
        Boolean indicating success
    """
    try:
        # Type checking
        if not isinstance(label_analysis, LabelAnalysis):
            logger.error(
                f"Expected LabelAnalysis object, got {type(label_analysis).__name__}"
            )
            return False

        if not isinstance(client, DynamoClient):
            logger.error(f"Expected DynamoClient object, got {type(client).__name__}")
            return False

        if not label_analysis:
            logger.warning("Cannot save empty label analysis")
            return False

        # Convert the model to DynamoDB format
        receipt_label_analysis = label_analysis.to_dynamo()

        # Use the DynamoClient's proper method to save the item
        client.addReceiptLabelAnalysis(receipt_label_analysis)

        logger.info(
            f"Saved label analysis for receipt {label_analysis.receipt_id}, image {label_analysis.image_id}"
        )
        return True
    except Exception as e:
        logger.error(f"Error saving label analysis: {str(e)}")
        return False


def get_label_analysis(
    receipt_id: str, image_id: str, client: DynamoClient
) -> Optional[LabelAnalysis]:
    """
    DEPRECATED: Use client.getReceiptAnalysis(image_id, receipt_id).label_analysis instead.

    Retrieve a LabelAnalysis object from DynamoDB.

    Args:
        receipt_id: The receipt ID
        image_id: The image ID
        client: DynamoClient instance

    Returns:
        LabelAnalysis object or None if not found
    """
    import warnings

    warnings.warn(
        "get_label_analysis is deprecated. Use client.getReceiptAnalysis(image_id, receipt_id).label_analysis instead.",
        DeprecationWarning,
        stacklevel=2,
    )

    try:
        # Type checking
        if not isinstance(client, DynamoClient):
            logger.error(f"Expected DynamoClient object, got {type(client).__name__}")
            return None

        # Convert receipt_id to int if it's a string (client method requires int)
        receipt_id_int = int(receipt_id) if isinstance(receipt_id, str) else receipt_id

        # Use the client's dedicated method to get the receipt label analysis
        # This will raise ValueError if not found
        receipt_label_analysis = client.getReceiptLabelAnalysis(
            image_id=image_id, receipt_id=receipt_id_int
        )

        # If we get here, we have a valid receipt_label_analysis
        logger.info(
            f"Retrieved label analysis for receipt {receipt_id}, image {image_id}"
        )

        # Use the updated from_dynamo method that accepts a ReceiptLabelAnalysis object directly
        label_analysis = LabelAnalysis.from_dynamo(receipt_label_analysis)

        return label_analysis

    except ValueError as e:
        # Only handle "does not exist" errors - re-raise other ValueErrors
        if "does not exist" in str(e).lower():
            logger.info(
                f"No label analysis found for receipt {receipt_id}, image {image_id}"
            )
            return None
        else:
            # Re-raise other ValueErrors
            logger.error(f"ValueError in get_label_analysis: {str(e)}")
            raise
    except Exception as e:
        # Log other exceptions but still re-raise them
        logger.error(f"Error retrieving label analysis: {str(e)}")
        raise


def list_label_analyses_by_receipt(
    receipt_id: str, client, limit: Optional[int] = None
) -> List[LabelAnalysis]:
    """List all label analyses for a specific receipt.

    Args:
        receipt_id: Receipt ID
        client: DynamoClient instance
        limit: Maximum number of items to return

    Returns:
        List of LabelAnalysis objects
    """
    try:
        # This uses a GSI with RECEIPT# as PK and begins_with for SK
        # Assuming such a GSI exists; may need adjustment based on actual table design
        items = client.query(
            key_condition={
                "PK": f"RECEIPT#{receipt_id}",
                "SK": {"begins_with": "ANALYSIS#LABEL"},
            },
            index_name="ReceiptIndex",
            limit=limit,
        )

        # Convert items to LabelAnalysis objects
        analyses = [LabelAnalysis.from_dynamo(item) for item in items]

        logger.info(
            f"Retrieved {len(analyses)} label analyses for receipt {receipt_id}"
        )
        return analyses
    except Exception as e:
        logger.error(f"Error listing label analyses for receipt {receipt_id}: {str(e)}")
        return []


# ======================== Structure Analysis Operations ========================


def save_structure_analysis(structure_analysis, client) -> bool:
    """Save a StructureAnalysis object to DynamoDB.

    Args:
        structure_analysis: The StructureAnalysis object to save
        client: DynamoClient instance

    Returns:
        Boolean indicating success
    """
    try:
        if not structure_analysis:
            logger.warning("Cannot save empty structure analysis")
            return False

        # Convert the model to DynamoDB format
        receipt_structure_analysis = structure_analysis.to_dynamo()

        # Use the DynamoClient's proper method to save the item
        client.addReceiptStructureAnalysis(receipt_structure_analysis)

        logger.info(
            f"Saved structure analysis for receipt {structure_analysis.receipt_id}, image {structure_analysis.image_id}"
        )
        return True
    except Exception as e:
        logger.error(f"Error saving structure analysis: {str(e)}")
        return False


def get_structure_analysis(
    receipt_id: int, image_id: str, client: DynamoClient
) -> Optional[StructureAnalysis]:
    """
    DEPRECATED: Use client.getReceiptAnalysis(image_id, receipt_id).structure_analysis instead.

    Retrieve a StructureAnalysis object from DynamoDB.

    Args:
        receipt_id: The receipt ID
        image_id: The image ID
        client: DynamoClient instance

    Returns:
        StructureAnalysis object or None if not found
    """
    import warnings

    warnings.warn(
        "get_structure_analysis is deprecated. Use client.getReceiptAnalysis(image_id, receipt_id).structure_analysis instead.",
        DeprecationWarning,
        stacklevel=2,
    )

    try:
        # Type checking
        if not isinstance(client, DynamoClient):
            logger.error(f"Expected DynamoClient object, got {type(client).__name__}")
            return None

        # Convert receipt_id to int if it's a string
        receipt_id_int = int(receipt_id) if isinstance(receipt_id, str) else receipt_id

        # Use the client's dedicated method to get the receipt structure analysis
        # All DynamoDB access should be handled by the receipt_dynamo package
        receipt_structure_analysis = client.getReceiptStructureAnalysis(
            receipt_id=receipt_id_int, image_id=image_id
        )

        # If we get here, we have a valid receipt_structure_analysis
        logger.info(
            f"Retrieved structure analysis for receipt {receipt_id}, image {image_id}"
        )

        # Convert to our model
        structure_analysis = StructureAnalysis.from_dynamo(receipt_structure_analysis)

        return structure_analysis

    except ValueError as e:
        # Only handle "does not exist" errors - re-raise other ValueErrors
        if "does not exist" in str(e).lower():
            logger.info(
                f"No structure analysis found for receipt {receipt_id}, image {image_id}"
            )
            return None
        else:
            # Re-raise other ValueErrors
            logger.error(f"ValueError in get_structure_analysis: {str(e)}")
            raise
    except Exception as e:
        # Log other exceptions but still re-raise them
        logger.error(f"Error retrieving structure analysis: {str(e)}")
        raise


# ======================== Line Item Analysis Operations ========================


def save_line_item_analysis(line_item_analysis, client) -> bool:
    """Save a LineItemAnalysis object to DynamoDB.

    Args:
        line_item_analysis: The LineItemAnalysis object to save
        client: DynamoClient instance

    Returns:
        Boolean indicating success
    """
    try:
        if not line_item_analysis:
            logger.warning("Cannot save empty line item analysis")
            return False

        # Convert the model to DynamoDB format
        receipt_line_item_analysis = line_item_analysis.to_dynamo()

        # Use the DynamoClient's proper method to save the item
        client.addReceiptLineItemAnalysis(receipt_line_item_analysis)

        logger.info(
            f"Saved line item analysis for receipt {line_item_analysis.receipt_id}, image {line_item_analysis.image_id}"
        )
        return True
    except Exception as e:
        logger.error(f"Error saving line item analysis: {str(e)}")
        return False


def get_line_item_analysis(
    receipt_id: int, image_id: str, client: DynamoClient
) -> Optional[LineItemAnalysis]:
    """
    DEPRECATED: Use client.getReceiptAnalysis(image_id, receipt_id).line_item_analysis instead.

    Retrieve a LineItemAnalysis object from DynamoDB.

    Args:
        receipt_id: The receipt ID
        image_id: The image ID
        client: DynamoClient instance

    Returns:
        LineItemAnalysis object or None if not found
    """
    import warnings

    warnings.warn(
        "get_line_item_analysis is deprecated. Use client.getReceiptAnalysis(image_id, receipt_id).line_item_analysis instead.",
        DeprecationWarning,
        stacklevel=2,
    )

    try:
        # Type checking
        if not isinstance(client, DynamoClient):
            logger.error(f"Expected DynamoClient object, got {type(client).__name__}")
            return None

        # Convert receipt_id to int if it's a string
        receipt_id_int = int(receipt_id) if isinstance(receipt_id, str) else receipt_id

        # Use the client's dedicated method to get the receipt line item analysis
        # All DynamoDB access should be handled by the receipt_dynamo package
        receipt_line_item_analysis = client.getReceiptLineItemAnalysis(
            image_id=image_id, receipt_id=receipt_id_int
        )

        # If we get here, we have a valid receipt_line_item_analysis
        logger.info(
            f"Retrieved line item analysis for receipt {receipt_id}, image {image_id}"
        )

        # Convert to our model
        line_item_analysis = LineItemAnalysis.from_dynamo(receipt_line_item_analysis)

        return line_item_analysis

    except ValueError as e:
        # Only handle "does not exist" errors - re-raise other ValueErrors
        if "does not exist" in str(e).lower():
            logger.info(
                f"No line item analysis found for receipt {receipt_id}, image {image_id}"
            )
            return None
        else:
            # Re-raise other ValueErrors
            logger.error(f"ValueError in get_line_item_analysis: {str(e)}")
            raise
    except AttributeError as e:
        # This happens if the client doesn't have the required method
        logger.error(
            f"The client doesn't implement getReceiptLineItemAnalysis: {str(e)}"
        )
        raise
    except Exception as e:
        # Log other exceptions but still re-raise them
        logger.error(f"Error retrieving line item analysis: {str(e)}")
        raise


# ======================== Validation Analysis Operations ========================


def save_validation_analysis(
    validation_analysis,
    client,
    receipt_id: Optional[int] = None,
    image_id: Optional[str] = None,
) -> bool:
    """Save a ValidationAnalysis object to DynamoDB using the split storage pattern.

    Args:
        validation_analysis: The ValidationAnalysis object to save
        client: DynamoClient instance
        receipt_id: Optional receipt ID to set if not already set
        image_id: Optional image ID to set if not already set

    Returns:
        Boolean indicating success
    """
    try:
        if not validation_analysis:
            logger.warning("Cannot save empty validation analysis")
            return False

        # Ensure receipt_id and image_id are set if provided as parameters
        if receipt_id is not None and (
            not hasattr(validation_analysis, "receipt_id")
            or not validation_analysis.receipt_id
        ):
            validation_analysis.receipt_id = receipt_id

        if image_id is not None and (
            not hasattr(validation_analysis, "image_id")
            or not validation_analysis.image_id
        ):
            validation_analysis.image_id = image_id

        # Check if they're set before proceeding
        if (
            not hasattr(validation_analysis, "receipt_id")
            or not validation_analysis.receipt_id
        ):
            logger.error("ValidationAnalysis is missing receipt_id")
            return False

        if (
            not hasattr(validation_analysis, "image_id")
            or not validation_analysis.image_id
        ):
            logger.error("ValidationAnalysis is missing image_id")
            return False

        # Use split storage pattern
        success = True

        # 1. Save summary
        try:
            summary_item = validation_analysis.to_dynamo_validation_summary()
            client.addReceiptValidationSummary(summary_item)
        except Exception as e:
            logger.error(f"Error saving validation summary: {str(e)}")
            logger.error(
                f"receipt_id: {getattr(validation_analysis, 'receipt_id', None)}, image_id: {getattr(validation_analysis, 'image_id', None)}"
            )
            success = False

        # 2. Save categories
        try:
            category_items = validation_analysis.to_dynamo_validation_categories()
            for item in category_items:
                client.addReceiptValidationCategory(item)
        except Exception as e:
            logger.error(f"Error saving validation categories: {str(e)}")
            success = False

        # 3. Save results
        logger.info(
            f"Saving validation results for receipt {validation_analysis.receipt_id}, image {validation_analysis.image_id}"
        )
        try:
            result_items = validation_analysis.to_dynamo_validation_results()
            for item in result_items:
                logger.info(f"Saving validation result: {item}")
                client.addReceiptValidationResult(item)
        except Exception as e:
            logger.error(f"Error saving validation results: {str(e)}")
            success = False

        if success:
            logger.info(
                f"Saved validation analysis for receipt {validation_analysis.receipt_id}, image {validation_analysis.image_id}"
            )
        return success
    except Exception as e:
        logger.error(f"Error saving validation analysis: {str(e)}")
        return False


def get_validation_analysis(
    receipt_id: str, image_id: str, client
) -> Optional[ValidationAnalysis]:
    """
    DEPRECATED: Use client.getReceiptAnalysis(image_id, receipt_id).validation_summary instead.

    Retrieve a ValidationAnalysis object from DynamoDB.

    Args:
        receipt_id: The receipt ID
        image_id: The image ID
        client: DynamoClient instance

    Returns:
        ValidationAnalysis object or None if not found
    """
    import warnings

    warnings.warn(
        "get_validation_analysis is deprecated. Use client.getReceiptAnalysis(image_id, receipt_id).validation_summary instead.",
        DeprecationWarning,
        stacklevel=2,
    )

    try:
        # Convert receipt_id to int if it's a string
        receipt_id_int = int(receipt_id) if isinstance(receipt_id, str) else receipt_id

        # Use the client's dedicated methods to get validation data
        # Get validation summary
        receipt_validation_summary = client.getReceiptValidationSummary(
            image_id, receipt_id_int
        )

        if not receipt_validation_summary:
            logger.info(
                f"No validation analysis summary found for receipt {receipt_id}, image {image_id}"
            )
            return None

        # Get validation categories
        validation_categories = client.getReceiptValidationCategoriesByReceipt(
            image_id, receipt_id_int
        )

        # Get validation results
        validation_results = client.getReceiptValidationResultsByReceipt(
            image_id, receipt_id_int
        )

        # Combine all items for from_dynamo_items method
        all_items = (
            [receipt_validation_summary] + validation_categories + validation_results
        )

        # Reconstruct ValidationAnalysis from items
        return ValidationAnalysis.from_dynamo_items(all_items)
    except Exception as e:
        logger.error(f"Error retrieving validation analysis: {str(e)}")
        return None


# ======================== Transaction Operations ========================


def save_analysis_transaction(
    label_analysis,
    structure_analysis,
    line_item_analysis,
    client,
    receipt_id: Optional[int] = None,
    image_id: Optional[str] = None,
) -> bool:
    """Save multiple analysis objects in a single transaction.

    Note: This doesn't include ValidationAnalysis which uses split storage pattern
    and would exceed transaction item limits in many cases.

    Args:
        label_analysis: LabelAnalysis object to save
        structure_analysis: StructureAnalysis object to save
        line_item_analysis: LineItemAnalysis object to save
        client: DynamoClient instance
        receipt_id: Optional receipt ID to set on all analysis objects if not already set
        image_id: Optional image ID to set on all analysis objects if not already set

    Returns:
        Boolean indicating success
    """
    try:
        # Make sure receipt_id and image_id are set on all objects if provided
        if receipt_id is not None and image_id is not None:
            if label_analysis:
                if (
                    not hasattr(label_analysis, "receipt_id")
                    or not label_analysis.receipt_id
                ):
                    label_analysis.receipt_id = receipt_id
                if (
                    not hasattr(label_analysis, "image_id")
                    or not label_analysis.image_id
                ):
                    label_analysis.image_id = image_id

            if structure_analysis:
                if (
                    not hasattr(structure_analysis, "receipt_id")
                    or not structure_analysis.receipt_id
                ):
                    structure_analysis.receipt_id = receipt_id
                if (
                    not hasattr(structure_analysis, "image_id")
                    or not structure_analysis.image_id
                ):
                    structure_analysis.image_id = image_id

            if line_item_analysis:
                if (
                    not hasattr(line_item_analysis, "receipt_id")
                    or not line_item_analysis.receipt_id
                ):
                    line_item_analysis.receipt_id = receipt_id
                if (
                    not hasattr(line_item_analysis, "image_id")
                    or not line_item_analysis.image_id
                ):
                    line_item_analysis.image_id = image_id

        # Create lists to hold the different analysis objects
        label_analyses = []
        structure_analyses = []
        line_item_analyses = []

        # Add each analysis to the appropriate list if provided
        if label_analysis:
            try:
                label_analyses.append(label_analysis.to_dynamo())
            except Exception as e:
                logger.error(
                    f"Error converting label_analysis to DynamoDB format: {str(e)}"
                )
                logger.error(
                    f"receipt_id: {getattr(label_analysis, 'receipt_id', None)}, image_id: {getattr(label_analysis, 'image_id', None)}"
                )
                raise

        if structure_analysis:
            try:
                structure_analyses.append(structure_analysis.to_dynamo())
            except Exception as e:
                logger.error(
                    f"Error converting structure_analysis to DynamoDB format: {str(e)}"
                )
                logger.error(
                    f"receipt_id: {getattr(structure_analysis, 'receipt_id', None)}, image_id: {getattr(structure_analysis, 'image_id', None)}"
                )
                raise

        if line_item_analysis:
            try:
                line_item_analyses.append(line_item_analysis.to_dynamo())
            except Exception as e:
                logger.error(
                    f"Error converting line_item_analysis to DynamoDB format: {str(e)}"
                )
                logger.error(
                    f"receipt_id: {getattr(line_item_analysis, 'receipt_id', None)}, image_id: {getattr(line_item_analysis, 'image_id', None)}"
                )
                raise

        # Execute operations for each analysis type
        success = True

        if label_analyses:
            try:
                client.addReceiptLabelAnalyses(label_analyses)
            except Exception as e:
                logger.error(f"Error saving label analyses: {str(e)}")
                success = False

        if structure_analyses:
            try:
                for analysis in structure_analyses:
                    client.addReceiptStructureAnalysis(analysis)
            except Exception as e:
                logger.error(f"Error saving structure analyses: {str(e)}")
                success = False

        if line_item_analyses:
            try:
                for analysis in line_item_analyses:
                    client.addReceiptLineItemAnalysis(analysis)
            except Exception as e:
                logger.error(f"Error saving line item analyses: {str(e)}")
                success = False

        if success:
            logger.info(f"Saved analysis objects in transaction")
        return success

    except Exception as e:
        logger.error(f"Error in transaction: {str(e)}")
        return False


# ======================== Query Operations ========================


def query_labels_by_date_range(
    start_date: str, end_date: str, client, limit: Optional[int] = None
) -> List[LabelAnalysis]:
    """Query label analyses by creation date range.

    Args:
        start_date: Start date in ISO format (YYYY-MM-DD)
        end_date: End date in ISO format (YYYY-MM-DD)
        client: DynamoClient instance
        limit: Maximum number of items to return

    Returns:
        List of LabelAnalysis objects within the date range
    """
    try:
        # Use the client's dedicated method to query by date range
        receipt_label_analyses = client.getReceiptLabelAnalysesByDateRange(
            start_date, end_date, limit
        )

        # Convert items to LabelAnalysis objects
        analyses = [LabelAnalysis.from_dynamo(item) for item in receipt_label_analyses]

        logger.info(
            f"Retrieved {len(analyses)} label analyses between {start_date} and {end_date}"
        )
        return analyses
    except Exception as e:
        logger.error(f"Error querying labels by date range: {str(e)}")
        return []


def query_validations_by_status(
    status: str, client, limit: Optional[int] = None
) -> List[Dict]:
    """Query validation analyses by status.

    Args:
        status: Validation status to query for
        client: DynamoClient instance
        limit: Maximum number of items to return

    Returns:
        List of validation summary items matching the status
    """
    try:
        # Use the client's dedicated method to query by status
        validation_summaries = client.getReceiptValidationSummariesByStatus(
            status, limit
        )

        logger.info(
            f"Retrieved {len(validation_summaries)} validation analyses with status '{status}'"
        )
        return validation_summaries
    except Exception as e:
        logger.error(f"Error querying validations by status: {str(e)}")
        return []


def get_receipt_analyses(receipt_id: int, image_id: str, client: DynamoClient) -> Tuple[
    Optional[LabelAnalysis],
    Optional[StructureAnalysis],
    Optional[LineItemAnalysis],
    Optional[ValidationAnalysis],
]:
    """
    Get all analysis types for a receipt in a single database query.

    This function uses the consolidated getReceiptAnalysis method to fetch all
    analysis types for a receipt, and then converts each DynamoDB entity to
    its corresponding model class.

    Args:
        receipt_id: The receipt ID
        image_id: The image ID
        client: DynamoClient instance

    Returns:
        Tuple containing all analysis types (label_analysis, structure_analysis,
        line_item_analysis, validation_analysis), with None for any missing types
    """
    try:
        # Get all analyses in a single query
        receipt_analysis = client.getReceiptAnalysis(image_id, receipt_id)

        # Convert each analysis to its model class
        label_analysis = None
        if receipt_analysis.label_analysis:
            label_analysis = LabelAnalysis.from_dynamo(receipt_analysis.label_analysis)

        structure_analysis = None
        if receipt_analysis.structure_analysis:
            structure_analysis = StructureAnalysis.from_dynamo(
                receipt_analysis.structure_analysis
            )

        line_item_analysis = None
        if receipt_analysis.line_item_analysis:
            line_item_analysis = LineItemAnalysis.from_dynamo(
                receipt_analysis.line_item_analysis
            )

        validation_analysis = None
        if receipt_analysis.validation_summary:
            validation_analysis = ValidationAnalysis.from_dynamo(
                receipt_analysis.validation_summary
            )

            # If available, also add validation categories and results
            if receipt_analysis.validation_categories:
                # Add categories to validation_analysis as needed
                pass

            if receipt_analysis.validation_results:
                # Add results to validation_analysis as needed
                pass

        return (
            label_analysis,
            structure_analysis,
            line_item_analysis,
            validation_analysis,
        )

    except Exception as e:
        logger.error(f"Error getting receipt analyses: {str(e)}")
        # Return None for all analysis types
        return None, None, None, None
