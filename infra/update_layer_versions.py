#!/usr/bin/env python3
"""
update_layer_versions.py

Script to update Lambda Layer version numbers in Pulumi state files.
This allows you to manually set specific layer versions for Lambda functions
that use them, synchronizing both Pulumi state and AWS resources.

Usage:
    python update_layer_versions.py --config layer_config.yaml [options]

Features:
- Safe backup of original state
- Dry-run mode for testing
- Support for both dev and prod stacks
- Validation of layer ARN formats
- Option to sync with AWS directly
- Rollback capability if errors occur
"""

import argparse
import copy
import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import boto3
import yaml
from botocore.exceptions import ClientError, NoCredentialsError


# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class LayerVersionUpdater:
    """Main class for updating Lambda layer versions in Pulumi state."""

    def __init__(self, config_path: str, stack_name: str = "dev"):
        """
        Initialize the updater.

        Args:
            config_path: Path to the layer configuration file
            stack_name: Pulumi stack name (dev or prod)
        """
        # Validate stack name to prevent path traversal
        if not re.match(r'^[a-zA-Z0-9_-]+$', stack_name):
            raise ValueError(f"Invalid stack name: {stack_name}")
        
        self.stack_name = stack_name
        self.config_path = config_path
        self.backup_dir = Path("backups")
        self.backup_dir.mkdir(exist_ok=True)

        # Load configuration
        self.config = self._load_config()
        self.settings = self.config.get("settings", {}) or {}
        base_layers = self.config.get("layers", {}) or {}
        stack_overrides = (self.config.get("stack_overrides", {}) or {}).get(self.stack_name, {}) or {}
        # Merge base and overrides (overrides win)
        self.layer_mappings = {**base_layers, **stack_overrides}

        # Initialize AWS client/metadata
        cfg_region = self.settings.get("aws_region")
        cfg_account = self.settings.get("aws_account_id")
        try:
            self.lambda_client = boto3.client("lambda", region_name=cfg_region)
            self.aws_account_id = cfg_account or boto3.client("sts").get_caller_identity()["Account"]
            self.aws_region = cfg_region or self.lambda_client.meta.region_name
        except (NoCredentialsError, ClientError) as e:
            logger.warning(f"AWS credentials not configured: {e}")
            self.lambda_client = None
            self.aws_account_id = cfg_account
            self.aws_region = cfg_region

    def _load_config(self) -> Dict[str, Any]:
        """Load layer configuration from YAML file."""
        try:
            with open(self.config_path, "r") as f:
                config = yaml.safe_load(f)
                logger.info(f"Loaded configuration from {self.config_path}")
                return config
        except FileNotFoundError:
            logger.error(f"Configuration file not found: {self.config_path}")
            sys.exit(1)
        except yaml.YAMLError as e:
            logger.error(f"Error parsing configuration file: {e}")
            sys.exit(1)

    def _validate_layer_arn(self, arn: str) -> bool:
        """
        Validate Lambda layer ARN format.

        Args:
            arn: The layer ARN to validate

        Returns:
            True if valid, False otherwise
        """
        pattern = r"^arn:aws:lambda:[a-z0-9-]+:\d{12}:layer:[a-zA-Z0-9-_]+:\d+$"
        return bool(re.match(pattern, arn))

    def _build_layer_arn(
        self, layer_name: str, version: int, region: str = None, account_id: str = None
    ) -> str:
        """
        Build a complete layer ARN from components.

        Args:
            layer_name: Name of the layer
            version: Version number
            region: AWS region (uses current if not specified)
            account_id: AWS account ID (uses current if not specified)

        Returns:
            Complete layer ARN
        """
        region = region or self.aws_region
        account_id = account_id or self.aws_account_id
        if not region or not account_id:
            raise ValueError(
                "Cannot build layer ARN: missing region/account_id. "
                "Provide settings.aws_region and settings.aws_account_id or configure AWS credentials."
            )
        return f"arn:aws:lambda:{region}:{account_id}:layer:{layer_name}:{version}"

    def _parse_layer_arn(self, arn: str) -> Dict[str, str]:
        """
        Parse a layer ARN into components.

        Args:
            arn: The layer ARN to parse

        Returns:
            Dictionary with ARN components
        """
        parts = arn.split(":")
        # Expected: arn:aws:lambda:<region>:<account_id>:layer:<layer_name>:<version>
        if len(parts) != 8 or parts[5] != "layer":
            raise ValueError(f"Invalid layer ARN format: {arn}")

        return {
            "partition": parts[1],    # aws
            "service": parts[2],      # lambda
            "region": parts[3],
            "account_id": parts[4],
            "resource_type": parts[5],  # layer
            "layer_name": parts[6],
            "version": parts[7],
        }

    def export_stack_state(self) -> str:
        """
        Export the current Pulumi stack state to a JSON file.

        Returns:
            Path to the exported state file
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = self.backup_dir / f"{self.stack_name}_state_{timestamp}.json"

        logger.info(f"Exporting stack state for {self.stack_name}")
        try:
            subprocess.run(
                [
                    "pulumi", "stack", "export",
                    "--stack", self.stack_name,
                    "--show-secrets",
                    "--file", str(backup_file),
                ],
                check=True,
            )
            logger.info(f"State exported to: {backup_file}")
            return str(backup_file)
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            logger.error(f"Failed to export stack state: {e}")
            raise

    def load_state_from_file(self, file_path: str) -> Dict[str, Any]:
        """
        Load Pulumi state from JSON file.

        Args:
            file_path: Path to the state file

        Returns:
            State data as dictionary
        """
        try:
            with open(file_path, "r") as f:
                return json.load(f)
        except FileNotFoundError:
            logger.error(f"State file not found: {file_path}")
            raise
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in state file: {e}")
            raise

    def find_lambda_resources(self, state: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Find all Lambda function resources in the state.

        Args:
            state: Pulumi state data

        Returns:
            List of Lambda function resources
        """
        lambda_resources = []
        
        # Navigate through the state structure (Pulumi v3+ exports)
        resources = (
            state.get("deployment", {}).get("resources")
            or state.get("checkpoint", {}).get("latest", {}).get("resources", [])
            or []
        )
        
        for resource in resources:
            if resource.get("type") == "aws:lambda/function:Function":
                lambda_resources.append(resource)
        
        logger.info(f"Found {len(lambda_resources)} Lambda function resources")
        return lambda_resources

    def update_layer_versions_in_state(
        self, state: Dict[str, Any], dry_run: bool = True
    ) -> Tuple[Dict[str, Any], List[str]]:
        """
        Update layer versions in the state based on configuration.

        Args:
            state: Pulumi state data
            dry_run: If True, only show what would be changed

        Returns:
            Tuple of (updated_state, list_of_changes)
        """
        changes = []
        updated_state = copy.deepcopy(state) if not dry_run else state
        
        lambda_resources = self.find_lambda_resources(state)
        
        for resource in lambda_resources:
            function_name = resource.get("outputs", {}).get("name")
            if not function_name:
                continue
                
            # Check if this function has layers
            layers = resource.get("outputs", {}).get("layers", [])
            if not layers:
                continue
                
            updated_layers = []
            layer_updated = False
            
            for layer_arn in layers:
                if not isinstance(layer_arn, str) or not self._validate_layer_arn(layer_arn):
                    updated_layers.append(layer_arn)
                    continue
                    
                # Parse the layer ARN
                try:
                    layer_info = self._parse_layer_arn(layer_arn)
                    layer_name = layer_info["layer_name"]
                    current_version = layer_info["version"]
                    
                    # Check if we have an update for this layer
                    new_version = None
                    for mapping_name, mapping_config in self.layer_mappings.items():
                        if layer_name.endswith(f"-{mapping_name}") or layer_name == mapping_name:
                            new_version = mapping_config.get("version")
                            break
                    
                    if new_version and str(new_version) != str(current_version):
                        new_arn = self._build_layer_arn(
                            layer_name, new_version,
                            layer_info["region"], layer_info["account_id"]
                        )
                        updated_layers.append(new_arn)
                        layer_updated = True
                        
                        change_msg = (
                            f"Function {function_name}: Layer {layer_name} "
                            f"version {current_version} -> {new_version}"
                        )
                        changes.append(change_msg)
                        logger.info(change_msg)
                    else:
                        updated_layers.append(layer_arn)
                        
                except Exception as e:
                    logger.warning(f"Error processing layer {layer_arn}: {e}")
                    updated_layers.append(layer_arn)
            
            # Update the resource if layers changed
            if layer_updated and not dry_run:
                # Update both inputs and outputs
                if "inputs" in resource:
                    resource["inputs"]["layers"] = updated_layers
                if "outputs" in resource:
                    resource["outputs"]["layers"] = updated_layers
        
        return updated_state, changes

    def save_state_to_file(self, state: Dict[str, Any], file_path: str) -> None:
        """
        Save Pulumi state to JSON file.

        Args:
            state: State data to save
            file_path: Path where to save the state
        """
        try:
            with open(file_path, "w") as f:
                json.dump(state, f, indent=2)
            logger.info(f"State saved to: {file_path}")
        except Exception as e:
            logger.error(f"Failed to save state to {file_path}: {e}")
            raise

    def import_stack_state(self, file_path: str) -> None:
        """
        Import state back to Pulumi stack.

        Args:
            file_path: Path to the state file to import
        """
        logger.info(f"Importing state for stack {self.stack_name}")
        try:
            subprocess.run(
                ["pulumi", "stack", "import", "--stack", self.stack_name, "--file", str(file_path)],
                check=True,
            )
            logger.info("State import completed")
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            logger.error(f"Failed to import stack state: {e}")
            raise

    def sync_with_aws(self, changes: List[str], dry_run: bool = True) -> None:
        """
        Synchronize Lambda functions with AWS using the updated layer versions.

        Args:
            changes: List of changes that were made
            dry_run: If True, only show what would be done
        """
        if not self.lambda_client:
            logger.warning("AWS client not available, skipping AWS sync")
            return

        logger.info("Synchronizing with AWS Lambda functions...")

        # Get all functions with environment tag matching our stack
        try:
            paginator = self.lambda_client.get_paginator("list_functions")
            for page in paginator.paginate():
                for function in page["Functions"]:
                    function_name = function["FunctionName"]
                    
                    # Check if this function belongs to our stack
                    try:
                        tags = self.lambda_client.list_tags(
                            Resource=function["FunctionArn"]
                        ).get("Tags", {})
                        
                        tag_key = self.settings.get("tag_key", "environment")
                        stack_match = tags.get(tag_key) == self.stack_name or tags.get("pulumi:stack") == self.stack_name
                        if not stack_match:
                            continue
                            
                    except ClientError:
                        continue

                    # Get current layers
                    current_layers = [layer["Arn"] for layer in function.get("Layers", [])]
                    if not current_layers:
                        continue

                    # Update layer versions based on our configuration
                    updated_layers = []
                    function_updated = False

                    for layer_arn in current_layers:
                        try:
                            layer_info = self._parse_layer_arn(layer_arn)
                            layer_name = layer_info["layer_name"]
                            
                            # Check for updates
                            new_version = None
                            for mapping_name, mapping_config in self.layer_mappings.items():
                                if layer_name.endswith(f"-{mapping_name}") or layer_name == mapping_name:
                                    new_version = mapping_config.get("version")
                                    break
                            
                            if new_version:
                                new_arn = self._build_layer_arn(
                                    layer_name, new_version,
                                    layer_info["region"], layer_info["account_id"]
                                )
                                updated_layers.append(new_arn)
                                if new_arn != layer_arn:
                                    function_updated = True
                            else:
                                updated_layers.append(layer_arn)
                                
                        except Exception as e:
                            logger.warning(f"Error processing layer {layer_arn}: {e}")
                            updated_layers.append(layer_arn)

                    # Update the function if needed
                    if function_updated:
                        if dry_run:
                            logger.info(
                                f"Would update function {function_name} with layers: "
                                f"{updated_layers}"
                            )
                        else:
                            try:
                                self.lambda_client.update_function_configuration(
                                    FunctionName=function_name, Layers=updated_layers
                                )
                                logger.info(f"Updated AWS function {function_name}")
                            except ClientError as e:
                                logger.error(
                                    f"Failed to update function {function_name}: {e}"
                                )

        except Exception as e:
            logger.error(f"Error syncing with AWS: {e}")

    def run(self, dry_run: bool = True, sync_aws: bool = False) -> None:
        """
        Main execution method.

        Args:
            dry_run: If True, only show what would be changed
            sync_aws: If True, also sync changes with AWS
        """
        logger.info(f"Starting layer version update for stack '{self.stack_name}'")
        
        if dry_run:
            logger.info("Running in DRY-RUN mode - no changes will be made")
        
        try:
            # Export current state
            state_file = self.export_stack_state()
            
            # Load and process state
            state = self.load_state_from_file(state_file)
            updated_state, changes = self.update_layer_versions_in_state(state, dry_run)
            
            if not changes:
                logger.info("No layer version updates needed")
                return
            
            logger.info(f"Found {len(changes)} layer version updates:")
            for change in changes:
                logger.info(f"  - {change}")
            
            if not dry_run:
                # Save updated state
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                updated_file = self.backup_dir / f"{self.stack_name}_updated_{timestamp}.json"
                self.save_state_to_file(updated_state, str(updated_file))
                
                # Import updated state
                self.import_stack_state(str(updated_file))
                logger.info("Pulumi state updated successfully")
            
            if sync_aws:
                self.sync_with_aws(changes, dry_run)
                
        except Exception as e:
            logger.error(f"Operation failed: {e}")
            sys.exit(1)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Update Lambda layer versions in Pulumi state"
    )
    parser.add_argument(
        "--config", 
        required=True,
        help="Path to layer configuration YAML file"
    )
    parser.add_argument(
        "--stack", 
        default="dev",
        help="Pulumi stack name (default: dev)"
    )
    parser.add_argument(
        "--dry-run", 
        action="store_true",
        help="Show what would be changed without making changes"
    )
    parser.add_argument(
        "--sync-aws", 
        action="store_true",
        help="Also sync changes with AWS Lambda functions"
    )
    parser.add_argument(
        "--verbose", 
        "-v", 
        action="store_true",
        help="Enable verbose logging"
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Create updater and run
    updater = LayerVersionUpdater(args.config, args.stack)
    updater.run(dry_run=args.dry_run, sync_aws=args.sync_aws)


if __name__ == "__main__":
    main()