"""Tests for the shared EMR Serverless Spark runtime settings."""

import tomllib
from pathlib import Path

from infra.components.emr_serverless_analytics import (
    _python_environment_buildspec,
)
from infra.components.emr_serverless_runtime import (
    EMR_PYTHON_VERSION,
    EMR_SPARK_RELEASE,
    EMR_SPARK_VERSION,
    spark_runtime_properties,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_emr_runtime_targets_spark_8_and_python_313() -> None:
    """The selected local runtime matches EMR Spark 8."""
    assert EMR_SPARK_RELEASE == "emr-spark-8.0.0"
    assert EMR_SPARK_VERSION == "4.0.2"
    assert EMR_PYTHON_VERSION == "3.13"


def test_spark_runtime_uses_packaged_python_environment() -> None:
    """Drivers and executors share the application package."""
    properties = spark_runtime_properties(
        "s3://artifacts/spark/python-environment.tar.gz"
    )

    assert properties == {
        "spark.archives": (
            "s3://artifacts/spark/python-environment.tar.gz#environment"
        ),
        "spark.emr-serverless.driverEnv.PYSPARK_DRIVER_PYTHON": (
            "./environment/bin/python"
        ),
        "spark.emr-serverless.driverEnv.PYSPARK_PYTHON": (
            "./environment/bin/python"
        ),
        "spark.executorEnv.PYSPARK_PYTHON": ("./environment/bin/python"),
        "spark.sql.ansi.enabled": "false",
    }


def test_python_environment_excludes_emr_native_dependencies() -> None:
    """The AWS archive reuses EMR's Spark and Arrow installations."""
    buildspec = _python_environment_buildspec()
    phases = buildspec["phases"]
    assert isinstance(phases, dict)
    build = phases["build"]
    assert isinstance(build, dict)
    assert build["on-failure"] == "ABORT"
    commands = build["commands"]
    assert isinstance(commands, list)
    command_text = "\n".join(commands)

    assert "pip install ./receipt_langsmith venv-pack" in command_text
    assert "version('receipt-langsmith')" in command_text
    assert "import receipt_langsmith" not in command_text
    assert "find_spec('pyspark') is None" in command_text
    assert "find_spec('pyarrow') is None" in command_text
    assert "include-system-site-packages = true" in command_text
    assert "receipt_langsmith[pyspark]" not in command_text
    assert "receipt_langsmith[emr]" not in command_text


def test_local_spark_extra_matches_emr_runtime() -> None:
    """Local and CI installs include the Spark dependencies AWS provides."""
    package_config = tomllib.loads(
        (REPO_ROOT / "receipt_langsmith" / "pyproject.toml").read_text()
    )
    local_spark_dependencies = package_config["project"][
        "optional-dependencies"
    ]["pyspark"]

    assert package_config["project"]["requires-python"] == ">=3.13"
    assert f"pyspark=={EMR_SPARK_VERSION}" in local_spark_dependencies
    assert any(
        dependency.startswith("pyarrow")
        for dependency in local_spark_dependencies
    )

    workflow = (REPO_ROOT / ".github" / "workflows" / "main.yml").read_text()
    assert 'pip install -e "receipt_langsmith[pyspark,dev]"' in workflow
