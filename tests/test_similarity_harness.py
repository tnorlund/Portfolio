"""Offline tests for the similarity harness (Round A).

No AWS, no Chroma, no network: a synthetic "capture" is built through the
same fixture writer capture_golden.py uses, then replayed through
evaluate.py's fake backend. Self-parity must be perfect; perturbations must
be detected; the scorecard must be deterministic modulo latency.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_EMBEDDINGS = _REPO_ROOT / "receipt_embeddings"
if str(_EMBEDDINGS) not in sys.path:
    sys.path.insert(0, str(_EMBEDDINGS))

from scripts.similarity_harness import (
    decision,
)
from scripts.similarity_harness import evaluate as ev  # noqa: E402
from scripts.similarity_harness import (
    fixtures_io,
)

from receipt_embeddings.testing import FakeVectorIndex  # noqa: E402
from receipt_embeddings.vector_client import (  # noqa: E402
    LINES_INDEX,
    WORDS_INDEX,
)

GOLD = {"image_id": "img-gold", "receipt_id": 1}


def _line_key(image_id: str, receipt_id: int, line_id: int) -> str:
    return f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}"


def _word_key(
    image_id: str, receipt_id: int, line_id: int, word_id: int
) -> str:
    return (
        f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}"
        f"#LINE#{line_id:05d}#WORD#{word_id:05d}"
    )


def _build_reference_index() -> FakeVectorIndex:
    """Tiny corpus with one gold receipt and two labeled neighbors."""
    index = FakeVectorIndex()
    # lines: gold query line + neighbors from receipts A (Vons) and B (Costco)
    index.add(
        LINES_INDEX,
        _line_key(**GOLD, line_id=1),
        [1.0, 0.0, 0.0, 0.0],
        {"image_id": GOLD["image_id"], "receipt_id": GOLD["receipt_id"]},
    )
    index.add(
        LINES_INDEX,
        _line_key("img-a", 1, 4),
        [0.98, 0.19, 0.0, 0.0],
        {
            "image_id": "img-a",
            "receipt_id": 1,
            "merchant_name": "Vons",
            "normalized_phone_10": "8055551234",
            "dynamo_place_id": "place-vons",
            "dynamo_merchant_name": "Vons",
        },
    )
    index.add(
        LINES_INDEX,
        _line_key("img-b", 2, 7),
        [0.90, 0.43, 0.0, 0.0],
        {
            "image_id": "img-b",
            "receipt_id": 2,
            "merchant_name": "Costco Wholesale",
            "dynamo_place_id": "place-costco",
            "dynamo_merchant_name": "Costco Wholesale",
        },
    )
    index.add(
        LINES_INDEX,
        _line_key("img-c", 3, 2),
        [0.0, 0.0, 1.0, 0.0],
        {"image_id": "img-c", "receipt_id": 3, "merchant_name": "Far Away"},
    )
    # Filler lines (no merchant/place) so top-10 selection is meaningful:
    # ranked between Costco and Far Away, they let a perturbation push a real
    # neighbor out of the top 10 and be caught by recall@10.
    for i in range(10):
        index.add(
            LINES_INDEX,
            _line_key("img-fill", 4, i + 1),
            [0.75, 0.6 + 0.005 * i, 0.1 * i, 0.0],
            {"image_id": "img-fill", "receipt_id": 4},
        )
    # words: one gold word + three neighbors
    index.add(
        WORDS_INDEX,
        _word_key(**GOLD, line_id=2, word_id=1),
        [0.0, 1.0, 0.0, 0.0],
        {"image_id": GOLD["image_id"], "receipt_id": GOLD["receipt_id"]},
    )
    for i, vec in enumerate(
        ([0.1, 0.99, 0.0, 0.0], [0.3, 0.9, 0.1, 0.0], [0.0, 0.7, 0.7, 0.0])
    ):
        index.add(
            WORDS_INDEX,
            _word_key("img-a", 1, 9, i + 1),
            vec,
            {"image_id": "img-a", "receipt_id": 1, "text": f"tok{i}"},
        )
    return index


def _items_to_neighbors(items) -> list[dict]:
    return [
        {
            "key": item.key,
            "distance": fixtures_io.round_distance(item.distance),
            "metadata": dict(item.metadata),
        }
        for item in items
    ]


def _build_fixture_set(reference: FakeVectorIndex) -> dict:
    """Synthetic capture: query the reference index like capture_golden."""
    gold_line = _line_key(**GOLD, line_id=1)
    merchant_neighbors = _items_to_neighbors(
        reference.search(
            reference.get_vector(gold_line, LINES_INDEX), LINES_INDEX, 20
        )
    )
    context = {
        **GOLD,
        "expected_phone": "8055551234",
        "expected_address": None,
        "line_texts": ["VONS", "805-555-1234", "TOTAL 12.34"],
    }
    queries = [
        {
            "tier": "chroma_phone",
            "query_key": gold_line,
            "line_id": 1,
            "neighbors": merchant_neighbors,
        }
    ]
    merchant_entry = {
        **GOLD,
        "context": context,
        "queries": queries,
        "decision": decision.decide_merchant(queries, context),
        "dynamo_place": {"place_id": "place-vons", "merchant_name": "Vons"},
    }

    gold_word = _word_key(**GOLD, line_id=2, word_id=1)
    word_entry = {
        **GOLD,
        "queries": [
            {
                "query_key": gold_word,
                "line_id": 2,
                "word_id": 1,
                "text": "TOTAL",
                "neighbors": _items_to_neighbors(
                    reference.search(
                        reference.get_vector(gold_word, WORDS_INDEX),
                        WORDS_INDEX,
                        30,
                    )
                ),
            }
        ],
    }

    section_neighbors = merchant_neighbors
    neighbor_labels = {
        _line_key("img-a", 1, 4): "HEADER",
        _line_key("img-b", 2, 7): "HEADER",
    }
    vote = decision.section_vote(section_neighbors, neighbor_labels, **GOLD)
    section_entry = {
        **GOLD,
        "rows": [
            {
                "row_id": 1,
                "query_key": gold_line,
                "neighbors": section_neighbors,
                "neighbor_labels": neighbor_labels,
            }
        ],
        "votes": [
            {
                "row_id": 1,
                "section_type": vote["section_type"],
                "confidence": vote["confidence"],
            }
        ],
        "section_updates": [],
    }

    line_keys = [
        gold_line,
        _line_key("img-a", 1, 4),
        _line_key("img-b", 2, 7),
        _line_key("img-c", 3, 2),
    ] + [_line_key("img-fill", 4, i + 1) for i in range(10)]
    vectors = {
        "lines": {
            key: reference.get_vector(key, LINES_INDEX) for key in line_keys
        },
        "words": {
            key: reference.get_vector(key, WORDS_INDEX)
            for key in (
                gold_word,
                _word_key("img-a", 1, 9, 1),
                _word_key("img-a", 1, 9, 2),
                _word_key("img-a", 1, 9, 3),
            )
        },
    }
    manifest = {
        "captured_at": "2026-08-31T00:00:00+00:00",
        "table": "synthetic",
        "distance_decimals": fixtures_io.DISTANCE_DECIMALS,
        "vector_decimals": fixtures_io.VECTOR_DECIMALS,
        "receipts": [GOLD],
        "counts": {
            "receipts": 1,
            "merchant_queries": 1,
            "word_queries": 1,
            "section_rows": 1,
        },
    }
    return {
        "manifest": manifest,
        "merchant": [merchant_entry],
        "words": [word_entry],
        "sections": [section_entry],
        "vectors": vectors,
    }


@pytest.fixture(name="fixture_dir")
def _fixture_dir(tmp_path: Path) -> Path:
    payload = _build_fixture_set(_build_reference_index())
    fixtures_io.write_fixtures(
        tmp_path,
        manifest=payload["manifest"],
        merchant=payload["merchant"],
        words=payload["words"],
        sections=payload["sections"],
        vectors=payload["vectors"],
    )
    return tmp_path


# --------------------------------------------------------------------------
# decision.py unit behavior
# --------------------------------------------------------------------------


class TestDecision:
    def test_phone_boost_and_place_walk(self) -> None:
        payload = _build_fixture_set(_build_reference_index())
        decided = payload["merchant"][0]["decision"]
        assert decided is not None
        assert decided["tier"] == "chroma_phone"
        assert decided["merchant_name"] == "Vons"
        assert decided["place_id"] == "place-vons"
        # similarity(1 - d/2) + 0.20 phone boost, capped at 1.0
        assert decided["confidence"] > decision.MIN_SIMILARITY_THRESHOLD

    def test_below_threshold_neighbors_are_ignored(self) -> None:
        neighbors = [
            {
                "key": "far",
                "distance": 1.9,
                "metadata": {
                    "image_id": "x",
                    "receipt_id": 9,
                    "merchant_name": "Far Away",
                    "dynamo_place_id": "place-far",
                },
            }
        ]
        assert (
            decision.decide_merchant(
                [{"tier": "chroma_text", "neighbors": neighbors}],
                {**GOLD, "line_texts": ["FAR AWAY"]},
            )
            is None
        )

    def test_own_receipt_neighbors_are_skipped(self) -> None:
        neighbors = [
            {
                "key": "self",
                "distance": 0.0,
                "metadata": {
                    "image_id": GOLD["image_id"],
                    "receipt_id": GOLD["receipt_id"],
                    "merchant_name": "Self",
                    "dynamo_place_id": "place-self",
                },
            }
        ]
        assert (
            decision.decide_merchant(
                [{"tier": "chroma_phone", "neighbors": neighbors}],
                {**GOLD, "line_texts": ["SELF STORE"]},
            )
            is None
        )

    def test_poison_guard_rejects_zero_overlap(self) -> None:
        neighbors = [
            {
                "key": "poison",
                "distance": 0.05,
                "metadata": {
                    "image_id": "img-p",
                    "receipt_id": 5,
                    "merchant_name": "Sprouts Farmers Market",
                    "dynamo_place_id": "place-sprouts",
                },
            }
        ]
        assert (
            decision.decide_merchant(
                [{"tier": "chroma_phone", "neighbors": neighbors}],
                {**GOLD, "line_texts": ["VONS", "TOTAL 9.99"]},
            )
            is None
        )

    def test_chroma_text_needs_high_confidence(self) -> None:
        def entry(dist: float) -> list[dict]:
            return [
                {
                    "tier": "chroma_text",
                    "neighbors": [
                        {
                            "key": "n",
                            "distance": dist,
                            "metadata": {
                                "image_id": "img-a",
                                "receipt_id": 1,
                                "merchant_name": "Vons",
                                "dynamo_place_id": "place-vons",
                            },
                        }
                    ],
                }
            ]

        ctx = {**GOLD, "line_texts": ["VONS STORE"]}
        # sim 0.75: above MIN (phone tier would take it) but below HIGH
        assert decision.decide_merchant(entry(0.5), ctx) is None
        # sim 0.95 clears the text-tier bar
        assert decision.decide_merchant(entry(0.1), ctx) is not None

    def test_section_vote_weights_by_similarity(self) -> None:
        neighbors = [
            {
                "key": "h1",
                "distance": 0.1,
                "metadata": {"image_id": "a", "receipt_id": 1},
            },
            {
                "key": "i1",
                "distance": 0.6,
                "metadata": {"image_id": "b", "receipt_id": 2},
            },
            {
                "key": "i2",
                "distance": 0.7,
                "metadata": {"image_id": "c", "receipt_id": 3},
            },
        ]
        labels = {"h1": "HEADER", "i1": "ITEMS", "i2": "ITEMS"}
        vote = decision.section_vote(neighbors, labels, **GOLD)
        assert vote is not None
        # weights: HEADER 0.9 vs ITEMS 0.4 + 0.3
        assert vote["section_type"] == "HEADER"
        assert 0 < vote["confidence"] < 1

    def test_section_vote_abstains_without_labels(self) -> None:
        neighbors = [
            {
                "key": "x",
                "distance": 0.2,
                "metadata": {"image_id": "a", "receipt_id": 1},
            }
        ]
        assert decision.section_vote(neighbors, {}, **GOLD) is None


def _import_or_skip(module_name: str):
    """importorskip that also tolerates broken transitive deps.

    Importing the resolver drags in receipt_chroma -> chromadb, whose import
    can fail with non-ImportError errors (e.g. pydantic-v1 ConfigError on
    newer Pythons). The parity check is best-effort: it runs wherever the
    source module imports, and skips — never fails — where it cannot.
    """
    try:
        return __import__(module_name, fromlist=["_"])
    except Exception as exc:  # noqa: BLE001 - any import failure skips
        pytest.skip(f"cannot import {module_name}: {exc!r}")


class TestConstantParity:
    """Vendored constants must match their source modules when importable."""

    def test_resolver_constants(self) -> None:
        resolver = _import_or_skip(
            "receipt_upload.merchant_resolution.resolver"
        )
        assert (
            decision.MIN_SIMILARITY_THRESHOLD
            == resolver.MIN_SIMILARITY_THRESHOLD
        )
        assert (
            decision.HIGH_CONFIDENCE_THRESHOLD
            == resolver.HIGH_CONFIDENCE_THRESHOLD
        )
        assert decision.PHONE_MATCH_BOOST == resolver.PHONE_MATCH_BOOST
        assert decision.ADDRESS_MATCH_BOOST == resolver.ADDRESS_MATCH_BOOST
        assert (
            decision._GENERIC_MERCHANT_TOKENS
            == resolver._GENERIC_MERCHANT_TOKENS
        )
        assert decision._MIN_TOKEN_LEN == resolver._MIN_TOKEN_LEN

    def test_section_verifier_constants(self) -> None:
        verifier = _import_or_skip("receipt_upload.section_verifier")
        assert decision.SECTION_KNN_NEIGHBORS == verifier.KNN_NEIGHBORS


# --------------------------------------------------------------------------
# fixtures_io
# --------------------------------------------------------------------------


class TestFixturesIO:
    def test_roundtrip(self, fixture_dir: Path) -> None:
        loaded = fixtures_io.load_fixtures(fixture_dir)
        assert loaded["manifest"]["counts"]["receipts"] == 1
        assert loaded["vectors"]["lines"]
        assert loaded["merchant"][0]["decision"]["merchant_name"] == "Vons"

    def test_write_is_byte_deterministic(self, tmp_path: Path) -> None:
        payload = _build_fixture_set(_build_reference_index())
        for name in ("one", "two"):
            fixtures_io.write_fixtures(
                tmp_path / name,
                manifest=payload["manifest"],
                merchant=payload["merchant"],
                words=payload["words"],
                sections=payload["sections"],
                vectors=payload["vectors"],
            )
        for filename in (
            fixtures_io.MANIFEST_FILE,
            fixtures_io.MERCHANT_FILE,
            fixtures_io.WORDS_FILE,
            fixtures_io.SECTIONS_FILE,
            fixtures_io.VECTORS_FILE,
        ):
            assert (tmp_path / "one" / filename).read_bytes() == (
                tmp_path / "two" / filename
            ).read_bytes(), filename


# --------------------------------------------------------------------------
# evaluate.py
# --------------------------------------------------------------------------


class TestEvaluate:
    def test_fake_backend_self_parity(self, fixture_dir: Path) -> None:
        fixtures = fixtures_io.load_fixtures(fixture_dir)
        backend = ev.build_fake_backend(fixtures)
        scorecard = ev.evaluate(fixtures, backend, "fake")
        assert scorecard["neighbor_recall"]["merchant_lines_at_10"] == 1.0
        assert scorecard["neighbor_recall"]["words_at_10"] == 1.0
        assert scorecard["neighbor_recall"]["words_at_30"] == 1.0
        assert scorecard["merchant"]["agreement_pct"] == 100.0
        assert scorecard["sections"]["vote_agreement_pct"] == 100.0
        assert all(
            delta == 0
            for delta in scorecard["merchant"][
                "tier_distribution_delta"
            ].values()
        )
        assert scorecard["est_cost_per_query_usd"] == 0.0
        assert scorecard["missing_query_vectors"] == 0
        assert scorecard["latency_ms"]["count"] > 0

    def test_detects_perturbed_backend(self, fixture_dir: Path) -> None:
        fixtures = fixtures_io.load_fixtures(fixture_dir)
        broken = copy.deepcopy(fixtures)
        # Flip the Vons neighbor vector away from the query: neighbors and
        # the merchant decision must change, and the scorecard must see it.
        broken["vectors"]["lines"][_line_key("img-a", 1, 4)] = [
            0.0,
            0.0,
            0.0,
            1.0,
        ]
        backend = ev.build_fake_backend(broken)
        scorecard = ev.evaluate(fixtures, backend, "fake")
        assert scorecard["neighbor_recall"]["merchant_lines_at_10"] < 1.0
        assert scorecard["merchant"]["agreement_pct"] < 100.0

    def test_scorecard_is_deterministic_modulo_latency(
        self, fixture_dir: Path
    ) -> None:
        fixtures = fixtures_io.load_fixtures(fixture_dir)
        cards = []
        for _ in range(2):
            card = ev.evaluate(
                fixtures, ev.build_fake_backend(fixtures), "fake"
            )
            card.pop("latency_ms")
            cards.append(json.dumps(card, sort_keys=True))
        assert cards[0] == cards[1]

    def test_dynamo_backend_is_a_stub(self) -> None:
        backend = ev.DynamoBackend()
        with pytest.raises(NotImplementedError):
            backend.search([1.0], LINES_INDEX, 5)
        with pytest.raises(NotImplementedError):
            backend.get_vector("key", LINES_INDEX)

    def test_fake_backend_requires_sidecar(self, fixture_dir: Path) -> None:
        fixtures = fixtures_io.load_fixtures(fixture_dir)
        fixtures["vectors"] = None
        with pytest.raises(SystemExit):
            ev.build_fake_backend(fixtures)

    def test_cli_writes_scorecard(
        self, fixture_dir: Path, tmp_path: Path, monkeypatch
    ) -> None:
        out = tmp_path / "scorecard.json"
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "evaluate.py",
                "--backend",
                "fake",
                "--fixtures",
                str(fixture_dir),
                "--out",
                str(out),
            ],
        )
        assert ev.main() == 0
        scorecard = json.loads(out.read_text())
        assert scorecard["backend"] == "fake"
        assert scorecard["merchant"]["agreement_pct"] == 100.0
