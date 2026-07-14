"""Tests for the shared Docker image CodeBuild buildspec."""

from infra.shared.buildspecs import docker_image_buildspec


def test_buildspec_updates_every_declared_lambda() -> None:
    buildspec = docker_image_buildspec(
        build_args={},
        platform="linux/arm64",
        debug_mode=False,
    )

    post_build = "\n".join(buildspec["phases"]["post_build"]["commands"])
    assert 'FUNCTION_NAMES="$LAMBDA_FUNCTION_NAMES"' in post_build
    assert "for FUNCTION_NAME in $FUNCTION_NAMES" in post_build
    assert (
        'update-function-code --function-name "$FUNCTION_NAME"' in post_build
    )
