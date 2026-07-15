"""Cost visibility tests for the live fix-place graph runner."""

import asyncio
from unittest.mock import AsyncMock

from receipt_agent.subagents.place_finder.graph import (
    run_receipt_place_finder,
)
from receipt_agent.utils.llm_factory import CostTrackingCallback


def test_fix_place_runner_attaches_cost_callback_to_graph():
    """The graph-level handler must propagate to every tool-calling turn."""
    graph = AsyncMock()
    callback = CostTrackingCallback()
    state_holder = {"cost_callback": callback}

    async def complete_with_result(_state, config):
        state_holder["place_result"] = {
            "found": True,
            "place_id": "ChIJ-test",
            "merchant_name": "Test Merchant",
            "confidence": 0.99,
            "fields_found": ["place_id", "merchant_name"],
        }
        return config

    graph.ainvoke.side_effect = complete_with_result

    result = asyncio.run(
        run_receipt_place_finder(
            graph=graph,
            state_holder=state_holder,
            image_id="image-1",
            receipt_id=1,
        )
    )

    assert result["found"] is True
    config = graph.ainvoke.call_args.kwargs["config"]
    assert callback.handler in config["callbacks"]
