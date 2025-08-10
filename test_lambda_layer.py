#!/usr/bin/env python3
"""
Test Lambda layer locally by simulating the Lambda environment.
This script tests if PIL/Pillow imports work correctly in a layer.
"""
import os
import sys
import subprocess
import tempfile
import shutil
from pathlib import Path


def download_layer(layer_name: str, version: int = None) -> str:
    """Download a Lambda layer zip file."""
    print(f"Downloading layer {layer_name}...")
    
    # Get latest version if not specified
    if version is None:
        cmd = [
            "aws", "lambda", "list-layer-versions",
            "--layer-name", layer_name,
            "--region", "us-east-1",
            "--query", "LayerVersions[0].Version",
            "--output", "text"
        ]
        version = int(subprocess.check_output(cmd).strip().decode().split('\n')[0])
        print(f"Using latest version: {version}")
    
    # Get download URL
    cmd = [
        "aws", "lambda", "get-layer-version",
        "--layer-name", layer_name,
        "--version-number", str(version),
        "--region", "us-east-1",
        "--query", "Content.Location",
        "--output", "text"
    ]
    url = subprocess.check_output(cmd).strip().decode()
    
    # Download to temp file
    temp_file = f"{layer_name}-v{version}.zip"
    subprocess.run(["curl", "-o", temp_file, url], check=True, capture_output=True)
    print(f"Downloaded to {temp_file}")
    return temp_file


def extract_layer(zip_file: str, extract_dir: str) -> None:
    """Extract layer zip to directory."""
    print(f"Extracting {zip_file} to {extract_dir}...")
    os.makedirs(extract_dir, exist_ok=True)
    subprocess.run(["unzip", "-q", "-o", zip_file, "-d", extract_dir], check=True)


def test_layer_locally(layer_dir: str) -> None:
    """Test if PIL can be imported from the layer."""
    print("\n=== Testing layer locally ===")
    
    # Check layer structure
    python_dir = Path(layer_dir) / "python"
    if not python_dir.exists():
        print(f"❌ ERROR: {python_dir} does not exist")
        return
    
    # Check for nested structure issue
    nested_lib = python_dir / "lib"
    if nested_lib.exists():
        print(f"⚠️  WARNING: Found nested lib directory at {nested_lib}")
        print("   This will cause import issues in Lambda!")
    
    # List PIL files
    pil_dir = python_dir / "PIL"
    if pil_dir.exists():
        imaging_files = list(pil_dir.glob("_imaging*"))
        print(f"✓ Found PIL directory with {len(imaging_files)} _imaging files")
        for f in imaging_files[:3]:
            print(f"  - {f.name}")
    else:
        # Check nested paths
        for site_packages in python_dir.rglob("site-packages"):
            pil_nested = site_packages / "PIL"
            if pil_nested.exists():
                print(f"⚠️  PIL found at nested path: {pil_nested}")
    
    # Try Python import (will fail on Mac for Linux binaries, but shows structure)
    print("\nTrying Python import (may fail on Mac due to Linux binaries):")
    test_script = f"""
import sys
sys.path.insert(0, '{python_dir}')
try:
    from PIL import Image
    print("✓ PIL Image imported successfully")
    from PIL import _imaging
    print("✓ PIL _imaging imported successfully")
except ImportError as e:
    print(f"✗ Import failed: {{e}}")
"""
    subprocess.run([sys.executable, "-c", test_script])


def test_layer_with_docker(layer_dir: str) -> None:
    """Test layer using Docker with Lambda Python runtime."""
    print("\n=== Testing layer with Docker (Lambda environment) ===")
    
    if not shutil.which("docker"):
        print("❌ Docker not found. Install Docker to test in Lambda environment.")
        return
    
    # Create test script
    test_script = """
import sys
sys.path.insert(0, '/opt/python')
print(f"Python path: {sys.path[:3]}")

try:
    from PIL import Image, _imaging
    print("✓ SUCCESS: PIL and _imaging imported successfully!")
    print(f"  Image module: {Image.__file__}")
    print(f"  _imaging module: {_imaging.__file__}")
    
    # Try creating an image
    img = Image.new('RGB', (100, 100), color='red')
    print(f"✓ Created test image: {img.size}")
except ImportError as e:
    print(f"✗ FAILED: {e}")
    import os
    print("\\nChecking /opt/python structure:")
    for root, dirs, files in os.walk('/opt/python'):
        level = root.replace('/opt/python', '').count(os.sep)
        indent = ' ' * 2 * level
        print(f"{indent}{os.path.basename(root)}/")
        subindent = ' ' * 2 * (level + 1)
        for file in files[:5]:  # Show first 5 files
            print(f"{subindent}{file}")
        if len(files) > 5:
            print(f"{subindent}... and {len(files)-5} more files")
        if level > 2:  # Limit depth
            break
"""
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write(test_script)
        test_file = f.name
    
    try:
        # Run in Lambda Python 3.12 container
        cmd = [
            "docker", "run", "--rm",
            "-v", f"{os.path.abspath(layer_dir)}:/opt",
            "-v", f"{test_file}:/test.py",
            "--platform", "linux/arm64",
            "--entrypoint", "python",
            "public.ecr.aws/lambda/python:3.12-arm64",
            "/test.py"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        print(result.stdout)
        if result.stderr:
            print(f"Errors: {result.stderr}")
    finally:
        os.unlink(test_file)


def main():
    """Main test function."""
    import argparse
    parser = argparse.ArgumentParser(description="Test Lambda layer locally")
    parser.add_argument("--layer-name", default="receipt-label-dev", 
                       help="Lambda layer name")
    parser.add_argument("--version", type=int, help="Layer version (default: latest)")
    parser.add_argument("--skip-download", action="store_true", 
                       help="Skip download if layer zip exists")
    parser.add_argument("--docker", action="store_true",
                       help="Test with Docker Lambda runtime")
    args = parser.parse_args()
    
    # Download layer
    zip_file = f"{args.layer_name}.zip"
    if not args.skip_download or not os.path.exists(zip_file):
        zip_file = download_layer(args.layer_name, args.version)
    
    # Extract layer
    extract_dir = f"layer-test-{args.layer_name}"
    extract_layer(zip_file, extract_dir)
    
    # Test layer
    test_layer_locally(extract_dir)
    
    if args.docker:
        test_layer_with_docker(extract_dir)
    
    print(f"\n✓ Test complete. Layer extracted to: {extract_dir}")
    print(f"  To test manually: python3 -c \"import sys; sys.path.insert(0, '{extract_dir}/python'); from PIL import Image\"")


if __name__ == "__main__":
    main()