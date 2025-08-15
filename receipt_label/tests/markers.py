"""Pytest markers for receipt_label test organization."""

import pytest

# Test tier markers
unit = pytest.mark.unit
integration = pytest.mark.integration
end_to_end = pytest.mark.end_to_end

# Production relevance marker
unused_in_production = pytest.mark.unused_in_production

# Performance markers
slow = pytest.mark.slow
fast = pytest.mark.fast

# Service markers
aws = pytest.mark.aws
chroma = pytest.mark.chroma
openai = pytest.mark.openai

# Feature markers
pattern_detection = pytest.mark.pattern_detection
completion = pytest.mark.completion
embedding = pytest.mark.embedding
cost_optimization = pytest.mark.cost_optimization