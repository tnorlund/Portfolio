"""Tests for the shared EMR Serverless Spark runtime settings."""

from infra.components.emr_serverless_analytics import (
    _python_environment_buildspec,
)
from infra.components.emr_serverless_runtime import (
    EMR_PYTHON_VERSION,
    EMR_SPARK_RELEASE,
    EMR_SPARK_VERSION,
    spark_runtime_properties,
)


def test_emr_runtime_targets_spark_8_and_python_313() -> None:
    assert EMR_SPARK_RELEASE == "emr-spark-8.0.0"
    assert EMR_SPARK_VERSION == "4.0.2"
    assert EMR_PYTHON_VERSION == "3.13"


def test_spark_runtime_uses_packaged_python_environment() -> None:
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
    buildspec = _python_environment_buildspec()
    phases = buildspec["phases"]
    assert isinstance(phases, dict)
    build = phases["build"]
    assert isinstance(build, dict)
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
