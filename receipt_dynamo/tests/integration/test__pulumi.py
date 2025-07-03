import json
import subprocess

import pytest

# Adjust this import to point to where your code lives
from receipt_dynamo.data._pulumi import load_env


def test_load_env_happy_path(mocker):
    """
    Test that load_env returns a dictionary of {key: value},
    given a valid Pulumi stack output.
    """
    # Mock subprocess.run to simulate Pulumi CLI output
    mock_subprocess = mocker.patch("subprocess.run")
    mock_subprocess.return_value.stdout = json.dumps(
        {"someKey": "someValue", "otherKey": 123}
    )

    result = load_env("dev")

    # Ensure the subprocess was called correctly
    mock_subprocess.assert_called_once_with(
        [
            "pulumi",
            "stack",
            "output",
            "--stack",
            "tnorlund/portfolio/dev",
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    # Validate the expected output
    assert isinstance(result, dict), "Expected a dictionary of stack outputs"
    assert result == {
        "someKey": "someValue",
        "otherKey": 123,
    }, "Output dictionary should match the mocked Pulumi CLI output"


@pytest.mark.integration
def test_load_env_empty_outputs(mocker):
    """
    Test that load_env gracefully returns an empty dictionary when the stack
    has no outputs.
    """
    mock_subprocess = mocker.patch("subprocess.run")
    mock_subprocess.return_value.stdout = json.dumps(
        {}
    )  # Simulate empty stack output

    result = load_env("dev")

    assert isinstance(result, dict)
    assert (
        result == {}
    ), "Expected an empty dictionary when stack has no outputs"


def test_load_env_nonexistent_stack(mocker):
    """
    Test behavior if Pulumi CLI fails (e.g., stack doesn't exist or
    credentials are invalid).
    """
    # Mock subprocess.run to raise CalledProcessError (which Pulumi would do
    # if the stack isn't found)
    mock_subprocess = mocker.patch("subprocess.run")
    mock_subprocess.side_effect = subprocess.CalledProcessError(
        returncode=1,
        cmd="pulumi stack output --json",
        output="",
        stderr="Stack not found",
    )

    result = load_env("dev")

    assert isinstance(result, dict)
    assert (
        result == {}
    ), "Expected an empty dictionary when stack selection fails"


@pytest.mark.integration
def test_load_env_invalid_json(mocker):
    """
    Test behavior if Pulumi CLI returns malformed JSON.
    """
    mock_subprocess = mocker.patch("subprocess.run")
    mock_subprocess.return_value.stdout = (
        "INVALID_JSON"  # Simulate corrupted JSON output
    )

    result = load_env("dev")

    assert isinstance(result, dict)
    assert result == {}, "Expected an empty dictionary when JSON parsing fails"
