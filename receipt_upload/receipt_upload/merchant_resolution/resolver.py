"""
Merchant resolution for receipt processing.

Two-tier resolution strategy:
1. Tier 1 (Fast): Query ChromaDB lines collection by normalized_phone_10 or
   normalized_full_address metadata fields
2. Tier 2 (Fallback): Use Place ID Finder agent to search Google Places API

The ChromaDB query uses the snapshot+delta pre-merged clients from
create_embeddings_and_compaction_run(), enabling immediate similarity search
against the freshest data.
"""

import logging
from dataclasses import dataclass
from typing import Any, List, Optional

from receipt_chroma import ChromaClient
from receipt_chroma.embedding.utils.normalize import (
    normalize_address,
    normalize_phone,
)
from receipt_dynamo import DynamoClient
from receipt_dynamo.entities import ReceiptLine, ReceiptWord

logger = logging.getLogger(__name__)

# Invalid place_id sentinel values to filter out
INVALID_PLACE_IDS = frozenset(("", "null", "NO_RESULTS", "INVALID"))


def _log(msg: str, *args: object) -> None:
    """Log message with immediate flush for CloudWatch visibility."""
    formatted = msg % args if args else msg
    print(f"[MERCHANT_RESOLVER] {formatted}", flush=True)
    logger.info(msg, *args)


@dataclass
class MerchantResult:
    """Result of merchant resolution."""

    place_id: Optional[str] = None
    merchant_name: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    confidence: float = 0.0
    resolution_tier: Optional[str] = (
        None  # "phone", "address", "place_id_finder"
    )
    source_image_id: Optional[str] = None  # For Tier 1, the source receipt
    source_receipt_id: Optional[int] = None


class MerchantResolver:
    """
    Resolves merchant information using two-tier strategy.

    Tier 1: Fast metadata filtering on ChromaDB lines collection
    Tier 2: Place ID Finder agent for Google Places API search
    """

    def __init__(
        self,
        dynamo_client: DynamoClient,
        places_client: Optional[Any] = None,
    ):
        """
        Initialize the merchant resolver.

        Args:
            dynamo_client: DynamoDB client for fetching receipt metadata
            places_client: Optional Google Places client for Tier 2
        """
        self.dynamo = dynamo_client
        self.places_client = places_client

    # pylint: disable=too-many-positional-arguments
    def resolve(
        self,
        lines_client: ChromaClient,
        lines: List[ReceiptLine],
        words: List[ReceiptWord],
        image_id: str,
        receipt_id: int,
    ) -> MerchantResult:  # pylint: disable=too-many-positional-arguments
        """
        Resolve merchant information for a receipt.

        Args:
            lines_client: ChromaClient with snapshot+delta pre-merged
            lines: Receipt lines from current receipt
            words: Receipt words from current receipt
            image_id: Current receipt's image_id
            receipt_id: Current receipt's receipt_id

        Returns:
            MerchantResult with resolved merchant information
        """
        # Tier 1: Try phone match first (most reliable)
        phone = self._extract_phone(words)
        if phone:
            _log("Tier 1: Trying phone match for %s", phone)
            result = self._query_by_phone(
                lines_client, phone, image_id, receipt_id
            )
            if result.place_id:
                _log(
                    "Tier 1 SUCCESS: Found merchant via phone: %s "
                    "(place_id=%s)",
                    result.merchant_name,
                    result.place_id,
                )
                return result

        # Tier 1: Try address match
        address = self._extract_address(words)
        if address:
            _log("Tier 1: Trying address match for %s...", address[:50])
            result = self._query_by_address(
                lines_client, address, image_id, receipt_id
            )
            if result.place_id:
                _log(
                    "Tier 1 SUCCESS: Found merchant via address: %s "
                    "(place_id=%s)",
                    result.merchant_name,
                    result.place_id,
                )
                return result

        # Tier 2: Fall back to Place ID Finder agent
        _log("Tier 1 failed, invoking Tier 2: Place ID Finder agent")
        result = self._run_place_id_finder(lines, words, image_id, receipt_id)

        if result.place_id:
            _log(
                "Tier 2 SUCCESS: Found merchant via Place ID Finder: %s "
                "(place_id=%s)",
                result.merchant_name,
                result.place_id,
            )
        else:
            _log("Tier 2: No merchant found")

        return result

    def _extract_phone(self, words: List[ReceiptWord]) -> Optional[str]:
        """
        Extract normalized 10-digit phone number from words.

        Looks for words with extracted_data.type == "phone".

        Args:
            words: List of ReceiptWord entities

        Returns:
            Normalized 10-digit phone number or None
        """
        for word in words:
            ext = getattr(word, "extracted_data", None) or {}
            if not ext:
                continue

            etype = str(ext.get("type", "")).lower()
            if etype == "phone":
                value = ext.get("value") or getattr(word, "text", "")
                phone = normalize_phone(value)
                if phone:
                    return phone

        return None

    def _extract_address(self, words: List[ReceiptWord]) -> Optional[str]:
        """
        Extract normalized address from words.

        Looks for words with extracted_data.type == "address" and builds
        a full address string.

        Args:
            words: List of ReceiptWord entities

        Returns:
            Normalized address string or None
        """
        address_parts: List[str] = []

        for word in words:
            ext = getattr(word, "extracted_data", None) or {}
            if not ext:
                continue

            etype = str(ext.get("type", "")).lower()
            if etype == "address":
                value = ext.get("value") or ""
                if value:
                    address_parts.append(str(value))

        if address_parts:
            # Deduplicate and join
            unique_parts = list(dict.fromkeys(address_parts))
            combined = " ".join(unique_parts)
            return normalize_address(combined)

        return None

    def _query_by_phone(
        self,
        lines_client: ChromaClient,
        phone: str,
        current_image_id: str,
        current_receipt_id: int,
    ) -> MerchantResult:
        """
        Query lines collection for receipts with matching phone number.

        Args:
            lines_client: ChromaClient with merged snapshot+delta
            phone: Normalized 10-digit phone number
            current_image_id: Current receipt's image_id (to exclude)
            current_receipt_id: Current receipt's receipt_id (to exclude)

        Returns:
            MerchantResult if match found, empty result otherwise
        """
        try:
            # Query with metadata filter
            results = lines_client.query(
                collection_name="lines",
                query_embeddings=[[0.0] * 1536],  # Dummy embedding for filter
                n_results=10,
                where={"normalized_phone_10": phone},
                include=["metadatas"],
            )

            if results and results.get("metadatas"):
                for metadata_list in results["metadatas"]:
                    for metadata in metadata_list:
                        # Skip current receipt
                        if (
                            metadata.get("image_id") == current_image_id
                            and metadata.get("receipt_id")
                            == current_receipt_id
                        ):
                            continue

                        # Found a match from a different receipt
                        source_image_id = metadata.get("image_id")
                        source_receipt_id = metadata.get("receipt_id")

                        # Get place_id from DynamoDB
                        place_id = self._get_place_id_from_dynamo(
                            source_image_id, source_receipt_id
                        )

                        if place_id:
                            return MerchantResult(
                                place_id=place_id,
                                merchant_name=metadata.get("merchant_name"),
                                phone=phone,
                                confidence=0.95,
                                resolution_tier="phone",
                                source_image_id=source_image_id,
                                source_receipt_id=source_receipt_id,
                            )

        except Exception as exc:  # pylint: disable=broad-exception-caught
            _log("Error querying by phone: %s", exc)
            logger.exception("Phone query failed")

        return MerchantResult()

    def _query_by_address(
        self,
        lines_client: ChromaClient,
        address: str,
        current_image_id: str,
        current_receipt_id: int,
    ) -> MerchantResult:
        """
        Query lines collection for receipts with matching address.

        Args:
            lines_client: ChromaClient with merged snapshot+delta
            address: Normalized address string
            current_image_id: Current receipt's image_id (to exclude)
            current_receipt_id: Current receipt's receipt_id (to exclude)

        Returns:
            MerchantResult if match found, empty result otherwise
        """
        try:
            # Query with metadata filter
            results = lines_client.query(
                collection_name="lines",
                query_embeddings=[[0.0] * 1536],  # Dummy embedding for filter
                n_results=10,
                where={"normalized_full_address": address},
                include=["metadatas"],
            )

            if results and results.get("metadatas"):
                for metadata_list in results["metadatas"]:
                    for metadata in metadata_list:
                        # Skip current receipt
                        if (
                            metadata.get("image_id") == current_image_id
                            and metadata.get("receipt_id")
                            == current_receipt_id
                        ):
                            continue

                        # Found a match from a different receipt
                        source_image_id = metadata.get("image_id")
                        source_receipt_id = metadata.get("receipt_id")

                        # Get place_id from DynamoDB
                        place_id = self._get_place_id_from_dynamo(
                            source_image_id, source_receipt_id
                        )

                        if place_id:
                            return MerchantResult(
                                place_id=place_id,
                                merchant_name=metadata.get("merchant_name"),
                                address=address,
                                confidence=0.80,
                                resolution_tier="address",
                                source_image_id=source_image_id,
                                source_receipt_id=source_receipt_id,
                            )

        except Exception as exc:  # pylint: disable=broad-exception-caught
            _log("Error querying by address: %s", exc)
            logger.exception("Address query failed")

        return MerchantResult()

    def _get_place_id_from_dynamo(
        self,
        image_id: str,
        receipt_id: int,
    ) -> Optional[str]:
        """
        Get place_id from DynamoDB for a receipt.

        Args:
            image_id: Receipt's image_id
            receipt_id: Receipt's receipt_id

        Returns:
            place_id if found and valid, None otherwise
        """
        try:
            place = self.dynamo.get_receipt_place(image_id, receipt_id)
            if place and place.place_id:
                if place.place_id not in INVALID_PLACE_IDS:
                    return place.place_id
        except Exception as exc:  # pylint: disable=broad-exception-caught
            _log("Error getting place_id from receipt_place: %s", exc)
            logger.exception("DynamoDB lookup failed")

        return None

    def _run_place_id_finder(
        self,
        lines: List[ReceiptLine],
        words: List[ReceiptWord],
        image_id: str,
        receipt_id: int,
    ) -> MerchantResult:
        """
        Run Place ID Finder agent to search Google Places API.

        This is the Tier 2 fallback when metadata filtering fails.

        Args:
            lines: Receipt lines
            words: Receipt words
            image_id: Receipt's image_id
            receipt_id: Receipt's receipt_id

        Returns:
            MerchantResult with Google Places data
        """
        try:
            # Import Place ID Finder
            # pylint: disable=import-outside-toplevel
            from receipt_agent.agents.place_id_finder.tools import (
                place_id_finder as place_id_finder_module,
            )

            # Extract merchant info from lines/words for the finder
            merchant_name = self._extract_merchant_name(lines)
            phone = self._extract_phone(words)
            address = self._extract_address(words)

            # Create a ReceiptRecord for the finder
            receipt_record = place_id_finder_module.ReceiptRecord(
                image_id=image_id,
                receipt_id=receipt_id,
                merchant_name=merchant_name,
                address=address,
                phone=phone,
            )

            # Create finder instance
            finder = place_id_finder_module.PlaceIdFinder(
                dynamo_client=self.dynamo,
                places_client=self.places_client,
            )

            # Search for place_id
            match = finder._search_places_for_receipt(  # pylint: disable=protected-access
                receipt_record
            )

            if match.found and match.place_id:
                return MerchantResult(
                    place_id=match.place_id,
                    merchant_name=match.place_name,
                    address=match.place_address,
                    phone=match.place_phone,
                    confidence=match.confidence
                    / 100.0,  # Convert 0-100 to 0-1
                    resolution_tier="place_id_finder",
                )

        except ImportError as exc:
            _log("WARNING: receipt_agent import failed: %s", exc)
            logger.warning("receipt_agent import failed", exc_info=True)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            _log("Error running Place ID Finder: %s", exc)
            logger.exception("Place ID Finder failed")

        return MerchantResult()

    def _extract_merchant_name(
        self,
        lines: List[ReceiptLine],
    ) -> Optional[str]:
        """
        Extract likely merchant name from receipt lines.

        Uses the first line as a simple heuristic - merchants typically
        appear at the top of receipts.

        Args:
            lines: Receipt lines

        Returns:
            Merchant name or None
        """
        if not lines:
            return None

        # Sort by line_id and take the first line
        sorted_lines = sorted(lines, key=lambda x: x.line_id)
        first_line = sorted_lines[0]

        # Skip very short lines or lines that look like dates/addresses
        text = first_line.text.strip()
        if len(text) < 3:
            return None

        # Simple heuristic: if it looks like an address (has numbers), skip
        # This is a basic check - the Place ID Finder will handle edge cases
        if text[0].isdigit():
            return None

        return text
