"""Business logic for recomputing RECEIPT_LINE_ITEM rows for one receipt.

Triggered when a receipt's summary changes -- the moment at which words,
sections, summary and merchant all exist -- so extraction always sees a
complete receipt. This also regenerates line items after resegmentation for
free: resegmentation rewrites the summary, which re-fires this stage.

Extraction is the canonical band-block decoder from
``receipt_upload.line_items``; those modules (and the priors asset) are
bundled into this Lambda's archive at deploy time as FileAssets referencing
the canonical sources -- no copies live in the repo, so the logic cannot
fork the way the shadowed ``build_receipt_rows`` once did.

Unlike the backfill script (which gates on VALID ITEMS sections), this
stage extracts on PENDING sections too and records
``source_section_status`` -- consumers filter on VALID when they want
precision; gating production on VALID is what once limited coverage to
hand-repaired receipts.

CONSISTENCY CHECKER
-------------------
The Mac worker now runs the same decoder on device and ships its rows with
the OCR upload (``receipt_upload.line_items.provenance``). When this stage
finds worker-written rows already in the table it COMPARES instead of
blindly overwriting:

* the recompute wins by default -- byte-identical to the pre-worker
  behavior, and the only outcome possible for a receipt with no worker rows;
* the worker's rows are preserved only when they are *strictly better* by
  ``items_boundary_extension_guard`` -- the same arithmetic guard the ITEMS
  boundary repair uses (smaller |delta| AND a better reconciliation status,
  never against an already-matching baseline, never across a no-baseline
  side). Preserved rows are re-stamped with the merchant/summary context the
  cloud has and the worker did not.

TIE HANDLING -- WHY THE CLOUD WINS
----------------------------------
A "tie" is equal reconciliation status AND equal |delta|. The guard
resolves every one of them for the recompute (``shrinks`` is false when the
deltas are equal, and a ``match`` baseline short-circuits before the
comparison even runs). Because both implementations are deterministic ports
of the same decoder, agreement -- and therefore a tie -- is the *normal*
outcome, so this rule decides the steady state: the cloud's rows overwrite
the worker's on essentially every receipt, and the worker survives only as
the inherited ``source_model_source == "swift-worker-v1"``.

That is deliberate, and it stays. Three findings, not one, drive it:

1. **The worker's row payload is a strict SUBSET of the cloud's.** The
   on-device JSON contract (``ReceiptLineItemPayload`` in
   ``ReceiptStructurePipeline.swift``) has no ``raw_text`` field at all, so
   every worker row lands with ``raw_text == ""``; ``collapsed_banding`` is
   likewise cloud-only. Preferring the worker on ties would therefore *lose
   the band text on every receipt it fired for* -- a corpus-wide data
   regression in exchange for a provenance stamp. This is observable today:
   dev ``2b630bec`` (IMG_3404) is the one live keep-worker receipt, and its
   five preserved rows carry ``raw_text == ""`` while the byte-identical
   prod copy carries ``"LEMON EACH $2.94"`` and friends. ``divergence
   .worker_rows_missing_raw_text`` counts the cost so it stays auditable
   instead of anecdotal.
2. **A tie is not an identical decode.** ``(status, |delta|)`` is a
   two-scalar projection: any regrouping that preserves the item sum --
   merging or splitting bands, moving a name between adjacent rows -- ties
   exactly while producing different rows. "Prefer the worker on ties"
   would hand those cases to the worker on an arithmetic coincidence, not
   on evidence that the worker was right.
3. **Version-gated tie-breaking cannot detect the staleness it exists to
   catch.** The obvious middle path -- prefer the worker only when its
   decoder version is at or ahead of the cloud's -- assumes the version
   moves when behavior does. It does not: #1369 shipped three real decoder
   fixes (branded tender rows, the printed-total baseline, the OFF
   substring bug) with ``line-items-blocks-v2`` unchanged on both sides. A
   worker built before #1369 and a Lambda built after it are indis-
   tinguishable by version string, which is exactly the skew the gate would
   need to see. The gate becomes implementable the day
   ``SWIFT_WORKER_DECODER_VERSION`` is bumped on every behavior change; it
   is not implementable before then, and a gate that silently never fires
   is worse than no gate.

The asymmetry underneath all three: the Lambda redeploys with CI, while
worker binaries need a manual ``scripts/update_ocr_workers.sh`` run after
every ``receipt_ocr_swift`` merge. Preferring the freshest *deployable*
decoder on a tie is the conservative default, and the strict-improvement
escape hatch still lets a worker that is genuinely, arithmetically better
win -- which is precisely what happened on dev IMG_3404 when dev's Lambda
was running pre-#1369 code.

None of this discards the on-device work: it is what makes the cloud a
*checker*. The value #1368 delivers is the cross-implementation agreement
signal below, not the ``extractor_version`` string on the row.

MEASURING AGREEMENT FROM LOGS ALONE
-----------------------------------
Every receipt reaching the comparison emits exactly one queryable line:

* ``LINE_ITEM_DIVERGENCE`` + JSON (counts, names, prices, both
  reconciliation statuses and deltas, and the decision) when worker rows
  were found and compared -- ``divergent`` is 0 or 1;
* ``LINE_ITEM_NO_WORKER_ROWS`` + JSON when there were none to compare, so a
  skip is a *countable* event rather than silence. Without it, a receipt
  whose worker rows a previous recompute already replaced is indis-
  tinguishable from one the worker never touched, and "no log line" reads
  as consensus when it may mean no comparison happened at all.

The skip record carries ``worker_sourced_section``: 1 means the ITEMS
section was proposed by a worker, i.e. the worker *did* run on this receipt
and its rows have since been overwritten -- a second recompute of an
already-checked receipt, not a receipt outside worker coverage.

Coverage and agreement rate, from these two markers alone::

    fields @timestamp,
        (@message like /LINE_ITEM_NO_WORKER_ROWS/) as never_compared,
        (@message like /"divergent": 0/) as agreed,
        (@message like /"divergent": 1/) as diverged
    | filter @message like /LINE_ITEM_DIVERGENCE|LINE_ITEM_NO_WORKER_ROWS/
    | stats sum(agreed) as agreed,
            sum(diverged) as diverged,
            sum(never_compared) as never_compared

``agreed / (agreed + diverged)`` is the cross-language agreement rate;
``(agreed + diverged) / total`` is comparison coverage. Split the skips
into "already overwritten" vs "no worker ever ran"::

    fields @timestamp, (@message like /"worker_sourced_section": 1/) as seen
    | filter @message like /LINE_ITEM_NO_WORKER_ROWS/
    | stats sum(seen) as already_overwritten, count() - sum(seen) as no_worker

And the divergent receipts themselves, for triage::

    fields @timestamp, @message
    | filter @message like /LINE_ITEM_DIVERGENCE/
    | filter @message like /"divergent": 1/
"""

import json
import logging
import os
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

# Warning #6 requires one first-party block in infra files.
# isort: off
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.data.shared_exceptions import EntityNotFoundError
from receipt_dynamo.entities.receipt_line_item import ReceiptLineItem
from receipt_upload.line_items.geometry import (
    extract_items,
    items_boundary_extension_guard,
    propose_items_boundary_extension,
    reconcile_detailed,
)
from receipt_upload.line_items.provenance import (
    is_worker_extractor_version,
    is_worker_model_source,
)

# isort: on

logger = logging.getLogger(__name__)

TABLE_NAME = os.environ.get("DYNAMODB_TABLE_NAME", "")
dynamo_client = DynamoClient(TABLE_NAME) if TABLE_NAME else None

EXTRACTOR_VERSION = "line-items-blocks-v2"
BOUNDARY_EXTENSION_SOURCE = "zone-gap-extend-v1"


def update_receipt_line_items(
    image_id: str,
    receipt_id: int,
    reocr_mechanism: str | None = None,
) -> dict[str, Any]:
    """Recompute and rewrite RECEIPT_LINE_ITEM rows for one receipt.

    ``reocr_mechanism`` is an optional diagnosed OCR-failure mechanism
    (e.g. "reverse-video-total" from a triage dossier) threaded through
    to the re-OCR trigger so the strategy ladder can pick a targeted
    capture strategy. The SQS path never sets it; direct callers
    (scripts, agents) may.
    """
    if dynamo_client is None:
        raise ValueError("DYNAMODB_TABLE_NAME environment variable not set")

    words = dynamo_client.list_receipt_words_from_receipt(image_id, receipt_id)
    sections = dynamo_client.get_receipt_sections_from_receipt(
        image_id, receipt_id
    )

    # ITEMS zone: any non-INVALID canonical ITEMS section; prefer VALID
    # over PENDING when both exist so provenance reflects the strongest
    # source. Exact match (like the backfill script): legacy
    # ITEMS_VALUE / ITEMS_DESCRIPTION zones are partial (prices-only or
    # names-only) and must not masquerade as a full ITEMS section.
    items_section = None
    for s in sections:
        if str(getattr(s, "section_type", "") or "").upper() != "ITEMS":
            continue
        status = str(getattr(s, "validation_status", "") or "").upper()
        if status == "INVALID":
            continue
        if items_section is None or status == "VALID":
            items_section = s
        if status == "VALID":
            break
    if items_section is None or not words:
        # Nothing to extract; clear stale rows so a receipt whose ITEMS
        # section was invalidated does not keep phantom items.
        deleted = dynamo_client.delete_receipt_line_items_for_receipt(
            image_id, receipt_id
        )
        return {"items": 0, "deleted": deleted, "reason": "no-items-zone"}

    word_dicts = [
        {
            "line_id": w.line_id,
            "word_id": w.word_id,
            "text": w.text,
            "x": w.bounding_box.get("x", 0.0),
            "y_mid": w.bounding_box.get("y", 0.0)
            + w.bounding_box.get("height", 0.0) / 2,
            "h": w.bounding_box.get("height", 0.0),
        }
        for w in words
    ]

    # Summary is fetched BEFORE extraction: the decoder's non-product
    # band filter needs the printed subtotal/tax/grand_total to recognize
    # summary figures leaking into the ITEMS zone.
    summary_dict = None
    merchant = None
    try:
        record = dynamo_client.get_receipt_summary(image_id, receipt_id)
        inner = getattr(record, "summary", None)
        if inner is not None:
            summary_dict = {
                "subtotal": getattr(inner, "subtotal", None),
                "grand_total": getattr(inner, "grand_total", None),
                "tax": getattr(inner, "tax", None),
            }
            merchant = getattr(inner, "merchant_name", None)
    except EntityNotFoundError:
        # Only "summary does not exist yet" is a valid no-baseline case.
        # Operational failures (throttling, access, malformed data) must
        # propagate so SQS retries instead of acking rows with a silently
        # wrong no-baseline status and a missing merchant.
        logger.info(
            "no summary for %s:%d; reconciliation is no-baseline",
            image_id[:8],
            receipt_id,
        )

    extension = None
    if summary_dict is not None:
        items_section, extension = _maybe_extend_items_section(
            image_id,
            receipt_id,
            items_section,
            sections,
            word_dicts,
            summary_dict,
        )

    line_ids = {int(x) for x in (items_section.line_ids or [])}

    items, collapsed = extract_items(
        word_dicts, line_ids, summary=summary_dict
    )

    recon = reconcile_detailed(
        [x for x in items if not x.get("is_discount")], summary_dict
    )
    status = recon.status

    now = datetime.now(timezone.utc)
    entities = []
    for idx, it in enumerate(items):
        name = it.get("name") or ""
        quality = (
            "low" if it.get("name_quality") == "low" or not name else "ok"
        )
        entities.append(
            ReceiptLineItem(
                receipt_id=receipt_id,
                image_id=image_id,
                item_index=idx,
                name=name,
                price=f"{it['price']:.2f}",
                line_ids=[int(x) for x in it["line_ids"]],
                extractor_version=EXTRACTOR_VERSION,
                extracted_at=now,
                quantity=it.get("quantity"),
                unit_price=it.get("unit_price"),
                is_discount=bool(it.get("is_discount")),
                raw_text=it.get("raw_text") or "",
                name_quality=quality,
                merchant_name=merchant,
                source_section_status=getattr(
                    items_section, "validation_status", None
                ),
                source_model_source=getattr(
                    items_section, "model_source", None
                ),
                reconciliation_status=status,
                collapsed_banding=bool(collapsed),
                baseline_figures_agreeing=recon.baseline_figures_agreeing,
            )
        )

    # Consistency check against rows the Mac worker already decoded on
    # device. No worker rows -> `entities`/`items`/`recon` are used verbatim
    # and this stage behaves exactly as it did before the worker produced
    # anything.
    entities, items, recon, divergence = _reconcile_with_worker_rows(
        image_id, receipt_id, entities, items, recon, summary_dict, merchant
    )
    status = recon.status

    deleted = dynamo_client.delete_receipt_line_items_for_receipt(
        image_id, receipt_id
    )
    if entities:
        dynamo_client.add_receipt_line_items(entities)

    reocr = _maybe_trigger_items_reocr(
        image_id,
        receipt_id,
        items,
        summary_dict,
        [word for word in word_dicts if word["line_id"] in line_ids],
        reocr_mechanism=reocr_mechanism,
    )
    refine = _maybe_trigger_line_item_refine(
        image_id, receipt_id, summary_dict, merchant
    )
    return {
        "items": len(entities),
        "deleted": deleted,
        "reconciliation": status,
        "baseline_source": recon.baseline_source,
        "baseline_figures_agreeing": recon.baseline_figures_agreeing,
        "section_extension": extension,
        "reocr_triggered": reocr,
        "refine_triggered": refine,
        "worker_divergence": divergence,
    }


DIVERGENCE_MARKER = "LINE_ITEM_DIVERGENCE"
NO_WORKER_ROWS_MARKER = "LINE_ITEM_NO_WORKER_ROWS"


def _delta(result: Any) -> float | None:
    """|item sum - baseline| in the shape ``evaluate_items_zone`` returns."""

    if result.item_sum is None or result.baseline is None:
        return None
    return round(result.item_sum - result.baseline, 2)


def _row_to_item(row: Any) -> dict[str, Any]:
    """A stored ReceiptLineItem as the decoder's item dict."""

    try:
        price = float(row.price)
    except (TypeError, ValueError):
        price = 0.0
    return {
        "name": row.name,
        "price": price,
        "quantity": row.quantity,
        "unit_price": row.unit_price,
        "is_discount": bool(row.is_discount),
        "name_quality": row.name_quality,
        "line_ids": list(row.line_ids or []),
        "raw_text": row.raw_text,
    }


def _reconcile_with_worker_rows(
    image_id: str,
    receipt_id: int,
    entities: list[ReceiptLineItem],
    items: list[dict],
    recon: Any,
    summary_dict: dict | None,
    merchant: str | None,
) -> tuple[list[ReceiptLineItem], list[dict], Any, dict[str, Any] | None]:
    """Compare a fresh recompute against worker-written rows.

    Returns the rows to persist, their item dicts (for the re-OCR trigger),
    their reconciliation result, and a divergence record (``None`` when the
    receipt carries no worker rows -- the pre-worker path).

    The recompute wins unless the worker's rows pass
    ``items_boundary_extension_guard`` with the recompute as ``before``: the
    worker is preserved only when it strictly shrinks |delta| AND strictly
    improves reconciliation status. Reusing that guard verbatim means the
    conservative cases fall out for free -- a matching recompute is never
    displaced, and a no-baseline side is never ranked against a baselined
    one (which is exactly the common case, since the worker decodes before
    any summary exists). TIES GO TO THE RECOMPUTE, on purpose; see the
    module docstring for why that is not a bug to be fixed.

    The skip -- no worker rows to compare -- is LOGGED, not silent: see
    ``NO_WORKER_ROWS_MARKER``. A silent skip makes an already-overwritten
    receipt look exactly like an agreeing one in CloudWatch, which would
    make the corpus-wide agreement rate uncomputable.
    """
    try:
        stored = dynamo_client.get_receipt_line_items_from_receipt(
            image_id, receipt_id
        )
    except EntityNotFoundError:
        stored = []
    worker_rows = [
        row
        for row in stored
        if is_worker_extractor_version(getattr(row, "extractor_version", None))
    ]
    if not worker_rows:
        logger.info(
            "%s %s",
            NO_WORKER_ROWS_MARKER,
            json.dumps(
                {
                    "image_id": image_id,
                    "receipt_id": receipt_id,
                    "stored_count": len(stored),
                    "cloud_count": len(entities),
                    "cloud_status": recon.status,
                    # 1 => a worker DID decode this receipt and its rows
                    # have already been replaced by an earlier recompute;
                    # 0 => the receipt is outside worker coverage.
                    "worker_sourced_section": int(
                        any(
                            is_worker_model_source(
                                getattr(row, "source_model_source", None)
                            )
                            for row in stored
                        )
                    ),
                },
                sort_keys=True,
            ),
        )
        return entities, items, recon, None

    worker_rows = sorted(worker_rows, key=lambda row: row.item_index)
    worker_items = [_row_to_item(row) for row in worker_rows]
    worker_recon = reconcile_detailed(
        [item for item in worker_items if not item["is_discount"]],
        summary_dict,
    )

    before = {"status": recon.status, "delta": _delta(recon)}
    after = {"status": worker_recon.status, "delta": _delta(worker_recon)}
    keep_worker, guard_reason = items_boundary_extension_guard(before, after)

    cloud_names = [entity.name for entity in entities]
    cloud_prices = [entity.price for entity in entities]
    worker_names = [row.name for row in worker_rows]
    worker_prices = [f"{item['price']:.2f}" for item in worker_items]
    divergent = (
        cloud_names != worker_names
        or cloud_prices != worker_prices
        or recon.status != worker_recon.status
    )

    record = {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "divergent": int(divergent),
        "decision": "keep-worker" if keep_worker else "keep-recompute",
        "guard_reason": guard_reason,
        "worker_extractor_version": worker_rows[0].extractor_version,
        "worker_count": len(worker_rows),
        "cloud_count": len(entities),
        "worker_status": worker_recon.status,
        "cloud_status": recon.status,
        "worker_delta": after["delta"],
        "cloud_delta": before["delta"],
        # The on-device contract has no raw_text field, so preserving the
        # worker's rows always costs the band text. Counted, not assumed --
        # this is the measured price of the tie rule (module docstring).
        "worker_rows_missing_raw_text": sum(
            1 for row in worker_rows if not (row.raw_text or "")
        ),
        "name_mismatches": sum(
            1
            for cloud, worker in zip(cloud_names, worker_names)
            if cloud != worker
        ),
        "price_mismatches": sum(
            1
            for cloud, worker in zip(cloud_prices, worker_prices)
            if cloud != worker
        ),
    }
    logger.log(
        logging.WARNING if divergent else logging.INFO,
        "%s %s",
        DIVERGENCE_MARKER,
        json.dumps(record, sort_keys=True),
    )
    if not keep_worker:
        return entities, items, recon, record

    # Preserve the worker's DECODE, refresh its CONTEXT: merchant and the
    # graded reconciliation only exist in the cloud, and the worker had no
    # summary to reconcile against when it wrote these rows.
    now = datetime.now(timezone.utc)
    kept = [
        replace(
            row,
            extracted_at=now,
            merchant_name=merchant,
            reconciliation_status=worker_recon.status,
            baseline_figures_agreeing=(worker_recon.baseline_figures_agreeing),
        )
        for row in worker_rows
    ]
    return kept, worker_items, worker_recon, record


def _maybe_extend_items_section(
    image_id: str,
    receipt_id: int,
    items_section: Any,
    sections: list[Any],
    words: list[dict],
    summary: dict,
) -> tuple[Any, dict[str, Any] | None]:
    """Persist a reconciliation-verified adjacent-row ITEMS extension."""

    try:
        rows = dynamo_client.get_receipt_rows_from_receipt(
            image_id, receipt_id
        )
    except EntityNotFoundError:
        rows = []
    proposal = propose_items_boundary_extension(
        words=words,
        summary=summary,
        current_line_ids={int(x) for x in (items_section.line_ids or [])},
        sections=sections,
        rows=rows,
        current_row_ids=getattr(items_section, "row_ids", None),
    )
    if proposal is None:
        return items_section, None

    model_source = str(getattr(items_section, "model_source", "") or "")
    sources = [part for part in model_source.split("+") if part]
    if BOUNDARY_EXTENSION_SOURCE not in sources:
        sources.append(BOUNDARY_EXTENSION_SOURCE)
    updated = replace(
        items_section,
        line_ids=proposal["line_ids"],
        row_ids=proposal["row_ids"],
        model_source="+".join(sources),
    )
    # replace() preserves validation_status and all verifier provenance.  In
    # particular, a VALID section must never be demoted by automatic repair.
    dynamo_client.update_receipt_section(updated)
    logger.info(
        "extended ITEMS boundary for %s:%d with lines=%s (%s/%s -> %s/%s)",
        image_id[:8],
        receipt_id,
        proposal["added_line_ids"],
        proposal["before"]["status"],
        proposal["before"]["delta"],
        proposal["after"]["status"],
        proposal["after"]["delta"],
    )
    return updated, {
        "added_line_ids": proposal["added_line_ids"],
        "added_row_ids": proposal["added_row_ids"],
        "before": proposal["before"],
        "after": proposal["after"],
        "model_source": updated.model_source,
    }


REOCR_REASON = "line_items_recon"
REOCR_MAX_ATTEMPTS = 2


def _maybe_trigger_items_reocr(
    image_id: str,
    receipt_id: int,
    items: list[dict],
    summary_dict: dict | None,
    zone_words: list[dict],
    reocr_mechanism: str | None = None,
) -> bool:
    """Fire a capped REGIONAL_REOCR of the ITEMS zone on reconciliation
    mismatch (the digit-misread signature no downstream logic can fix).

    Failure here must never fail the message: line-item rows are already
    written, and re-OCR is an improvement pass, not a correctness
    requirement. The attempt cap exists because some glyphs are
    unreadable at any resolution (Twin Peaks) -- without it, every
    recompute of a permanently-mismatched receipt would re-fire.

    Each attempt climbs the SMART strategy ladder: attempt 1 gets the
    mechanism's best strategy, attempt 2 a DIFFERENT one (never a
    repeat of a capture that already failed).
    """
    fn = os.environ.get("TRIGGER_REOCR_FUNCTION_NAME")
    if not fn or dynamo_client is None:
        return False
    try:
        from receipt_upload.line_items.blocks import should_reocr_items_zone

        subtotal = None
        if summary_dict and summary_dict.get("subtotal") is not None:
            subtotal = float(summary_dict["subtotal"])
        if not should_reocr_items_zone(items, subtotal):
            return False

        jobs, _ = dynamo_client.list_ocr_jobs_for_image(image_id)
        prior = [
            j
            for j in jobs
            if getattr(j, "job_type", "") == "REGIONAL_REOCR"
            and getattr(j, "receipt_id", None) == receipt_id
            and getattr(j, "reocr_reason", "") == REOCR_REASON
        ]
        if len(prior) >= REOCR_MAX_ATTEMPTS:
            logger.info(
                "re-OCR cap reached for %s:%d (%d attempts)",
                image_id[:8],
                receipt_id,
                len(prior),
            )
            return False

        from receipt_upload.line_items.reocr import items_zone_reocr_region
        from receipt_upload.line_items.reocr_strategy import choose_strategy

        receipt = dynamo_client.get_receipt(image_id, receipt_id)
        image = dynamo_client.get_image(image_id)
        region = items_zone_reocr_region(
            zone_words, receipt, image.width, image.height
        )
        if region is None:
            return False

        attempt_number = len(prior) + 1
        strategy = choose_strategy(reocr_mechanism, attempt_number)

        import boto3

        boto3.client("lambda").invoke(
            FunctionName=fn,
            InvocationType="Event",
            Payload=json.dumps(
                {
                    "image_id": image_id,
                    "receipt_id": receipt_id,
                    "reocr_region": region,
                    "reocr_reason": REOCR_REASON,
                    "reocr_strategy": strategy,
                    "reocr_mechanism": reocr_mechanism,
                }
            ).encode(),
        )
        logger.info(
            "triggered items-zone re-OCR for %s:%d region=%s "
            "strategy=%s mechanism=%s attempt=%d",
            image_id[:8],
            receipt_id,
            region,
            strategy,
            reocr_mechanism,
            attempt_number,
        )
        return True
    except Exception:  # noqa: BLE001 - best-effort improvement pass
        logger.exception(
            "re-OCR trigger failed for %s:%d (non-fatal)",
            image_id[:8],
            receipt_id,
        )
        return False


REFINE_MAX_ATTEMPTS = 3

REFINE_SUMMARY_FIGURES = ("subtotal", "tax", "grand_total")


def _refine_summaries_equal(left: dict | None, right: dict | None) -> bool:
    """Compare two refine_summary payloads figure by figure.

    Dynamo round-trips the figures as Decimal, so raw dict equality is
    unreliable — normalize through float() and keep None distinct from
    any number (an absent figure is not a zero).
    """
    if left is None or right is None:
        return left is None and right is None
    for key in REFINE_SUMMARY_FIGURES:
        a, b = left.get(key), right.get(key)
        if a is None or b is None:
            if a is not None or b is not None:
                return False
            continue
        try:
            if float(a) != float(b):
                return False
        except (TypeError, ValueError):
            return False
    return True


def _maybe_trigger_line_item_refine(
    image_id: str,
    receipt_id: int,
    summary_dict: dict | None,
    merchant_name: str | None,
) -> bool:
    """Enqueue a LINE_ITEM_REFINE pass on the Mac worker (Tier 3 of the
    worker-authority migration).

    The worker re-decodes the receipt's STORED OCR JSON — the same word
    universe the persisted rows reference — with the real summary carried
    on the job, so the graded baseline and zone-gap boundary extension
    run on device and land via the worker's own Dynamo write surface.
    The cloud recompute above remains authoritative-by-default: both
    sides run the same deterministic decoder over the same inputs, so
    the pass converges rather than races.

    Gated by ENABLE_LINE_ITEM_REFINE so ops controls rollout: an
    outdated worker binary decodes the unknown job_type as FIRST_PASS
    and would fail the job trying to OCR a JSON pointer — noisy, not
    destructive, but not free either. Best-effort: failure never fails
    the message (rows are already written).

    The refine pass writes LINE ITEMS ONLY (no sections), so it emits no
    stream event that could route back here — the loop is closed on the
    worker side. This function closes it on the cloud side too: a refine
    job is enqueued only when the summary it would carry DIFFERS from the
    summary of every existing non-FAILED refine job for the receipt, so
    repeated summary events (or the same summary re-delivered) cannot
    burn through REFINE_MAX_ATTEMPTS and lock out a genuine later change.
    """
    enabled = os.environ.get("ENABLE_LINE_ITEM_REFINE", "").lower() in (
        "1",
        "true",
    )
    # The OCR job queue is created AFTER this component in __main__, so
    # (like TRIGGER_REOCR_FUNCTION_NAME) no resource ref is possible:
    # the deterministic queue NAME is passed and the URL resolved at
    # runtime.
    queue_url = os.environ.get("OCR_JOB_QUEUE_URL")
    queue_name = os.environ.get("OCR_JOB_QUEUE_NAME")
    if not enabled or dynamo_client is None:
        return False
    if not queue_url and not queue_name:
        return False
    if summary_dict is None or not any(
        summary_dict.get(k) is not None
        for k in ("subtotal", "grand_total")
    ):
        # Without a printed figure the refine pass has no graded
        # baseline to add; the single-pass decode already ran.
        return False
    pending_job = None
    try:
        import uuid

        from receipt_dynamo.constants import OCRJobType, OCRStatus
        from receipt_dynamo.entities import OCRJob

        jobs, _ = dynamo_client.list_ocr_jobs_for_image(image_id)
        refine_jobs = [
            j
            for j in jobs
            if getattr(j, "job_type", "") == "LINE_ITEM_REFINE"
            and getattr(j, "receipt_id", None) == receipt_id
        ]
        if any(
            str(getattr(j, "status", "")).upper() == "PENDING"
            for j in refine_jobs
        ):
            return False
        if len(refine_jobs) >= REFINE_MAX_ATTEMPTS:
            return False

        refine_summary = {
            "subtotal": summary_dict.get("subtotal"),
            "tax": summary_dict.get("tax"),
            "grand_total": summary_dict.get("grand_total"),
        }
        # A second pass only earns its keep when the summary CHANGED:
        # re-running the same deterministic decode over the same JSON
        # with the same figures reproduces the same rows. Comparing the
        # carried figures (rather than counting events) is what keeps a
        # repeated summary write from consuming the attempt budget.
        if any(
            _refine_summaries_equal(
                getattr(j, "refine_summary", None), refine_summary
            )
            for j in refine_jobs
            if str(getattr(j, "status", "")).upper() != "FAILED"
        ):
            return False

        # The refine input is the ORIGINAL OCR-result JSON: its 1-based
        # array-position ids are the persisted line/word ids, so the
        # refine decode shares the rows' word universe by construction.
        # FIRST_PASS results are image-level (receipt_id None) and hold
        # every receipt's lines, so any of them can source any receipt;
        # a REFINEMENT result holds ONE receipt's warped-crop lines, so
        # it is only a valid source for that same receipt.
        source_jobs = [
            j
            for j in jobs
            if str(getattr(j, "status", "")).upper() == "COMPLETED"
            and (
                getattr(j, "job_type", "") == "FIRST_PASS"
                or (
                    getattr(j, "job_type", "") == "REFINEMENT"
                    and getattr(j, "receipt_id", None) == receipt_id
                )
            )
        ]
        source_jobs.sort(
            key=lambda j: getattr(j, "created_at", None) or datetime.min,
            reverse=True,
        )
        decision = None
        for source in source_jobs:
            try:
                decision = dynamo_client.get_ocr_routing_decision(
                    image_id, source.job_id
                )
                break
            except EntityNotFoundError:
                continue
        if decision is None:
            return False

        now = datetime.now(timezone.utc)
        job_id = str(uuid.uuid4())
        refine_job = OCRJob(
            image_id=image_id,
            job_id=job_id,
            s3_bucket=decision.s3_bucket,
            s3_key=decision.s3_key,
            created_at=now,
            updated_at=now,
            status=OCRStatus.PENDING.value,
            job_type=OCRJobType.LINE_ITEM_REFINE.value,
            receipt_id=receipt_id,
            refine_summary=refine_summary,
            refine_merchant_name=merchant_name,
        )
        dynamo_client.add_ocr_job(refine_job)
        pending_job = refine_job

        import boto3

        sqs = boto3.client("sqs")
        if not queue_url:
            queue_url = sqs.get_queue_url(QueueName=queue_name)["QueueUrl"]
        sqs.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps(
                {"job_id": job_id, "image_id": image_id}
            ),
        )
        logger.info(
            "triggered line-item refine for %s:%d (attempt %d)",
            image_id[:8],
            receipt_id,
            len(refine_jobs) + 1,
        )
        return True
    except Exception:  # noqa: BLE001 - best-effort improvement pass
        logger.exception(
            "line-item refine trigger failed for %s:%d (non-fatal)",
            image_id[:8],
            receipt_id,
        )
        # A PENDING row whose queue message never got published would
        # suppress every future refine for this receipt (the PENDING
        # check above), so a transient SQS blip would disable the
        # feature permanently. Mark it FAILED so the suppression lifts.
        if pending_job is not None:
            try:
                from receipt_dynamo.constants import OCRStatus

                pending_job.status = OCRStatus.FAILED.value
                pending_job.updated_at = datetime.now(timezone.utc)
                dynamo_client.update_ocr_job(pending_job)
            except Exception:  # noqa: BLE001 - best effort
                logger.exception(
                    "could not fail orphaned refine job for %s:%d",
                    image_id[:8],
                    receipt_id,
                )
        return False


def deduplicate_messages(
    records: list[dict[str, Any]],
) -> tuple[dict[tuple[str, int], list[str]], list[str]]:
    """Deduplicate SQS records by (image_id, receipt_id).

    Returns (unique_receipts -> message_ids, malformed_message_ids).
    """
    unique: dict[tuple[str, int], list[str]] = defaultdict(list)
    malformed: list[str] = []
    for record in records:
        msg_id = record.get("messageId", "")
        try:
            body = json.loads(record.get("body", "{}"))
            data = body.get("entity_data") or body
            image_id = data["image_id"]
            receipt_id = int(data["receipt_id"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            logger.warning("malformed message %s", msg_id)
            malformed.append(msg_id)
            continue
        unique[(image_id, receipt_id)].append(msg_id)
    return dict(unique), malformed
