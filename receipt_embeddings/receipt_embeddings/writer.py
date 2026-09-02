"""Idempotent OpenAI-realtime to DynamoDB embedding writer."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from receipt_embeddings.keys import (
    canonical_from_dynamo_key,
    embedding_item_key,
)
from receipt_embeddings.openai import embed_texts
from receipt_embeddings.protocols import DynamoBatchClient, DynamoItem
from receipt_embeddings.service_limits import (
    LINE_INDEX,
    MAX_BATCH_GET_ITEMS,
    MAX_BATCH_WRITE_ITEMS,
    WORD_INDEX,
)

from receipt_dynamo.entities import (
    ReceiptEmbedding,
    ReceiptLineEmbedding,
    ReceiptWordEmbedding,
)

EmbeddingKind = Literal["line", "word"]
Embedder = Callable[..., list[list[float]]]


@dataclass(frozen=True, slots=True)
class EmbeddingWriteRequest:
    """One line or word embedding item before vector generation."""

    kind: EmbeddingKind
    image_id: str
    receipt_id: int
    line_id: int
    text: str
    embedding_input: str | None = None
    merchant_name: str = ""
    place_id: str = ""
    row_line_ids: tuple[int, ...] = ()
    section_type: str = ""
    normalized_phone_10: str = ""
    normalized_full_address: str = ""
    word_id: int | None = None
    label_status: str = "none"
    vector: Sequence[float] | None = None

    @property
    def key(self) -> dict[str, dict[str, str]]:
        if self.kind == "word":
            if self.word_id is None:
                raise ValueError("word requests require word_id")
            return embedding_item_key(
                self.image_id, self.receipt_id, self.line_id, self.word_id
            )
        if self.kind != "line":
            raise ValueError(f"unsupported embedding kind: {self.kind!r}")
        return embedding_item_key(self.image_id, self.receipt_id, self.line_id)

    @property
    def canonical_key(self) -> str:
        return canonical_from_dynamo_key(self.key)

    @property
    def index(self) -> str:
        return WORD_INDEX if self.kind == "word" else LINE_INDEX

    def build_entity(self, vector: Sequence[float]) -> ReceiptEmbedding:
        if self.kind == "word":
            if self.word_id is None:
                raise ValueError("word requests require word_id")
            return ReceiptWordEmbedding(
                image_id=self.image_id,
                receipt_id=self.receipt_id,
                line_id=self.line_id,
                word_id=self.word_id,
                text=self.text,
                merchant_name=self.merchant_name,
                label_status=self.label_status,
                word_vector=list(vector),
            )
        if not self.row_line_ids:
            raise ValueError("line requests require row_line_ids")
        return ReceiptLineEmbedding(
            image_id=self.image_id,
            receipt_id=self.receipt_id,
            line_id=self.line_id,
            text=self.text,
            merchant_name=self.merchant_name,
            place_id=self.place_id,
            row_line_ids=list(self.row_line_ids),
            section_type=self.section_type,
            line_vector=list(vector),
            normalized_phone_10=self.normalized_phone_10,
            normalized_full_address=self.normalized_full_address,
        )


@dataclass(frozen=True, slots=True)
class EmbeddingWriteFailure:
    key: str
    stage: Literal["validate", "read", "embed", "write"]
    error: str


@dataclass(slots=True)
class EmbeddingWriteReport:
    written_keys: list[str] = field(default_factory=list)
    skipped_existing_keys: list[str] = field(default_factory=list)
    failures: list[EmbeddingWriteFailure] = field(default_factory=list)

    @property
    def written(self) -> int:
        return len(self.written_keys)

    @property
    def skipped(self) -> int:
        return len(self.skipped_existing_keys) + len(self.failures)

    @property
    def incomplete(self) -> bool:
        """True when any item failed validate, read, embed, or write."""

        return bool(self.failures)

    def as_dict(self) -> dict[str, object]:
        return {
            "written": self.written,
            "skipped": self.skipped,
            "skipped_existing": len(self.skipped_existing_keys),
            "failed": len(self.failures),
            "written_keys": self.written_keys,
            "skipped_existing_keys": self.skipped_existing_keys,
            "failures": [
                {"key": value.key, "stage": value.stage, "error": value.error}
                for value in self.failures
            ],
        }


def write_report_incomplete(
    report: EmbeddingWriteReport | Mapping[str, object] | None,
) -> bool:
    """True when a dual-write or engine report is missing or failed.

    ``None`` (flag-off dual-write) is complete. The dict shape from
    ``maybe_dual_write_embeddings`` is incomplete when it carries
    ``error`` or a non-zero ``failed`` count. An
    ``EmbeddingWriteReport`` is incomplete iff it has failures.

    Callers keep their own response to this predicate (never-raise vs
    abort-before-delete); this helper only names the shared check.
    """

    if report is None:
        return False
    if isinstance(report, EmbeddingWriteReport):
        return report.incomplete
    return bool(report.get("error") or report.get("failed"))


class EmbeddingWriter:
    """Write only missing embedding items and report every isolated failure."""

    def __init__(
        self,
        dynamodb_client: DynamoBatchClient,
        table_name: str,
        *,
        openai_client: object | None = None,
        embedder: Embedder = embed_texts,
        model: str = "text-embedding-3-small",
        max_retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not table_name:
            raise ValueError("table_name must not be empty")
        self._client = dynamodb_client
        self.table_name = table_name
        self._openai_client = openai_client
        self._embedder = embedder
        self._model = model
        self._max_retries = max_retries
        self._sleep = sleep

    @staticmethod
    def _key_id(key: DynamoItem) -> str:
        return f"{key['PK']['S']}#{key['SK']['S']}"

    def _existing_keys(
        self,
        requests: list[EmbeddingWriteRequest],
        report: EmbeddingWriteReport,
    ) -> tuple[set[str], set[str]]:
        existing: set[str] = set()
        read_failed: set[str] = set()
        for offset in range(0, len(requests), MAX_BATCH_GET_ITEMS):
            chunk = requests[offset : offset + MAX_BATCH_GET_ITEMS]
            pending = [request.key for request in chunk]
            try:
                for attempt in range(self._max_retries + 1):
                    response = self._client.batch_get_item(
                        RequestItems={
                            self.table_name: {
                                "Keys": pending,
                                "ProjectionExpression": "PK, SK",
                                "ConsistentRead": True,
                            }
                        }
                    )
                    for item in response.get("Responses", {}).get(
                        self.table_name, []
                    ):
                        existing.add(
                            self._key_id({"PK": item["PK"], "SK": item["SK"]})
                        )
                    pending = (
                        response.get("UnprocessedKeys", {})
                        .get(self.table_name, {})
                        .get("Keys", [])
                    )
                    if not pending:
                        break
                    if attempt < self._max_retries:
                        self._sleep(0.1 * (2**attempt))
                for key in pending:
                    key_id = self._key_id(key)
                    read_failed.add(key_id)
                    report.failures.append(
                        EmbeddingWriteFailure(
                            key=key_id,
                            stage="read",
                            error="BatchGetItem remained unprocessed after retries",
                        )
                    )
            except Exception as exc:  # noqa: BLE001 - isolate and report
                for request in chunk:
                    key_id = self._key_id(request.key)
                    read_failed.add(key_id)
                    report.failures.append(
                        EmbeddingWriteFailure(
                            key=key_id, stage="read", error=str(exc)
                        )
                    )
        return existing, read_failed

    def _embed_one(self, request: EmbeddingWriteRequest) -> list[float]:
        if request.vector is not None:
            return [float(value) for value in request.vector]
        vectors = self._embedder(
            client=self._openai_client,
            texts=[request.embedding_input or request.text],
            model=self._model,
        )
        if len(vectors) != 1:
            raise ValueError("OpenAI realtime embedding returned no vector")
        return vectors[0]

    @staticmethod
    def _assert_safe_item(item: Mapping[str, object]) -> None:
        sk = item.get("SK", {}).get("S", "")
        item_type = item.get("TYPE", {}).get("S")
        if not sk.startswith("RECEIPT#") or not sk.endswith("#EMBEDDING"):
            raise ValueError("writer refuses non-embedding sort keys")
        if item_type not in {
            ReceiptLineEmbedding.TYPE,
            ReceiptWordEmbedding.TYPE,
        }:
            raise ValueError("writer refuses non-embedding item types")

    def _write_requests(
        self,
        values: list[tuple[str, dict[str, object]]],
        report: EmbeddingWriteReport,
    ) -> None:
        for offset in range(0, len(values), MAX_BATCH_WRITE_ITEMS):
            chunk = values[offset : offset + MAX_BATCH_WRITE_ITEMS]
            pending = [{"PutRequest": {"Item": item}} for _, item in chunk]
            try:
                for attempt in range(self._max_retries + 1):
                    response = self._client.batch_write_item(
                        RequestItems={self.table_name: pending},
                        ReturnConsumedCapacity="INDEXES",
                    )
                    pending = response.get("UnprocessedItems", {}).get(
                        self.table_name, []
                    )
                    if not pending:
                        report.written_keys.extend(key for key, _ in chunk)
                        break
                    if attempt < self._max_retries:
                        self._sleep(0.1 * (2**attempt))
                if pending:
                    pending_ids = {
                        self._key_id(value["PutRequest"]["Item"])
                        for value in pending
                    }
                    for key, item in chunk:
                        if self._key_id(item) in pending_ids:
                            report.failures.append(
                                EmbeddingWriteFailure(
                                    key=key,
                                    stage="write",
                                    error=(
                                        "BatchWriteItem remained unprocessed "
                                        "after retries"
                                    ),
                                )
                            )
                        else:
                            report.written_keys.append(key)
            except Exception:
                # A batch exception does not identify the failing item. Retry
                # singly so healthy items still land and failures are attributable.
                for key, item in chunk:
                    try:
                        response = self._client.batch_write_item(
                            RequestItems={
                                self.table_name: [
                                    {"PutRequest": {"Item": item}}
                                ]
                            },
                            ReturnConsumedCapacity="INDEXES",
                        )
                        unprocessed = response.get("UnprocessedItems", {}).get(
                            self.table_name, []
                        )
                        if unprocessed:
                            raise RuntimeError("item remained unprocessed")
                        report.written_keys.append(key)
                    except Exception as item_exc:  # noqa: BLE001
                        report.failures.append(
                            EmbeddingWriteFailure(
                                key=key, stage="write", error=str(item_exc)
                            )
                        )

    def write(
        self, requests: Sequence[EmbeddingWriteRequest]
    ) -> EmbeddingWriteReport:
        report = EmbeddingWriteReport()
        unique: list[EmbeddingWriteRequest] = []
        seen: set[str] = set()
        for request in requests:
            try:
                key_id = self._key_id(request.key)
            except (TypeError, ValueError) as exc:
                report.failures.append(
                    EmbeddingWriteFailure(
                        key="<invalid>", stage="validate", error=str(exc)
                    )
                )
                continue
            if key_id in seen:
                report.failures.append(
                    EmbeddingWriteFailure(
                        key=key_id,
                        stage="validate",
                        error="duplicate request key",
                    )
                )
                continue
            seen.add(key_id)
            unique.append(request)

        existing, read_failed = self._existing_keys(unique, report)
        report.skipped_existing_keys.extend(sorted(existing))
        to_write: list[tuple[str, dict[str, object]]] = []
        for request in unique:
            key_id = self._key_id(request.key)
            if key_id in existing or key_id in read_failed:
                continue
            try:
                entity = request.build_entity(self._embed_one(request))
                item = entity.to_item()
                self._assert_safe_item(item)
                to_write.append((request.canonical_key, item))
            except Exception as exc:  # noqa: BLE001 - isolate and report
                report.failures.append(
                    EmbeddingWriteFailure(
                        key=key_id, stage="embed", error=str(exc)
                    )
                )
        self._write_requests(to_write, report)
        return report


__all__ = [
    "EmbeddingWriteFailure",
    "EmbeddingWriteReport",
    "EmbeddingWriteRequest",
    "EmbeddingWriter",
    "write_report_incomplete",
]
