"""Contract tests for the post-OCR section pipeline ordering and invariants.

The AWS side of the Mac-OCR handoff runs, per receipt, inside the lines
pipeline worker of MerchantResolvingEmbeddingProcessor:

    merchant resolution -> assign_and_persist_sections -> verify_receipt_sections

These tests pin that ordering plus the two safety invariants:
  * section predictions are ADDITIVE PENDING records that never overwrite
    existing (e.g. human VALID) sections, and
  * vector KNN verification only ANNOTATES sections — it never changes their
    section_type or row membership.
"""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from datetime import datetime, timezone

import pytest
from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.data.shared_exceptions import EntityAlreadyExistsError
from receipt_dynamo.entities import (
    ReceiptLine,
    ReceiptRow,
    ReceiptSection,
    ReceiptWord,
)
from receipt_embeddings import ScoredItem
from receipt_embeddings.formatting import build_receipt_rows

from receipt_upload.section_assignment import (
    MODEL_SOURCE,
    assign_and_persist_sections,
    extract_row_features,
    learn_prior,
)
from receipt_upload.section_verifier import (
    VERIFICATION_SOURCE,
    verify_receipt_sections,
)

_IMAGE_ID = "00000000-0000-4000-8000-000000000001"
_NEIGHBOR_IMAGE_ID = "00000000-0000-4000-8000-000000000002"
_CREATED_AT = datetime(2026, 7, 16, tzinfo=timezone.utc)


def _geometry(x: float, y: float, w: float, h: float) -> dict:
    return {
        "bounding_box": {"x": x, "y": y, "width": w, "height": h},
        "top_left": {"x": x, "y": y + h},
        "top_right": {"x": x + w, "y": y + h},
        "bottom_left": {"x": x, "y": y},
        "bottom_right": {"x": x + w, "y": y},
        "angle_degrees": 0.0,
        "angle_radians": 0.0,
    }


def _line(line_id: int, text: str, y: float) -> ReceiptLine:
    return ReceiptLine(
        image_id=_IMAGE_ID,
        receipt_id=1,
        line_id=line_id,
        text=text,
        confidence=0.95,
        **_geometry(0.08, y, 0.8, 0.03),
    )


def _word(line_id: int, word_id: int, text: str, y: float) -> ReceiptWord:
    return ReceiptWord(
        image_id=_IMAGE_ID,
        receipt_id=1,
        line_id=line_id,
        word_id=word_id,
        text=text,
        confidence=0.95,
        **_geometry(0.08 + 0.2 * (word_id - 1), y, 0.15, 0.03),
    )


def _receipt_fixture() -> tuple[list[ReceiptLine], list[ReceiptWord]]:
    lines = [
        _line(1, "SPROUTS FARMERS MARKET", 0.90),
        _line(2, "ORGANIC BANANAS 3.99", 0.60),
        _line(3, "TOTAL 3.99", 0.45),
    ]
    words = [
        _word(1, 1, "SPROUTS", 0.90),
        _word(1, 2, "FARMERS", 0.90),
        _word(1, 3, "MARKET", 0.90),
        _word(2, 1, "ORGANIC", 0.60),
        _word(2, 2, "BANANAS", 0.60),
        _word(2, 3, "3.99", 0.60),
        _word(3, 1, "TOTAL", 0.45),
        _word(3, 2, "3.99", 0.45),
    ]
    return lines, words


# ---------------------------------------------------------------------------
# Ordering inside the lines pipeline worker
# ---------------------------------------------------------------------------


class _FakeDynamo:
    """Just the surface the lines worker touches."""

    def __init__(
        self,
        rows: list[ReceiptRow],
        sections: list[ReceiptSection] | None = None,
    ) -> None:
        self.rows = rows
        self.sections = list(sections or [])
        self.table_name = "table"
        self._client = object()  # handed to the (stubbed) vector seam

    def get_receipt_rows_from_receipt(self, _image_id, _receipt_id):
        return list(self.rows)

    def get_receipt_sections_from_receipt(self, _image_id, _receipt_id):
        return list(self.sections)


def test_lines_worker_orders_merchant_sections_verification(
    monkeypatch,
):
    """Merchant resolution must precede section assignment (sections are
    merchant-conditioned), and assignment must precede verification. The
    worker builds ONE vector-seam client bound to its own table and hands
    it to both the resolver and the verifier."""
    import receipt_dynamo as receipt_dynamo_module

    import receipt_upload.merchant_resolution.embedding_processor as ep
    import receipt_upload.merchant_resolution.resolver as resolver_module
    import receipt_upload.section_assignment as assignment_module
    import receipt_upload.section_verifier as verifier_module
    import receipt_upload.vector_search as vector_search_module
    from receipt_upload.merchant_resolution.resolver import MerchantResult

    lines, words = _receipt_fixture()
    persisted_rows = build_receipt_rows(lines, words, created_at=_CREATED_AT)

    def _verified_section(section_type, model_source, verification_status):
        return ReceiptSection(
            image_id=_IMAGE_ID,
            receipt_id=1,
            section_type=section_type,
            line_ids=[1],
            row_ids=[1],
            confidence=0.9,
            validation_status=ValidationStatus.PENDING.value,
            model_source=model_source,
            created_at=_CREATED_AT,
            verification_status=verification_status,
        )

    # Seed exact, suffixed, human, and unverified sources so the stats filter
    # is proven to count ONLY the exact "upload-determinism-v1" literal.
    seeded_sections = [
        _verified_section("STOREFRONT", "upload-determinism-v1", "AGREED"),
        _verified_section(
            "ITEMS", "upload-determinism-v1+remap-v1", "DISAGREED"
        ),
        _verified_section("TOTAL_LINE", "human-qa", "DISAGREED"),
        _verified_section("FOOTER", "upload-determinism-v1", None),
    ]
    fake_dynamo = _FakeDynamo(persisted_rows, sections=seeded_sections)
    fake_vector_client = object()
    calls: list[str] = []
    section_by_line = {1: "STOREFRONT", 2: "ITEMS", 3: "TOTAL_LINE"}

    class _FakeResolver:
        def __init__(self, **kwargs) -> None:
            # The worker's one seam client reaches the resolver.
            assert kwargs.get("vector_client") is fake_vector_client

        def resolve(self, **kwargs) -> MerchantResult:
            calls.append("resolve_merchant")
            assert kwargs.get("lines_client") is None
            return MerchantResult(merchant_name=None)

    def _fake_assign(dynamo, rows, assign_lines, merchant_name, model=None):
        calls.append("assign_sections")
        assert dynamo is fake_dynamo
        # Section assignment must run on the PERSISTED visual rows.
        assert [row.row_id for row in rows] == [
            row.row_id for row in persisted_rows
        ]
        assert merchant_name is None  # resolution already happened (no match)
        return [], dict(section_by_line)

    def _fake_verify(
        chroma, dynamo, rows, row_embeddings, *, vector_client=None
    ):
        calls.append("verify_sections")
        assert chroma is None
        # The same seam client the resolver got verifies sections.
        assert vector_client is fake_vector_client
        assert len(rows) == len(row_embeddings)
        return []

    monkeypatch.setattr(
        receipt_dynamo_module, "DynamoClient", lambda _table: fake_dynamo
    )
    monkeypatch.setattr(resolver_module, "MerchantResolver", _FakeResolver)
    monkeypatch.setattr(
        assignment_module, "assign_and_persist_sections", _fake_assign
    )
    monkeypatch.setattr(
        verifier_module, "verify_receipt_sections", _fake_verify
    )
    monkeypatch.setattr(
        vector_search_module,
        "vector_search_client",
        lambda *_a, **_k: fake_vector_client,
    )

    row_line_ids_list = [row.line_ids for row in persisted_rows]
    row_embeddings = [[0.1, 0.2] for _ in persisted_rows]

    result = ep._run_lines_pipeline_worker(
        lines_data=[asdict(line) for line in lines],
        words_data=[asdict(word) for word in words],
        word_labels_data=[],
        row_embeddings=row_embeddings,
        row_line_ids_list=row_line_ids_list,
        image_id=_IMAGE_ID,
        receipt_id=1,
        table_name="table",
        google_places_api_key=None,
    )

    assert result["success"] is True
    assert calls == [
        "resolve_merchant",
        "assign_sections",
        "verify_sections",
    ]

    # Metrics-only observability keys surface in the worker result: row
    # provenance (persisted D1 rows here), section-proposal stats, and the
    # verification outcome counters.
    assert result["row_source"] == "persisted"
    assert result["row_count"] == len(persisted_rows)
    assert result["section_proposed_count"] == 0  # fake assign created none
    assert result["section_mean_confidence"] is None
    assert result["verified_row_count"] == 0
    # Seeded sources: exact-AGREED counts; suffixed(+remap-v1) and human
    # DISAGREED sections and the unverified exact section do NOT.
    assert result["verification_agreed_count"] == 1
    assert result["verification_disagreement_count"] == 0
    assert result["verification_abstained_count"] == 0


def test_lines_worker_tags_reconstructed_rows_without_changing_behavior(
    monkeypatch,
):
    """When ingest left no persisted rows (legacy/dev replays), the worker
    reconstructs them in-process. That fallback must be tagged as
    row_source="reconstructed" for metrics, while section assignment still
    runs on rows with identical identities (row_id = primary line id)."""
    import receipt_dynamo as receipt_dynamo_module

    import receipt_upload.merchant_resolution.embedding_processor as ep
    import receipt_upload.merchant_resolution.resolver as resolver_module
    import receipt_upload.section_assignment as assignment_module
    import receipt_upload.section_verifier as verifier_module
    import receipt_upload.vector_search as vector_search_module
    from receipt_upload.merchant_resolution.resolver import MerchantResult

    lines, words = _receipt_fixture()
    expected_rows = build_receipt_rows(lines, words, created_at=_CREATED_AT)
    fake_dynamo = _FakeDynamo([])  # ingest persisted nothing
    assigned_row_ids: list[int] = []

    class _FakeResolver:
        def __init__(self, **_kwargs) -> None:
            pass

        def resolve(self, **_kwargs) -> MerchantResult:
            return MerchantResult(merchant_name=None)

    def _fake_assign(_dynamo, rows, _lines, _merchant, model=None):
        assigned_row_ids.extend(row.row_id for row in rows)
        return [], {}

    monkeypatch.setattr(
        receipt_dynamo_module, "DynamoClient", lambda _table: fake_dynamo
    )
    monkeypatch.setattr(resolver_module, "MerchantResolver", _FakeResolver)
    monkeypatch.setattr(
        assignment_module, "assign_and_persist_sections", _fake_assign
    )
    monkeypatch.setattr(
        verifier_module, "verify_receipt_sections", lambda *_a, **_k: []
    )
    monkeypatch.setattr(
        vector_search_module,
        "vector_search_client",
        lambda *_a, **_k: object(),
    )

    result = ep._run_lines_pipeline_worker(
        lines_data=[asdict(line) for line in lines],
        words_data=[asdict(word) for word in words],
        word_labels_data=[],
        row_embeddings=[[0.1, 0.2] for _ in expected_rows],
        row_line_ids_list=[row.line_ids for row in expected_rows],
        image_id=_IMAGE_ID,
        receipt_id=1,
        table_name="table",
        google_places_api_key=None,
    )

    assert result["success"] is True
    assert result["row_source"] == "reconstructed"
    assert result["row_count"] == len(expected_rows)
    # Reconstruction preserves row identities exactly.
    assert assigned_row_ids == [row.row_id for row in expected_rows]


def test_model_source_literal_is_pinned():
    """The deterministic pipeline's identity is the exact string
    "upload-determinism-v1". The verifier and the verification-stats filter
    exact-match it, so changing the constant (or suffixing values) silently
    breaks them. Pin the literal itself."""
    assert MODEL_SOURCE == "upload-determinism-v1"


def test_visual_row_identity_is_leftmost_line():
    """A multi-line visual row's row_id must be the LEFTMOST line's id: the
    lines worker joins persisted rows to RowEmbeddingRecords via
    rows_by_id[record.primary_line.line_id], and primary_line is leftmost.
    The right-hand line is listed first to prove ordering is by geometry,
    not input order."""
    left = ReceiptLine(
        image_id=_IMAGE_ID,
        receipt_id=1,
        line_id=9,
        text="ORGANIC BANANAS",
        confidence=0.95,
        **_geometry(0.05, 0.60, 0.40, 0.03),
    )
    right = ReceiptLine(
        image_id=_IMAGE_ID,
        receipt_id=1,
        line_id=7,
        text="3.99",
        confidence=0.95,
        **_geometry(0.70, 0.60, 0.20, 0.03),
    )

    rows = build_receipt_rows([right, left], [], created_at=_CREATED_AT)

    assert len(rows) == 1
    assert rows[0].line_ids == [9, 7]  # sorted left-to-right
    assert rows[0].row_id == 9  # leftmost line, NOT first-listed (7)


# ---------------------------------------------------------------------------
# Additive PENDING persistence
# ---------------------------------------------------------------------------


class _StrictSectionStore:
    """Fails the test if the assignment path ever mutates a section."""

    def __init__(self, existing: list[ReceiptSection]) -> None:
        self.sections = list(existing)
        self.duplicate_types: set[str] = set()

    def add_receipt_section(self, section: ReceiptSection) -> None:
        if section.section_type in self.duplicate_types:
            raise EntityAlreadyExistsError("concurrent add")
        self.sections.append(section)

    def get_receipt_sections_from_receipt(self, _image_id, _receipt_id):
        return list(self.sections)

    def update_receipt_section(self, section: ReceiptSection) -> None:
        raise AssertionError(
            "assign_and_persist_sections must never update existing sections"
        )


def _tiny_model(rows, lines) -> dict:
    features = extract_row_features(rows, lines)
    return {
        "global": learn_prior(
            [
                list(
                    zip(
                        features,
                        ["STOREFRONT", "ITEMS", "TOTAL_LINE"],
                        strict=True,
                    )
                )
            ]
        ),
        "merchants": {},
    }


def _visual_rows(lines, words):
    return build_receipt_rows(lines, words, created_at=_CREATED_AT)


def test_section_predictions_are_additive_pending_records():
    lines, words = _receipt_fixture()
    rows = _visual_rows(lines, words)
    human_valid = ReceiptSection(
        image_id=_IMAGE_ID,
        receipt_id=1,
        section_type="ITEMS",
        line_ids=[2],
        row_ids=[2],
        confidence=1.0,
        validation_status=ValidationStatus.VALID.value,
        model_source="human-qa",
        created_at=_CREATED_AT,
    )
    store = _StrictSectionStore([human_valid])

    created, by_line = assign_and_persist_sections(
        store, rows, lines, None, model=_tiny_model(rows, lines)
    )

    # Predictions for a type that already exists are dropped, not merged and
    # not overwritten: the human VALID ITEMS section is exactly as seeded.
    created_types = {section.section_type for section in created}
    assert "ITEMS" not in created_types
    assert created_types == {"STOREFRONT", "TOTAL_LINE"}
    assert store.sections[0] is human_valid
    assert store.sections[0].validation_status == ValidationStatus.VALID.value

    # Everything the model writes is PENDING and stamped with its source.
    for section in created:
        assert section.validation_status == ValidationStatus.PENDING.value
        assert section.model_source == MODEL_SOURCE

    # The returned line map prefers the human VALID section for its lines.
    assert by_line[2] == "ITEMS"
    assert by_line[1] == "STOREFRONT"
    assert by_line[3] == "TOTAL_LINE"


def test_concurrent_add_of_same_type_is_swallowed():
    lines, words = _receipt_fixture()
    rows = _visual_rows(lines, words)
    store = _StrictSectionStore([])
    store.duplicate_types = {"STOREFRONT"}  # another worker won the write

    created, _ = assign_and_persist_sections(
        store, rows, lines, None, model=_tiny_model(rows, lines)
    )

    assert "STOREFRONT" not in {s.section_type for s in created}
    assert {s.section_type for s in created} == {"ITEMS", "TOTAL_LINE"}


# ---------------------------------------------------------------------------
# Verification annotates, never mutates
# ---------------------------------------------------------------------------


class _VerifierStore:
    def __init__(
        self, by_receipt: dict[tuple[str, int], list[ReceiptSection]]
    ):
        self.by_receipt = {
            key: list(sections) for key, sections in by_receipt.items()
        }
        self.updates: list[ReceiptSection] = []

    def get_receipt_sections_from_receipt(self, image_id, receipt_id):
        return list(self.by_receipt.get((image_id, receipt_id), []))

    def update_receipt_section(self, section: ReceiptSection) -> None:
        self.updates.append(section)
        sections = self.by_receipt[(section.image_id, section.receipt_id)]
        self.by_receipt[(section.image_id, section.receipt_id)] = [
            section if item.section_type == section.section_type else item
            for item in sections
        ]


class _FakeLinesQuery:
    """Vector seam stub: cross-receipt neighbors per query row."""

    def __init__(self, per_row_neighbors: list[list[dict]]):
        self.per_row_neighbors = per_row_neighbors
        self.query_index = 0
        self.vectors: dict[str, list[float]] = {}

    def search(self, vector, index, top_k, filters=None):
        assert index == "line-embeddings"
        assert top_k == 15
        assert filters is None
        neighbors = self.per_row_neighbors[self.query_index]
        results = []
        for position, metadata in enumerate(neighbors):
            key = f"neighbor-{self.query_index}-{position}"
            self.vectors[key] = list(vector)
            results.append(
                ScoredItem(key=key, distance=0.0, metadata=metadata)
            )
        self.query_index += 1
        return results

    def get_vector(self, key):
        return list(self.vectors[key])


def _row(row_id: int, line_ids: list[int]) -> ReceiptRow:
    return ReceiptRow(
        image_id=_IMAGE_ID,
        receipt_id=1,
        row_id=row_id,
        line_ids=line_ids,
        grouping_version="visual-rows-v1",
        y_min=0.9 - 0.1 * row_id,
        y_max=0.93 - 0.1 * row_id,
        x_min=0.05,
        x_max=0.95,
        created_at=_CREATED_AT,
    )


def _neighbor(line_ids: list[int]) -> dict:
    return {
        "image_id": _NEIGHBOR_IMAGE_ID,
        "receipt_id": 2,
        "row_line_ids": json.dumps(line_ids),
    }


def _same_receipt_neighbor() -> dict:
    return {"image_id": _IMAGE_ID, "receipt_id": 1, "row_line_ids": "[9]"}


def _pending_section(section_type: str, row_id: int) -> ReceiptSection:
    return ReceiptSection(
        image_id=_IMAGE_ID,
        receipt_id=1,
        section_type=section_type,
        line_ids=[row_id],
        row_ids=[row_id],
        confidence=0.8,
        validation_status=ValidationStatus.PENDING.value,
        model_source=MODEL_SOURCE,
        created_at=_CREATED_AT,
    )


def test_verifier_annotates_without_changing_type_or_membership():
    rows = [_row(1, [1]), _row(2, [2]), _row(3, [3])]
    row_embeddings = [[1.0, 0.0], [0.0, 1.0], [0.5, 0.5]]

    target_sections = [
        _pending_section("STOREFRONT", 1),  # neighbor agrees
        _pending_section("TOTAL_LINE", 2),  # neighbor says ITEMS
        _pending_section("FOOTER", 3),  # only same-receipt neighbor -> abstain
    ]
    neighbor_sections = [
        ReceiptSection(
            image_id=_NEIGHBOR_IMAGE_ID,
            receipt_id=2,
            section_type=section_type,
            line_ids=line_ids,
            created_at=_CREATED_AT,
            validation_status=ValidationStatus.VALID.value,
        )
        for section_type, line_ids in (
            ("STOREFRONT", [5]),
            ("ITEMS", [6]),
        )
    ]
    store = _VerifierStore(
        {
            (_IMAGE_ID, 1): target_sections,
            (_NEIGHBOR_IMAGE_ID, 2): neighbor_sections,
        }
    )
    chroma = _FakeLinesQuery(
        [
            [_neighbor([5]), _same_receipt_neighbor()],
            [_neighbor([6])],
            [_same_receipt_neighbor()],
        ]
    )

    verified = verify_receipt_sections(chroma, store, rows, row_embeddings)

    # Independent predictions exist only where cross-receipt VALID evidence
    # was found (rows 1 and 2).
    assert {v.row_id for v in verified} == {1, 2}

    updated = {
        section.section_type: section
        for section in store.by_receipt[(_IMAGE_ID, 1)]
    }
    assert updated["STOREFRONT"].verification_status == "AGREED"
    assert updated["TOTAL_LINE"].verification_status == "DISAGREED"
    assert updated["TOTAL_LINE"].verification_section_type == "ITEMS"
    assert updated["TOTAL_LINE"].disagreement_row_ids == [2]
    assert updated["FOOTER"].verification_status == "ABSTAINED"
    assert updated["FOOTER"].verification_section_type is None

    for original in target_sections:
        annotated = updated[original.section_type]
        # The invariant: verification NEVER rewrites the proposal itself.
        assert annotated.section_type == original.section_type
        assert annotated.row_ids == original.row_ids
        assert annotated.line_ids == original.line_ids
        assert annotated.model_source == MODEL_SOURCE
        assert annotated.verification_source == VERIFICATION_SOURCE
        # Disagreement leaves the proposal PENDING (it never promotes or
        # replaces); agreement also stays PENDING — promotion is a human/QA
        # decision downstream.
        assert annotated.validation_status == ValidationStatus.PENDING.value


def test_verifier_never_demotes_human_valid_sections():
    rows = [_row(1, [1])]
    row_embeddings = [[1.0, 0.0]]
    human_valid = replace(
        _pending_section("STOREFRONT", 1),
        validation_status=ValidationStatus.VALID.value,
    )
    neighbor_sections = [
        ReceiptSection(
            image_id=_NEIGHBOR_IMAGE_ID,
            receipt_id=2,
            section_type="ITEMS",
            line_ids=[6],
            created_at=_CREATED_AT,
            validation_status=ValidationStatus.VALID.value,
        )
    ]
    store = _VerifierStore(
        {
            (_IMAGE_ID, 1): [human_valid],
            (_NEIGHBOR_IMAGE_ID, 2): neighbor_sections,
        }
    )
    chroma = _FakeLinesQuery([[_neighbor([6])]])

    verify_receipt_sections(chroma, store, rows, row_embeddings)

    section = store.by_receipt[(_IMAGE_ID, 1)][0]
    assert section.verification_status == "DISAGREED"
    # A VALID section stays VALID even when the KNN vote disagrees.
    assert section.validation_status == ValidationStatus.VALID.value
    assert section.section_type == "STOREFRONT"


def test_verifier_ignores_non_model_sections():
    rows = [_row(1, [1])]
    human_section = replace(
        _pending_section("STOREFRONT", 1), model_source="human-qa"
    )
    store = _VerifierStore({(_IMAGE_ID, 1): [human_section]})
    chroma = _FakeLinesQuery([[_same_receipt_neighbor()]])

    verify_receipt_sections(chroma, store, rows, [[1.0, 0.0]])

    # Sections not produced by the deterministic model are left untouched.
    assert store.updates == []
