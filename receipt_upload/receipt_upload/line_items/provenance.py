"""Producer provenance for sections and RECEIPT_LINE_ITEM rows.

Two producers now write receipt structure:

* the **Mac worker** (``receipt_ocr_swift``) decodes sections and line items
  on device and ships them inside the single-pass OCR payload, and
* the **stream stage** (``infra/receipt_line_item_updater``) recomputes them
  in the cloud whenever a receipt's summary changes.

Rows therefore have to say which one wrote them, so the stream stage can act
as a CONSISTENCY CHECKER instead of an unconditional overwriter. The stamp
lives in two fields that already exist on the entities:

``ReceiptSection.model_source``
    ``"swift-worker-v1"`` (worker) vs ``"upload-determinism-v1"`` (cloud).
    May carry ``+``-joined suffixes appended by later repairs, e.g.
    ``"swift-worker-v1+zone-gap-extend-v1"``.

``ReceiptLineItem.extractor_version``
    ``"swift-worker-v1+line-items-blocks-v2"`` (worker: build + algorithm)
    vs ``"line-items-blocks-v2"`` (cloud: algorithm only). The worker build
    prefix is what distinguishes the two, because a cloud recompute over a
    worker-provided ITEMS section inherits ``source_model_source ==
    "swift-worker-v1"`` and so cannot be told apart on that field alone.

Keep ``SWIFT_WORKER_DECODER_VERSION`` equal to the Lambda's
``EXTRACTOR_VERSION`` and to ``swiftWorkerDecoderVersion`` in
``ReceiptStructurePipeline.swift``: a skew between the three is the signal
that the Swift port has forked from the canonical Python decoder again.
"""

SWIFT_WORKER_MODEL_SOURCE = "swift-worker-v1"
SWIFT_WORKER_DECODER_VERSION = "line-items-blocks-v2"
SWIFT_WORKER_EXTRACTOR_VERSION = (
    f"{SWIFT_WORKER_MODEL_SOURCE}+{SWIFT_WORKER_DECODER_VERSION}"
)

# Any worker build, not just today's, is "pre-computed on device".
WORKER_PREFIX = "swift-worker-"


def is_worker_extractor_version(extractor_version) -> bool:
    """Whether a RECEIPT_LINE_ITEM row was written by a Mac worker build."""

    return str(extractor_version or "").startswith(WORKER_PREFIX)


def is_worker_model_source(model_source) -> bool:
    """Whether a section was first proposed by a Mac worker build.

    Tolerates the ``+``-joined repair suffixes later stages append to
    ``model_source`` (e.g. ``zone-gap-extend-v1``).
    """

    return str(model_source or "").split("+")[0].startswith(WORKER_PREFIX)
