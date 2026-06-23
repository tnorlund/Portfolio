"""Tests for LayoutLM confusion-target loading in unified pattern builder."""

import importlib.util
from dataclasses import dataclass
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "infra"
    / "label_evaluator_step_functions"
    / "lambdas"
    / "unified_pattern_builder.py"
)
AUDIT_HANDLER_PATH = (
    Path(__file__).resolve().parents[1]
    / "infra"
    / "label_evaluator_step_functions"
    / "lambdas"
    / "synthetic_augmentation_audit.py"
)
INFRA_PATH = (
    Path(__file__).resolve().parents[1]
    / "infra"
    / "label_evaluator_step_functions"
    / "infrastructure.py"
)
DOCKERFILE_PATTERN_BUILDER_PATH = (
    Path(__file__).resolve().parents[1]
    / "infra"
    / "label_evaluator_step_functions"
    / "lambdas"
    / "Dockerfile.unified_pattern_builder"
)
INFRA_MAIN_PATH = Path(__file__).resolve().parents[1] / "infra" / "__main__.py"


@dataclass
class FakeJob:
    job_id: str
    name: str
    created_at: str


@dataclass
class FakeMetric:
    metric_name: str
    value: object
    epoch: int | None


class FakeDynamoClient:
    def __init__(self, jobs=None, metrics_by_name=None):
        self.jobs = jobs or []
        self.metrics_by_name = metrics_by_name or {}
        self.list_jobs_calls = 0

    def list_jobs(self, limit=500):
        self.list_jobs_calls += 1
        return self.jobs[:limit], None

    def list_job_metrics(
        self,
        job_id,
        metric_name=None,
        last_evaluated_key=None,
    ):
        assert last_evaluated_key is None
        return self.metrics_by_name.get((job_id, metric_name), []), None


def _load_module(monkeypatch):
    import boto3

    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setattr(boto3, "client", lambda *_args, **_kwargs: object())

    spec = importlib.util.spec_from_file_location(
        "unified_pattern_builder_for_test",
        MODULE_PATH,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_resolve_layoutlm_training_job_uses_newest_featured_job(monkeypatch):
    module = _load_module(monkeypatch)
    client = FakeDynamoClient(
        jobs=[
            FakeJob("job-v13", "layoutlm-v13", "2026-04-01T00:00:00Z"),
            FakeJob("job-v14-old", "layoutlm-v14", "2026-04-01T00:00:00Z"),
            FakeJob("job-v14-new", "layoutlm-v14", "2026-05-01T00:00:00Z"),
            FakeJob("other", "not-layoutlm", "2026-06-01T00:00:00Z"),
        ]
    )

    assert module._resolve_layoutlm_training_job(client, "featured") == "job-v14-new"
    assert client.list_jobs_calls == 1


def test_resolve_layoutlm_training_job_keeps_explicit_job(monkeypatch):
    module = _load_module(monkeypatch)
    client = FakeDynamoClient()

    assert module._resolve_layoutlm_training_job(client, "job-explicit") == (
        "job-explicit"
    )
    assert client.list_jobs_calls == 0


def test_event_flag_enabled_handles_string_values(monkeypatch):
    module = _load_module(monkeypatch)

    assert module._event_flag_enabled(None, default=True) is True
    assert module._event_flag_enabled(False) is False
    assert module._event_flag_enabled("false") is False
    assert module._event_flag_enabled("0") is False
    assert module._event_flag_enabled("true") is True


def test_llm_execution_metadata_reports_no_spend_mode(monkeypatch):
    module = _load_module(monkeypatch)
    monkeypatch.setenv("RECEIPT_AGENT_DISABLE_PAID_LLM", "true")
    monkeypatch.setenv("OPENROUTER_MODEL_PROFILE", "cheap")

    config = type(
        "Config",
        (),
        {
            "openrouter_api_key": "test-key",
            "openrouter_model": "openai/gpt-5.5",
            "disable_paid_llm": True,
        },
    )()

    metadata = module._llm_execution_metadata(config)

    assert metadata == {
        "mode": "deterministic_fallback",
        "paid_llm_disabled": True,
        "api_call_allowed": False,
        "api_key_present": True,
        "configured_model": "openai/gpt-5.5",
        "model_profile": "cheap",
        "latest_openai_model": "gpt-5.5",
        "latest_model_source": (
            "https://developers.openai.com/api/docs/guides/latest-model"
        ),
        "latest_model_verified_at": "2026-06-23",
    }


def test_load_confusion_target_context_selects_best_f1_epoch_and_collapses_bio(
    monkeypatch,
):
    module = _load_module(monkeypatch)
    labels = ["B-MERCHANT_NAME", "I-MERCHANT_NAME", "O"]
    client = FakeDynamoClient(
        metrics_by_name={
            (
                "job-1",
                "confusion_matrix",
            ): [
                FakeMetric(
                    "confusion_matrix",
                    {
                        "labels": labels,
                        "matrix": [
                            [1, 0, 9],
                            [0, 1, 8],
                            [2, 3, 50],
                        ],
                    },
                    epoch=1,
                ),
                FakeMetric(
                    "confusion_matrix",
                    {
                        "labels": labels,
                        "matrix": [
                            [10, 2, 3],
                            [1, 7, 4],
                            [5, 6, 100],
                        ],
                    },
                    epoch=2,
                ),
            ],
            (
                "job-1",
                "val_f1",
            ): [
                FakeMetric("val_f1", 0.72, epoch=1),
                FakeMetric("val_f1", 0.81, epoch=2),
            ],
        }
    )

    context = module._load_confusion_target_context(
        client,
        training_job_id="job-1",
    )

    assert context["job_id"] == "job-1"
    assert context["epoch"] == 2
    assert context["confusion_matrix"] == {
        "labels": ["MERCHANT_NAME", "O"],
        "matrix": [
            [20, 7],
            [11, 100],
        ],
    }


def test_select_confusion_matrix_metric_falls_back_to_latest_epoch(monkeypatch):
    module = _load_module(monkeypatch)
    selected = module._select_confusion_matrix_metric(
        [
            FakeMetric("confusion_matrix", {"labels": ["O"], "matrix": [[1]]}, 1),
            FakeMetric("confusion_matrix", {"labels": ["O"], "matrix": [[2]]}, 3),
        ],
        [],
    )

    assert selected.epoch == 3


def test_load_similar_merchant_examples_queries_chroma_and_closes(
    monkeypatch,
):
    module = _load_module(monkeypatch)

    class FakeChromaClient:
        closed = False

        def close(self):
            self.closed = True

    fake_chroma = FakeChromaClient()

    monkeypatch.setenv("SIMILAR_MERCHANT_EXAMPLES_PER_TARGET", "2")
    monkeypatch.setenv("SIMILAR_MERCHANT_CHROMA_N_RESULTS", "9")
    monkeypatch.setattr(
        module,
        "_create_pattern_chroma_client",
        lambda: (
            fake_chroma,
            {"status": "available", "source": "test_chroma"},
        ),
    )
    monkeypatch.setattr(module, "_create_pattern_embed_fn", lambda: "embed-fn")

    def fake_query(
        chroma_client,
        merchant_name,
        embed_fn,
        confusion_targets,
        *,
        max_per_target,
        n_results,
    ):
        assert chroma_client is fake_chroma
        assert merchant_name == "Sprouts"
        assert embed_fn == "embed-fn"
        assert confusion_targets == ["target"]
        assert max_per_target == 2
        assert n_results == 9
        return [{"merchant_name": "Trader Joe's", "label": "MERCHANT_NAME"}]

    monkeypatch.setattr(module, "_query_similar_merchant_examples", fake_query)

    examples, source = module._load_similar_merchant_examples(
        "Sprouts",
        ["target"],
    )

    assert examples == [{"merchant_name": "Trader Joe's", "label": "MERCHANT_NAME"}]
    assert source["status"] == "success"
    assert source["source"] == "test_chroma"
    assert source["example_count"] == 1
    assert source["target_count"] == 1
    assert fake_chroma.closed is True


def test_load_similar_merchant_examples_skips_without_chroma(monkeypatch):
    module = _load_module(monkeypatch)
    monkeypatch.setattr(
        module,
        "_create_pattern_chroma_client",
        lambda: (
            None,
            {
                "status": "skipped",
                "source": "none",
                "reason": "missing_chromadb_bucket",
            },
        ),
    )

    examples, source = module._load_similar_merchant_examples(
        "Sprouts",
        ["target"],
    )

    assert examples == []
    assert source == {
        "status": "skipped",
        "source": "none",
        "reason": "missing_chromadb_bucket",
    }


def test_pattern_builder_prompts_llm_with_similar_merchant_evidence():
    source = MODULE_PATH.read_text()

    evidence_lookup = source.index("_load_similar_merchant_examples(")
    prompt_build = source.index("prompt = build_discovery_prompt(")
    llm_call = source.index("patterns = discover_patterns_with_llm(")

    assert evidence_lookup < prompt_build < llm_call
    assert "similar_merchant_examples=similar_merchant_examples" in source


def test_pattern_builder_lambda_has_chroma_snapshot_contract():
    config_block = (
        INFRA_PATH.read_text()
        .split(
            "unified_pattern_builder_config = {",
            1,
        )[1]
        .split("unified_pattern_builder_docker_image", 1)[0]
    )

    assert '"ephemeral_storage": 10240' in config_block
    assert '"CHROMADB_BUCKET": chromadb_bucket_name or ""' in config_block
    assert '"RECEIPT_AGENT_OPENAI_API_KEY": openai_api_key' in config_block
    assert '"RECEIPT_AGENT_CHROMA_PERSIST_DIRECTORY": "/tmp/chromadb"' in (config_block)


def test_synthetic_augmentation_audit_lambda_contract():
    handler_source = AUDIT_HANDLER_PATH.read_text(encoding="utf-8")
    dockerfile_source = DOCKERFILE_PATTERN_BUILDER_PATH.read_text(encoding="utf-8")
    infra_source = INFRA_PATH.read_text(encoding="utf-8")
    main_source = INFRA_MAIN_PATH.read_text(encoding="utf-8")

    assert "build_synthetic_augmentation_audit_from_dynamo" in handler_source
    assert "baseline_job_ref" in handler_source
    assert "augmented_job_ref" in handler_source
    assert "synthetic_receipt_plan" in handler_source
    assert "audit_result_s3_uri" in handler_source
    assert "synthetic_augmentation_audits" in handler_source
    assert "synthetic_augmentation_audit.py" in dockerfile_source
    assert '"commands": ["synthetic_augmentation_audit.handler"]' in infra_source
    assert "sagemaker:ListTags" in infra_source
    assert "synthetic_augmentation_completion_rule = EventRule" in infra_source
    assert "synthetic_augmentation_audit_lambda.arn" in infra_source
    assert 'target_id="synthetic-augmentation-audit"' in infra_source
    assert "layoutlm_start_training_lambda_arn" in infra_source
    assert "layoutlm_start_training=(args[5] or None)" in infra_source
    assert "synthetic_augmentation_audit_lambda_arn" in infra_source
    assert "synthetic_augmentation_audit_lambda_arn" in main_source
    assert "layoutlm_start_training_lambda_arn" in main_source
