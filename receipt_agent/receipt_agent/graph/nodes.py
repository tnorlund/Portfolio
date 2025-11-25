"""
LangGraph node functions for the validation workflow.

Each node represents a distinct phase in the validation process,
with clear inputs, outputs, and responsibilities.
"""

import logging
from typing import Any, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from receipt_agent.state.models import (
    ChromaSearchResult,
    EvidenceType,
    MerchantCandidate,
    ReceiptContext,
    ValidationResult,
    ValidationState,
    ValidationStatus,
    VerificationEvidence,
    VerificationStep,
)

logger = logging.getLogger(__name__)


async def load_metadata(
    state: ValidationState,
    dynamo_client: Any,
) -> dict[str, Any]:
    """
    Load current receipt metadata and context from DynamoDB.

    This is the entry node that fetches the current state of the
    receipt we're validating.
    """
    logger.info(
        f"Loading metadata for {state.image_id}#{state.receipt_id}"
    )

    updates: dict[str, Any] = {"current_step": "load_metadata"}

    try:
        # Get current metadata
        metadata = dynamo_client.get_receipt_metadata(
            image_id=state.image_id,
            receipt_id=state.receipt_id,
        )

        if metadata:
            updates.update({
                "current_merchant_name": metadata.merchant_name,
                "current_place_id": metadata.place_id,
                "current_address": metadata.address,
                "current_phone": metadata.phone_number,
                "current_validation_status": metadata.validation_status,
            })
        else:
            logger.warning(
                f"No metadata found for {state.image_id}#{state.receipt_id}"
            )

        # Get receipt context (lines, words, etc.)
        _, receipt, words, lines, _, labels = dynamo_client.get_receipt_details(
            image_id=state.image_id,
            receipt_id=state.receipt_id,
        )

        if receipt:
            raw_lines = []
            if lines:
                raw_lines = [ln.text for ln in sorted(lines, key=lambda x: x.line_id)]

            # Extract candidate data
            extracted_merchant = None
            extracted_address = None
            extracted_phone = None

            if words:
                for word in words:
                    ext = getattr(word, "extracted_data", None) or {}
                    data_type = (ext.get("type") or "").lower()
                    value = ext.get("value") or word.text

                    if data_type == "merchant_name" and not extracted_merchant:
                        extracted_merchant = value
                    elif data_type == "address" and not extracted_address:
                        extracted_address = value
                    elif data_type == "phone" and not extracted_phone:
                        extracted_phone = value

            updates["receipt_context"] = ReceiptContext(
                image_id=state.image_id,
                receipt_id=state.receipt_id,
                raw_text=raw_lines[:50],  # Limit
                extracted_merchant_name=extracted_merchant,
                extracted_address=extracted_address,
                extracted_phone=extracted_phone,
                line_embeddings_available=True,  # Will verify in next step
                word_embeddings_available=True,
            )

        # Add initial message to conversation
        initial_message = HumanMessage(
            content=f"""Validate the merchant metadata for receipt {state.image_id}#{state.receipt_id}.

Current metadata:
- Merchant Name: {updates.get('current_merchant_name', 'Unknown')}
- Place ID: {updates.get('current_place_id', 'None')}
- Address: {updates.get('current_address', 'None')}
- Phone: {updates.get('current_phone', 'None')}
- Validation Status: {updates.get('current_validation_status', 'Unknown')}

Please verify this information is accurate using ChromaDB similarity search and cross-reference with other receipts."""
        )
        updates["messages"] = [initial_message]

    except Exception as e:
        logger.error(f"Error loading metadata: {e}")
        updates["errors"] = [f"Failed to load metadata: {str(e)}"]

    return updates


async def search_similar_receipts(
    state: ValidationState,
    chroma_client: Any,
    embed_fn: Any,
) -> dict[str, Any]:
    """
    Search ChromaDB for similar receipts and lines.

    This node uses the receipt's address, phone, and merchant name
    to find similar receipts that can help validate the metadata.
    """
    logger.info(
        f"Searching similar receipts for {state.image_id}#{state.receipt_id}"
    )

    updates: dict[str, Any] = {"current_step": "search_similar"}
    chroma_line_results: list[ChromaSearchResult] = []
    merchant_candidates: list[MerchantCandidate] = []
    verification_steps: list[VerificationStep] = []

    try:
        # Build search queries from context
        queries = []
        if state.current_address:
            queries.append(("address", state.current_address))
        if state.current_phone:
            queries.append(("phone", state.current_phone))
        if state.current_merchant_name:
            queries.append(("merchant", state.current_merchant_name))

        # Also use extracted data if different
        if state.receipt_context:
            ctx = state.receipt_context
            if ctx.extracted_address and ctx.extracted_address != state.current_address:
                queries.append(("extracted_address", ctx.extracted_address))
            if ctx.extracted_phone and ctx.extracted_phone != state.current_phone:
                queries.append(("extracted_phone", ctx.extracted_phone))

        # Search ChromaDB for each query
        for query_type, query_text in queries:
            if not query_text:
                continue

            step = VerificationStep(
                step_name=f"chroma_search_{query_type}",
                question=f"Are there similar receipts matching '{query_text}'?",
            )

            try:
                # Generate embedding
                query_embedding = embed_fn([query_text])[0]

                # Query lines collection
                results = chroma_client.query(
                    collection_name="lines",
                    query_embeddings=[query_embedding],
                    n_results=10,
                    include=["metadatas", "documents", "distances"],
                )

                ids = results.get("ids", [[]])[0]
                documents = results.get("documents", [[]])[0]
                metadatas = results.get("metadatas", [[]])[0]
                distances = results.get("distances", [[]])[0]

                matches_found = 0
                for doc_id, doc, meta, dist in zip(ids, documents, metadatas, distances):
                    similarity = max(0.0, 1.0 - (dist / 2))

                    # Skip if same receipt
                    if (
                        meta.get("image_id") == state.image_id
                        and meta.get("receipt_id") == state.receipt_id
                    ):
                        continue

                    if similarity > 0.5:
                        matches_found += 1
                        chroma_line_results.append(
                            ChromaSearchResult(
                                chroma_id=doc_id,
                                image_id=meta.get("image_id", ""),
                                receipt_id=int(meta.get("receipt_id", 0)),
                                similarity_score=similarity,
                                document_text=doc,
                                metadata=meta,
                            )
                        )

                        # Track merchant candidates
                        neighbor_merchant = meta.get("merchant_name")
                        if neighbor_merchant:
                            existing = next(
                                (c for c in merchant_candidates
                                 if c.merchant_name == neighbor_merchant),
                                None
                            )
                            if existing:
                                existing.confidence_score = max(
                                    existing.confidence_score, similarity
                                )
                            else:
                                merchant_candidates.append(
                                    MerchantCandidate(
                                        merchant_name=neighbor_merchant,
                                        place_id=meta.get("place_id"),
                                        address=meta.get("normalized_full_address"),
                                        phone_number=meta.get("normalized_phone_10"),
                                        confidence_score=similarity,
                                        source="chroma",
                                        matched_fields=[query_type],
                                    )
                                )

                step.answer = f"Found {matches_found} similar receipts"
                step.passed = matches_found > 0
                step.evidence.append(
                    VerificationEvidence(
                        evidence_type=EvidenceType.CHROMA_SIMILARITY,
                        description=f"ChromaDB search for {query_type}",
                        confidence=min(1.0, matches_found / 5),
                        supporting_data={
                            "query": query_text,
                            "matches_found": matches_found,
                        },
                    )
                )

            except Exception as e:
                step.answer = f"Error: {str(e)}"
                step.passed = False
                logger.error(f"ChromaDB search error for {query_type}: {e}")

            verification_steps.append(step)

        # Sort candidates by confidence
        merchant_candidates.sort(key=lambda x: -x.confidence_score)

        # Create AI message summarizing findings
        summary_parts = [f"Found {len(chroma_line_results)} similar receipt lines."]
        if merchant_candidates:
            summary_parts.append(
                f"Top merchant candidates: {', '.join(c.merchant_name for c in merchant_candidates[:3])}"
            )

        updates.update({
            "chroma_line_results": chroma_line_results,
            "merchant_candidates": merchant_candidates,
            "verification_steps": state.verification_steps + verification_steps,
            "messages": [AIMessage(content=" ".join(summary_parts))],
        })

    except Exception as e:
        logger.error(f"Error in similarity search: {e}")
        updates["errors"] = state.errors + [f"Similarity search failed: {str(e)}"]

    return updates


async def verify_consistency(
    state: ValidationState,
    dynamo_client: Any,
) -> dict[str, Any]:
    """
    Verify consistency across receipts from the same merchant.

    This node checks if the place_id, address, and phone are
    consistent with other receipts from the same merchant.
    """
    logger.info(
        f"Verifying consistency for merchant: {state.current_merchant_name}"
    )

    updates: dict[str, Any] = {"current_step": "verify_consistency"}
    verification_steps: list[VerificationStep] = []

    try:
        if not state.current_merchant_name:
            step = VerificationStep(
                step_name="consistency_check",
                question="Is there a merchant name to verify?",
                answer="No merchant name found in metadata",
                passed=False,
            )
            verification_steps.append(step)
            updates["verification_steps"] = state.verification_steps + verification_steps
            return updates

        # Get other receipts from same merchant
        metadatas, _ = dynamo_client.get_receipt_metadatas_by_merchant(
            merchant_name=state.current_merchant_name,
            limit=50,
        )

        if not metadatas:
            step = VerificationStep(
                step_name="consistency_check",
                question=f"Are there other receipts from {state.current_merchant_name}?",
                answer="No other receipts found for this merchant",
                passed=None,  # Inconclusive
            )
            verification_steps.append(step)
            updates["verification_steps"] = state.verification_steps + verification_steps
            return updates

        # Aggregate and check consistency
        place_ids: dict[str, int] = {}
        addresses: dict[str, int] = {}
        phones: dict[str, int] = {}

        for meta in metadatas:
            # Skip the receipt we're validating
            if (
                meta.image_id == state.image_id
                and meta.receipt_id == state.receipt_id
            ):
                continue

            if meta.place_id:
                place_ids[meta.place_id] = place_ids.get(meta.place_id, 0) + 1
            if meta.address:
                addresses[meta.address] = addresses.get(meta.address, 0) + 1
            if meta.phone_number:
                phones[meta.phone_number] = phones.get(meta.phone_number, 0) + 1

        # Check place_id consistency
        step_place = VerificationStep(
            step_name="place_id_consistency",
            question=f"Is place_id '{state.current_place_id}' consistent with other receipts?",
        )

        if not place_ids:
            step_place.answer = "No place_ids found on other receipts"
            step_place.passed = None
        elif state.current_place_id in place_ids:
            count = place_ids[state.current_place_id]
            total = sum(place_ids.values())
            step_place.answer = f"Yes, {count}/{total} receipts have this place_id"
            step_place.passed = count / total > 0.5
            step_place.evidence.append(
                VerificationEvidence(
                    evidence_type=EvidenceType.PLACE_ID_MATCH,
                    description=f"Place ID consistency check",
                    confidence=count / total,
                    supporting_data=place_ids,
                )
            )
        else:
            most_common = max(place_ids.items(), key=lambda x: x[1])[0]
            step_place.answer = f"No, most common place_id is {most_common}"
            step_place.passed = False
            step_place.evidence.append(
                VerificationEvidence(
                    evidence_type=EvidenceType.PLACE_ID_MATCH,
                    description=f"Place ID mismatch - expected {most_common}",
                    confidence=0.0,
                    supporting_data=place_ids,
                )
            )

        verification_steps.append(step_place)

        # Check address consistency
        step_addr = VerificationStep(
            step_name="address_consistency",
            question=f"Is address consistent with other receipts?",
        )

        if state.current_address and addresses:
            if state.current_address in addresses:
                count = addresses[state.current_address]
                total = sum(addresses.values())
                step_addr.answer = f"Yes, {count}/{total} receipts have this address"
                step_addr.passed = True
                step_addr.evidence.append(
                    VerificationEvidence(
                        evidence_type=EvidenceType.ADDRESS_MATCH,
                        description="Address matches other receipts",
                        confidence=count / total,
                        supporting_data={"matching": count, "total": total},
                    )
                )
            else:
                step_addr.answer = "Address not found on other receipts"
                step_addr.passed = False
        else:
            step_addr.answer = "Insufficient data to verify address"
            step_addr.passed = None

        verification_steps.append(step_addr)

        # Check phone consistency
        step_phone = VerificationStep(
            step_name="phone_consistency",
            question="Is phone number consistent?",
        )

        if state.current_phone and phones:
            # Normalize for comparison
            current_digits = "".join(c for c in state.current_phone if c.isdigit())
            matched = any(
                current_digits in "".join(c for c in p if c.isdigit())
                for p in phones
            )
            if matched:
                step_phone.answer = "Yes, phone matches other receipts"
                step_phone.passed = True
                step_phone.evidence.append(
                    VerificationEvidence(
                        evidence_type=EvidenceType.PHONE_MATCH,
                        description="Phone number verified",
                        confidence=0.9,
                        supporting_data={"phones_found": list(phones.keys())[:3]},
                    )
                )
            else:
                step_phone.answer = "Phone not found on other receipts"
                step_phone.passed = False
        else:
            step_phone.answer = "Insufficient data to verify phone"
            step_phone.passed = None

        verification_steps.append(step_phone)

        # Add summary message
        passed_steps = sum(1 for s in verification_steps if s.passed is True)
        total_checks = len(verification_steps)
        summary = f"Consistency check: {passed_steps}/{total_checks} verifications passed."

        updates.update({
            "verification_steps": state.verification_steps + verification_steps,
            "messages": [AIMessage(content=summary)],
        })

    except Exception as e:
        logger.error(f"Error in consistency verification: {e}")
        updates["errors"] = state.errors + [f"Consistency check failed: {str(e)}"]

    return updates


async def check_google_places(
    state: ValidationState,
    places_api: Optional[Any],
) -> dict[str, Any]:
    """
    Verify merchant information against Google Places API.

    This is an optional verification step that checks if the
    stored metadata matches what Google Places reports.
    """
    logger.info(f"Checking Google Places for {state.current_merchant_name}")

    updates: dict[str, Any] = {"current_step": "check_places"}
    verification_steps: list[VerificationStep] = []

    if places_api is None:
        step = VerificationStep(
            step_name="google_places_check",
            question="Can we verify with Google Places?",
            answer="Google Places API not configured",
            passed=None,
        )
        verification_steps.append(step)
        updates["verification_steps"] = state.verification_steps + verification_steps
        return updates

    try:
        step = VerificationStep(
            step_name="google_places_verification",
            question="Does Google Places confirm this merchant?",
        )

        place_data = None

        # Try place_id first
        if state.current_place_id:
            try:
                place_data = places_api.get_place_details(state.current_place_id)
            except Exception:
                pass

        # Fall back to phone search
        if not place_data and state.current_phone:
            try:
                place_data = places_api.search_by_phone(state.current_phone)
            except Exception:
                pass

        # Fall back to text search
        if not place_data and state.current_merchant_name:
            try:
                place_data = places_api.search_by_text(state.current_merchant_name)
            except Exception:
                pass

        if place_data and place_data.get("name"):
            # Compare with current metadata
            from difflib import SequenceMatcher

            places_name = place_data.get("name", "")
            name_similarity = SequenceMatcher(
                None,
                (state.current_merchant_name or "").lower(),
                places_name.lower(),
            ).ratio()

            if name_similarity > 0.8:
                step.answer = f"Confirmed: Google Places shows '{places_name}'"
                step.passed = True
                step.evidence.append(
                    VerificationEvidence(
                        evidence_type=EvidenceType.GOOGLE_PLACES,
                        description="Merchant verified by Google Places",
                        confidence=name_similarity,
                        supporting_data={
                            "google_name": places_name,
                            "google_address": place_data.get("formatted_address"),
                            "google_phone": place_data.get("formatted_phone_number"),
                            "name_similarity": name_similarity,
                        },
                    )
                )

                # Update merchant candidates with Places data
                places_candidate = MerchantCandidate(
                    merchant_name=places_name,
                    place_id=place_data.get("place_id"),
                    address=place_data.get("formatted_address"),
                    phone_number=place_data.get("formatted_phone_number"),
                    category=", ".join(place_data.get("types", [])[:3]),
                    confidence_score=name_similarity,
                    source="google_places",
                    matched_fields=["name"],
                )
                updates["merchant_candidates"] = (
                    state.merchant_candidates + [places_candidate]
                )
            else:
                step.answer = (
                    f"Mismatch: Google shows '{places_name}' "
                    f"(similarity: {name_similarity:.2f})"
                )
                step.passed = False
                step.evidence.append(
                    VerificationEvidence(
                        evidence_type=EvidenceType.GOOGLE_PLACES,
                        description="Name mismatch with Google Places",
                        confidence=name_similarity,
                        supporting_data={
                            "current_name": state.current_merchant_name,
                            "google_name": places_name,
                        },
                    )
                )
        else:
            step.answer = "Could not verify with Google Places"
            step.passed = None

        verification_steps.append(step)

        updates.update({
            "verification_steps": state.verification_steps + verification_steps,
            "messages": [AIMessage(content=step.answer or "Google Places check complete")],
        })

    except Exception as e:
        logger.error(f"Error checking Google Places: {e}")
        updates["errors"] = state.errors + [f"Google Places check failed: {str(e)}"]

    return updates


async def make_decision(
    state: ValidationState,
    llm: Any,
) -> dict[str, Any]:
    """
    Make the final validation decision based on all evidence.

    This node uses an LLM to synthesize all the verification steps
    and evidence to make a final decision about the metadata validity.
    """
    logger.info(
        f"Making validation decision for {state.image_id}#{state.receipt_id}"
    )

    updates: dict[str, Any] = {
        "current_step": "decision",
        "should_continue": False,
    }

    try:
        # Gather all evidence
        all_evidence: list[VerificationEvidence] = []
        for step in state.verification_steps:
            all_evidence.extend(step.evidence)

        # Count verification results
        passed = sum(1 for s in state.verification_steps if s.passed is True)
        failed = sum(1 for s in state.verification_steps if s.passed is False)
        inconclusive = sum(1 for s in state.verification_steps if s.passed is None)

        # Calculate confidence
        total_steps = len(state.verification_steps)
        if total_steps > 0:
            confidence = passed / total_steps
        else:
            confidence = 0.0

        # Boost confidence if we have strong evidence
        high_confidence_evidence = [
            e for e in all_evidence if e.confidence > 0.8
        ]
        if high_confidence_evidence:
            confidence = min(1.0, confidence + 0.1)

        # Determine status
        if failed > passed:
            status = ValidationStatus.INVALID
        elif confidence >= 0.7:
            status = ValidationStatus.VALIDATED
        elif confidence >= 0.4:
            status = ValidationStatus.NEEDS_REVIEW
        else:
            status = ValidationStatus.PENDING

        # Build reasoning using LLM
        evidence_summary = "\n".join([
            f"- {step.step_name}: {step.answer} (passed: {step.passed})"
            for step in state.verification_steps
        ])

        # Get LLM reasoning
        reasoning_prompt = f"""Based on the following verification steps, provide a brief explanation for why the receipt metadata is being marked as {status.value}:

Current Metadata:
- Merchant: {state.current_merchant_name}
- Place ID: {state.current_place_id}
- Address: {state.current_address}
- Phone: {state.current_phone}

Verification Results:
{evidence_summary}

Passed: {passed}, Failed: {failed}, Inconclusive: {inconclusive}
Confidence: {confidence:.2f}

Provide a 2-3 sentence explanation:"""

        reasoning = ""
        try:
            response = await llm.ainvoke(reasoning_prompt)
            reasoning = response.content if hasattr(response, "content") else str(response)
        except Exception as e:
            logger.warning(f"LLM reasoning failed: {e}")
            reasoning = (
                f"Validation result: {passed}/{total_steps} checks passed. "
                f"Confidence: {confidence:.2f}."
            )

        # Build recommendations
        recommendations: list[str] = []
        if status == ValidationStatus.INVALID:
            # Suggest corrections based on merchant candidates
            if state.merchant_candidates:
                best_candidate = max(
                    state.merchant_candidates,
                    key=lambda c: c.confidence_score,
                )
                if best_candidate.confidence_score > 0.7:
                    recommendations.append(
                        f"Consider updating to merchant: {best_candidate.merchant_name}"
                    )
                    if best_candidate.place_id:
                        recommendations.append(
                            f"Suggested place_id: {best_candidate.place_id}"
                        )

        elif status == ValidationStatus.NEEDS_REVIEW:
            recommendations.append(
                "Manual review recommended - verification results inconclusive"
            )

        # Determine validated merchant
        validated_merchant = None
        if status == ValidationStatus.VALIDATED:
            validated_merchant = MerchantCandidate(
                merchant_name=state.current_merchant_name or "",
                place_id=state.current_place_id,
                address=state.current_address,
                phone_number=state.current_phone,
                confidence_score=confidence,
                source="validated",
                matched_fields=[
                    s.step_name for s in state.verification_steps if s.passed
                ],
            )

        # Create final result
        result = ValidationResult(
            status=status,
            confidence=confidence,
            validated_merchant=validated_merchant,
            verification_steps=state.verification_steps,
            evidence_summary=all_evidence,
            reasoning=reasoning,
            recommendations=recommendations,
        )

        updates.update({
            "result": result,
            "messages": [
                AIMessage(
                    content=f"Validation complete: {status.value} (confidence: {confidence:.2f})\n{reasoning}"
                )
            ],
        })

    except Exception as e:
        logger.error(f"Error making decision: {e}")
        updates["errors"] = state.errors + [f"Decision failed: {str(e)}"]
        updates["result"] = ValidationResult(
            status=ValidationStatus.ERROR,
            confidence=0.0,
            reasoning=f"Error during validation: {str(e)}",
        )

    return updates

