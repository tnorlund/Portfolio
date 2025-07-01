# Test file to verify self-hosted runner setup
# This file will trigger Python tests on the self-hosted runner


def test_self_hosted_runner():
    """Simple test to verify self-hosted runner is working."""
    assert 1 + 1 == 2, "Math still works on self-hosted runner!"


def test_enhanced_gsi3_feature():
    """Test related to our enhanced GSI3 implementation."""
    # Simulate the enhanced GSI3 composite key logic
    priorities = ["job", "user", "batch", "environment"]

    # Test priority hierarchy
    def get_scope_priority(scope_type):
        try:
            return priorities.index(scope_type)
        except ValueError:
            return 999

    assert get_scope_priority("job") < get_scope_priority("user")
    assert get_scope_priority("user") < get_scope_priority("batch")
    assert get_scope_priority("batch") < get_scope_priority("environment")

    print("✅ Enhanced GSI3 priority hierarchy working correctly!")


if __name__ == "__main__":
    test_self_hosted_runner()
    test_enhanced_gsi3_feature()
    print("🎉 Self-hosted runner test completed successfully!")
    print("💰 Testing cost optimization with hybrid runners!")
