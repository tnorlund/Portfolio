"""
Merchant Harmonizer - Find similar receipts and ensure consistent metadata.

This tool:
1. Finds receipts that belong together (same merchant) using ChromaDB similarity
2. Uses Google Places API to determine the correct/canonical metadata
3. Reports inconsistencies and suggests updates

Guard Rails:
- Uses embeddings to find truly similar content
- Cross-references multiple signals (address, phone, merchant name)
- Validates against Google Places before suggesting updates
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class ReceiptGroup:
    """A group of receipts that appear to be from the same merchant."""

    # Identifying signals
    primary_address: Optional[str] = None
    primary_phone: Optional[str] = None
    primary_merchant_name: Optional[str] = None

    # Google Places data (source of truth)
    place_id: Optional[str] = None
    canonical_name: Optional[str] = None
    canonical_address: Optional[str] = None
    canonical_phone: Optional[str] = None

    # Receipts in this group
    receipts: list[tuple[str, int]] = field(default_factory=list)  # (image_id, receipt_id)

    # Inconsistencies found
    inconsistencies: list[dict] = field(default_factory=list)

    # Confidence in the grouping
    confidence: float = 0.0


@dataclass
class HarmonizationResult:
    """Result of harmonizing a receipt."""

    image_id: str
    receipt_id: int

    # Current metadata
    current_merchant_name: Optional[str] = None
    current_place_id: Optional[str] = None
    current_address: Optional[str] = None
    current_phone: Optional[str] = None

    # Recommended metadata (from Google Places + consensus)
    recommended_merchant_name: Optional[str] = None
    recommended_place_id: Optional[str] = None
    recommended_address: Optional[str] = None
    recommended_phone: Optional[str] = None

    # Group info
    group_size: int = 0
    similar_receipts: list[tuple[str, int]] = field(default_factory=list)

    # Status
    needs_update: bool = False
    changes_needed: list[str] = field(default_factory=list)
    confidence: float = 0.0
    reasoning: str = ""


class MerchantHarmonizer:
    """
    Harmonizes merchant metadata across similar receipts.

    Uses ChromaDB to find receipts with similar content (address, phone, merchant name),
    then uses Google Places API to determine the correct metadata, and reports
    any inconsistencies.
    """

    def __init__(
        self,
        dynamo_client: Any,
        chroma_client: Any,
        places_client: Any,
        embed_fn: Any,
    ):
        self.dynamo = dynamo_client
        self.chroma = chroma_client
        self.places = places_client
        self.embed_fn = embed_fn

    async def find_similar_receipts(
        self,
        image_id: str,
        receipt_id: int,
        n_results: int = 20,
    ) -> list[dict]:
        """
        Find receipts similar to the given one based on content.

        Uses multiple signals:
        - Address lines
        - Phone number lines
        - Merchant name words
        """
        # Get the target receipt's details
        try:
            receipt_details = self.dynamo.get_receipt_details(
                image_id=image_id,
                receipt_id=receipt_id,
            )
        except Exception as e:
            logger.warning(f"Could not get receipt details for {image_id}#{receipt_id}: {e}")
            return []

        if not receipt_details:
            return []

        lines = receipt_details.lines or []
        words = receipt_details.words or []
        metadata = self.dynamo.get_receipt_metadata(image_id=image_id, receipt_id=receipt_id)

        # Collect signals from the receipt
        signals = {
            "address": metadata.address if metadata else None,
            "phone": metadata.phone_number if metadata else None,
            "merchant_name": metadata.merchant_name if metadata else None,
        }

        # Also extract from labeled words
        for word in words:
            label = getattr(word, 'label', None)
            if label == 'ADDRESS' and not signals["address"]:
                signals["address"] = word.text
            elif label == 'PHONE' and not signals["phone"]:
                signals["phone"] = word.text
            elif label == 'MERCHANT_NAME' and not signals["merchant_name"]:
                signals["merchant_name"] = word.text

        # Search for similar receipts using each signal
        similar_receipts: dict[tuple[str, int], dict] = {}

        for signal_type, signal_value in signals.items():
            if not signal_value:
                continue

            try:
                query_embedding = self.embed_fn([signal_value])[0]
                results = self.chroma.query(
                    collection_name="lines",
                    query_embeddings=[query_embedding],
                    n_results=n_results,
                    include=["metadatas", "documents", "distances"],
                )

                ids = results.get("ids", [[]])[0]
                documents = results.get("documents", [[]])[0]
                metadatas = results.get("metadatas", [[]])[0]
                distances = results.get("distances", [[]])[0]

                for doc_id, doc, meta, dist in zip(ids, documents, metadatas, distances):
                    other_image_id = meta.get("image_id")
                    other_receipt_id = meta.get("receipt_id")

                    if not other_image_id or other_receipt_id is None:
                        continue

                    # Skip self
                    if other_image_id == image_id and int(other_receipt_id) == receipt_id:
                        continue

                    key = (other_image_id, int(other_receipt_id))
                    similarity = max(0.0, 1.0 - (dist / 2))

                    if key not in similar_receipts:
                        similar_receipts[key] = {
                            "image_id": other_image_id,
                            "receipt_id": int(other_receipt_id),
                            "signals_matched": [],
                            "total_similarity": 0.0,
                            "metadata": {
                                "merchant_name": meta.get("merchant_name"),
                                "place_id": meta.get("place_id"),
                                "address": meta.get("normalized_full_address"),
                                "phone": meta.get("normalized_phone_10"),
                            },
                        }

                    similar_receipts[key]["signals_matched"].append({
                        "type": signal_type,
                        "query": signal_value,
                        "matched_text": doc,
                        "similarity": similarity,
                    })
                    similar_receipts[key]["total_similarity"] += similarity

            except Exception as e:
                logger.warning(f"Error searching for {signal_type}: {e}")

        # Filter: require high similarity on at least one signal
        MIN_SIMILARITY = 0.75
        filtered = {}
        for key, receipt in similar_receipts.items():
            # Check if any signal has high similarity
            high_sim_signals = [
                s for s in receipt["signals_matched"]
                if s["similarity"] >= MIN_SIMILARITY
            ]
            if high_sim_signals:
                receipt["high_similarity_signals"] = len(high_sim_signals)
                filtered[key] = receipt

        # Sort by: number of high-similarity signals, then total similarity
        results = sorted(
            filtered.values(),
            key=lambda x: (x["high_similarity_signals"], x["total_similarity"]),
            reverse=True,
        )

        return results

    def get_canonical_metadata(
        self,
        address: Optional[str] = None,
        phone: Optional[str] = None,
        merchant_name: Optional[str] = None,
    ) -> Optional[dict]:
        """
        Get canonical metadata from Google Places API.

        Searches using available signals and returns the best match.
        """
        if not self.places:
            return None

        result = None

        try:
            # Try phone search first (most specific)
            if phone and len(phone.replace("-", "").replace(" ", "")) >= 10:
                result = self.places.search_by_phone(phone)

            # Try address search
            if not result and address:
                result = self.places.search_by_address(address)

            # Try text search with merchant name + address
            if not result and merchant_name:
                query = merchant_name
                if address:
                    query += f" {address}"
                result = self.places.search_by_text(query)

            if result:
                return {
                    "place_id": result.get("place_id"),
                    "name": result.get("name"),
                    "address": result.get("formatted_address"),
                    "phone": result.get("formatted_phone_number") or result.get("international_phone_number"),
                }
        except Exception as e:
            logger.warning(f"Google Places search failed: {e}")

        return None

    async def harmonize_receipt(
        self,
        image_id: str,
        receipt_id: int,
    ) -> HarmonizationResult:
        """
        Harmonize a single receipt's metadata.

        1. Find similar receipts
        2. Determine consensus metadata
        3. Validate against Google Places
        4. Report needed changes
        """
        result = HarmonizationResult(
            image_id=image_id,
            receipt_id=receipt_id,
        )

        # Get current metadata
        try:
            metadata = self.dynamo.get_receipt_metadata(image_id=image_id, receipt_id=receipt_id)
        except Exception as e:
            logger.warning(f"Could not get metadata for {image_id}#{receipt_id}: {e}")
            result.reasoning = f"Could not load metadata: {e}"
            result.confidence = 0.0
            return result

        if metadata:
            result.current_merchant_name = metadata.merchant_name
            result.current_place_id = metadata.place_id
            result.current_address = metadata.address
            result.current_phone = metadata.phone_number

        # Find similar receipts
        similar = await self.find_similar_receipts(image_id, receipt_id)
        result.group_size = len(similar) + 1  # Include self
        result.similar_receipts = [(s["image_id"], s["receipt_id"]) for s in similar[:10]]

        if not similar:
            result.reasoning = "No similar receipts found. Cannot determine consensus."
            result.confidence = 0.0
            return result

        # Build consensus from similar receipts
        place_ids: dict[str, int] = {}
        addresses: dict[str, int] = {}
        phones: dict[str, int] = {}
        merchant_names: dict[str, int] = {}

        for s in similar:
            meta = s["metadata"]
            if meta.get("place_id"):
                place_ids[meta["place_id"]] = place_ids.get(meta["place_id"], 0) + 1
            if meta.get("address"):
                addresses[meta["address"]] = addresses.get(meta["address"], 0) + 1
            if meta.get("phone"):
                phones[meta["phone"]] = phones.get(meta["phone"], 0) + 1
            if meta.get("merchant_name"):
                merchant_names[meta["merchant_name"]] = merchant_names.get(meta["merchant_name"], 0) + 1

        # Get most common values
        consensus_place_id = max(place_ids.items(), key=lambda x: x[1])[0] if place_ids else None
        consensus_address = max(addresses.items(), key=lambda x: x[1])[0] if addresses else None
        consensus_phone = max(phones.items(), key=lambda x: x[1])[0] if phones else None
        consensus_merchant = max(merchant_names.items(), key=lambda x: x[1])[0] if merchant_names else None

        # Filter similar receipts to only those with matching place_id or merchant name
        truly_similar = [
            s for s in similar
            if (s["metadata"].get("place_id") == result.current_place_id and result.current_place_id)
            or (s["metadata"].get("merchant_name") == result.current_merchant_name and result.current_merchant_name)
        ]

        # Rebuild consensus from truly similar receipts only
        if truly_similar:
            place_ids = {}
            addresses = {}
            phones = {}
            merchant_names = {}

            for s in truly_similar:
                meta = s["metadata"]
                if meta.get("place_id"):
                    place_ids[meta["place_id"]] = place_ids.get(meta["place_id"], 0) + 1
                if meta.get("address"):
                    addresses[meta["address"]] = addresses.get(meta["address"], 0) + 1
                if meta.get("phone"):
                    phones[meta["phone"]] = phones.get(meta["phone"], 0) + 1
                if meta.get("merchant_name"):
                    merchant_names[meta["merchant_name"]] = merchant_names.get(meta["merchant_name"], 0) + 1

            consensus_place_id = max(place_ids.items(), key=lambda x: x[1])[0] if place_ids else result.current_place_id
            consensus_address = max(addresses.items(), key=lambda x: x[1])[0] if addresses else result.current_address
            consensus_phone = max(phones.items(), key=lambda x: x[1])[0] if phones else result.current_phone
            consensus_merchant = max(merchant_names.items(), key=lambda x: x[1])[0] if merchant_names else result.current_merchant_name

            result.group_size = len(truly_similar) + 1
            result.similar_receipts = [(s["image_id"], s["receipt_id"]) for s in truly_similar[:10]]

        # Try to get canonical data from Google Places using CURRENT metadata (not consensus)
        canonical = self.get_canonical_metadata(
            address=result.current_address,
            phone=result.current_phone,
            merchant_name=result.current_merchant_name,
        )

        # Use CONSENSUS as the recommendation, Google Places only to VALIDATE
        result.recommended_place_id = consensus_place_id or result.current_place_id
        result.recommended_merchant_name = consensus_merchant or result.current_merchant_name
        result.recommended_address = consensus_address or result.current_address
        result.recommended_phone = consensus_phone or result.current_phone

        # Google Places validation (confirms, doesn't override)
        google_validated = False
        google_validation_notes = []

        if canonical:
            # Check if Google Places confirms the consensus/current data
            if canonical.get("place_id") == result.recommended_place_id:
                google_validated = True
                google_validation_notes.append("✓ place_id confirmed by Google Places")
            elif canonical.get("place_id"):
                google_validation_notes.append(f"⚠ Google Places returned different place_id: {canonical.get('place_id')}")

            if canonical.get("name") and result.recommended_merchant_name:
                # Fuzzy match on merchant name
                if canonical.get("name").lower() in result.recommended_merchant_name.lower() or \
                   result.recommended_merchant_name.lower() in canonical.get("name").lower():
                    google_validation_notes.append("✓ merchant name confirmed by Google Places")
                else:
                    google_validation_notes.append(f"⚠ Google Places name differs: {canonical.get('name')}")

        # Confidence based on consensus strength + Google validation
        if truly_similar:
            consensus_strength = len(truly_similar) / 10  # More receipts = higher confidence
            result.confidence = min(0.9, 0.5 + consensus_strength)
            if google_validated:
                result.confidence = min(1.0, result.confidence + 0.1)
        else:
            result.confidence = 0.3  # Low confidence with no similar receipts

        result.google_validation = google_validation_notes  # type: ignore

        # Check what needs to change
        changes = []

        if result.recommended_place_id and result.current_place_id != result.recommended_place_id:
            changes.append(f"place_id: {result.current_place_id} → {result.recommended_place_id}")

        if result.recommended_merchant_name and result.current_merchant_name != result.recommended_merchant_name:
            changes.append(f"merchant_name: {result.current_merchant_name} → {result.recommended_merchant_name}")

        if result.recommended_address and result.current_address != result.recommended_address:
            changes.append(f"address: {result.current_address} → {result.recommended_address}")

        if result.recommended_phone and result.current_phone != result.recommended_phone:
            # Normalize phone comparison (ignore formatting)
            current_digits = ''.join(filter(str.isdigit, result.current_phone or ""))
            recommended_digits = ''.join(filter(str.isdigit, result.recommended_phone or ""))
            if current_digits != recommended_digits:
                changes.append(f"phone: {result.current_phone} → {result.recommended_phone}")

        result.needs_update = len(changes) > 0
        result.changes_needed = changes

        if changes:
            result.reasoning = f"Found {len(similar)} similar receipts. Metadata differs from consensus/Google Places."
        else:
            result.reasoning = f"Metadata matches consensus from {len(similar)} similar receipts."

        return result

    async def harmonize_all_for_merchant(
        self,
        merchant_name: str,
        apply_updates: bool = False,
    ) -> list[HarmonizationResult]:
        """
        Harmonize all receipts for a given merchant.

        Args:
            merchant_name: The merchant to harmonize
            apply_updates: If True, actually update the metadata in DynamoDB

        Returns:
            List of harmonization results
        """
        # Get all receipts for this merchant
        metadatas, _ = self.dynamo.get_receipt_metadatas_by_merchant(
            merchant_name=merchant_name,
            limit=100,
        )

        results = []
        for meta in metadatas:
            result = await self.harmonize_receipt(
                image_id=meta.image_id,
                receipt_id=meta.receipt_id,
            )
            results.append(result)

            if apply_updates and result.needs_update:
                # TODO: Implement actual DynamoDB update
                logger.info(
                    f"Would update {meta.image_id}#{meta.receipt_id}: {result.changes_needed}"
                )

        return results

    async def find_all_inconsistencies(
        self,
        limit: int = 100,
    ) -> list[HarmonizationResult]:
        """
        Scan receipts and find all metadata inconsistencies.

        Returns receipts that need updates.
        """
        # Get a sample of receipts
        metadatas, _ = self.dynamo.list_receipt_metadatas(limit=limit)

        inconsistent = []
        for meta in metadatas:
            result = await self.harmonize_receipt(
                image_id=meta.image_id,
                receipt_id=meta.receipt_id,
            )
            if result.needs_update:
                inconsistent.append(result)

        return inconsistent

