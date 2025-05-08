"""
Job definition models for configuring LayoutLM training jobs.

This module provides Pydantic models for defining, validating, and managing
job configurations for training LayoutLM models on receipt data.
"""

import os
import yaml
import json
from typing import List, Dict, Optional, Union, Literal
from datetime import datetime
from pydantic import (
    BaseModel,
    Field,
    validator,
    root_validator,
    model_validator,
)


class ResourceConfig(BaseModel):
    """Resource requirements for training jobs."""

    instance_type: str = "p3.2xlarge"
    min_gpu_count: int = Field(1, ge=1)
    max_gpu_count: int = Field(1, ge=1)
    spot_instance: bool = True
    max_runtime: int = Field(86400, ge=1)  # seconds

    @validator("max_gpu_count")
    def max_gpu_must_be_gte_min(cls, v, values):
        """Validate that max_gpu_count >= min_gpu_count."""
        if "min_gpu_count" in values and v < values["min_gpu_count"]:
            raise ValueError("max_gpu_count must be >= min_gpu_count")
        return v


class ModelConfig(BaseModel):
    """LayoutLM model configuration."""

    type: Literal["layoutlm"] = "layoutlm"
    version: str = "v2"
    pretrained_model_name: str = "microsoft/layoutlm-base-uncased"
    max_seq_length: int = Field(512, ge=1)
    doc_stride: int = Field(128, ge=1)


class EarlyStoppingConfig(BaseModel):
    """Configuration for early stopping during training."""

    enabled: bool = True
    patience: int = Field(3, ge=1)
    metric: str = "val_f1"
    mode: Literal["max", "min"] = "max"


class TrainingConfig(BaseModel):
    """Training hyperparameters and configuration."""

    epochs: int = Field(..., ge=1)
    batch_size: int = Field(..., ge=1)
    gradient_accumulation_steps: int = Field(1, ge=1)
    learning_rate: float = Field(..., gt=0)
    weight_decay: float = Field(0.01, ge=0)
    warmup_ratio: float = Field(0.1, ge=0, le=1)
    early_stopping: EarlyStoppingConfig = EarlyStoppingConfig()
    mixed_precision: bool = True

    @validator("epochs")
    def reasonable_epochs(cls, v):
        """Check that epochs is reasonable."""
        if v > 100:
            raise ValueError("epochs > 100 seems unusually high")
        return v

    @validator("learning_rate")
    def reasonable_learning_rate(cls, v):
        """Check that learning_rate is reasonable."""
        if v > 1.0:
            raise ValueError("learning_rate > 1.0 seems unusually high")
        return v


class DataSourceConfig(BaseModel):
    """Configuration for a data source (DynamoDB, S3, etc.)."""

    type: str
    table: Optional[str] = None
    path: Optional[str] = None
    query_params: Optional[Dict] = None

    @model_validator(mode="after")
    def check_required_fields(self) -> "DataSourceConfig":
        """Check that the required fields for each data source type are present."""
        source_type = self.type
        if source_type == "dynamodb" and not self.table:
            raise ValueError(
                "table must be specified for dynamodb data source"
            )
        elif source_type == "sroie" and not self.path:
            raise ValueError("path must be specified for sroie data source")
        return self


class DataAugmentationConfig(BaseModel):
    """Configuration for data augmentation."""

    enabled: bool = False
    methods: List[str] = []
    probabilities: Optional[Dict[str, float]] = None


class DatasetConfig(BaseModel):
    """Configuration for training, validation and test datasets."""

    train_data: List[DataSourceConfig]
    validation_data: List[DataSourceConfig]
    test_data: Optional[List[DataSourceConfig]] = None
    data_augmentation: Optional[DataAugmentationConfig] = (
        DataAugmentationConfig()
    )


class CheckpointConfig(BaseModel):
    """Configuration for model checkpointing."""

    save_strategy: Literal["epoch", "steps"] = "epoch"
    save_steps: Optional[int] = None
    save_total_limit: int = Field(5, ge=1)
    metrics_to_track: List[str] = ["loss", "f1", "precision", "recall"]

    @validator("save_steps")
    def validate_save_steps(cls, v, values):
        """Validate that save_steps is provided if strategy is steps."""
        if values.get("save_strategy") == "steps" and v is None:
            raise ValueError(
                "save_steps must be specified when save_strategy is 'steps'"
            )
        return v


class OutputConfig(BaseModel):
    """Configuration for model output."""

    base_s3_path: str
    save_model: bool = True
    save_optimizer_state: bool = True

    @validator("base_s3_path")
    def validate_s3_path(cls, v):
        """Validate that base_s3_path is a valid S3 path."""
        if not v.startswith("s3://"):
            raise ValueError("base_s3_path must start with 's3://'")
        return v


class NotificationConfig(BaseModel):
    """Configuration for job notifications."""

    on_completion: List[str] = ["email"]
    on_failure: List[str] = ["email"]
    recipients: Dict[str, List[str]]


class JobDependency(BaseModel):
    """Dependency configuration for jobs."""

    job_id: str
    type: str
    condition: Optional[str] = None


class LayoutLMJobDefinition(BaseModel):
    """Complete job definition for LayoutLM training."""

    name: str
    description: str
    priority: int = Field(5, ge=1, le=10)
    estimated_duration: int = Field(..., ge=1)  # seconds
    tags: List[str] = []
    resources: ResourceConfig = ResourceConfig()
    model: ModelConfig = ModelConfig()
    training: TrainingConfig
    dataset: DatasetConfig
    checkpoints: CheckpointConfig = CheckpointConfig()
    output: OutputConfig
    notifications: Optional[NotificationConfig] = None
    dependencies: List[JobDependency] = []
    created_at: datetime = Field(default_factory=datetime.now)
    created_by: Optional[str] = None

    @classmethod
    def from_yaml(cls, file_path: str) -> "LayoutLMJobDefinition":
        """Load a job definition from a YAML file."""
        with open(file_path, "r") as f:
            data = yaml.safe_load(f)

        # Handle nested structure if job is the top-level key
        if "job" in data:
            data = data["job"]

        return cls(**data)

    @classmethod
    def from_json(cls, file_path: str) -> "LayoutLMJobDefinition":
        """Load a job definition from a JSON file."""
        with open(file_path, "r") as f:
            data = json.load(f)

        # Handle nested structure if job is the top-level key
        if "job" in data:
            data = data["job"]

        return cls(**data)

    def to_yaml(self, file_path: str) -> None:
        """Save the job definition to a YAML file."""
        data = {"job": json.loads(self.json())}
        with open(file_path, "w") as f:
            yaml.dump(data, f, default_flow_style=False)

    def to_json(self, file_path: str) -> None:
        """Save the job definition to a JSON file."""
        data = {"job": json.loads(self.json())}
        with open(file_path, "w") as f:
            json.dump(data, f, indent=2)

    def to_job_config(self) -> Dict:
        """Convert the job definition to a format compatible with the Job class."""
        return json.loads(self.json())


class JobDefinition:
    """
    Represents a job definition loaded from a YAML/JSON file.

    This class encapsulates the configuration for a training job,
    including model parameters, training settings, and dependencies.
    """

    def __init__(self, config_dict):
        """
        Initialize a JobDefinition from a configuration dictionary.

        Args:
            config_dict: Dictionary containing job configuration
        """
        self.config_dict = config_dict

        # Required fields
        self.name = config_dict.get("name")
        self.description = config_dict.get("description", "")
        self.config = config_dict.get("config", {})

        # Optional metadata
        self.priority = config_dict.get("priority", "medium").lower()
        self.estimated_duration = config_dict.get("estimated_duration")
        self.tags = config_dict.get("tags", {})

        # Dependencies - new field
        self.dependencies = self._parse_dependencies(
            config_dict.get("dependencies", [])
        )

        # Validate required fields
        if not self.name:
            raise ValueError("Job definition must include a name")

        if not self.config:
            raise ValueError("Job definition must include a config section")

        # Validate priority
        valid_priorities = ["low", "medium", "high", "critical"]
        if self.priority not in valid_priorities:
            raise ValueError(
                f"Priority must be one of: {', '.join(valid_priorities)}"
            )

    def _parse_dependencies(self, dependencies_list):
        """
        Parse job dependencies from the configuration.

        Args:
            dependencies_list: List of dependency configurations

        Returns:
            List of parsed dependency dictionaries
        """
        parsed_dependencies = []

        for dep in dependencies_list:
            if not isinstance(dep, dict):
                raise ValueError(
                    f"Each dependency must be a dictionary, got {type(dep)}"
                )

            job_id = dep.get("job_id")
            if not job_id:
                raise ValueError("Each dependency must include a job_id")

            dependency_type = dep.get("type", "COMPLETION").upper()
            valid_types = ["COMPLETION", "SUCCESS", "FAILURE", "ARTIFACT"]
            if dependency_type not in valid_types:
                raise ValueError(
                    f"Dependency type must be one of: {', '.join(valid_types)}"
                )

            condition = dep.get("condition")
            if dependency_type == "ARTIFACT" and not condition:
                raise ValueError(
                    "ARTIFACT dependency type requires a condition"
                )

            parsed_dependencies.append(
                {
                    "job_id": job_id,
                    "type": dependency_type,
                    "condition": condition,
                    "description": dep.get("description", ""),
                }
            )

        return parsed_dependencies

    def to_dict(self):
        """
        Convert the job definition to a dictionary.

        Returns:
            Dictionary representation of the job definition
        """
        return {
            "name": self.name,
            "description": self.description,
            "priority": self.priority,
            "estimated_duration": self.estimated_duration,
            "tags": self.tags,
            "dependencies": self.dependencies,
            "config": self.config,
        }

    def create_dependencies(self, job_id, job_service):
        """
        Create job dependencies in DynamoDB.

        Args:
            job_id: ID of the job being created
            job_service: JobService instance

        Returns:
            List of created dependency objects
        """
        from receipt_trainer.jobs.validator import validate_job_dependency
        import logging

        logger = logging.getLogger(__name__)
        created_dependencies = []

        for dep in self.dependencies:
            dependency_job_id = dep["job_id"]
            dependency_type = dep["type"]
            condition = dep["condition"]

            # Validate the dependency
            is_valid, error = validate_job_dependency(
                dependent_job_id=job_id,
                dependency_job_id=dependency_job_id,
                dependency_type=dependency_type,
                condition=condition,
                job_service=job_service,
            )

            if not is_valid:
                logger.warning(f"Skipping invalid dependency: {error}")
                continue

            try:
                # Create the dependency
                dependency = job_service.add_job_dependency(
                    job_id=job_id,
                    depends_on_job_id=dependency_job_id,
                    dependency_type=dependency_type,
                    condition=condition,
                )
                created_dependencies.append(dependency)
                logger.info(
                    f"Created dependency: {job_id} depends on {dependency_job_id} (type: {dependency_type})"
                )
            except Exception as e:
                logger.error(f"Error creating dependency: {str(e)}")

        return created_dependencies
