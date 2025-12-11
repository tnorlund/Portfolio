Shared tools package
====================

Scope:
- Shared connectors only: `chroma.py`, `dynamo.py`, `places.py`, and `registry.py`.
- Agent-specific tools now live under `agents/<agent>/tools`.

Out of scope:
- Legacy harmonizer/label harmonizer implementations (v1/v2) have been removed.
- Agent-level utilities should not be added here; place them with the owning agent.
