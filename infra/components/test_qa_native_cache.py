"""Offline checks for native QA traces and atomic cache publication."""

import asyncio
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, TypedDict
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError
from langchain_core.runnables import RunnableLambda
from langgraph.graph import END, START, StateGraph


def load(relative: str) -> Any:
    path = Path(__file__).parents[1] / relative
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = load("qa_agent_step_functions/handlers/build_viz_cache.py")
definition = load("qa_agent_step_functions/definition.py")


@pytest.fixture
def runner(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    return load("qa_agent_step_functions/lambdas/run_question.py")


class GraphState(TypedDict):
    answer: str


def test_real_graph_captures_nodes_without_nested_duplicates(
    runner: Any,
) -> None:
    callback = runner.TraceCaptureCallback()
    graph = StateGraph(GraphState)

    def plan(state: GraphState) -> dict:
        return RunnableLambda(lambda value: {"answer": "done"}).invoke(state)

    graph.add_node("plan", plan)
    graph.add_edge(START, "plan")
    graph.add_edge("plan", END)
    graph.compile().invoke({"answer": ""}, {"callbacks": [callback]})
    trace = callback.get_trace()
    assert len(trace) == 1
    assert trace[0]["type"] == "plan"
    assert trace[0]["outputs"] == {"answer": "done"}
    assert trace[0]["status"] == "ok"
    assert trace[0]["duration_ms"] >= 0


def test_failed_question_keeps_incurred_cost_and_tool_error(
    runner: Any,
) -> None:
    async def answer(*args: Any, callbacks: list) -> None:
        cost, trace = callbacks
        cost.on_llm_end(
            SimpleNamespace(
                llm_output={
                    "token_usage": {
                        "cost": 0.02,
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 15,
                    }
                }
            )
        )
        run_id = uuid4()
        trace.on_tool_start({"name": "search"}, "milk", run_id=run_id)
        trace.on_tool_error(RuntimeError("search failed"), run_id=run_id)
        raise RuntimeError("search failed")

    result = asyncio.run(
        runner._run_question(
            asyncio.Semaphore(1),
            answer,
            lambda **kw: (None, {}),
            None,
            None,
            "Milk spending?",
            0,
        )
    )
    assert result["success"] is False
    assert result["cost"] == 0.02
    assert result["tokens"] == {"input": 10, "output": 5, "total": 15}
    assert result["llmCalls"] == result["toolInvocations"] == 1
    assert result["trace"][0]["error"] == "search failed"


def test_graph_error_result_is_not_counted_as_success(runner: Any) -> None:
    async def fail(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("graph failed")

    graph = SimpleNamespace(ainvoke=fail)
    result = asyncio.run(
        runner._run_question(
            asyncio.Semaphore(1),
            runner.answer_question,
            lambda **kw: (graph, {}),
            None,
            None,
            "Milk spending?",
            0,
        )
    )
    assert result["success"] is False
    assert result["error"] == "graph failed"


class MemoryS3:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.fail_key: str | None = None

    def get_object(self, *, Bucket: str, Key: str) -> dict:
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[Key])}

    def get_paginator(self, name: str) -> Any:
        return SimpleNamespace(
            paginate=lambda **kw: [
                {
                    "Contents": [
                        {"Key": key}
                        for key in self.objects
                        if key.startswith(kw["Prefix"])
                    ]
                }
            ]
        )

    def put_object(
        self, *, Bucket: str, Key: str, Body: bytes, **kw: Any
    ) -> None:
        if self.fail_key and Key.endswith(self.fail_key):
            raise RuntimeError("S3 write failed")
        self.objects[Key] = Body


@pytest.fixture
def batch(monkeypatch: pytest.MonkeyPatch) -> tuple:
    s3 = MemoryS3()
    monkeypatch.setenv("BATCH_BUCKET", "qa-cache")
    monkeypatch.setattr(builder.boto3, "client", lambda name: s3)
    result = {
        "question": "Milk spending?",
        "questionIndex": 0,
        "traceId": "trace-0",
        "startedAt": 100,
        "success": True,
        "cost": 0.02,
        "answer": "$5",
        "receiptCount": 1,
        "evidence": [{"image_id": "image", "receipt_id": 1, "amount": 5}],
        "trace": [
            {
                "type": "synthesize",
                "start_ts": 101,
                "duration_ms": 50,
                "status": "ok",
            }
        ],
    }
    s3.objects["qa-runs/run/question-results.ndjson"] = json.dumps(
        result
    ).encode()
    s3.objects["qa-runs/run/receipts.json"] = json.dumps(
        {
            "image_1": {
                "cdn_webp_s3_key": "image.webp",
                "width": 100,
                "height": 200,
            },
        }
    ).encode()
    s3.objects["metadata.json"] = b'{"execution_id": "previous"}'
    event = {
        "execution_id": "run",
        "total_questions": 1,
        "results_ndjson_key": "qa-runs/run/question-results.ndjson",
        "receipts_lookup_path": "s3://qa-cache/qa-runs/run/receipts.json",
    }
    return s3, event


def test_cache_preserves_contract_and_actual_timing(batch: tuple) -> None:
    s3, event = batch
    metadata = builder.handler(event, None)
    question = json.loads(
        s3.objects[metadata["questions_prefix"] + "question-0.json"]
    )
    assert question["questionIndex"] == 0
    assert len(question["trace"]) == 1
    step = question["trace"][0]
    assert step["durationMs"] == 50
    assert step["startOffsetMs"] == 1000
    assert step["receipts"][0]["thumbnailKey"] == "image.webp"
    assert metadata["total_cost"] == 0.02
    assert json.loads(s3.objects["metadata.json"]) == metadata


def test_failed_write_keeps_previous_cache_visible(batch: tuple) -> None:
    s3, event = batch
    s3.fail_key = "question-0.json"
    with pytest.raises(RuntimeError, match="S3 write failed"):
        builder.handler(event, None)
    assert (
        json.loads(s3.objects["metadata.json"])["execution_id"] == "previous"
    )


def test_incomplete_batch_does_not_publish(batch: tuple) -> None:
    s3, event = batch
    event["total_questions"] = 2
    with pytest.raises(ValueError, match="Incomplete"):
        builder.handler(event, None)
    assert (
        json.loads(s3.objects["metadata.json"])["execution_id"] == "previous"
    )


def test_api_uses_new_pointer_and_supports_legacy_cache(
    batch: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    s3, event = batch
    monkeypatch.setenv("S3_CACHE_BUCKET", "qa-cache")
    api = load("routes/qa_viz_cache/lambdas/index.py")
    builder.handler(event, None)
    request = {
        "requestContext": {"http": {"method": "GET"}},
        "queryStringParameters": {"index": "0"},
    }
    response = json.loads(api.handler(request, None)["body"])
    assert response["questions"][0]["traceId"] == "trace-0"
    request["queryStringParameters"] = {"all": "true"}
    response = json.loads(api.handler(request, None)["body"])
    assert len(response["questions"]) == 1
    assert response["questions"][0]["traceId"] == "trace-0"
    request["queryStringParameters"] = {"index": "0"}
    s3.objects["metadata.json"] = b"{}"
    s3.objects["questions/question-0.json"] = b'{"traceId": "old"}'
    response = json.loads(api.handler(request, None)["body"])
    assert response["questions"][0]["traceId"] == "old"


def test_workflow_has_no_export_polling_or_spark() -> None:
    flow = definition.build_state_machine_definition(
        run_all_questions_arn="run",
        query_metadata_arn="query",
        build_cache_arn="build",
        batch_bucket="qa-cache",
    )
    assert set(flow["States"]) == {
        "RunAllQuestions",
        "QueryReceiptData",
        "BuildVizCache",
    }
    for state in flow["States"].values():
        assert state["TimeoutSeconds"] > 0
        assert state["Retry"]


def test_missing_receipt_does_not_publish(batch: tuple) -> None:
    s3, event = batch
    s3.objects["qa-runs/run/receipts.json"] = b"{}"
    with pytest.raises(ValueError, match="Missing receipt metadata"):
        builder.handler(event, None)
    assert (
        json.loads(s3.objects["metadata.json"])["execution_id"] == "previous"
    )


def test_checkpoint_survives_failure_of_another_question_and_is_reused(
    runner: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    s3 = MemoryS3()
    monkeypatch.setattr(runner, "s3_client", s3)
    calls = []

    async def run(**kwargs: Any) -> dict:
        calls.append(kwargs["question_index"])
        if kwargs["question_index"] == 1:
            raise RuntimeError("runtime failed")
        return {"questionIndex": 0, "question": "first", "cost": 0.02}

    monkeypatch.setattr(runner, "_run_question", run)

    async def attempt() -> None:
        await runner._run_and_store_question("qa-cache", "run", 0, "first")
        await runner._run_and_store_question("qa-cache", "run", 1, "second")

    with pytest.raises(RuntimeError, match="runtime failed"):
        asyncio.run(attempt())
    assert "qa-runs/run/q00.json" in s3.objects
    assert "qa-runs/run/question-results.ndjson" not in s3.objects
    result = asyncio.run(
        runner._run_and_store_question("qa-cache", "run", 0, "first")
    )
    assert result["cost"] == 0.02
    assert calls == [0, 1]


def test_receipt_lookup_failure_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "test-table")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    query = load("qa_agent_step_functions/handlers/query_receipt_metadata.py")

    def fail(**kwargs: Any) -> None:
        raise RuntimeError("DynamoDB unavailable")

    monkeypatch.setattr(
        query, "DynamoClient", lambda **kw: SimpleNamespace(get_receipt=fail)
    )
    with pytest.raises(RuntimeError, match="DynamoDB unavailable"):
        query.handler(
            {
                "receipt_keys": [{"image_id": "image", "receipt_id": 1}],
                "execution_id": "run",
                "batch_bucket": "qa-cache",
            },
            None,
        )


def test_runner_retries_timeouts_using_same_execution_checkpoint() -> None:
    flow = definition.build_state_machine_definition(
        run_all_questions_arn="run",
        query_metadata_arn="query",
        build_cache_arn="build",
        batch_bucket="qa-cache",
    )
    state = flow["States"]["RunAllQuestions"]
    errors = {
        error for retry in state["Retry"] for error in retry["ErrorEquals"]
    }
    assert {"States.Timeout", "States.TaskFailed"} <= errors
    assert (
        state["Parameters"]["Payload"]["execution_id.$"] == "$$.Execution.Name"
    )
