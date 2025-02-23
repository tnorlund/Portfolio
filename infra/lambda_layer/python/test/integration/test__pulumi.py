import pytest
from pulumi import automation as auto

# Adjust this import to point to where your code lives
from dynamo.data._pulumi import load_env


def test_load_env_happy_path(mocker):
    """
    Test that load_env returns a dictionary of {key: value},
    given stack outputs with 'value' attributes.
    """
    # 1. Create a mock for the Stack object
    mock_stack = mocker.MagicMock()

    # 2. Define a fake output dictionary from Pulumi's perspective:
    #    { "someKey": OutputValue(value="someValue"), "otherKey": OutputValue(value=123) }
    #    Since we only need the .value attribute, each “val” can be a simple mock with .value
    mock_stack.outputs.return_value = {
        "someKey": mocker.MagicMock(value="someValue"),
        "otherKey": mocker.MagicMock(value=123),
    }

    # 3. Patch auto.select_stack to return our mock stack
    mocker.patch.object(auto, "select_stack", return_value=mock_stack)

    # 4. Call load_env
    result = load_env(env="dev")

    # 5. Assertions
    auto.select_stack.assert_called_once_with(
        stack_name="tnorlund/portfolio/dev",
        project_name="portfolio",
        program=mocker.ANY,
    )

    assert isinstance(result, dict), "Expected a dictionary of stack outputs"
    assert result == {
        "someKey": "someValue",
        "otherKey": 123,
    }, "Output dictionary should match the mocked Pulumi outputs"


@pytest.mark.integration
def test_load_env_empty_outputs(mocker):
    """
    Test that load_env gracefully returns an empty dictionary when the stack has no outputs.
    """
    mock_stack = mocker.MagicMock()
    # Mock an empty outputs dict
    mock_stack.outputs.return_value = {}

    mocker.patch.object(auto, "select_stack", return_value=mock_stack)

    result = load_env("dev")
    assert isinstance(result, dict)
    assert result == {}, "Expected an empty dictionary when stack has no outputs"


def test_load_env_nonexistent_stack(mocker):
    """
    Test behavior if selecting a stack fails (e.g., stack doesn't exist or credentials are invalid).
    This might raise an exception, which we can verify.
    """
    # Make select_stack raise an exception (e.g., PulumiException)
    mocker.patch.object(
        auto,
        "select_stack",
        side_effect=Exception("Stack not found or credentials invalid"),
    )

    # Depending on how you want to handle this scenario, you can catch
    # the exception or let it bubble up. Here, we confirm it raises.
    with pytest.raises(Exception, match="Stack not found or credentials invalid"):
        load_env("dev")
