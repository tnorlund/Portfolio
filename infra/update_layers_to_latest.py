#!/usr/bin/env python3
"""
update_layers_to_latest.py

Automatically update Pulumi state to point to the latest Lambda layer versions in AWS.

This script:
1. Queries AWS for the latest version of each layer
2. Exports current Pulumi state
3. Updates layer ARNs to point to latest versions
4. Imports updated state back to Pulumi

Usage:
    python update_layers_to_latest.py --stack dev [--dry-run] [--layers receipt-dynamo-dev,receipt-label-dev]
"""

import argparse
import copy
import json
import logging
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import boto3
from botocore.exceptions import ClientError, NoCredentialsError

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class LayerVersionUpdater:
    """Update Lambda layer versions in Pulumi state to latest AWS versions."""

    def __init__(
        self, stack_name: str, layer_names: Optional[List[str]] = None
    ):
        """
        Initialize the updater.

        Args:
            stack_name: Pulumi stack name (e.g., "dev")
            layer_names: Optional list of specific layer names to update.
                        If None, updates all layers found in state.
        """
        if not re.match(r"^[a-zA-Z0-9_-]+$", stack_name):
            raise ValueError(
                f"Invalid stack name: {stack_name}. "
                "Must contain only alphanumeric characters, hyphens, and underscores."
            )

        self.stack_name = stack_name
        self.layer_names = layer_names
        self.backup_dir = Path("backups")
        self.backup_dir.mkdir(exist_ok=True)

        # Initialize AWS client
        try:
            self.lambda_client = boto3.client("lambda")
            self.aws_account_id = boto3.client("sts").get_caller_identity()[
                "Account"
            ]
            self.aws_region = self.lambda_client.meta.region_name
            logger.info(
                f"Connected to AWS (Account: {self.aws_account_id}, Region: {self.aws_region})"
            )
        except (NoCredentialsError, ClientError) as e:
            logger.error(f"AWS credentials not configured: {e}")
            sys.exit(1)

    def get_latest_layer_version(self, layer_name: str) -> Optional[int]:
        """
        Get the latest version number for a Lambda layer.

        Args:
            layer_name: Name of the layer (e.g., "receipt-dynamo-dev")

        Returns:
            Latest version number, or None if layer doesn't exist
        """
        try:
            response = self.lambda_client.list_layer_versions(
                LayerName=layer_name, MaxItems=1
            )
            versions = response.get("LayerVersions", [])
            if versions:
                latest_version = versions[0]["Version"]
                logger.info(
                    f"Found latest version {latest_version} for layer {layer_name}"
                )
                return latest_version
            else:
                logger.warning(f"No versions found for layer {layer_name}")
                return None
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                logger.warning(f"Layer {layer_name} not found in AWS")
            else:
                logger.error(f"Error querying layer {layer_name}: {e}")
            return None

    def find_all_layers_in_state(
        self, state: Dict[str, Any]
    ) -> Dict[str, int]:
        """
        Find all Lambda layer ARNs in Pulumi state and extract their current versions.

        Returns:
            Dictionary mapping layer base name to current version
        """
        layers_found = {}

        # Handle different Pulumi state formats
        resources = (
            state.get("deployment", {}).get("resources")
            or state.get("checkpoint", {})
            .get("latest", {})
            .get("resources", [])
            or state.get("resources", [])
            or []
        )

        for resource in resources:
            outputs = resource.get("outputs", {})
            layers = outputs.get("layers", [])

            if not layers:
                continue

            for layer_arn in layers:
                if not isinstance(layer_arn, str):
                    continue

                # Parse layer ARN: arn:aws:lambda:region:account:layer:layer-name:version
                match = re.match(
                    r"arn:aws:lambda:[^:]+:\d+:layer:([^:]+):(\d+)$", layer_arn
                )
                if match:
                    layer_name = match.group(1)
                    version = int(match.group(2))

                    # Store the highest version we've seen for this layer
                    if (
                        layer_name not in layers_found
                        or version > layers_found[layer_name]
                    ):
                        layers_found[layer_name] = version

        return layers_found

    def export_pulumi_state(self) -> Dict[str, Any]:
        """Export current Pulumi state to a dictionary."""
        logger.info(f"Exporting Pulumi state for stack {self.stack_name}...")
        try:
            result = subprocess.run(
                ["pulumi", "stack", "export", "--stack", self.stack_name],
                capture_output=True,
                text=True,
                check=True,
            )
            state = json.loads(result.stdout)
            logger.info("State exported successfully")
            return state
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to export Pulumi state: {e.stderr}")
            sys.exit(1)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Pulumi state JSON: {e}")
            sys.exit(1)

    def backup_state(self, state: Dict[str, Any]) -> Path:
        """Create a backup of the current state."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = (
            self.backup_dir / f"state_{self.stack_name}_{timestamp}.json"
        )

        with open(backup_path, "w") as f:
            json.dump(state, f, indent=2)

        logger.info(f"State backed up to {backup_path}")
        return backup_path

    def update_layer_versions_in_state(
        self, state: Dict[str, Any], dry_run: bool = True
    ) -> Tuple[Dict[str, Any], List[str]]:
        """
        Update layer versions in Pulumi state to latest AWS versions.

        Returns:
            Tuple of (updated_state, list_of_changes)
        """
        updated_state = copy.deepcopy(state)
        changes = []

        # Find all layers currently in state
        layers_in_state = self.find_all_layers_in_state(state)
        logger.info(f"Found {len(layers_in_state)} unique layers in state")

        # Get latest versions from AWS
        layer_updates = {}
        for layer_name, current_version in layers_in_state.items():
            # Filter by layer_names if specified
            if self.layer_names:
                # Check if this layer matches any of the requested names
                matches = False
                for requested_name in self.layer_names:
                    if layer_name == requested_name or layer_name.endswith(
                        f"-{requested_name}"
                    ):
                        matches = True
                        break
                if not matches:
                    continue

            latest_version = self.get_latest_layer_version(layer_name)
            if latest_version and latest_version != current_version:
                layer_updates[layer_name] = {
                    "current": current_version,
                    "latest": latest_version,
                }
                changes.append(
                    f"Layer {layer_name}: version {current_version} -> {latest_version}"
                )

        if not layer_updates:
            logger.info("No layer version updates needed")
            return updated_state, changes

        logger.info(f"Updating {len(layer_updates)} layers...")

        # Update layer ARNs in state
        # Handle different Pulumi state formats
        resources = (
            updated_state.get("deployment", {}).get("resources")
            or updated_state.get("checkpoint", {})
            .get("latest", {})
            .get("resources", [])
            or updated_state.get("resources", [])
            or []
        )

        for resource in resources:
            outputs = resource.get("outputs", {})
            layers = outputs.get("layers", [])

            if not layers:
                continue

            updated_layers = []
            for layer_arn in layers:
                if not isinstance(layer_arn, str):
                    updated_layers.append(layer_arn)
                    continue

                # Parse layer ARN
                match = re.match(
                    r"(arn:aws:lambda:[^:]+:\d+:layer:)([^:]+)(:)(\d+)$",
                    layer_arn,
                )
                if match:
                    arn_prefix = match.group(1)
                    layer_name = match.group(2)
                    colon = match.group(3)
                    version = int(match.group(4))

                    # Check if this layer needs updating
                    if layer_name in layer_updates:
                        new_version = layer_updates[layer_name]["latest"]
                        new_arn = (
                            f"{arn_prefix}{layer_name}{colon}{new_version}"
                        )
                        updated_layers.append(new_arn)
                        logger.debug(f"Updated {layer_arn} -> {new_arn}")
                    else:
                        updated_layers.append(layer_arn)
                else:
                    updated_layers.append(layer_arn)

            # Update the resource outputs
            if updated_layers != layers:
                outputs["layers"] = updated_layers

        return updated_state, changes

    def import_pulumi_state(self, state: Dict[str, Any]) -> None:
        """Import updated state back to Pulumi."""
        logger.info("Importing updated state to Pulumi...")
        try:
            state_json = json.dumps(state)
            result = subprocess.run(
                ["pulumi", "stack", "import", "--stack", self.stack_name],
                input=state_json,
                text=True,
                capture_output=True,
                check=True,
            )
            logger.info("State imported successfully")
            logger.info(result.stdout)
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to import Pulumi state: {e.stderr}")
            logger.error(f"Stdout: {e.stdout}")
            sys.exit(1)

    def run(self, dry_run: bool = True) -> None:
        """Run the update process."""
        logger.info(
            f"Starting layer version update for stack '{self.stack_name}'"
        )
        if dry_run:
            logger.info("DRY RUN MODE - no changes will be made")

        # Export state
        state = self.export_pulumi_state()

        # Backup state
        backup_path = self.backup_state(state)

        # Update layer versions
        updated_state, changes = self.update_layer_versions_in_state(
            state, dry_run
        )

        if not changes:
            logger.info(
                "No changes needed - all layers are already at latest version"
            )
            return

        # Show changes
        logger.info("\n" + "=" * 60)
        logger.info("CHANGES TO BE MADE:")
        logger.info("=" * 60)
        for change in changes:
            logger.info(f"  • {change}")
        logger.info("=" * 60 + "\n")

        if dry_run:
            logger.info("DRY RUN - State would be updated but no changes made")
            logger.info(f"Backup saved to {backup_path}")
            return

        # Confirm before proceeding
        response = input(
            f"\nUpdate Pulumi state for stack '{self.stack_name}'? (yes/no): "
        )
        if response.lower() not in ["yes", "y"]:
            logger.info("Update cancelled by user")
            return

        # Import updated state
        self.import_pulumi_state(updated_state)

        logger.info("\n✅ Layer versions updated successfully!")
        logger.info(f"Backup saved to {backup_path}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Update Pulumi state to use latest Lambda layer versions"
    )
    parser.add_argument(
        "--stack", required=True, help="Pulumi stack name (e.g., 'dev')"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without making changes",
    )
    parser.add_argument(
        "--layers",
        help="Comma-separated list of specific layer names to update (e.g., 'receipt-dynamo-dev,receipt-label-dev'). "
        "If not specified, updates all layers found in state.",
    )

    args = parser.parse_args()

    layer_names = None
    if args.layers:
        layer_names = [name.strip() for name in args.layers.split(",")]

    updater = LayerVersionUpdater(args.stack, layer_names)
    updater.run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
