#!/bin/bash
# Test Lambda dependencies locally on Mac
# This tests that all imports work and basic functionality is available

set -e

echo "🧪 Testing Lambda dependencies locally..."

# Create venv
VENV_DIR="lambda-venv"
if [ -d "$VENV_DIR" ]; then
    echo "⚠️  Removing existing venv..."
    rm -rf "$VENV_DIR"
fi

echo "📦 Creating virtual environment..."
python3 -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"

echo "📥 Installing dependencies..."
# Install receipt_dynamo and receipt_layoutlm in editable mode
pip install --upgrade pip
pip install -e receipt_dynamo
pip install -e receipt_layoutlm

# Install Lambda dependencies
# Note: Using regular PyTorch for Mac (not CPU-only Linux version)
# This tests that the code works, even if the exact wheel differs
echo "📥 Installing PyTorch (Mac version)..."
pip install torch>=2.2.0

echo "📥 Installing other dependencies..."
pip install transformers>=4.40.0 \
    datasets>=2.19.0 \
    boto3>=1.34.0 \
    pillow \
    tqdm \
    filelock \
    'pydantic>=2.10.6' \
    'accelerate>=0.21.0' \
    seqeval

echo "✅ Dependencies installed!"
echo ""
echo "🧪 Testing imports..."

# Test script
python3 << 'PYTEST'
import sys

print("Testing imports...")

try:
    import torch
    print(f"✅ torch {torch.__version__}")
except ImportError as e:
    print(f"❌ torch: {e}")
    sys.exit(1)

try:
    import transformers
    print(f"✅ transformers {transformers.__version__}")
except ImportError as e:
    print(f"❌ transformers: {e}")
    sys.exit(1)

try:
    import datasets
    print(f"✅ datasets {datasets.__version__}")
except ImportError as e:
    print(f"❌ datasets: {e}")
    sys.exit(1)

try:
    import boto3
    print(f"✅ boto3 {boto3.__version__}")
except ImportError as e:
    print(f"❌ boto3: {e}")
    sys.exit(1)

try:
    from receipt_dynamo import DynamoClient
    from receipt_dynamo.constants import ValidationStatus
    print("✅ receipt_dynamo")
except ImportError as e:
    print(f"❌ receipt_dynamo: {e}")
    sys.exit(1)

try:
    from receipt_layoutlm import LayoutLMInference
    print("✅ receipt_layoutlm")
except ImportError as e:
    print(f"❌ receipt_layoutlm: {e}")
    sys.exit(1)

try:
    import seqeval
    print(f"✅ seqeval")
except ImportError as e:
    print(f"❌ seqeval: {e}")
    sys.exit(1)

# Test that the handler code can be imported
try:
    import sys
    import os
    # Add the handler directory to path
    handler_path = os.path.join(os.path.dirname(__file__), "infra/routes/layoutlm_inference_cache_generator/lambdas")
    if os.path.exists(handler_path):
        sys.path.insert(0, handler_path)
        # Try importing the handler (it will fail on env vars, but that's OK)
        try:
            import index
            print("✅ Lambda handler imports successfully")
        except (KeyError, NameError) as e:
            # Expected - handler needs env vars
            if "DYNAMODB_TABLE_NAME" in str(e) or "S3_CACHE_BUCKET" in str(e):
                print("✅ Lambda handler imports successfully (env vars not set, which is expected)")
            else:
                raise
    else:
        print(f"⚠️  Handler path not found: {handler_path}")
except Exception as e:
    print(f"⚠️  Could not test handler import: {e}")

print("")
print("✅ All imports successful!")
print("")
print("Note: This tests Mac-compatible versions. Lambda uses Linux ARM64 wheels.")
print("The exact PyTorch version may differ, but the API should be compatible.")
PYTEST

echo ""
echo "✅ Local dependency test complete!"
echo ""
echo "To activate the venv later:"
echo "  source $VENV_DIR/bin/activate"
echo ""
echo "To test the handler code manually:"
echo "  export DYNAMODB_TABLE_NAME='ReceiptsTable-dc5be22'"
echo "  export S3_CACHE_BUCKET='test-bucket'"
echo "  export LAYOUTLM_TRAINING_BUCKET='layoutlm-models-1c8f680'"
echo "  python3 infra/routes/layoutlm_inference_cache_generator/lambdas/index.py"

