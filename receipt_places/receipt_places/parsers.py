"""
Parsers for Google Places API (Legacy) responses.

Handles routing different endpoint responses (details, findplacefromtext, textsearch, etc.)
to typed Place objects, with proper error handling.
"""

import logging
from typing import Any

from pydantic import ValidationError

from receipt_places.types import (
    LegacyAutocompleteResponse,
    LegacyCandidatesResponse,
    LegacyDetailsResponse,
    LegacySearchResponse,
    Place,
    Prediction,
)
from receipt_places.validators import (
    PlacesValidationError,
    validate_place_complete,
)

logger = logging.getLogger(__name__)


class ParseError(Exception):
    """Base error for response parsing failures."""


class APIError(ParseError):
    """Raised when the API returns an error status."""


def _check_api_status(status: str, error_message: str | None = None) -> None:
    """
    Check if the API returned a success status.

    Args:
        status: The status string from the API response
        error_message: Optional error message from the API

    Raises:
        APIError: If status is not OK or ZERO_RESULTS
    """
    if status not in ("OK", "ZERO_RESULTS"):
        msg = f"Places API error: status={status}"
        if error_message:
            msg += f", message={error_message}"
        raise APIError(msg)


def parse_place_details_response(
    payload: dict[str, Any],
    expected_fields: set[str] | None = None,
) -> Place:
    """
    Parse a places/details/json response into a typed Place object.

    This is called after get_place_details() and requires validation of all
    the fields you requested.

    Args:
        payload: Raw JSON response dict from the API
        expected_fields: Set of field names you requested (e.g. {'place_id', 'geometry', 'rating'})

    Returns:
        Validated Place object

    Raises:
        APIError: If the API returned an error status
        ValidationError: If parsing fails (malformed data)
        PlacesValidationError: If validation gates fail
    """
    try:
        response = LegacyDetailsResponse.model_validate(payload)
    except ValidationError as e:
        raise ParseError(
            f"Failed to parse details response envelope: {e}"
        ) from e

    # Check API status
    _check_api_status(response.status, response.error_message)

    # Extract the result dict
    if not response.result:
        raise APIError("Places API returned OK but no result dict")

    # Parse the result into a Place object
    try:
        place = Place.model_validate(response.result)
    except ValidationError as e:
        raise ParseError(
            f"Failed to parse Place object from result: {e}"
        ) from e

    # Run data quality gates
    try:
        validate_place_complete(place, expected_fields=expected_fields)
    except PlacesValidationError as e:
        logger.error("Place validation failed: %s", e)
        raise

    return place


def parse_place_candidates_response(
    payload: dict[str, Any],
    expected_fields: set[str] | None = None,
) -> Place | None:
    """
    Parse a places/findplacefromtext/json response into a typed Place object.

    Returns the first candidate if found, None otherwise.

    Args:
        payload: Raw JSON response dict from the API
        expected_fields: Set of field names you expected in each candidate

    Returns:
        Place object from the first candidate, or None if no candidates

    Raises:
        APIError: If the API returned an error status
        ValidationError: If parsing fails
        PlacesValidationError: If validation gates fail
    """
    try:
        response = LegacyCandidatesResponse.model_validate(payload)
    except ValidationError as e:
        raise ParseError(
            f"Failed to parse candidates response envelope: {e}"
        ) from e

    # Check API status
    _check_api_status(response.status, response.error_message)

    # No candidates found
    if not response.candidates:
        logger.info("findplacefromtext returned no candidates")
        return None

    # Parse the first candidate
    candidate_dict = response.candidates[0]
    try:
        place = Place.model_validate(candidate_dict)
    except ValidationError as e:
        raise ParseError(f"Failed to parse Place from candidate: {e}") from e

    # Run data quality gates
    try:
        validate_place_complete(place, expected_fields=expected_fields)
    except PlacesValidationError as e:
        logger.error("Candidate Place validation failed: %s", e)
        raise

    return place


def parse_place_search_response(
    payload: dict[str, Any],
    expected_fields: set[str] | None = None,
) -> list[Place]:
    """
    Parse a textsearch/json or nearbysearch/json response into a list of Place objects.

    Args:
        payload: Raw JSON response dict from the API
        expected_fields: Set of field names you expected in each result

    Returns:
        List of validated Place objects (may be empty)

    Raises:
        APIError: If the API returned an error status
        ValidationError: If parsing fails
        PlacesValidationError: If validation gates fail for any result
    """
    try:
        response = LegacySearchResponse.model_validate(payload)
    except ValidationError as e:
        raise ParseError(
            f"Failed to parse search response envelope: {e}"
        ) from e

    # Check API status
    _check_api_status(response.status, response.error_message)

    # No results found
    if not response.results:
        logger.info("Search returned no results")
        return []

    # Parse all results
    places = []
    for i, result_dict in enumerate(response.results):
        try:
            place = Place.model_validate(result_dict)
        except ValidationError as e:
            logger.warning("Failed to parse result #%s: %s, skipping", i, e)
            continue

        # Run data quality gates
        try:
            validate_place_complete(place, expected_fields=expected_fields)
        except PlacesValidationError as e:
            logger.warning("Result #%s validation failed: %s, skipping", i, e)
            continue

        places.append(place)

    return places


def parse_place_autocomplete_response(
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Parse a places/autocomplete/json response.

    Note: This returns raw prediction dicts (not Place objects) because autocomplete
    predictions are incomplete and don't map cleanly to the Place model.

    Args:
        payload: Raw JSON response dict from the API

    Returns:
        List of prediction dicts with at least 'description' and 'place_id'

    Raises:
        APIError: If the API returned an error status
        ValidationError: If parsing fails
    """
    try:
        response = LegacyAutocompleteResponse.model_validate(payload)
    except ValidationError as e:
        raise ParseError(
            f"Failed to parse autocomplete response envelope: {e}"
        ) from e

    # Check API status
    _check_api_status(response.status, response.error_message)

    # No predictions found
    if not response.predictions:
        logger.info("Autocomplete returned no predictions")
        return []

    # Validate that predictions have the critical fields
    predictions = []
    for i, pred_dict in enumerate(response.predictions):
        try:
            pred = Prediction.model_validate(pred_dict)
            # Only include if we have at least a description
            if pred.description:
                predictions.append(
                    {
                        "description": pred.description,
                        "place_id": pred.place_id,
                        "types": pred.types,
                    }
                )
        except ValidationError as e:
            logger.warning(
                "Prediction #%s validation failed: %s, skipping", i, e
            )
            continue

    return predictions
