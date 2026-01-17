from dataclasses import dataclass
from datetime import datetime
from typing import Any, Generator

from receipt_dynamo.constants import CoreMLExportStatus
from receipt_dynamo.entities.util import (
    _repr_str,
    assert_valid_uuid,
    normalize_enum,
)


@dataclass(eq=True, unsafe_hash=False)
class CoreMLExportJob:
    """
    Represents a CoreML export job in DynamoDB.

    This entity tracks the export of a trained LayoutLM model to CoreML format.
    Export jobs are created after training completes and processed by a macOS
    worker since coremltools only runs on macOS.

    Attributes:
        export_id: UUID identifying this export job.
        job_id: UUID of the training Job that produced the model.
        model_s3_uri: S3 URI to the model checkpoint to export.
        status: Current status (PENDING, RUNNING, SUCCEEDED, FAILED).
        quantize: Quantization mode (None, "float16", "int8", "int4").
        output_s3_prefix: S3 prefix where exported bundle should be stored.
        created_at: Timestamp when export was queued.
        updated_at: Timestamp of last status update.
        completed_at: Timestamp when export finished (success or failure).
        mlpackage_s3_uri: S3 URI to the exported .mlpackage (after success).
        bundle_s3_uri: S3 URI to the full bundle directory (after success).
        model_size_bytes: Size of the exported model in bytes.
        export_duration_seconds: Time taken to export in seconds.
        error_message: Error message if export failed.
    """

    REQUIRED_KEYS = {
        "PK",
        "SK",
        "TYPE",
        "job_id",
        "model_s3_uri",
        "created_at",
        "status",
    }

    export_id: str
    job_id: str
    model_s3_uri: str
    created_at: datetime
    status: str = CoreMLExportStatus.PENDING.value
    quantize: str | None = None
    output_s3_prefix: str | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None
    mlpackage_s3_uri: str | None = None
    bundle_s3_uri: str | None = None
    model_size_bytes: int | None = None
    export_duration_seconds: float | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        """Validate and normalize initialization arguments."""
        assert_valid_uuid(self.export_id)
        assert_valid_uuid(self.job_id)

        if not isinstance(self.model_s3_uri, str) or not self.model_s3_uri:
            raise ValueError("model_s3_uri must be a non-empty string")

        if not isinstance(self.created_at, datetime):
            raise ValueError("created_at must be a datetime")

        self.status = normalize_enum(self.status, CoreMLExportStatus)

        if self.quantize is not None:
            valid_quantize = [None, "float16", "int8", "int4"]
            if self.quantize not in valid_quantize:
                raise ValueError(f"quantize must be one of {valid_quantize}")

        if self.updated_at is not None and not isinstance(
            self.updated_at, datetime
        ):
            raise ValueError("updated_at must be a datetime or None")

        if self.completed_at is not None and not isinstance(
            self.completed_at, datetime
        ):
            raise ValueError("completed_at must be a datetime or None")

        if self.model_size_bytes is not None and not isinstance(
            self.model_size_bytes, int
        ):
            raise ValueError("model_size_bytes must be an integer or None")

        if self.export_duration_seconds is not None and not isinstance(
            self.export_duration_seconds, (int, float)
        ):
            raise ValueError(
                "export_duration_seconds must be a number or None"
            )

    @property
    def key(self) -> dict[str, Any]:
        """Generate the primary key for the export job."""
        return {
            "PK": {"S": f"COREML_EXPORT#{self.export_id}"},
            "SK": {"S": "EXPORT"},
        }

    def gsi1_key(self) -> dict[str, Any]:
        """Generate GSI1 key for querying by training job ID."""
        return {
            "GSI1PK": {"S": f"JOB#{self.job_id}"},
            "GSI1SK": {"S": f"COREML_EXPORT#{self.export_id}"},
        }

    def gsi2_key(self) -> dict[str, Any]:
        """Generate GSI2 key for querying by status."""
        return {
            "GSI2PK": {"S": f"COREML_EXPORT_STATUS#{self.status}"},
            "GSI2SK": {"S": f"COREML_EXPORT#{self.export_id}"},
        }

    def to_item(self) -> dict[str, Any]:
        """Convert to DynamoDB item format."""
        item = {
            **self.key,
            **self.gsi1_key(),
            **self.gsi2_key(),
            "TYPE": {"S": "COREML_EXPORT"},
            "job_id": {"S": self.job_id},
            "model_s3_uri": {"S": self.model_s3_uri},
            "created_at": {"S": self.created_at.isoformat()},
            "status": {"S": self.status},
        }

        if self.quantize is not None:
            item["quantize"] = {"S": self.quantize}
        else:
            item["quantize"] = {"NULL": True}

        if self.output_s3_prefix is not None:
            item["output_s3_prefix"] = {"S": self.output_s3_prefix}
        else:
            item["output_s3_prefix"] = {"NULL": True}

        if self.updated_at is not None:
            item["updated_at"] = {"S": self.updated_at.isoformat()}
        else:
            item["updated_at"] = {"NULL": True}

        if self.completed_at is not None:
            item["completed_at"] = {"S": self.completed_at.isoformat()}
        else:
            item["completed_at"] = {"NULL": True}

        if self.mlpackage_s3_uri is not None:
            item["mlpackage_s3_uri"] = {"S": self.mlpackage_s3_uri}
        else:
            item["mlpackage_s3_uri"] = {"NULL": True}

        if self.bundle_s3_uri is not None:
            item["bundle_s3_uri"] = {"S": self.bundle_s3_uri}
        else:
            item["bundle_s3_uri"] = {"NULL": True}

        if self.model_size_bytes is not None:
            item["model_size_bytes"] = {"N": str(self.model_size_bytes)}
        else:
            item["model_size_bytes"] = {"NULL": True}

        if self.export_duration_seconds is not None:
            item["export_duration_seconds"] = {
                "N": str(self.export_duration_seconds)
            }
        else:
            item["export_duration_seconds"] = {"NULL": True}

        if self.error_message is not None:
            item["error_message"] = {"S": self.error_message}
        else:
            item["error_message"] = {"NULL": True}

        return item

    def __repr__(self) -> str:
        return (
            "CoreMLExportJob("
            f"export_id={_repr_str(self.export_id)}, "
            f"job_id={_repr_str(self.job_id)}, "
            f"model_s3_uri={_repr_str(self.model_s3_uri)}, "
            f"created_at={self.created_at}, "
            f"status={_repr_str(self.status)}, "
            f"quantize={_repr_str(self.quantize)}, "
            f"output_s3_prefix={_repr_str(self.output_s3_prefix)}, "
            f"updated_at={self.updated_at}, "
            f"completed_at={self.completed_at}, "
            f"mlpackage_s3_uri={_repr_str(self.mlpackage_s3_uri)}, "
            f"bundle_s3_uri={_repr_str(self.bundle_s3_uri)}, "
            f"model_size_bytes={self.model_size_bytes}, "
            f"export_duration_seconds={self.export_duration_seconds}, "
            f"error_message={_repr_str(self.error_message)}"
            ")"
        )

    def __iter__(self) -> Generator[tuple[str, Any], None, None]:
        yield "export_id", self.export_id
        yield "job_id", self.job_id
        yield "model_s3_uri", self.model_s3_uri
        yield "created_at", self.created_at
        yield "status", self.status
        yield "quantize", self.quantize
        yield "output_s3_prefix", self.output_s3_prefix
        yield "updated_at", self.updated_at
        yield "completed_at", self.completed_at
        yield "mlpackage_s3_uri", self.mlpackage_s3_uri
        yield "bundle_s3_uri", self.bundle_s3_uri
        yield "model_size_bytes", self.model_size_bytes
        yield "export_duration_seconds", self.export_duration_seconds
        yield "error_message", self.error_message

    def __eq__(self, other) -> bool:
        if not isinstance(other, CoreMLExportJob):
            return False
        return (
            self.export_id == other.export_id
            and self.job_id == other.job_id
            and self.model_s3_uri == other.model_s3_uri
            and self.created_at == other.created_at
            and self.status == other.status
            and self.quantize == other.quantize
            and self.output_s3_prefix == other.output_s3_prefix
            and self.updated_at == other.updated_at
            and self.completed_at == other.completed_at
            and self.mlpackage_s3_uri == other.mlpackage_s3_uri
            and self.bundle_s3_uri == other.bundle_s3_uri
            and self.model_size_bytes == other.model_size_bytes
            and self.export_duration_seconds == other.export_duration_seconds
            and self.error_message == other.error_message
        )

    def __hash__(self) -> int:
        return hash(
            (
                self.export_id,
                self.job_id,
                self.model_s3_uri,
                self.created_at,
                self.status,
                self.quantize,
                self.output_s3_prefix,
                self.updated_at,
                self.completed_at,
                self.mlpackage_s3_uri,
                self.bundle_s3_uri,
                self.model_size_bytes,
                self.export_duration_seconds,
                self.error_message,
            )
        )

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> "CoreMLExportJob":
        """Converts a DynamoDB item to a CoreMLExportJob object.

        Args:
            item: The DynamoDB item to convert.

        Returns:
            CoreMLExportJob: The CoreMLExportJob object.

        Raises:
            ValueError: When the item format is invalid.
        """
        if not cls.REQUIRED_KEYS.issubset(item.keys()):
            missing_keys = cls.REQUIRED_KEYS - item.keys()
            additional_keys = item.keys() - cls.REQUIRED_KEYS
            raise ValueError(
                f"Invalid item format\nmissing keys: {missing_keys}"
                f"\nadditional keys: {additional_keys}"
            )
        try:
            export_id = item["PK"]["S"].split("#")[1]
            job_id = item["job_id"]["S"]
            model_s3_uri = item["model_s3_uri"]["S"]
            created_at = datetime.fromisoformat(item["created_at"]["S"])
            status = item["status"]["S"]

            quantize = (
                item["quantize"]["S"]
                if "quantize" in item and "S" in item["quantize"]
                else None
            )

            output_s3_prefix = (
                item["output_s3_prefix"]["S"]
                if "output_s3_prefix" in item
                and "S" in item["output_s3_prefix"]
                else None
            )

            updated_at = (
                datetime.fromisoformat(item["updated_at"]["S"])
                if "updated_at" in item and "S" in item["updated_at"]
                else None
            )

            completed_at = (
                datetime.fromisoformat(item["completed_at"]["S"])
                if "completed_at" in item and "S" in item["completed_at"]
                else None
            )

            mlpackage_s3_uri = (
                item["mlpackage_s3_uri"]["S"]
                if "mlpackage_s3_uri" in item
                and "S" in item["mlpackage_s3_uri"]
                else None
            )

            bundle_s3_uri = (
                item["bundle_s3_uri"]["S"]
                if "bundle_s3_uri" in item and "S" in item["bundle_s3_uri"]
                else None
            )

            model_size_bytes = (
                int(item["model_size_bytes"]["N"])
                if "model_size_bytes" in item
                and "N" in item["model_size_bytes"]
                else None
            )

            export_duration_seconds = (
                float(item["export_duration_seconds"]["N"])
                if "export_duration_seconds" in item
                and "N" in item["export_duration_seconds"]
                else None
            )

            error_message = (
                item["error_message"]["S"]
                if "error_message" in item and "S" in item["error_message"]
                else None
            )

            return cls(
                export_id=export_id,
                job_id=job_id,
                model_s3_uri=model_s3_uri,
                created_at=created_at,
                status=status,
                quantize=quantize,
                output_s3_prefix=output_s3_prefix,
                updated_at=updated_at,
                completed_at=completed_at,
                mlpackage_s3_uri=mlpackage_s3_uri,
                bundle_s3_uri=bundle_s3_uri,
                model_size_bytes=model_size_bytes,
                export_duration_seconds=export_duration_seconds,
                error_message=error_message,
            )
        except Exception as e:
            raise ValueError(
                f"Error converting item to CoreMLExportJob: {e}"
            ) from e


def item_to_coreml_export_job(item: dict[str, Any]) -> CoreMLExportJob:
    """Converts a DynamoDB item to a CoreMLExportJob object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        CoreMLExportJob: The CoreMLExportJob object.

    Raises:
        ValueError: When the item format is invalid.
    """
    return CoreMLExportJob.from_item(item)
