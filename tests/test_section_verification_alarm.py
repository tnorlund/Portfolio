"""Verify the alarm consumes the upload handler's table-scoped EMF metric."""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("table", ["receipts-dev", "receipts-prod"])
def test_section_alarm_is_scoped_to_its_stack_table(table):
    """A failure in one environment must not page the other environment."""
    source = Path(__file__).resolve().parents[1] / "infra/__main__.py"
    program = ast.parse(source.read_text())
    declaration = next(
        node
        for node in program.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name)
            and target.id == "upload_section_verification_alarm"
            for target in node.targets
        )
    )
    # Evaluate only the alarm constructor with inert resources, never the
    # Pulumi program or an AWS provider. This checks the actual declaration.
    namespace = {
        "aws": SimpleNamespace(
            cloudwatch=SimpleNamespace(
                MetricAlarm=lambda name, **config: {"name": name, **config}
            )
        ),
        "dynamodb_table": SimpleNamespace(name=table),
        "notification_system": SimpleNamespace(
            critical_error_topic_arn="test-critical-topic"
        ),
        "stack": table.removeprefix("receipts-"),
    }
    exec(  # pylint: disable=exec-used
        compile(
            ast.Module(body=[declaration], type_ignores=[]), source, "exec"
        ),
        namespace,
    )
    alarm = namespace["upload_section_verification_alarm"]
    assert alarm["namespace"] == "EmbeddingWorkflow"
    assert alarm["metric_name"] == "UploadLambdaSectionVerificationError"
    assert alarm["dimensions"] == {"TableName": table}
    assert alarm["statistic"] == "Sum"
    assert alarm["comparison_operator"] == "GreaterThanThreshold"
    assert alarm["threshold"] == 0
    assert alarm["evaluation_periods"] == 1
    assert alarm["treat_missing_data"] == "notBreaching"
    assert alarm["alarm_actions"] == ["test-critical-topic"]
