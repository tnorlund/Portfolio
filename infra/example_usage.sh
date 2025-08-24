#!/bin/bash
#
# Example usage of the layer version update tool
#

set -e
DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

echo "🚀 Lambda Layer Version Update Examples"
echo "======================================="

# Example 1: Dry run to see what would change
echo ""
echo "1. Dry-run to preview changes:"
echo "python \"$DIR/update_layer_versions.py\" --config \"$DIR/layer_config.yaml\" --stack dev --dry-run"
echo ""

# Example 2: Update development stack
echo "2. Update development stack:"
echo "python \"$DIR/update_layer_versions.py\" --config \"$DIR/layer_config.yaml\" --stack dev"
echo ""

# Example 3: Update production with AWS sync
echo "3. Update production with AWS synchronization:"
echo "python \"$DIR/update_layer_versions.py\" --config \"$DIR/layer_config.yaml\" --stack prod --sync-aws"
echo ""

# Example 4: Verbose output for debugging
echo "4. Verbose output for debugging:"
echo "python \"$DIR/update_layer_versions.py\" --config \"$DIR/layer_config.yaml\" --stack dev --dry-run --verbose"
echo ""

# Example 5: Quick test of current config
echo "5. Test current configuration (dry-run):"
if [ -f "$DIR/layer_config.yaml" ]; then
    echo "Running test with current configuration..."
    python "$DIR/update_layer_versions.py" --config "$DIR/layer_config.yaml" --stack dev --dry-run --verbose
else
    echo "❌ $DIR/layer_config.yaml not found. Please create it first."
fi

echo ""
echo "📚 For more information, see README_LAYER_UPDATE.md"