import importlib
from types import SimpleNamespace

import pytest


class FakePineconeIndex:
    def __init__(self, metadata=None, query_score=0.8):
        self.metadata = metadata or {}
        self.query_score = query_score

    def fetch(self, ids, namespace="words"):
        vector = SimpleNamespace(
            id=ids[0], values=[0.0], metadata=self.metadata
        )
        return SimpleNamespace(vectors={ids[0]: vector})

    def query(
        self,
        vector=None,
        top_k=10,
        include_metadata=True,
        filter=None,
        namespace="words",
    ):
        match = SimpleNamespace(id="m1", score=self.query_score, metadata={})
        return SimpleNamespace(matches=[match])


def _make_label(label: str) -> SimpleNamespace:
    return SimpleNamespace(
        image_id="img", receipt_id=1, line_id=1, word_id=1, label=label
    )


@pytest.mark.unit
def test_validate_address(mocker):
    module = importlib.import_module(
        "receipt_label.label_validation.validate_address"
    )
    fake_index = FakePineconeIndex()
    mocker.patch.object(module, "pinecone_index", fake_index)

    word = SimpleNamespace(text="123 Main St")
    label = _make_label("ADDRESS")
    metadata = SimpleNamespace(canonical_address="123 main st, city")

    result = module.validate_address(word, label, metadata)
    assert result.status == "VALIDATED"
    assert result.is_consistent
    assert result.avg_similarity == 1.0

    bad_meta = SimpleNamespace(canonical_address="456 other ave")
    result = module.validate_address(word, label, bad_meta)
    assert not result.is_consistent
    assert result.avg_similarity == 0.0


@pytest.mark.unit
def test_validate_currency(mocker):
    module = importlib.import_module(
        "receipt_label.label_validation.validate_currency"
    )
    fake_index = FakePineconeIndex(query_score=0.9)
    mocker.patch.object(module, "pinecone_index", fake_index)

    word = SimpleNamespace(text="$10.00")
    label = _make_label("TOTAL")
    result = module.validate_currency(word, label)
    assert result.status == "VALIDATED"
    assert result.is_consistent
    assert result.avg_similarity == pytest.approx(0.9)

    word_invalid = SimpleNamespace(text="hello")
    result = module.validate_currency(word_invalid, label)
    assert not result.is_consistent
    assert result.avg_similarity == pytest.approx(0.9)


@pytest.mark.unit
def test_validate_phone_number(mocker):
    module = importlib.import_module(
        "receipt_label.label_validation.validate_phone_number"
    )
    fake_index = FakePineconeIndex(query_score=0.85)
    mocker.patch.object(module, "pinecone_index", fake_index)

    word = SimpleNamespace(text="(555) 123-4567")
    label = _make_label("PHONE_NUMBER")
    result = module.validate_phone_number(word, label)
    assert result.is_consistent
    assert result.avg_similarity == pytest.approx(0.85)

    word_invalid = SimpleNamespace(text="not a phone")
    result = module.validate_phone_number(word_invalid, label)
    assert not result.is_consistent
    assert result.avg_similarity == pytest.approx(0.85)


@pytest.mark.unit
def test_validate_merchant_name(mocker):
    module = importlib.import_module(
        "receipt_label.label_validation.validate_merchant_name"
    )
    fake_index = FakePineconeIndex(query_score=0.8)
    mocker.patch.object(module, "pinecone_index", fake_index)

    word = SimpleNamespace(text="STARBUCKS")
    label = _make_label("MERCHANT_NAME")
    result = module.validate_merchant_name_pinecone(word, label, "STARBUCKS")
    assert result.is_consistent
    assert result.avg_similarity == pytest.approx(0.8)

    bad_word = SimpleNamespace(text="1234")
    result = module.validate_merchant_name_pinecone(
        bad_word, label, "STARBUCKS"
    )
    assert not result.is_consistent

    meta = SimpleNamespace(canonical_merchant_name="Starbucks")
    result = module.validate_merchant_name_google(word, label, meta)
    assert result.is_consistent
    assert result.avg_similarity == pytest.approx(1.0)

    meta_bad = SimpleNamespace(canonical_merchant_name="Other")
    result = module.validate_merchant_name_google(word, label, meta_bad)
    assert not result.is_consistent


@pytest.mark.unit
def test_validate_date_and_time(mocker):
    date_module = importlib.import_module(
        "receipt_label.label_validation.validate_date"
    )
    time_module = importlib.import_module(
        "receipt_label.label_validation.validate_time"
    )
    fake_index = FakePineconeIndex(query_score=0.88)
    mocker.patch.object(date_module, "pinecone_index", fake_index)
    mocker.patch.object(time_module, "pinecone_index", fake_index)

    label = _make_label("DATE")
    word_date = SimpleNamespace(text="2024-05-01")
    result = date_module.validate_date(word_date, label)
    assert result.is_consistent
    assert result.avg_similarity == pytest.approx(0.88)

    word_date_bad = SimpleNamespace(text="invalid")
    result = date_module.validate_date(word_date_bad, label)
    assert not result.is_consistent

    label_time = _make_label("TIME")
    word_time = SimpleNamespace(text="12:30 PM")
    result = time_module.validate_time(word_time, label_time)
    assert result.is_consistent
    assert result.avg_similarity == pytest.approx(0.88)

    word_time_bad = SimpleNamespace(text="99:99")
    result = time_module.validate_time(word_time_bad, label_time)
    assert not result.is_consistent
