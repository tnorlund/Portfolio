"""
Native Pulumi implementation for receipt_dynamo Lambda Layer.
This replaces the custom shell script approach with pure Pulumi resources.

No more ARG_MAX issues - uses only native AWS resources and Pulumi components.
"""

import hashlib
import os
import zipfile
from pathlib import Path
from typing import Optional

import pulumi
import pulumi_aws as aws
from pulumi import FileArchive


class NativeReceiptDynamoLayer:
    """
    Native Pulumi implementation for receipt_dynamo Lambda layer.
    
    Uses only Pulumi's built-in components - no custom shell scripts.
    This eliminates the "argument list too long" error completely.
    """
    
    def __init__(self, name: str = "receipt-dynamo", stack: Optional[str] = None, test_mode: bool = False):
        self.name = name
        self.stack = stack or (pulumi.get_stack() if not test_mode else "test")
        self.package_path = Path("receipt_dynamo")
        
        # Verify package exists
        if not self.package_path.exists():
            raise FileNotFoundError(f"Package directory not found: {self.package_path}")
        
        print(f"🚀 Creating native Lambda layer for {self.name}")
        
        if not test_mode:
            self._create_layer()
        else:
            print("🧪 Test mode - skipping Pulumi resource creation")
    
    def _create_layer(self):
        """Create the Lambda layer using native Pulumi components only."""
        
        # Step 1: Calculate content hash for change detection
        content_hash = self._calculate_content_hash()
        print(f"📦 Content hash: {content_hash[:12]}...")
        
        # Step 2: Create layer archive using native FileArchive
        layer_archive = self._create_layer_archive(content_hash)
        
        # Step 3: Create Lambda layer version using native AWS resource
        self.layer_version = aws.lambda_.LayerVersion(
            f"{self.name}-layer-{self.stack}",
            layer_name=f"{self.stack}-{self.name}",
            code=layer_archive,
            compatible_runtimes=["python3.12"],
            compatible_architectures=["arm64", "x86_64"],  # Support both architectures
            description="Native Pulumi layer for receipt_dynamo - DynamoDB operations",
            opts=pulumi.ResourceOptions(
                # Only recreate when content actually changes
                replace_on_changes=["code"],
                additional_secret_outputs=["code"]
            )
        )
        
        # Export the layer ARN for use by Lambda functions
        self.arn = self.layer_version.arn
        
        # Only export if we're in a proper Pulumi program context
        try:
            pulumi.export(f"native_{self.name.replace('-', '_')}_layer_arn", self.arn)
            pulumi.export(f"native_{self.name.replace('-', '_')}_layer_version", self.layer_version.version)
        except Exception as e:
            # If we can't export (e.g., in test mode), that's okay
            print(f"⚠️  Could not export outputs (testing mode?): {e}")
        
        print(f"✅ Native layer {self.name} created successfully")
    
    def test_mode_init(self):
        """Initialize in test mode without creating Pulumi resources."""
        # Calculate content hash for testing
        content_hash = self._calculate_content_hash()
        print(f"📦 Test mode - Content hash: {content_hash[:12]}...")
        
        # Create archive for testing
        layer_archive = self._create_layer_archive(content_hash)
        print(f"📁 Test mode - Archive created successfully")
        
        # Set mock ARN for testing
        self.arn = f"arn:aws:lambda:us-east-1:123456789012:layer:test-{self.name}:1"
        self.layer_version = None  # Not created in test mode
        
        print(f"✅ Test mode - Native layer {self.name} initialized successfully")
    
    def _calculate_content_hash(self) -> str:
        """
        Calculate hash of package contents for reliable change detection.
        
        This replaces the complex hash logic from custom scripts.
        """
        hash_md5 = hashlib.md5()
        
        # Include pyproject.toml for dependency tracking
        pyproject_path = self.package_path / "pyproject.toml"
        if pyproject_path.exists():
            with open(pyproject_path, 'rb') as f:
                hash_md5.update(b"pyproject.toml")
                hash_md5.update(f.read())
        
        # Walk through package source files
        source_dir = self.package_path / "receipt_dynamo"
        if source_dir.exists():
            for root, dirs, files in os.walk(source_dir):
                # Sort for consistent ordering
                dirs.sort()
                files.sort()
                
                for file in files:
                    # Skip compiled Python files and cache
                    if file.endswith(('.pyc', '.pyo')) or file.startswith('.'):
                        continue
                    
                    file_path = Path(root) / file
                    try:
                        with open(file_path, 'rb') as f:
                            # Include file path in hash for structure changes
                            rel_path = file_path.relative_to(self.package_path)
                            hash_md5.update(str(rel_path).encode())
                            hash_md5.update(f.read())
                    except (IOError, OSError) as e:
                        print(f"⚠️  Warning: Could not read {file_path}: {e}")
                        continue
        
        return hash_md5.hexdigest()
    
    def _create_layer_archive(self, content_hash: str) -> FileArchive:
        """
        Create Lambda layer archive using Pulumi's native FileArchive.
        
        This replaces the complex script-based zip creation.
        """
        # Create temporary zip file for the layer
        temp_zip_path = Path(f"/tmp/native-{self.name}-{content_hash[:8]}.zip")
        
        print(f"📁 Creating layer archive at {temp_zip_path}")
        
        with zipfile.ZipFile(temp_zip_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zipf:
            
            # Add Python package files with proper Lambda layer structure
            source_dir = self.package_path / "receipt_dynamo"
            if source_dir.exists():
                for root, dirs, files in os.walk(source_dir):
                    for file in files:
                        # Skip unwanted files
                        if file.endswith(('.pyc', '.pyo')) or file.startswith('.') or file == '__pycache__':
                            continue
                        
                        file_path = Path(root) / file
                        
                        # Calculate path relative to the package directory
                        rel_path = file_path.relative_to(self.package_path)
                        
                        # Add to zip with python/ prefix (required by Lambda layers)
                        archive_path = f"python/{rel_path}"
                        zipf.write(file_path, archive_path)
                        
                print(f"📦 Added {len(zipf.namelist())} files to layer archive")
            else:
                raise FileNotFoundError(f"Source directory not found: {source_dir}")
        
        # Return Pulumi FileArchive
        return FileArchive(str(temp_zip_path))


# Factory function for easy import
def create_native_dynamo_layer(stack: Optional[str] = None) -> NativeReceiptDynamoLayer:
    """
    Factory function to create the native receipt_dynamo layer.
    
    Usage:
        from native_receipt_dynamo_layer import create_native_dynamo_layer
        
        # Create the layer
        dynamo_layer = create_native_dynamo_layer()
        
        # Use in Lambda functions
        lambda_function = aws.lambda_.Function(
            "my-function",
            layers=[dynamo_layer.arn],
            # ... other config
        )
    """
    return NativeReceiptDynamoLayer(stack=stack)


# Only create layer when running in a proper Pulumi context
# This prevents errors when importing for testing or development
def _is_pulumi_context():
    """Check if we're running in a proper Pulumi context."""
    try:
        import sys
        # Check if we're being run by Pulumi
        return 'pulumi' in sys.modules and hasattr(pulumi, '_program')
    except:
        return False

if __name__ != "__main__" and _is_pulumi_context():
    # Only create when imported AND in Pulumi context
    try:
        native_dynamo_layer = create_native_dynamo_layer()
        
        # Export for backward compatibility
        dynamo_layer = native_dynamo_layer
        
        print("🎉 Native receipt_dynamo layer ready!")
        print(f"   ARN: {native_dynamo_layer.arn}")
        print("   No more shell script issues!")
        
    except Exception as e:
        print(f"❌ Failed to create native layer: {e}")
        print("   Falling back to existing layer implementation...")
        # Could import the old implementation as fallback here
        raise
else:
    # When not in Pulumi context, just expose the class for testing
    print("ℹ️  Native layer class available for testing (not creating resources)")
    dynamo_layer = None  # Will be None when testing


if __name__ == "__main__":
    # When run as script, just create and show info
    layer = create_native_dynamo_layer()
    print(f"Layer ARN: {layer.arn}")