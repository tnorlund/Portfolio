# receipt_agent/ (LangGraph agents)

Deltas to the root `AGENTS.md`.

- Lint: CI runs `black --check` and `isort --check-only` only on `.py` files
  changed versus the base branch. Format the files you touch and nothing else;
  a package-wide reformat here produces a noisy diff for no CI benefit.
- QA graph (`agents/question_answering/`): the synthesize node must read the
  agent's `state.messages`; when it ignored them it contradicted correct agent
  answers and dropped grades to D/F. Shape and synthesize must receive the
  summary-tier receipts and the precomputed aggregates or the answer truncates.
- Google Places is billed per call. Go through `receipt_places` (DynamoDB
  cached) via `tools/places.py`; never call the Places API directly.
- LLM access goes through OpenRouter (`config/settings.py`); model ids come from
  `OPENROUTER_MODEL` / `RECEIPT_AGENT_OPENROUTER_MODEL` (default
  `x-ai/grok-4.1-fast`). Read them from settings or env, do not hard-code new
  ids in nodes.
- Tests: `pytest receipt_agent/tests` from the repo root, offline. Anything
  that needs LangSmith, Chroma Cloud, or real AWS is integration and skipped.
- Evaluating answer quality end to end is the `qa-agent-eval` skill.
