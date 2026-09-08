from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Any, Generator

from receipt_dynamo.constants import OCRJobType, OCRStatus
from receipt_dynamo.entities.entity_factory import (
    EntityFactory,
    create_image_receipt_pk_parser,
    create_ocr_job_extractors,
    create_ocr_job_sk_parser,
)
from receipt_dynamo.entities.util import (
    _repr_str,
    assert_valid_uuid,
    normalize_enum,
)

# Contract shared with the Swift OCR worker and the strategy ladder in
# receipt_upload.line_items.reocr_strategy -- keep the values in sync.
VALID_REOCR_STRATEGIES = ("plain", "invert", "deskew", "upscale2x")


@dataclass(eq=True, unsafe_hash=False)
class OCRJob:
    """
    Represents an OCR job in DynamoDB.
    """

    REQUIRED_KEYS = {
        "PK",
        "SK",
        "TYPE",
        "s3_bucket",
        "s3_key",
        "created_at",
        "status",
        "job_type",
        "receipt_id",
    }

    image_id: str
    job_id: str
    s3_bucket: str
    s3_key: str
    created_at: datetime
    updated_at: datetime | None = None
    status: str = OCRStatus.PENDING.value
    job_type: str = OCRJobType.FIRST_PASS.value
    receipt_id: int | None = None
    reocr_region: dict[str, float] | None = None
    reocr_reason: str | None = None
    # SMART re-OCR fields (all optional / absent-tolerant). The trigger
    # writes strategy + mechanism; the overlay writes the completion
    # metrics. Consumed by the Swift worker under exactly these names.
    reocr_strategy: str | None = None
    reocr_mechanism: str | None = None
    reocr_words_accepted: int | None = None
    reocr_words_rejected: int | None = None
    reocr_delta_before: float | None = None
    reocr_delta_after: float | None = None
    # LINE_ITEM_REFINE fields: the real summary (from ReceiptSummary) and
    # merchant, carried on the job so the Mac worker decodes with the
    # graded baseline instead of its own scanned figures. Values may be
    # None per figure (a summary without a printed tax is normal).
    refine_summary: dict[str, float | None] | None = None
    refine_merchant_name: str | None = None

    def __post_init__(self) -> None:
        """Validate and normalize initialization arguments."""
        self._validate_fields()
        self.status = normalize_enum(self.status, OCRStatus)
        self.job_type = normalize_enum(self.job_type, OCRJobType)
        self.reocr_region = deepcopy(self.reocr_region)

    def _validate_fields(self) -> None:
        """Validate fields at construction and before persistence."""
        assert_valid_uuid(self.image_id)
        assert_valid_uuid(self.job_id)

        if not isinstance(self.s3_bucket, str):
            raise ValueError("s3_bucket must be a string")
        if not self.s3_bucket:
            raise ValueError("s3_bucket must be non-empty")

        if not isinstance(self.s3_key, str):
            raise ValueError("s3_key must be a string")
        if not self.s3_key:
            raise ValueError("s3_key must be non-empty")

        if not isinstance(self.created_at, datetime):
            raise ValueError("created_at must be a datetime")

        if self.updated_at is not None and not isinstance(
            self.updated_at, datetime
        ):
            raise ValueError("updated_at must be a datetime or None")

        normalize_enum(self.status, OCRStatus)
        normalize_enum(self.job_type, OCRJobType)

        if self.receipt_id is not None:
            if isinstance(self.receipt_id, bool) or not isinstance(
                self.receipt_id, int
            ):
                raise ValueError("receipt_id must be an integer or None")
            if self.receipt_id <= 0:
                raise ValueError("receipt_id must be greater than zero")

        if self.reocr_region is not None:
            if not isinstance(self.reocr_region, dict):
                raise ValueError("reocr_region must be a dict or None")
            required = {"x", "y", "width", "height"}
            if set(self.reocr_region.keys()) != required:
                raise ValueError(
                    "reocr_region must contain exactly: x, y, width, height"
                )
            for key in required:
                value = self.reocr_region[key]
                if (
                    isinstance(value, bool)
                    or not isinstance(value, int | float)
                    or not isfinite(value)
                ):
                    raise ValueError(
                        f"reocr_region[{key}] must be numeric, got {type(value)}"
                    )
            if (
                self.reocr_region["width"] <= 0
                or self.reocr_region["height"] <= 0
            ):
                raise ValueError(
                    "reocr_region width and height must be greater than 0"
                )

        if self.refine_summary is not None:
            if not isinstance(self.refine_summary, dict):
                raise ValueError("refine_summary must be a dict or None")
            required = {"subtotal", "tax", "grand_total"}
            if set(self.refine_summary.keys()) != required:
                raise ValueError(
                    "refine_summary must contain exactly: "
                    "subtotal, tax, grand_total"
                )
            for key, value in self.refine_summary.items():
                if value is None:
                    continue
                if (
                    isinstance(value, bool)
                    or not isinstance(value, int | float)
                    or not isfinite(value)
                ):
                    raise ValueError(
                        f"refine_summary[{key}] must be numeric or None"
                    )

        if self.refine_merchant_name is not None and not isinstance(
            self.refine_merchant_name, str
        ):
            raise ValueError("refine_merchant_name must be a string or None")

        if self.reocr_reason is not None and not isinstance(
            self.reocr_reason, str
        ):
            raise ValueError("reocr_reason must be a string or None")

        if self.reocr_strategy is not None and (
            not isinstance(self.reocr_strategy, str)
            or self.reocr_strategy not in VALID_REOCR_STRATEGIES
        ):
            raise ValueError(
                "reocr_strategy must be one of "
                f"{VALID_REOCR_STRATEGIES} or None"
            )

        if self.reocr_mechanism is not None and (
            not isinstance(self.reocr_mechanism, str)
            or not self.reocr_mechanism
        ):
            raise ValueError(
                "reocr_mechanism must be a non-empty string or None"
            )

        for count_field in ("reocr_words_accepted", "reocr_words_rejected"):
            count = getattr(self, count_field)
            if count is None:
                continue
            if isinstance(count, bool) or not isinstance(count, int):
                raise ValueError(f"{count_field} must be an integer or None")
            if count < 0:
                raise ValueError(f"{count_field} must not be negative")

        for delta_field in ("reocr_delta_before", "reocr_delta_after"):
            delta = getattr(self, delta_field)
            if delta is None:
                continue
            if (
                isinstance(delta, bool)
                or not isinstance(delta, int | float)
                or not isfinite(delta)
            ):
                raise ValueError(
                    f"{delta_field} must be a finite number or None"
                )

    @property
    def key(self) -> dict[str, Any]:
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {"S": f"OCR_JOB#{self.job_id}"},
        }

    def gsi1_key(self) -> dict[str, Any]:
        return {
            "GSI1PK": {"S": f"OCR_JOB_STATUS#{self.status}"},
            "GSI1SK": {"S": f"OCR_JOB#{self.job_id}"},
        }

    def gsi2_key(self) -> dict[str, Any]:
        return {
            "GSI2PK": {"S": f"OCR_JOB_STATUS#{self.status}"},
            "GSI2SK": {"S": f"OCR_JOB#{self.job_id}"},
        }

    def to_item(self) -> dict[str, Any]:
        self.status = normalize_enum(self.status, OCRStatus)
        self.job_type = normalize_enum(self.job_type, OCRJobType)
        self._validate_fields()
        return {
            **self.key,
            **self.gsi1_key(),
            **self.gsi2_key(),
            "TYPE": {"S": "OCR_JOB"},
            "s3_bucket": {"S": self.s3_bucket},
            "s3_key": {"S": self.s3_key},
            "created_at": {"S": self.created_at.isoformat()},
            "updated_at": (
                {"S": self.updated_at.isoformat()}
                if self.updated_at is not None
                else {"NULL": True}
            ),
            "status": {"S": self.status},
            "job_type": {"S": self.job_type},
            "receipt_id": (
                {"N": str(self.receipt_id)}
                if self.receipt_id is not None
                else {"NULL": True}
            ),
            "reocr_region": (
                {
                    "M": {
                        key: {"N": str(float(value))}
                        for key, value in self.reocr_region.items()
                    }
                }
                if self.reocr_region is not None
                else {"NULL": True}
            ),
            "reocr_reason": (
                {"S": self.reocr_reason}
                if self.reocr_reason is not None
                else {"NULL": True}
            ),
            "reocr_strategy": (
                {"S": self.reocr_strategy}
                if self.reocr_strategy is not None
                else {"NULL": True}
            ),
            "reocr_mechanism": (
                {"S": self.reocr_mechanism}
                if self.reocr_mechanism is not None
                else {"NULL": True}
            ),
            "reocr_words_accepted": (
                {"N": str(self.reocr_words_accepted)}
                if self.reocr_words_accepted is not None
                else {"NULL": True}
            ),
            "reocr_words_rejected": (
                {"N": str(self.reocr_words_rejected)}
                if self.reocr_words_rejected is not None
                else {"NULL": True}
            ),
            "reocr_delta_before": (
                {"N": str(float(self.reocr_delta_before))}
                if self.reocr_delta_before is not None
                else {"NULL": True}
            ),
            "reocr_delta_after": (
                {"N": str(float(self.reocr_delta_after))}
                if self.reocr_delta_after is not None
                else {"NULL": True}
            ),
            "refine_summary": (
                {
                    "M": {
                        key: (
                            {"N": str(float(value))}
                            if value is not None
                            else {"NULL": True}
                        )
                        for key, value in self.refine_summary.items()
                    }
                }
                if self.refine_summary is not None
                else {"NULL": True}
            ),
            "refine_merchant_name": (
                {"S": self.refine_merchant_name}
                if self.refine_merchant_name is not None
                else {"NULL": True}
            ),
        }

    def __repr__(self) -> str:
        return (
            "OCRJob("
            f"image_id={_repr_str(self.image_id)}, "
            f"job_id={_repr_str(self.job_id)}, "
            f"s3_bucket={_repr_str(self.s3_bucket)}, "
            f"s3_key={_repr_str(self.s3_key)}, "
            f"created_at={self.created_at}, "
            f"updated_at={self.updated_at}, "
            f"status={_repr_str(self.status)}, "
            f"job_type={_repr_str(self.job_type)}, "
            f"receipt_id={self.receipt_id}, "
            f"reocr_region={self.reocr_region}, "
            f"reocr_reason={_repr_str(self.reocr_reason)}, "
            f"reocr_strategy={_repr_str(self.reocr_strategy)}, "
            f"reocr_mechanism={_repr_str(self.reocr_mechanism)}, "
            f"reocr_words_accepted={self.reocr_words_accepted}, "
            f"reocr_words_rejected={self.reocr_words_rejected}, "
            f"reocr_delta_before={self.reocr_delta_before}, "
            f"reocr_delta_after={self.reocr_delta_after}"
            ")"
        )

    def __iter__(self) -> Generator[tuple[str, Any], None, None]:
        yield "image_id", self.image_id
        yield "job_id", self.job_id
        yield "s3_bucket", self.s3_bucket
        yield "s3_key", self.s3_key
        yield "created_at", self.created_at
        yield "updated_at", self.updated_at
        yield "status", self.status
        yield "job_type", self.job_type
        yield "receipt_id", self.receipt_id
        yield "reocr_region", self.reocr_region
        yield "reocr_reason", self.reocr_reason
        yield "reocr_strategy", self.reocr_strategy
        yield "reocr_mechanism", self.reocr_mechanism
        yield "reocr_words_accepted", self.reocr_words_accepted
        yield "reocr_words_rejected", self.reocr_words_rejected
        yield "reocr_delta_before", self.reocr_delta_before
        yield "reocr_delta_after", self.reocr_delta_after
        yield "refine_summary", self.refine_summary
        yield "refine_merchant_name", self.refine_merchant_name

    def __eq__(self, other) -> bool:
        if not isinstance(other, OCRJob):
            return False
        return (
            self.image_id == other.image_id
            and self.job_id == other.job_id
            and self.s3_bucket == other.s3_bucket
            and self.s3_key == other.s3_key
            and self.created_at == other.created_at
            and self.updated_at == other.updated_at
            and self.status == other.status
            and self.job_type == other.job_type
            and self.receipt_id == other.receipt_id
            and self.reocr_region == other.reocr_region
            and self.reocr_reason == other.reocr_reason
            and self.reocr_strategy == other.reocr_strategy
            and self.reocr_mechanism == other.reocr_mechanism
            and self.reocr_words_accepted == other.reocr_words_accepted
            and self.reocr_words_rejected == other.reocr_words_rejected
            and self.reocr_delta_before == other.reocr_delta_before
            and self.reocr_delta_after == other.reocr_delta_after
            and self.refine_summary == other.refine_summary
            and self.refine_merchant_name == other.refine_merchant_name
        )

    def __hash__(self) -> int:
        return hash(
            (
                self.image_id,
                self.job_id,
                self.s3_bucket,
                self.s3_key,
                self.created_at,
                self.updated_at,
                self.status,
                self.job_type,
                self.receipt_id,
                (
                    tuple(sorted(self.reocr_region.items()))
                    if self.reocr_region is not None
                    else None
                ),
                self.reocr_reason,
                self.reocr_strategy,
                self.reocr_mechanism,
                self.reocr_words_accepted,
                self.reocr_words_rejected,
                self.reocr_delta_before,
                self.reocr_delta_after,
            )
        )

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> "OCRJob":
        """Converts a DynamoDB item to an OCRJob object.

        Args:
            item: The DynamoDB item to convert.

        Returns:
            OCRJob: The OCRJob object.

        Raises:
            ValueError: When the item format is invalid.
        """

        # OCRJob-specific extractors (in addition to common OCR extractors)
        def _extract_reocr_region(
            item_dict: dict[str, Any],
        ) -> dict[str, float] | None:
            if "reocr_region" not in item_dict:
                return None
            raw_region = item_dict["reocr_region"]
            if raw_region.get("NULL"):
                return None
            region_map = raw_region.get("M")
            if not isinstance(region_map, dict):
                raise ValueError("reocr_region must be a map (M) or NULL")
            required = ("x", "y", "width", "height")
            parsed: dict[str, float] = {}
            for key in required:
                value = region_map.get(key, {}).get("N")
                if value is None:
                    raise ValueError(
                        f"reocr_region missing numeric field: {key}"
                    )
                parsed[key] = float(value)
            return parsed

        def _extract_optional_string(field_name: str):
            def _extract(item_dict: dict[str, Any]) -> str | None:
                if field_name not in item_dict:
                    return None
                attribute = item_dict[field_name]
                if attribute == {"NULL": True}:
                    return None
                if (
                    not isinstance(attribute, dict)
                    or set(attribute) != {"S"}
                    or not isinstance(attribute["S"], str)
                ):
                    raise ValueError(
                        f"{field_name} must be a DynamoDB string (S) or NULL"
                    )
                return attribute["S"]

            return _extract

        def _extract_refine_summary(
            item_dict: dict[str, Any],
        ) -> dict[str, float | None] | None:
            if "refine_summary" not in item_dict:
                return None
            raw = item_dict["refine_summary"]
            if raw.get("NULL"):
                return None
            summary_map = raw.get("M")
            if not isinstance(summary_map, dict):
                raise ValueError("refine_summary must be a map (M) or NULL")
            parsed: dict[str, float | None] = {}
            for key in ("subtotal", "tax", "grand_total"):
                attribute = summary_map.get(key)
                if attribute is None or attribute.get("NULL"):
                    parsed[key] = None
                    continue
                value = attribute.get("N")
                if value is None:
                    raise ValueError(
                        f"refine_summary[{key}] must be numeric (N) or NULL"
                    )
                parsed[key] = float(value)
            return parsed

        custom_extractors = {
            **create_ocr_job_extractors(),
            "job_type": EntityFactory.extract_string_field("job_type"),
            "receipt_id": EntityFactory.extract_int_field("receipt_id"),
            "reocr_region": _extract_reocr_region,
            "refine_summary": _extract_refine_summary,
            "refine_merchant_name": _extract_optional_string(
                "refine_merchant_name"
            ),
            "reocr_reason": _extract_optional_string("reocr_reason"),
            "reocr_strategy": _extract_optional_string("reocr_strategy"),
            "reocr_mechanism": _extract_optional_string("reocr_mechanism"),
            "reocr_words_accepted": EntityFactory.extract_int_field(
                "reocr_words_accepted"
            ),
            "reocr_words_rejected": EntityFactory.extract_int_field(
                "reocr_words_rejected"
            ),
            "reocr_delta_before": EntityFactory.extract_float_field(
                "reocr_delta_before"
            ),
            "reocr_delta_after": EntityFactory.extract_float_field(
                "reocr_delta_after"
            ),
        }

        if item.get("TYPE", {}).get("S") != "OCR_JOB":
            raise ValueError("Invalid OCRJob TYPE")
        if not item.get("SK", {}).get("S", "").startswith("OCR_JOB#"):
            raise ValueError("Invalid OCRJob SK format")

        job = EntityFactory.create_entity(
            entity_class=cls,
            item=item,
            required_keys=cls.REQUIRED_KEYS,
            key_parsers={
                "PK": create_image_receipt_pk_parser(),
                "SK": create_ocr_job_sk_parser(),
            },
            custom_extractors=custom_extractors,
        )
        expected = job.to_item()
        # Identity keys must round-trip exactly. GSI *SK* values derive from the
        # immutable job_id, so legacy rows match them too.
        for key in ("PK", "SK", "TYPE", "GSI1SK", "GSI2SK"):
            if item.get(key) != expected.get(key):
                raise ValueError("Invalid OCRJob keys")
        # GSI1PK/GSI2PK encode the mutable status. Legacy OCRJob rows in dev and
        # prod were written at status=PENDING and never had their GSI status
        # partitions rewritten on status transitions, so real rows carry e.g.
        # status=COMPLETED with GSI1PK/GSI2PK=OCR_JOB_STATUS#PENDING (48 of 49
        # Costco rows sampled in dev ReceiptsTable-dc5be22). The status field is
        # authoritative on read, so tolerate a stale-but-well-formed partition
        # here rather than hard-failing a read of pre-existing data; only reject
        # partitions that are not a valid OCR_JOB_STATUS#<status> value.
        valid_status_partitions = {
            f"OCR_JOB_STATUS#{status.value}" for status in OCRStatus
        }
        partitions = []
        for key in ("GSI1PK", "GSI2PK"):
            partition = item.get(key, {})
            if (
                not isinstance(partition, dict)
                or partition.get("S") not in valid_status_partitions
            ):
                raise ValueError("Invalid OCRJob keys")
            partitions.append(partition["S"])
        # GSI1PK and GSI2PK are always written together from the same status, so
        # legitimate legacy rows keep them equal even when both are stale.
        # Divergent partitions indicate corruption, not a benign status drift.
        if partitions[0] != partitions[1]:
            raise ValueError("Invalid OCRJob keys")
        return job


def item_to_ocr_job(item: dict[str, Any]) -> OCRJob:
    """Converts a DynamoDB item to an OCRJob object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        OCRJob: The OCRJob object.

    Raises:
        ValueError: When the item format is invalid.
    """
    return OCRJob.from_item(item)
