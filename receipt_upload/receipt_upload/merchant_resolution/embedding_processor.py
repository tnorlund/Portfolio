"""
Merchant-resolving embedding processor for unified upload container.

This processor:
1. Uses receipt_chroma.create_embeddings_and_compaction_run() to generate embeddings
   and get snapshot+delta pre-merged clients
2. Resolves merchant information using the MerchantResolver (two-tier strategy)
3. Enriches the receipt in DynamoDB with merchant data
4. Returns immediately while compaction happens asynchronously

Tracing:
- The process_embeddings method creates a parent trace per receipt
- Child traces for merchant resolution and label validation nest under the parent
"""

import logging
import os
from concurrent.futures import as_completed
from typing import Any, Dict, List, Optional, Tuple

import boto3
from receipt_chroma import create_embeddings_and_compaction_run
from receipt_upload.label_validation import (
    LLMBatchValidator,
    LightweightLabelValidator,
)
from receipt_upload.label_validation.langsmith_logging import (
    log_label_validation,
    log_merchant_resolution,
)
from receipt_upload.merchant_resolution.resolver import (
    MerchantResolver,
    MerchantResult,
)

from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.entities import ReceiptLine, ReceiptWord, ReceiptWordLabel

logger = logging.getLogger(__name__)


def _get_traceable():
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


def _get_label_validation_project() -> str:
    """Get the Langsmith project name for label validation from env var."""
    return os.environ.get("LANGCHAIN_PROJECT", "receipt-label-validation")


def _get_context_thread_pool_executor():
    """Get ContextThreadPoolExecutor if langsmith is available, else ThreadPoolExecutor.

    ContextThreadPoolExecutor automatically propagates context variables (including
    Langsmith trace context) to child threads, enabling proper trace nesting.

    See: https://docs.smith.langchain.com/reference/python/utils/langsmith.utils.ContextThreadPoolExecutor
    """
    try:
        from langsmith.utils import ContextThreadPoolExecutor

        return ContextThreadPoolExecutor
    except ImportError:
        from concurrent.futures import ThreadPoolExecutor

        return ThreadPoolExecutor


def _log(msg: str) -> None:
    """Log message with immediate flush for CloudWatch visibility."""
    print(f"[MERCHANT_EMBEDDING_PROCESSOR] {msg}", flush=True)
    logger.info(msg)


class MerchantResolvingEmbeddingProcessor:
    """
    Generate embeddings and resolve merchant information for a receipt.

    This processor:
    1. Generates embeddings using receipt_chroma orchestration
    2. Queries the merged snapshot+delta clients for merchant resolution
    3. Updates DynamoDB with merchant information
    4. Triggers async compaction via SQS
    """

    def __init__(
        self,
        table_name: str,
        chromadb_bucket: str,
        chroma_http_endpoint: Optional[str] = None,
        google_places_api_key: Optional[str] = None,
        openai_api_key: Optional[str] = None,
        lines_queue_url: Optional[str] = None,
        words_queue_url: Optional[str] = None,
    ):
        """
        Initialize the processor.

        Args:
            table_name: DynamoDB table name
            chromadb_bucket: S3 bucket for ChromaDB snapshots/deltas
            chroma_http_endpoint: Optional HTTP endpoint (unused, kept for compat)
            google_places_api_key: Google Places API key for Tier 2 resolution
            openai_api_key: OpenAI API key for embeddings
            lines_queue_url: SQS queue URL for lines compaction (set via env)
            words_queue_url: SQS queue URL for words compaction (set via env)
        """
        self.dynamo = DynamoClient(table_name)
        self.chromadb_bucket = chromadb_bucket
        self.openai_api_key = openai_api_key
        self.google_places_api_key = google_places_api_key

        # Set queue URLs in environment for receipt_chroma to use
        if lines_queue_url:
            os.environ["CHROMADB_LINES_QUEUE_URL"] = lines_queue_url
        if words_queue_url:
            os.environ["CHROMADB_WORDS_QUEUE_URL"] = words_queue_url

        # Initialize Places client if API key provided
        self.places_client = None
        if google_places_api_key:
            try:
                from receipt_places import PlacesClient

                self.places_client = PlacesClient(
                    api_key=google_places_api_key
                )
            except ImportError:
                _log("WARNING: receipt_places not available")

        # Initialize merchant resolver
        self.merchant_resolver = MerchantResolver(
            dynamo_client=self.dynamo,
            places_client=self.places_client,
        )

        # S3 client for snapshot downloads
        self.s3_client = boto3.client("s3")

    def process_embeddings(
        self,
        image_id: str,
        receipt_id: int,
        lines: Optional[List[ReceiptLine]] = None,
        words: Optional[List[ReceiptWord]] = None,
    ) -> Dict[str, Any]:
        """
        Generate embeddings, resolve merchant, and enrich receipt.

        This method creates a parent Langsmith trace for the entire receipt
        processing pipeline. Child traces for merchant resolution and label
        validation will nest under this parent.

        Args:
            image_id: Receipt's image_id
            receipt_id: Receipt's receipt_id
            lines: Optional list of ReceiptLine entities (fetched if not provided)
            words: Optional list of ReceiptWord entities (fetched if not provided)

        Returns:
            Dict with success status, merchant info, and compaction run details
        """
        # Create traced wrapper for hierarchical tracing
        traceable = _get_traceable()

        @traceable(
            name="receipt_processing",
            project_name=_get_label_validation_project(),
            tags=["upload_lambda"],
            metadata={
                "image_id": image_id,
                "receipt_id": receipt_id,
            },
        )
        def _traced_process_embeddings(
            image_id: str,
            receipt_id: int,
            lines: Optional[List[ReceiptLine]],
            words: Optional[List[ReceiptWord]],
        ) -> Dict[str, Any]:
            return self._process_embeddings_impl(
                image_id=image_id,
                receipt_id=receipt_id,
                lines=lines,
                words=words,
            )

        return _traced_process_embeddings(
            image_id=image_id,
            receipt_id=receipt_id,
            lines=lines,
            words=words,
        )

    def _process_embeddings_impl(
        self,
        image_id: str,
        receipt_id: int,
        lines: Optional[List[ReceiptLine]] = None,
        words: Optional[List[ReceiptWord]] = None,
    ) -> Dict[str, Any]:
        """Implementation of process_embeddings (called within trace context)."""
        # Fetch lines/words if not provided
        if lines is None or words is None:
            lines = self.dynamo.list_receipt_lines_from_receipt(
                image_id, receipt_id
            )
            words = self.dynamo.list_receipt_words_from_receipt(
                image_id, receipt_id
            )
            _log(
                f"Fetched {len(lines)} lines and {len(words)} words from DynamoDB"
            )
        else:
            _log(f"Using provided {len(lines)} lines and {len(words)} words")

        # Get word labels for enrichment
        word_labels: List[ReceiptWordLabel] = []
        try:
            word_labels, _ = self.dynamo.list_receipt_word_labels_for_receipt(
                image_id, receipt_id
            )
        except Exception as e:
            _log(f"Could not fetch word labels: {e}")

        # Get existing receipt place for merchant context
        receipt_place = None
        try:
            receipt_place = self.dynamo.get_receipt_place(
                image_id, receipt_id
            )
        except Exception as e:
            _log(f"Could not fetch receipt place: {e}")

        # Step 1: Generate embeddings and get merged snapshot+delta clients
        _log(
            f"Creating embeddings for {image_id}#{receipt_id} "
            f"({len(lines)} lines, {len(words)} words)"
        )

        # Resolve merchant name from receipt_place
        merchant_name = None
        if receipt_place and receipt_place.merchant_name:
            merchant_name = receipt_place.merchant_name

        try:
            result = create_embeddings_and_compaction_run(
                receipt_lines=lines,
                receipt_words=words,
                image_id=image_id,
                receipt_id=receipt_id,
                chromadb_bucket=self.chromadb_bucket,
                dynamo_client=self.dynamo,
                s3_client=self.s3_client,
                receipt_place=receipt_place,
                receipt_word_labels=word_labels,
                merchant_name=merchant_name,
                sqs_notify=True,  # Trigger async compaction
            )
        except Exception as e:
            _log(f"ERROR: Failed to create embeddings: {e}")
            logger.exception("Embedding creation failed")
            return {
                "success": False,
                "error": str(e),
                "merchant_found": False,
            }

        merchant_result = MerchantResult()
        validation_stats: Dict[str, Any] = {}

        try:
            # Step 2: Run merchant resolution AND label validation in PARALLEL
            # These are independent operations that can run concurrently
            _log("Starting Phase 2: parallel merchant resolution + label validation")

            # Use ContextThreadPoolExecutor to automatically propagate trace context
            # This ensures child threads inherit the parent trace
            ThreadPoolExecutorClass = _get_context_thread_pool_executor()

            def _resolve_merchant() -> MerchantResult:
                """Run merchant resolution (traced internally)."""
                return self.merchant_resolver.resolve(
                    lines_client=result.lines_client,
                    lines=lines,
                    words=words,
                    image_id=image_id,
                    receipt_id=receipt_id,
                    line_embeddings=result.line_embeddings,
                )

            def _validate_labels() -> Dict[str, Any]:
                """Run label validation (traced internally)."""
                # merchant_name is None here since validation doesn't depend on it
                return self._validate_pending_labels(
                    image_id=image_id,
                    receipt_id=receipt_id,
                    word_labels=word_labels,
                    words=words,
                    words_client=result.words_client,
                    merchant_name=None,  # Not needed for validation
                    word_embeddings=result.word_embeddings,
                )

            with ThreadPoolExecutorClass(max_workers=2) as executor:
                # ContextThreadPoolExecutor automatically propagates trace context
                merchant_future = executor.submit(_resolve_merchant)
                validation_future = executor.submit(_validate_labels)

                # Wait for both to complete
                for future in as_completed([merchant_future, validation_future]):
                    try:
                        future.result()  # Raise any exceptions
                    except Exception as e:
                        _log(f"WARNING: Parallel task failed: {e}")
                        logger.exception("Parallel task failed")

                # Get results (may raise if failed)
                try:
                    merchant_result = merchant_future.result()
                except Exception as e:
                    _log(f"WARNING: Merchant resolution failed: {e}")
                    merchant_result = MerchantResult()

                try:
                    validation_stats = validation_future.result()
                except Exception as e:
                    _log(f"WARNING: Label validation failed: {e}")
                    validation_stats = {}

            _log("Phase 2 complete: merchant + validation finished")

            # Step 3: Log merchant resolution to Langsmith (after parallel phase)
            similarity_matches_data = None
            if merchant_result.similarity_matches:
                similarity_matches_data = [
                    {
                        "image_id": m.image_id,
                        "receipt_id": m.receipt_id,
                        "merchant_name": m.merchant_name,
                        "embedding_similarity": m.embedding_similarity,
                        "metadata_boost": m.metadata_boost,
                        "total_confidence": m.total_confidence,
                    }
                    for m in merchant_result.similarity_matches[:5]
                ]

            log_merchant_resolution(
                image_id=image_id,
                receipt_id=receipt_id,
                resolution_tier=merchant_result.resolution_tier or "not_found",
                merchant_name=merchant_result.merchant_name,
                place_id=merchant_result.place_id,
                confidence=merchant_result.confidence,
                phone_extracted=merchant_result.phone,
                address_extracted=merchant_result.address,
                similarity_matches=similarity_matches_data,
                source_receipt=(
                    f"{merchant_result.source_image_id}#{merchant_result.source_receipt_id}"
                    if merchant_result.source_image_id
                    else None
                ),
            )

            # Step 4: Enrich receipt in DynamoDB if merchant found
            if merchant_result.place_id:
                _log(
                    f"Enriching receipt with merchant: {merchant_result.merchant_name} "
                    f"(place_id={merchant_result.place_id}, "
                    f"tier={merchant_result.resolution_tier})"
                )
                self._enrich_receipt_place(
                    image_id=image_id,
                    receipt_id=receipt_id,
                    merchant_result=merchant_result,
                    existing_place=receipt_place,
                )
            else:
                _log("No merchant found - receipt will not be enriched")

        except Exception as e:
            _log(f"WARNING: Phase 2 failed: {e}")
            logger.exception("Phase 2 failed")

        finally:
            # Always close the clients to release file locks
            result.close()

        return {
            "success": True,
            "run_id": result.compaction_run.run_id,
            "lines_count": len(lines),
            "words_count": len(words),
            "merchant_found": merchant_result.place_id is not None,
            "merchant_name": merchant_result.merchant_name,
            "merchant_place_id": merchant_result.place_id,
            "merchant_resolution_tier": merchant_result.resolution_tier,
            "merchant_confidence": merchant_result.confidence,
            **validation_stats,
        }

    def _enrich_receipt_place(
        self,
        image_id: str,
        receipt_id: int,
        merchant_result: MerchantResult,
        existing_place: Optional[Any] = None,
    ) -> None:
        """
        Update receipt place in DynamoDB with merchant information.

        Args:
            image_id: Receipt's image_id
            receipt_id: Receipt's receipt_id
            merchant_result: Resolved merchant information
            existing_place: Optional pre-fetched place to avoid duplicate query
        """
        try:
            # Use provided place or fetch if not available
            place = existing_place
            if place is None:
                try:
                    place = self.dynamo.get_receipt_place(image_id, receipt_id)
                except Exception:
                    place = None

            if place:
                # Update existing place with merchant info
                updates = {}

                if merchant_result.place_id:
                    updates["place_id"] = merchant_result.place_id

                if merchant_result.merchant_name:
                    if not place.merchant_name:
                        updates["merchant_name"] = merchant_result.merchant_name

                if merchant_result.address:
                    if not place.formatted_address:
                        updates["formatted_address"] = merchant_result.address

                if merchant_result.phone:
                    if not place.phone_number:
                        updates["phone_number"] = merchant_result.phone

                if updates:
                    self.dynamo.update_receipt_place(
                        image_id=image_id,
                        receipt_id=receipt_id,
                        **updates,
                    )
                    _log(
                        f"Updated receipt place with: {list(updates.keys())}"
                    )
            else:
                # Create new receipt place if none exists
                # Only create if we have both place_id AND merchant_name
                # (ReceiptPlace requires merchant_name to be non-empty)
                if merchant_result.place_id and merchant_result.merchant_name:
                    from receipt_dynamo.entities import ReceiptPlace

                    new_place = ReceiptPlace(
                        image_id=image_id,
                        receipt_id=receipt_id,
                        place_id=merchant_result.place_id,
                        merchant_name=merchant_result.merchant_name,
                        formatted_address=merchant_result.address or "",
                        phone_number=merchant_result.phone or "",
                    )
                    self.dynamo.add_receipt_place(new_place)
                    _log(
                        f"Created new receipt place for {image_id}#{receipt_id}"
                    )
                elif merchant_result.place_id:
                    # Have place_id but no merchant_name - log for debugging
                    # This can happen when ChromaDB matches don't have merchant_name
                    _log(
                        f"Skipping receipt place creation - have place_id "
                        f"({merchant_result.place_id}) but no merchant_name"
                    )

        except Exception as e:
            _log(f"ERROR: Failed to enrich receipt place: {e}")
            logger.exception("Place enrichment failed")
            # Don't raise - this is a dual-write, metadata update may have succeeded

    def _validate_pending_labels(
        self,
        image_id: str,
        receipt_id: int,
        word_labels: List[ReceiptWordLabel],
        words: List[ReceiptWord],
        words_client: Any,
        merchant_name: Optional[str] = None,
        word_embeddings: Optional[Dict[Tuple[int, int], List[float]]] = None,
    ) -> Dict[str, Any]:
        """
        Validate pending labels using two-tier strategy.

        Tier 1 (ChromaDB Similarity): Use LightweightLabelValidator to find
        consensus among similar validated words. Auto-validate high-confidence
        matches without LLM calls.

        Tier 2 (LLM Fallback): For labels that ChromaDB couldn't validate with
        high confidence, fall back to LLM validation with similarity evidence.

        Args:
            image_id: Receipt's image_id
            receipt_id: Receipt's receipt_id
            word_labels: List of word labels to validate
            words: List of ReceiptWord entities with positions
            words_client: ChromaDB client with words collection
            merchant_name: Optional merchant name for context
            word_embeddings: Optional cached embeddings from orchestration

        Returns:
            Dict with validation statistics
        """
        from receipt_upload.label_validation.validator import ValidationDecision

        if not word_labels:
            return {"labels_validated": 0, "labels_corrected": 0, "chroma_validated": 0}

        # Filter to only PENDING labels
        pending_label_entities = [
            label
            for label in word_labels
            if label.validation_status == ValidationStatus.PENDING.value
        ]

        if not pending_label_entities:
            _log("No pending labels to validate")
            return {"labels_validated": 0, "labels_corrected": 0, "chroma_validated": 0}

        _log(f"Validating {len(pending_label_entities)} pending labels (ChromaDB first, then LLM)")

        # Build word lookup by (line_id, word_id)
        word_lookup: Dict[tuple, ReceiptWord] = {}
        for word in words:
            word_lookup[(word.line_id, word.word_id)] = word

        # Initialize ChromaDB similarity validator with cached embeddings
        similarity_validator = LightweightLabelValidator(
            words_client=words_client,
            merchant_name=merchant_name,
            word_embeddings=word_embeddings or {},  # Use cached embeddings
        )

        # =========================================================================
        # TIER 1: ChromaDB Consensus Validation
        # =========================================================================
        # Try to validate labels using similarity consensus first (no LLM cost)
        chroma_validated_count = 0
        chroma_needs_review = []  # Labels that need LLM review
        labels_needing_llm = []  # Labels ChromaDB couldn't validate

        for label in pending_label_entities:
            try:
                result = similarity_validator.validate_label(
                    image_id=image_id,
                    receipt_id=receipt_id,
                    line_id=label.line_id,
                    word_id=label.word_id,
                    predicted_label=label.label,
                )

                if result.decision == ValidationDecision.AUTO_VALIDATE:
                    # High confidence from ChromaDB - auto-validate without LLM
                    label.validation_status = ValidationStatus.VALID.value
                    label.label_proposed_by = (
                        f"chroma-validated:{label.label_proposed_by or 'auto'}"
                    )
                    label.reasoning = result.reason
                    self.dynamo.update_receipt_word_label(label)
                    chroma_validated_count += 1

                    # Get word text for logging
                    word = word_lookup.get((label.line_id, label.word_id))
                    word_text = word.text if word else ""

                    # Log to Langsmith
                    log_label_validation(
                        image_id=image_id,
                        receipt_id=receipt_id,
                        line_id=label.line_id,
                        word_id=label.word_id,
                        word_text=word_text,
                        predicted_label=label.label,
                        final_label=label.label,
                        validation_source="chroma",
                        decision="valid",
                        confidence=result.confidence,
                        reasoning=result.reason,
                        merchant_name=merchant_name,
                    )

                    _log(
                        f"  ChromaDB validated: {label.line_id}_{label.word_id} "
                        f"label={label.label} (conf={result.confidence:.2f})"
                    )

                elif result.decision == ValidationDecision.AUTO_INVALID:
                    # Strong evidence AGAINST the prediction - mark as invalid
                    label.validation_status = ValidationStatus.INVALID.value
                    label.label_proposed_by = (
                        f"chroma-invalidated:{label.label_proposed_by or 'auto'}"
                    )
                    label.reasoning = result.reason
                    self.dynamo.update_receipt_word_label(label)

                    # Get word text for logging
                    word = word_lookup.get((label.line_id, label.word_id))
                    word_text = word.text if word else ""

                    # Serialize label_scores for logging
                    label_scores_data = [
                        {
                            "label": s.label,
                            "match_count": s.match_count,
                            "avg_similarity": round(s.avg_similarity, 4),
                            "score": round(s.score, 2),
                        }
                        for s in (result.label_scores or [])
                    ]

                    # Log to Langsmith
                    log_label_validation(
                        image_id=image_id,
                        receipt_id=receipt_id,
                        line_id=label.line_id,
                        word_id=label.word_id,
                        word_text=word_text,
                        predicted_label=label.label,
                        final_label=label.label,  # Keep original for audit
                        validation_source="chroma",
                        decision="invalid",
                        confidence=result.confidence,
                        reasoning=result.reason,
                        merchant_name=merchant_name,
                        suggested_label=result.suggested_label,
                        label_scores=label_scores_data,
                    )

                    _log(
                        f"  ChromaDB invalidated: {label.line_id}_{label.word_id} "
                        f"label={label.label} suggested={result.suggested_label} "
                        f"(conf={result.confidence:.2f})"
                    )

                elif result.decision == ValidationDecision.NEEDS_REVIEW:
                    # ChromaDB found disagreement - needs LLM to decide
                    chroma_needs_review.append({
                        "label": label,
                        "chroma_result": result,
                    })
                    labels_needing_llm.append(label)

                else:  # KEEP_PENDING - not enough data
                    labels_needing_llm.append(label)

            except Exception as e:
                _log(f"WARNING: ChromaDB validation failed for {label.line_id}_{label.word_id}: {e}")
                labels_needing_llm.append(label)

        _log(
            f"Tier 1 (ChromaDB): validated={chroma_validated_count}, "
            f"needs_review={len(chroma_needs_review)}, needs_llm={len(labels_needing_llm)}"
        )

        # Create lookup for chroma results (for labels that had NEEDS_REVIEW)
        chroma_results_lookup = {
            (item["label"].line_id, item["label"].word_id): item["chroma_result"]
            for item in chroma_needs_review
        }

        # =========================================================================
        # TIER 2: LLM Validation (Fallback)
        # =========================================================================
        # For labels that ChromaDB couldn't validate, use LLM
        if not labels_needing_llm:
            _log("All labels validated by ChromaDB, skipping LLM")
            return {
                "labels_validated": chroma_validated_count,
                "labels_corrected": 0,
                "chroma_validated": chroma_validated_count,
            }

        _log(f"Tier 2: Validating {len(labels_needing_llm)} labels with LLM")

        # Convert words to dicts for LLM prompt
        words_data = []
        for word in words:
            x_center, y_center = word.calculate_centroid()
            words_data.append({
                "text": word.text,
                "line_id": word.line_id,
                "word_id": word.word_id,
                "x": x_center,
                "y": y_center,
            })

        # Build pending labels list with word text
        pending_labels_data = []
        for label in labels_needing_llm:
            word = word_lookup.get((label.line_id, label.word_id))
            word_text = word.text if word else ""
            pending_labels_data.append({
                "line_id": label.line_id,
                "word_id": label.word_id,
                "label": label.label,
                "word_text": word_text,
                "entity": label,  # Keep reference for updating
            })

        # Query similar words for LLM evidence
        similar_evidence: Dict[str, List[Dict]] = {}
        for label_data in pending_labels_data:
            word_id_str = f"{label_data['line_id']}_{label_data['word_id']}"
            try:
                line_id_val: int = int(label_data['line_id'])
                word_id_val: int = int(label_data['word_id'])
                chroma_id = (
                    f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}"
                    f"#LINE#{line_id_val:05d}#WORD#{word_id_val:05d}"
                )
                embedding = similarity_validator._get_word_embedding(
                    chroma_id, line_id_val, word_id_val
                )

                if embedding:
                    similar = similarity_validator._query_similar_validated(
                        embedding=embedding,
                        exclude_id=chroma_id,
                        n_results=10,
                    )
                    similar_evidence[word_id_str] = similar
                else:
                    similar_evidence[word_id_str] = []
            except Exception as e:
                _log(f"WARNING: Failed to get similar words for {word_id_str}: {e}")
                similar_evidence[word_id_str] = []

        # Call LLM to validate remaining labels
        try:
            llm_validator = LLMBatchValidator(temperature=0.0, timeout=120)
            llm_results = llm_validator.validate_receipt_labels(
                pending_labels=[
                    {k: v for k, v in label.items() if k != "entity"}
                    for label in pending_labels_data
                ],
                words=words_data,
                similar_evidence=similar_evidence,
                merchant_name=merchant_name,
            )
        except Exception as e:
            _log(f"ERROR: LLM validation failed: {e}")
            logger.exception("LLM validation failed")
            return {
                "labels_validated": chroma_validated_count,
                "labels_corrected": 0,
                "chroma_validated": chroma_validated_count,
                "error": str(e),
            }

        # Update labels in DynamoDB based on LLM results
        validated_count = 0
        corrected_count = 0
        result_lookup = {r.word_id: r for r in llm_results}

        for label_data in pending_labels_data:
            word_id = f"{label_data['line_id']}_{label_data['word_id']}"
            label_entity = label_data["entity"]
            llm_result = result_lookup.get(word_id)

            if not llm_result:
                _log(f"WARNING: No LLM result for {word_id}")
                continue

            try:
                # Map LLM confidence to numeric value
                confidence_map = {"high": 0.9, "medium": 0.7, "low": 0.5}
                confidence_score = confidence_map.get(llm_result.confidence, 0.7)

                # Normalize decision: CORRECT/CORRECTED -> INVALID
                decision = llm_result.decision.upper()
                if decision in ("CORRECT", "CORRECTED"):
                    decision = "INVALID"
                elif decision == "NEEDS REVIEW":
                    decision = "NEEDS_REVIEW"

                # Get chroma suggestion if available (from Tier 1 NEEDS_REVIEW)
                chroma_result = chroma_results_lookup.get(
                    (label_entity.line_id, label_entity.word_id)
                )
                chroma_suggested = chroma_result.suggested_label if chroma_result else None
                chroma_scores = None
                if chroma_result and chroma_result.label_scores:
                    chroma_scores = [
                        {
                            "label": s.label,
                            "match_count": s.match_count,
                            "avg_similarity": round(s.avg_similarity, 4),
                            "score": round(s.score, 2),
                        }
                        for s in chroma_result.label_scores
                    ]

                if decision == "VALID":
                    # Keep original label, mark as validated
                    label_entity.validation_status = ValidationStatus.VALID.value
                    label_entity.label_proposed_by = (
                        f"llm-validated:{label_entity.label_proposed_by or 'auto'}"
                    )
                    label_entity.reasoning = llm_result.reasoning
                    self.dynamo.update_receipt_word_label(label_entity)
                    validated_count += 1

                    # Log to Langsmith
                    log_label_validation(
                        image_id=image_id,
                        receipt_id=receipt_id,
                        line_id=label_entity.line_id,
                        word_id=label_entity.word_id,
                        word_text=label_data.get("word_text", ""),
                        predicted_label=label_data["label"],
                        final_label=llm_result.label,
                        validation_source="llm",
                        decision="valid",
                        confidence=confidence_score,
                        reasoning=llm_result.reasoning,
                        similar_words=similar_evidence.get(word_id, []),
                        merchant_name=merchant_name,
                        suggested_label=chroma_suggested,
                        label_scores=chroma_scores,
                    )

                elif decision == "NEEDS_REVIEW":
                    # LLM couldn't decide - mark for human review
                    label_entity.validation_status = ValidationStatus.NEEDS_REVIEW.value
                    label_entity.label_proposed_by = (
                        f"llm-needs-review:{label_entity.label_proposed_by or 'auto'}"
                    )
                    label_entity.reasoning = llm_result.reasoning
                    self.dynamo.update_receipt_word_label(label_entity)

                    # Log to Langsmith
                    log_label_validation(
                        image_id=image_id,
                        receipt_id=receipt_id,
                        line_id=label_entity.line_id,
                        word_id=label_entity.word_id,
                        word_text=label_data.get("word_text", ""),
                        predicted_label=label_data["label"],
                        final_label=llm_result.label,
                        validation_source="llm",
                        decision="needs_review",
                        confidence=confidence_score,
                        reasoning=llm_result.reasoning,
                        similar_words=similar_evidence.get(word_id, []),
                        merchant_name=merchant_name,
                        suggested_label=chroma_suggested,
                        label_scores=chroma_scores,
                    )

                    _log(
                        f"Marked {word_id} as NEEDS_REVIEW: {llm_result.reasoning[:50]}..."
                    )

                elif decision == "INVALID":
                    # LLM invalidated the label and provided a correction
                    if llm_result.label != label_entity.label:
                        # Invalidate old label (keep for audit trail), create new one
                        from datetime import datetime, timezone

                        # 1. Mark old label as INVALID (audit trail)
                        label_entity.validation_status = ValidationStatus.INVALID.value
                        label_entity.reasoning = (
                            f"Invalidated by LLM - corrected to {llm_result.label}. "
                            f"{llm_result.reasoning}"
                        )
                        self.dynamo.update_receipt_word_label(label_entity)

                        # 2. Create new label with corrected value
                        new_label = ReceiptWordLabel(
                            image_id=image_id,
                            receipt_id=receipt_id,
                            line_id=label_entity.line_id,
                            word_id=label_entity.word_id,
                            label=llm_result.label,
                            reasoning=llm_result.reasoning,
                            timestamp_added=datetime.now(timezone.utc),
                            validation_status=ValidationStatus.VALID.value,
                            label_proposed_by=f"llm-invalidated:{label_entity.label_proposed_by or 'auto'}",
                            label_consolidated_from=label_entity.label,
                        )
                        self.dynamo.add_receipt_word_label(new_label)
                        corrected_count += 1

                        # Log correction to Langsmith
                        log_label_validation(
                            image_id=image_id,
                            receipt_id=receipt_id,
                            line_id=label_entity.line_id,
                            word_id=label_entity.word_id,
                            word_text=label_data.get("word_text", ""),
                            predicted_label=label_data["label"],
                            final_label=llm_result.label,
                            validation_source="llm",
                            decision="invalid",
                            confidence=confidence_score,
                            reasoning=llm_result.reasoning,
                            similar_words=similar_evidence.get(word_id, []),
                            merchant_name=merchant_name,
                            suggested_label=chroma_suggested,
                            label_scores=chroma_scores,
                        )

                        _log(
                            f"Corrected {word_id}: {label_entity.label} -> {llm_result.label}"
                        )
                    else:
                        # Same label, just validate it
                        label_entity.validation_status = ValidationStatus.VALID.value
                        label_entity.label_proposed_by = (
                            f"llm-validated:{label_entity.label_proposed_by or 'auto'}"
                        )
                        label_entity.reasoning = llm_result.reasoning
                        self.dynamo.update_receipt_word_label(label_entity)
                        validated_count += 1

                        # Log to Langsmith
                        log_label_validation(
                            image_id=image_id,
                            receipt_id=receipt_id,
                            line_id=label_entity.line_id,
                            word_id=label_entity.word_id,
                            word_text=label_data.get("word_text", ""),
                            predicted_label=label_data["label"],
                            final_label=llm_result.label,
                            validation_source="llm",
                            decision="valid",
                            confidence=confidence_score,
                            reasoning=llm_result.reasoning,
                            similar_words=similar_evidence.get(word_id, []),
                            merchant_name=merchant_name,
                            suggested_label=chroma_suggested,
                            label_scores=chroma_scores,
                        )

                else:
                    label_entity.validation_status = ValidationStatus.NEEDS_REVIEW.value
                    label_entity.label_proposed_by = (
                        f"llm-needs-review:{label_entity.label_proposed_by or 'auto'}"
                    )
                    label_entity.reasoning = (
                        f"Unrecognized decision '{decision}'. {llm_result.reasoning}"
                    )
                    self.dynamo.update_receipt_word_label(label_entity)

                    log_label_validation(
                        image_id=image_id,
                        receipt_id=receipt_id,
                        line_id=label_entity.line_id,
                        word_id=label_entity.word_id,
                        word_text=label_data.get("word_text", ""),
                        predicted_label=label_data["label"],
                        final_label=llm_result.label,
                        validation_source="llm",
                        decision="needs_review",
                        confidence=confidence_score,
                        reasoning=llm_result.reasoning,
                        similar_words=similar_evidence.get(word_id, []),
                        merchant_name=merchant_name,
                        suggested_label=chroma_suggested,
                        label_scores=chroma_scores,
                    )

            except Exception as e:
                _log(
                    f"WARNING: Failed to update label {word_id}: {e}"
                )

        total_validated = chroma_validated_count + validated_count
        _log(
            f"Label validation complete: "
            f"chroma={chroma_validated_count}, llm={validated_count}, "
            f"corrected={corrected_count}, total={total_validated}"
        )

        return {
            "labels_validated": total_validated,
            "labels_corrected": corrected_count,
            "chroma_validated": chroma_validated_count,
            "llm_validated": validated_count,
        }
