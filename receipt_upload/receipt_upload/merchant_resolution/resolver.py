"""
Merchant resolution for receipt processing.

Two-tier resolution strategy:
1. Tier 1 (ChromaDB Similarity): Query ChromaDB lines collection by embedding
   similarity, then compare normalized metadata (phone, address) to boost
   confidence. This handles OCR errors like "Westlake" vs "Mestlake".
2. Tier 2 (Fallback): Use Place ID Finder agent to search Google Places API

The ChromaDB query uses the snapshot+delta pre-merged clients from
create_embeddings_and_compaction_run(), enabling immediate similarity search
against the freshest data.

Tracing:
- All key methods are decorated with @traceable for LangSmith visibility
- This enables parallel execution to be visible in the waterfall graph
"""

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from receipt_chroma import ChromaClient
from receipt_chroma.embedding.formatting.line_format import (
    format_line_context_embedding_input,
)
from receipt_chroma.embedding.utils.normalize import (
    normalize_address,
    normalize_phone,
)
from receipt_dynamo import DynamoClient
from receipt_dynamo.entities import ReceiptLine, ReceiptWord

logger = logging.getLogger(__name__)


def _get_traceable() -> Callable:
    """Get the traceable decorator if langsmith is available."""
    try:
        from langsmith.run_helpers import traceable

        return traceable
    except ImportError:
        # Return a no-op decorator if langsmith not installed
        def noop_decorator(*args, **kwargs):
            def wrapper(fn):
                return fn

            return wrapper

        return noop_decorator


def _get_project_name() -> str:
    """Get the Langsmith project name from environment."""
    return os.environ.get("LANGCHAIN_PROJECT", "receipt-label-validation")


# Invalid place_id sentinel values to filter out
INVALID_PLACE_IDS = frozenset(("", "null", "NO_RESULTS", "INVALID"))

# Similarity thresholds for ChromaDB search
MIN_SIMILARITY_THRESHOLD = 0.70  # Minimum to consider a match
HIGH_CONFIDENCE_THRESHOLD = 0.85  # High confidence match
PHONE_MATCH_BOOST = 0.20  # Boost when normalized phone matches
ADDRESS_MATCH_BOOST = 0.15  # Boost when normalized address matches

# Minimum token length for merchant name cross-validation
_MIN_TOKEN_LEN = 3  # Ignore tokens shorter than this (e.g., "A", "of", "&")


def _log(msg: str, *args: object) -> None:
    """Log message with immediate flush for CloudWatch visibility."""
    formatted = msg % args if args else msg
    print(f"[MERCHANT_RESOLVER] {formatted}", flush=True)
    logger.info(msg, *args)


def tokenize_text(text: str) -> Set[str]:
    """Extract lowercase alphanumeric tokens (>= *_MIN_TOKEN_LEN* chars) from *text*."""
    return {
        t
        for t in re.split(r"[^a-zA-Z0-9]+", text.lower())
        if len(t) >= _MIN_TOKEN_LEN
    }


def merchant_name_matches_receipt(
    merchant_name: Optional[str],
    lines: List[ReceiptLine],
    n_lines: int = 10,
) -> bool:
    """
    Check whether *merchant_name* has meaningful token overlap with the
    receipt's OCR text (top *n_lines* lines, where the merchant name
    usually appears).

    Returns ``True`` (pass) when:
    - *merchant_name* is empty / None (nothing to validate)
    - The merchant name has fewer than 2 significant tokens (too short
      to validate reliably — e.g. "JOi")
    - At least one significant token from the merchant name appears
      somewhere in the receipt text
    """
    if not merchant_name:
        return True  # Nothing to validate against

    merchant_tokens = tokenize_text(merchant_name)
    if len(merchant_tokens) < 2:
        return True  # Too short to validate reliably

    if not lines:
        return True  # No receipt text to validate against

    # Build a token set from the top N receipt lines (by y-coordinate)
    sorted_lines = sorted(
        lines, key=lambda l: l.calculate_centroid()[1]
    )
    receipt_text = " ".join(
        l.text for l in sorted_lines[:n_lines] if l.text
    )
    receipt_tokens = tokenize_text(receipt_text)

    return bool(merchant_tokens & receipt_tokens)


@dataclass
class SimilarityMatch:
    """A candidate match from ChromaDB similarity search."""

    image_id: str
    receipt_id: int
    merchant_name: Optional[str]
    normalized_phone: Optional[str]
    normalized_address: Optional[str]
    embedding_similarity: float  # 0.0 to 1.0 (converted from distance)
    metadata_boost: float = 0.0  # Additional confidence from metadata match
    place_id: Optional[str] = None

    @property
    def total_confidence(self) -> float:
        """Combined confidence from embedding similarity and metadata boost."""
        return min(1.0, self.embedding_similarity + self.metadata_boost)


@dataclass
class MerchantResult:
    """Result of merchant resolution."""

    place_id: Optional[str] = None
    merchant_name: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    confidence: float = 0.0
    resolution_tier: Optional[str] = None
    # "chroma_phone", "chroma_address", "chroma_text", "place_id_finder"
    source_image_id: Optional[str] = None  # For Tier 1, the source receipt
    source_receipt_id: Optional[int] = None
    # Debug info for similarity matches
    similarity_matches: List[SimilarityMatch] = field(default_factory=list)


class MerchantResolver:
    """
    Resolves merchant information using two-tier strategy.

    Tier 1: ChromaDB embedding similarity search with metadata comparison
    Tier 2: Place ID Finder agent for Google Places API search
    """

    def __init__(
        self,
        dynamo_client: DynamoClient,
        places_client: Optional[Any] = None,
        openai_client: Optional[Any] = None,
    ):
        """
        Initialize the merchant resolver.

        Args:
            dynamo_client: DynamoDB client for fetching receipt metadata
            places_client: Optional Google Places client for Tier 2
            openai_client: Optional OpenAI client for embedding generation
        """
        self.dynamo = dynamo_client
        self.places_client = places_client
        self._openai_client = openai_client

    @property
    def openai_client(self) -> Any:
        """Lazy-load OpenAI client."""
        if self._openai_client is None:
            try:
                from openai import (  # pylint: disable=import-outside-toplevel
                    OpenAI,
                )

                api_key = os.environ.get("OPENAI_API_KEY")
                if api_key:
                    self._openai_client = OpenAI(api_key=api_key)
                else:
                    _log(
                        "WARNING: OPENAI_API_KEY not set, similarity search disabled"
                    )
            except ImportError:
                _log("WARNING: openai package not available")
        return self._openai_client

    def _generate_embedding(self, text: str) -> Optional[List[float]]:
        """Generate embedding for a text string using OpenAI."""
        if not self.openai_client or not text:
            return None

        try:
            from receipt_chroma.embedding.openai.realtime import (  # pylint: disable=import-outside-toplevel
                embed_texts,
            )

            embeddings = embed_texts(
                client=self.openai_client,
                texts=[text],
                model="text-embedding-3-small",
            )
            return embeddings[0] if embeddings else None
        except Exception as exc:  # pylint: disable=broad-exception-caught
            _log("Error generating embedding: %s", exc)
            return None

    # pylint: disable=too-many-positional-arguments
    def resolve(
        self,
        lines_client: ChromaClient,
        lines: List[ReceiptLine],
        words: List[ReceiptWord],
        image_id: str,
        receipt_id: int,
        line_embeddings: Optional[Dict[int, List[float]]] = None,
    ) -> MerchantResult:  # pylint: disable=too-many-positional-arguments
        """
        Resolve merchant information for a receipt.

        Uses embedding similarity search with metadata comparison, then falls
        back to Google Places API if no good match found.

        Args:
            lines_client: ChromaClient with snapshot+delta pre-merged
            lines: Receipt lines from current receipt
            words: Receipt words from current receipt
            image_id: Current receipt's image_id
            receipt_id: Current receipt's receipt_id
            line_embeddings: Optional cached embeddings from orchestration
                             (avoids redundant OpenAI API calls)

        Returns:
            MerchantResult with resolved merchant information
        """
        # Create traced wrapper for LangSmith visibility
        traceable = _get_traceable()

        @traceable(
            name="merchant_resolution",
            project_name=_get_project_name(),
            tags=["merchant", "resolution"],
            metadata={
                "image_id": image_id,
                "receipt_id": receipt_id,
            },
        )
        def _traced_resolve() -> MerchantResult:
            return self._resolve_impl(
                lines_client=lines_client,
                lines=lines,
                words=words,
                image_id=image_id,
                receipt_id=receipt_id,
                line_embeddings=line_embeddings,
            )

        return _traced_resolve()

    def _resolve_impl(
        self,
        lines_client: ChromaClient,
        lines: List[ReceiptLine],
        words: List[ReceiptWord],
        image_id: str,
        receipt_id: int,
        line_embeddings: Optional[Dict[int, List[float]]] = None,
    ) -> MerchantResult:
        """Implementation of resolve() - called within trace context."""
        # Store embeddings cache for use in _similarity_search
        self._line_embeddings = line_embeddings or {}
        # Store receipt lines for merchant name cross-validation
        self._receipt_lines = lines
        # Extract contact info from receipt
        phone = self._extract_phone(words)
        address = self._extract_address(words)

        _log(
            "Resolving merchant for %s#%d (phone=%s, address=%s...)",
            image_id[:8],
            receipt_id,
            phone or "none",
            (address[:30] + "...") if address else "none",
        )

        # Tier 1: ChromaDB similarity search with metadata comparison
        # Uses cached embeddings from orchestration to avoid redundant API calls
        # Try phone line first (most reliable identifier)
        if phone:
            phone_line = self._get_line_for_phone(words, lines, phone)
            if phone_line:
                _log(
                    "Tier 1: Similarity search for phone line: %s",
                    phone_line.text,
                )
                result = self._similarity_search(
                    lines_client=lines_client,
                    query_line=phone_line,
                    current_image_id=image_id,
                    current_receipt_id=receipt_id,
                    expected_phone=phone,
                    expected_address=address,
                    resolution_tier="chroma_phone",
                )
                if (
                    result.place_id
                    and result.confidence >= MIN_SIMILARITY_THRESHOLD
                ):
                    _log(
                        "Tier 1 SUCCESS (phone): %s (place_id=%s, conf=%.2f)",
                        result.merchant_name,
                        result.place_id,
                        result.confidence,
                    )
                    return result

        # Try address line
        if address:
            address_line = self._get_line_for_address(words, lines, address)
            if address_line:
                _log(
                    "Tier 1: Similarity search for address line: %s",
                    address_line.text,
                )
                result = self._similarity_search(
                    lines_client=lines_client,
                    query_line=address_line,
                    current_image_id=image_id,
                    current_receipt_id=receipt_id,
                    expected_phone=phone,
                    expected_address=address,
                    resolution_tier="chroma_address",
                )
                if (
                    result.place_id
                    and result.confidence >= MIN_SIMILARITY_THRESHOLD
                ):
                    _log(
                        "Tier 1 SUCCESS (address): %s (place_id=%s, conf=%.2f)",
                        result.merchant_name,
                        result.place_id,
                        result.confidence,
                    )
                    return result

        # Try first line (often merchant name)
        merchant_line = self._get_merchant_line(lines)
        if merchant_line:
            _log(
                "Tier 1: Similarity search for merchant line: %s",
                merchant_line.text,
            )
            result = self._similarity_search(
                lines_client=lines_client,
                query_line=merchant_line,
                current_image_id=image_id,
                current_receipt_id=receipt_id,
                expected_phone=phone,
                expected_address=address,
                resolution_tier="chroma_text",
            )
            if (
                result.place_id
                and result.confidence >= MIN_SIMILARITY_THRESHOLD
            ):
                _log(
                    "Tier 1 SUCCESS (merchant): %s (place_id=%s, conf=%.2f)",
                    result.merchant_name,
                    result.place_id,
                    result.confidence,
                )
                return result

        # Tier 2: Fall back to Place ID Finder agent (Google Places API)
        _log("Tier 1 failed, invoking Tier 2: Place ID Finder agent")
        result = self._run_place_id_finder(
            lines_client, lines, words, image_id, receipt_id
        )

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

    def _get_line_for_phone(
        self,
        words: List[ReceiptWord],
        lines: List[ReceiptLine],
        phone: str,
    ) -> Optional[ReceiptLine]:
        """Get the ReceiptLine containing the phone number."""
        for word in words:
            ext = getattr(word, "extracted_data", None) or {}
            if ext.get("type") == "phone":
                # Find the corresponding line
                for line in lines:
                    if line.line_id == word.line_id:
                        return line
        return None

    def _get_line_for_address(
        self,
        words: List[ReceiptWord],
        lines: List[ReceiptLine],
        address: str,
    ) -> Optional[ReceiptLine]:
        """Get the ReceiptLine containing the address."""
        for word in words:
            ext = getattr(word, "extracted_data", None) or {}
            if ext.get("type") == "address":
                # Find the corresponding line
                for line in lines:
                    if line.line_id == word.line_id:
                        return line
        return None

    def _get_merchant_line(
        self, lines: List[ReceiptLine]
    ) -> Optional[ReceiptLine]:
        """Get the first line (often contains merchant name)."""
        if not lines:
            return None
        # Sort by y-coordinate (top to bottom) and return first
        sorted_lines = sorted(lines, key=lambda l: l.calculate_centroid()[1])
        return sorted_lines[0] if sorted_lines else None

    def _similarity_search(
        self,
        lines_client: ChromaClient,
        query_line: ReceiptLine,
        current_image_id: str,
        current_receipt_id: int,
        expected_phone: Optional[str],
        expected_address: Optional[str],
        resolution_tier: str,
    ) -> MerchantResult:
        """
        Search ChromaDB by embedding similarity and compare metadata.

        Args:
            lines_client: ChromaClient with merged snapshot+delta
            query_line: The line to search for (uses cached embedding if available)
            current_image_id: Current receipt's image_id (to exclude)
            current_receipt_id: Current receipt's receipt_id (to exclude)
            expected_phone: Normalized phone to compare against results
            expected_address: Normalized address to compare against results
            resolution_tier: Tier name for logging

        Returns:
            MerchantResult with best match or empty result
        """
        # Create traced wrapper for LangSmith visibility
        traceable = _get_traceable()

        @traceable(
            name=f"similarity_search_{resolution_tier}",
            project_name=_get_project_name(),
            tags=["chroma", "similarity", resolution_tier],
            metadata={
                "image_id": current_image_id,
                "receipt_id": current_receipt_id,
                "line_text": query_line.text[:50] if query_line.text else "",
                "expected_phone": expected_phone,
                "expected_address": (
                    expected_address[:30] if expected_address else None
                ),
            },
        )
        def _traced_search() -> MerchantResult:
            return self._similarity_search_impl(
                lines_client=lines_client,
                query_line=query_line,
                current_image_id=current_image_id,
                current_receipt_id=current_receipt_id,
                expected_phone=expected_phone,
                expected_address=expected_address,
                resolution_tier=resolution_tier,
            )

        return _traced_search()

    def _similarity_search_impl(
        self,
        lines_client: ChromaClient,
        query_line: ReceiptLine,
        current_image_id: str,
        current_receipt_id: int,
        expected_phone: Optional[str],
        expected_address: Optional[str],
        resolution_tier: str,
    ) -> MerchantResult:
        """Implementation of _similarity_search - called within trace context."""
        # Use cached embedding if available, otherwise generate
        embedding = self._line_embeddings.get(query_line.line_id)
        if not embedding:
            # Fallback to generating embedding (should rarely happen)
            _log(
                "Cache miss for line %d, generating embedding",
                query_line.line_id,
            )
            formatted_text = format_line_context_embedding_input(
                query_line, []  # No context available in fallback
            )
            embedding = self._generate_embedding(formatted_text)
            if not embedding:
                _log(
                    "Could not generate embedding for line %d",
                    query_line.line_id,
                )
                return MerchantResult()

        try:
            # Query ChromaDB by embedding similarity
            results = lines_client.query(
                collection_name="lines",
                query_embeddings=[embedding],
                n_results=20,
                include=["metadatas", "distances", "documents"],
            )

            if not results or not results.get("metadatas"):
                return MerchantResult()

            # Process results and compute confidence with metadata comparison
            matches: List[SimilarityMatch] = []
            metadatas = results.get("metadatas", [[]])[0]
            distances = results.get("distances", [[]])[0]

            for metadata, distance in zip(metadatas, distances):
                # Skip current receipt
                meta_image_id = metadata.get("image_id")
                meta_receipt_id = metadata.get("receipt_id")
                if (
                    meta_image_id == current_image_id
                    and meta_receipt_id == current_receipt_id
                ):
                    continue

                # Convert distance to similarity (ChromaDB uses L2 distance)
                # For normalized embeddings: similarity = 1 - (distance / 2)
                similarity = max(0.0, 1.0 - (distance / 2))

                if similarity < MIN_SIMILARITY_THRESHOLD:
                    continue

                # Compute metadata boost
                metadata_boost = 0.0
                result_phone = metadata.get("normalized_phone_10")
                result_address = metadata.get("normalized_full_address")

                if expected_phone and result_phone:
                    if expected_phone == result_phone:
                        metadata_boost += PHONE_MATCH_BOOST
                        _log("  Phone match boost: %s", result_phone)

                if expected_address and result_address:
                    # For addresses, use fuzzy comparison (OCR errors)
                    if self._addresses_similar(
                        expected_address, result_address
                    ):
                        metadata_boost += ADDRESS_MATCH_BOOST
                        _log("  Address match boost: %s", result_address[:30])

                match = SimilarityMatch(
                    image_id=meta_image_id,
                    receipt_id=int(meta_receipt_id) if meta_receipt_id else 0,
                    merchant_name=metadata.get("merchant_name"),
                    normalized_phone=result_phone,
                    normalized_address=result_address,
                    embedding_similarity=similarity,
                    metadata_boost=metadata_boost,
                )
                matches.append(match)

            if not matches:
                return MerchantResult()

            # Sort by total confidence and get best match
            matches.sort(key=lambda m: m.total_confidence, reverse=True)
            best = matches[0]

            _log(
                "Best match: %s (sim=%.2f, boost=%.2f, total=%.2f)",
                best.merchant_name,
                best.embedding_similarity,
                best.metadata_boost,
                best.total_confidence,
            )

            # Cross-validate: reject matches whose merchant name has
            # zero token overlap with the receipt's OCR text.  This
            # catches metadata-poisoning and over-representation bugs
            # (e.g. Sprouts dominating ChromaDB, wrong phone metadata).
            receipt_lines = getattr(self, "_receipt_lines", [])
            validated_matches: List[SimilarityMatch] = []
            for match in matches:
                if self._merchant_name_matches_receipt(
                    match.merchant_name, receipt_lines
                ):
                    validated_matches.append(match)
                else:
                    _log(
                        "Rejected match: %s — no token overlap with "
                        "receipt text (sim=%.2f, tier=%s)",
                        match.merchant_name,
                        match.total_confidence,
                        resolution_tier,
                    )

            if not validated_matches:
                _log(
                    "All %d matches rejected by OCR cross-validation",
                    len(matches),
                )
                return MerchantResult()

            # Use the best validated match
            best = validated_matches[0]

            # Try validated matches in order until we find one with
            # a place_id in DynamoDB
            for match in validated_matches[:5]:
                place_id, dynamo_merchant_name = self._get_place_from_dynamo(
                    match.image_id, match.receipt_id
                )
                if place_id:
                    match.place_id = place_id
                    # Prefer DynamoDB merchant_name (authoritative, may be
                    # corrected via fix-place) over ChromaDB metadata which
                    # can be stale/poisoned.
                    merchant_name = (
                        dynamo_merchant_name
                        or match.merchant_name
                    )
                    return MerchantResult(
                        place_id=place_id,
                        merchant_name=merchant_name,
                        phone=match.normalized_phone,
                        address=match.normalized_address,
                        confidence=match.total_confidence,
                        resolution_tier=resolution_tier,
                        source_image_id=match.image_id,
                        source_receipt_id=match.receipt_id,
                        similarity_matches=validated_matches[:5],
                    )

        except Exception as exc:  # pylint: disable=broad-exception-caught
            _log("Error in similarity search: %s", exc)
            logger.exception("Similarity search failed")

        return MerchantResult()

    def _addresses_similar(self, addr1: str, addr2: str) -> bool:
        """
        Check if two addresses are similar (handles OCR errors).

        Uses simple character-level similarity to handle typos like
        "Westlake" vs "Mestlake".
        """
        if not addr1 or not addr2:
            return False

        # Normalize for comparison
        a1 = addr1.upper().replace(" ", "")
        a2 = addr2.upper().replace(" ", "")

        # Exact match
        if a1 == a2:
            return True

        # Length difference check
        if abs(len(a1) - len(a2)) > 5:
            return False

        # Simple character overlap ratio
        shorter = min(len(a1), len(a2))
        if shorter == 0:
            return False

        # Count matching characters in order
        matches = sum(1 for c1, c2 in zip(a1, a2) if c1 == c2)
        ratio = matches / shorter

        return ratio >= 0.85  # 85% character match

    @staticmethod
    def _tokenize(text: str) -> Set[str]:
        """Extract lowercase alphanumeric tokens from text."""
        return tokenize_text(text)

    def _merchant_name_matches_receipt(
        self,
        merchant_name: Optional[str],
        lines: List[ReceiptLine],
        n_lines: int = 10,
    ) -> bool:
        """Thin wrapper around module-level :func:`merchant_name_matches_receipt`."""
        return merchant_name_matches_receipt(merchant_name, lines, n_lines)

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

    def _get_place_from_dynamo(
        self,
        image_id: str,
        receipt_id: int,
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Get place_id and merchant_name from DynamoDB for a receipt.

        Args:
            image_id: Receipt's image_id
            receipt_id: Receipt's receipt_id

        Returns:
            (place_id, merchant_name) tuple. Both may be None.
        """
        try:
            place = self.dynamo.get_receipt_place(image_id, receipt_id)
            if place and place.place_id:
                if place.place_id not in INVALID_PLACE_IDS:
                    return place.place_id, getattr(place, "merchant_name", None)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            _log("Error getting place from receipt_place: %s", exc)
            logger.exception("DynamoDB lookup failed")

        return None, None

    def _extract_merchant_name(
        self, lines: List[ReceiptLine]
    ) -> Optional[str]:
        """
        Extract merchant name from receipt lines.

        Uses the first line (top of receipt) which typically contains the store name.

        Args:
            lines: List of ReceiptLine entities

        Returns:
            Merchant name or None if not found
        """
        merchant_line = self._get_merchant_line(lines)
        if merchant_line and merchant_line.text:
            # Clean up the text - remove extra whitespace
            name = " ".join(merchant_line.text.split())
            return name if len(name) >= 2 else None
        return None

    def _run_place_id_finder(
        self,
        lines_client: ChromaClient,
        lines: List[ReceiptLine],
        words: List[ReceiptWord],
        image_id: str,
        receipt_id: int,
    ) -> MerchantResult:
        """
        Run Place ID Finder agent to search Google Places API.

        This is the Tier 2 fallback when metadata filtering fails. Uses the
        agentic approach which can reason about receipt content (e.g., extract
        merchant names from website domains like "Sprouts.com").

        Args:
            lines_client: ChromaClient for similarity search
            lines: Receipt lines
            words: Receipt words
            image_id: Receipt's image_id
            receipt_id: Receipt's receipt_id

        Returns:
            MerchantResult with Google Places data
        """
        # Create traced wrapper for LangSmith visibility
        traceable = _get_traceable()

        @traceable(
            name="place_id_finder",
            project_name=_get_project_name(),
            tags=["tier2", "places_api"],
            metadata={
                "image_id": image_id,
                "receipt_id": receipt_id,
            },
        )
        def _traced_place_id_finder() -> MerchantResult:
            return self._run_place_id_finder_impl(
                lines_client=lines_client,
                lines=lines,
                words=words,
                image_id=image_id,
                receipt_id=receipt_id,
            )

        return _traced_place_id_finder()

    def _run_place_id_finder_impl(
        self,
        lines_client: ChromaClient,
        lines: List[ReceiptLine],
        words: List[ReceiptWord],
        image_id: str,
        receipt_id: int,
    ) -> MerchantResult:
        """Implementation of _run_place_id_finder - called within trace context."""
        import asyncio  # pylint: disable=import-outside-toplevel

        # Get current Langsmith callbacks for parent trace context
        langsmith_callbacks = None
        try:
            from langsmith.run_helpers import (  # pylint: disable=import-outside-toplevel
                get_current_run_tree,
            )

            run_tree = get_current_run_tree()
            if run_tree:
                # Get callbacks that will make agent traces children of current trace
                from langchain_core.tracers.langchain import (  # pylint: disable=import-outside-toplevel
                    LangChainTracer,
                )

                langsmith_callbacks = [
                    LangChainTracer(
                        project_name=os.environ.get(
                            "LANGCHAIN_PROJECT", "receipt-label-validation"
                        ),
                        client=run_tree.client,
                    )
                ]
                _log("Got Langsmith callbacks for parent trace context")
        except ImportError:
            _log("Langsmith not available for trace context propagation")
        except Exception as e:  # pylint: disable=broad-exception-caught
            _log("Could not get Langsmith callbacks: %s", e)

        try:
            # Try agentic approach first (can reason about receipt content)
            # pylint: disable=import-outside-toplevel
            from receipt_agent.agents.place_id_finder import (
                create_place_id_finder_graph,
                run_place_id_finder,
            )

            _log("Tier 2: Using agentic Place ID Finder")

            # Create embed function from OpenAI client
            def embed_fn(texts: List[str]) -> List[List[float]]:
                if not self.openai_client or not texts:
                    return []
                from receipt_chroma.embedding.openai.realtime import (  # pylint: disable=import-outside-toplevel
                    embed_texts,
                )

                return embed_texts(
                    client=self.openai_client,
                    texts=texts,
                    model="text-embedding-3-small",
                )

            # Create the agentic graph
            graph, state_holder = create_place_id_finder_graph(
                dynamo_client=self.dynamo,
                chroma_client=lines_client,
                embed_fn=embed_fn,
                places_api=self.places_client,
            )

            # Run the agent (sync wrapper for async)
            result = asyncio.get_event_loop().run_until_complete(
                run_place_id_finder(
                    graph=graph,
                    state_holder=state_holder,
                    image_id=image_id,
                    receipt_id=receipt_id,
                    callbacks=langsmith_callbacks,
                )
            )

            if result and result.get("found") and result.get("place_id"):
                return MerchantResult(
                    place_id=result["place_id"],
                    merchant_name=result.get("place_name"),
                    address=result.get("place_address"),
                    phone=result.get("place_phone"),
                    confidence=result.get("confidence", 0.0),
                    resolution_tier="place_id_finder_agentic",
                )

            _log(
                "Agentic finder did not find place_id, reason: %s",
                result.get("reasoning", "unknown") if result else "no result",
            )

        except ImportError as exc:
            _log(
                "WARNING: receipt_agent import failed, falling back to simple search: %s",
                exc,
            )
            logger.warning(
                "receipt_agent agentic import failed", exc_info=True
            )
            # Fall through to simple search below
        except RuntimeError as exc:
            # Handle "no running event loop" error in Lambda
            if "no running event loop" in str(
                exc
            ) or "cannot be called from a running event loop" in str(exc):
                _log("Event loop issue, trying asyncio.run(): %s", exc)
                try:
                    # pylint: disable=import-outside-toplevel
                    from receipt_agent.agents.place_id_finder import (
                        create_place_id_finder_graph,
                        run_place_id_finder,
                    )

                    def embed_fn(texts: List[str]) -> List[List[float]]:
                        if not self.openai_client or not texts:
                            return []
                        from receipt_chroma.embedding.openai.realtime import (  # pylint: disable=import-outside-toplevel
                            embed_texts,
                        )

                        return embed_texts(
                            client=self.openai_client,
                            texts=texts,
                            model="text-embedding-3-small",
                        )

                    graph, state_holder = create_place_id_finder_graph(
                        dynamo_client=self.dynamo,
                        chroma_client=lines_client,
                        embed_fn=embed_fn,
                        places_api=self.places_client,
                    )

                    result = asyncio.run(
                        run_place_id_finder(
                            graph=graph,
                            state_holder=state_holder,
                            image_id=image_id,
                            receipt_id=receipt_id,
                            callbacks=langsmith_callbacks,
                        )
                    )

                    if (
                        result
                        and result.get("found")
                        and result.get("place_id")
                    ):
                        return MerchantResult(
                            place_id=result["place_id"],
                            merchant_name=result.get("place_name"),
                            address=result.get("place_address"),
                            phone=result.get("place_phone"),
                            confidence=result.get("confidence", 0.0),
                            resolution_tier="place_id_finder_agentic",
                        )
                except (
                    Exception
                ) as inner_exc:  # pylint: disable=broad-exception-caught
                    _log("Agentic fallback also failed: %s", inner_exc)
            else:
                _log("RuntimeError in agentic finder: %s", exc)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            _log("Error running agentic Place ID Finder: %s", exc)
            logger.exception("Agentic Place ID Finder failed")

        # Fallback to simple search if agentic approach fails
        _log("Tier 2: Falling back to simple Place ID Finder search")
        try:
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
            _log("Error running simple Place ID Finder: %s", exc)
            logger.exception("Simple Place ID Finder failed")

        return MerchantResult()
