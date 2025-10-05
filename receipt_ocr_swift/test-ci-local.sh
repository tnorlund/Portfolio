#!/bin/bash
# Test script to verify CI/CD workflow locally

set -e

echo "🧪 Testing CI/CD workflow locally..."

# Test 1: Build Swift package
echo "📦 Testing Swift package build..."
cd receipt_ocr_swift
swift build --product receipt-ocr
echo "✅ Swift package build successful"

# Test 2: Run unit tests
echo "🧪 Running unit tests..."
swift test --filter ReceiptOCRCoreTests
echo "✅ Unit tests passed"

# Test 3: Test CLI help
echo "❓ Testing CLI help..."
swift run receipt-ocr --help
echo "✅ CLI help works"

# Test 4: Test local image processing
echo "🖼️ Testing local image processing..."
mkdir -p test_output

# Create test image if it doesn't exist
if [ ! -f "../img 1.png" ]; then
  echo "Creating test image..."
  echo "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==" | base64 -d > "../img 1.png"
fi

swift run receipt-ocr --process-local-image "../img 1.png" --output-dir test_output --stub-ocr --env dev
ls -la test_output/
rm -rf test_output
echo "✅ Local image processing works"

# Test 5: Test Python wrapper
echo "🐍 Testing Python wrapper..."
cd ../receipt_upload
python -m receipt_upload.mac_ocr --help
echo "✅ Python wrapper help works"

echo "🎉 All CI/CD tests passed locally!"
