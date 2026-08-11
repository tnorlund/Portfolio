"""Shared runtime settings for EMR Serverless Spark jobs."""

from __future__ import annotations

EMR_SPARK_RELEASE = "emr-spark-8.0.0"
EMR_SPARK_VERSION = "4.0.2"
EMR_PYTHON_VERSION = "3.13"
PYTHON_ENVIRONMENT_NAME = "environment"


def spark_runtime_properties(
    python_environment_uri: str,
) -> dict[str, str]:
    """Return application-wide Spark settings for the packaged environment."""
    environment_python = f"./{PYTHON_ENVIRONMENT_NAME}/bin/python"
    return {
        "spark.archives": (
            f"{python_environment_uri}#{PYTHON_ENVIRONMENT_NAME}"
        ),
        "spark.emr-serverless.driverEnv.PYSPARK_DRIVER_PYTHON": (
            environment_python
        ),
        "spark.emr-serverless.driverEnv.PYSPARK_PYTHON": (environment_python),
        "spark.executorEnv.PYSPARK_PYTHON": environment_python,
        # Spark 4 enables ANSI mode by default. Preserve the Spark 3 behavior
        # while existing semi-structured trace casts are migrated separately.
        "spark.sql.ansi.enabled": "false",
    }
