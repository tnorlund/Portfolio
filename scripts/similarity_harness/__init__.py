"""Similarity evaluation harness (Round A).

``capture_golden.py`` snapshots Chroma answers for the three genuine vector
query families. ``evaluate.py`` scores any ``VectorSearchClient`` against
those fixtures with the SPEC §8 / AGENT_PLAN metrics.
"""
