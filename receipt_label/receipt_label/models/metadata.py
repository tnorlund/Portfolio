from dataclasses import field
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..version import __version__


class MetadataMixin:
    """
    Mixin class to provide standardized metadata handling for all analysis classes.

    This mixin provides consistent methods for tracking:
    - Processing metrics (time, API calls)
    - Source information (model/version)
    - Creation and modification timestamps
    - Processing history

    It is designed to be used with dataclasses to ensure consistent metadata
    handling across all analysis classes.
    """

    metadata: Dict = field(default_factory=dict)
    version: str = __version__
    timestamp_added: Optional[str] = None
    timestamp_updated: Optional[str] = None

    def initialize_metadata(self) -> None:
        """Initialize metadata with default values if not already set."""
        if self.timestamp_added is None:
            self.timestamp_added = datetime.now().isoformat()

        # Ensure basic metadata structure exists
        if "processing_metrics" not in self.metadata:
            self.metadata["processing_metrics"] = {}

        if "source_info" not in self.metadata:
            self.metadata["source_info"] = {}

        if "processing_history" not in self.metadata:
            self.metadata["processing_history"] = []

            # Add creation event to history
            self.add_history_event("created")

    def add_processing_metric(self, name: str, value: Any) -> None:
        """
        Add a processing metric to the metadata.

        Args:
            name: Name of the metric (e.g., "processing_time_ms", "api_calls")
            value: Value of the metric
        """
        if "processing_metrics" not in self.metadata:
            self.metadata["processing_metrics"] = {}

        self.metadata["processing_metrics"][name] = value

    def set_source_info(
        self, model: str, version: Optional[str] = None, **kwargs: Any
    ) -> None:
        """
        Set information about the source that generated this analysis.

        Args:
            model: The model or algorithm used (e.g., "gpt-4", "custom-labeler")
            version: Version of the model or algorithm
            **kwargs: Additional source information to include
        """
        if "source_info" not in self.metadata:
            self.metadata["source_info"] = {}

        self.metadata["source_info"]["model"] = model

        if version:
            self.metadata["source_info"]["version"] = version

        # Add any additional info
        for key, value in kwargs.items():
            self.metadata["source_info"][key] = value

    def add_history_event(
        self, action: str, details: Optional[Dict[Any, Any]] = None
    ) -> None:
        """
        Add an event to the processing history.

        Args:
            action: The action that occurred (e.g., "created", "updated", "validated")
            details: Optional details about the action
        """
        if "processing_history" not in self.metadata:
            self.metadata["processing_history"] = []

        event = {
            "action": action,
            "timestamp": datetime.now().isoformat(),
            "version": self.version,
        }

        if details:
            event.update(details)

        self.metadata["processing_history"].append(event)

        # Update the timestamp_updated field
        self.timestamp_updated = event["timestamp"]

    def update_version(
        self, new_version: str, reason: Optional[str] = None
    ) -> None:
        """
        Update the version of this analysis.

        Args:
            new_version: The new version number or string
            reason: Optional reason for the version update
        """
        old_version = self.version
        self.version = new_version

        # Add to history
        details = {"previous_version": old_version, "new_version": new_version}

        if reason:
            details["reason"] = reason

        self.add_history_event("version_updated", details)

    def is_newer_than(self, other_version: str) -> bool:
        """
        Check if this analysis is newer than the specified version.

        Args:
            other_version: The version to compare against

        Returns:
            True if this analysis is newer, False otherwise
        """
        try:
            return float(self.version) > float(other_version)
        except ValueError:
            # If versions aren't numeric, do string comparison
            return self.version > other_version

    def get_creation_date(self) -> Optional[datetime]:
        """
        Get the creation date as a datetime object.

        Returns:
            The creation date or None if not available
        """
        if not self.timestamp_added:
            return None

        return datetime.fromisoformat(self.timestamp_added)

    def get_update_date(self) -> Optional[datetime]:
        """
        Get the last update date as a datetime object.

        Returns:
            The update date or None if not available
        """
        if not self.timestamp_updated:
            return None

        return datetime.fromisoformat(self.timestamp_updated)

    def get_elapsed_time(self) -> Optional[float]:
        """
        Get the elapsed time in seconds between creation and last update.

        Returns:
            The elapsed time in seconds or None if not available
        """
        creation = self.get_creation_date()
        update = self.get_update_date()

        if not creation or not update:
            return None

        return (update - creation).total_seconds()

    def to_dict(self) -> Dict:
        """
        Convert the metadata to a dictionary representation.

        Returns:
            A dictionary with all metadata fields
        """
        return {
            "metadata": self.metadata,
            "version": self.version,
            "timestamp_added": self.timestamp_added,
            "timestamp_updated": self.timestamp_updated,
        }

    @staticmethod
    def from_dict(data: Dict) -> Dict:
        """
        Extract metadata fields from a dictionary.

        Args:
            data: The dictionary containing metadata fields

        Returns:
            A dictionary with extracted metadata fields
        """
        return {
            "metadata": data.get("metadata", {}),
            "version": data.get("version", "1.0"),
            "timestamp_added": data.get("timestamp_added"),
            "timestamp_updated": data.get("timestamp_updated"),
        }
