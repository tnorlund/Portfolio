#!/usr/bin/env python3
"""
Shared buildspec generators for Lambda layers and CodeBuild Docker images.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# pylint: disable=unused-argument


def lambda_layer_buildspec(
    *,
    version: Optional[str],
    python_versions: List[str],
    package_extras: Optional[str],
    needs_pillow: bool,
    package_name: str,
    layer_name: str,
    debug_mode: bool,
) -> Dict[str, Any]:
    """
    Buildspec for Lambda layer builds.

    If ``version`` is provided, targets a single Python runtime; otherwise uses all
    ``python_versions``.
    """
    versions = [version] if version else python_versions
    primary = versions[0]
    extras = f"[{package_extras}]" if package_extras else ""

    install_commands = [
        "echo Installing native libraries for Pillow…",
        "dnf install -y libjpeg-turbo libpng libtiff libwebp freetype lcms2 zlib",
        "pip install build",
        'echo "Setting up pip error handling..."',
        "set -e",
    ]

    artifacts: Dict[str, Any]

    if version:
        build_commands = [
            "echo Build directory prep",
            (
                '[ "$DEBUG_MODE" = "True" ] && echo '
                '"DEBUG: Starting build for Python ${PYTHON_VERSION}" || true'
            ),
            (
                '[ "$DEBUG_MODE" = "True" ] && echo "DEBUG: Current directory:" '
                "&& pwd || true"
            ),
            (
                '[ "$DEBUG_MODE" = "True" ] && echo "DEBUG: Directory contents:" '
                "&& ls -la || true"
            ),
            "echo Checking source structure:",
            "ls -la source/ || echo 'source directory not found'",
            "ls -la source/pyproject.toml || echo 'pyproject.toml not found in source'",
            (
                'if [ -d "dependencies" ]; then '
                'echo "Found local dependencies, building them first..."; '
                "mkdir -p dep_wheels; "
                "for dep_dir in dependencies/*; do "
                'if [ -d "$dep_dir" ]; then '
                'dep_name=$(basename "$dep_dir"); '
                'echo "  - Building $dep_name"; '
                'cd "$dep_dir"; '
                "python3 -m build --wheel --outdir ../../dep_wheels/; "
                "cd ../../; "
                "fi; "
                "done; "
                'echo "Installing local dependency wheels..."; '
                f"python{version} -m pip install --no-cache-dir dep_wheels/*.whl "
                f"-t build/python/lib/python{version}/site-packages || true; "
                "fi"
            ),
            "rm -rf build && mkdir -p build",
            f"mkdir -p build/python/lib/python{version}/site-packages",
            'echo "Building main package wheel"',
            "cd source && python3 -m build --wheel --outdir ../dist/ && cd ..",
            'echo "Installing wheel with optimization exclusions for Lambda layer"',
            'echo "Finding wheel file..."',
            "WHEEL_FILE=$(ls dist/*.whl | head -1)",
            'echo "Found wheel: $WHEEL_FILE"',
            (
                f"python{version} -m pip install --no-cache-dir "
                "--find-links dep_wheels "
                f'"$WHEEL_FILE{extras}" '
                f"-t build/python/lib/python{version}/site-packages || "
                '{ echo "First pip install attempt failed, retrying..."; '
                f"python{version} -m pip install --no-cache-dir "
                "--find-links dep_wheels "
                f'"$WHEEL_FILE{extras}" '
                f"-t build/python/lib/python{version}/site-packages; }}"
            ),
            'if [ "$NEEDS_PILLOW" = "True" ]; then '
            'echo "Installing Pillow before flattening"; '
            f"python{version} -m pip install --no-cache-dir Pillow "
            f"-t build/python/lib/python{version}/site-packages; "
            "fi",
            'echo "Removing packages provided by AWS Lambda runtime"',
            "rm -rf build/python/lib/python*/site-packages/boto* || true",
            "rm -rf build/python/lib/python*/site-packages/*boto* || true",
            "rm -rf build/python/lib/python*/site-packages/dateutil* || true",
            "rm -rf build/python/lib/python*/site-packages/jmespath* || true",
            "rm -rf build/python/lib/python*/site-packages/s3transfer* || true",
            "rm -rf build/python/lib/python*/site-packages/six* || true",
            'echo "Cleaning up unnecessary files from all packages"',
            (
                "find build -type d -name '__pycache__' -exec rm -rf {} + "
                "2>/dev/null || true"
            ),
            (
                "find build -type d -name 'tests' -o -name 'test' -exec rm -rf "
                "{} + 2>/dev/null || true"
            ),
            (
                "find build -type f -name '*.pyc' -o -name '*.pyo' -exec rm -f "
                "{} + 2>/dev/null || true"
            ),
            (
                "find build -type f \\( -name '*.md' -o -name '*.yml' "
                "-o -name '*.yaml' -o -name '*.rst' \\) "
                "-not -path '*/dist-info/*' -exec rm -f {} + 2>/dev/null || true"
            ),
            (
                "find build -type f -name '*.dist-info/RECORD' -exec sed -i "
                "'/\\.pyc/d' {} + 2>/dev/null || true"
            ),
            'echo "Copying native libraries"',
            "mkdir -p build/lib && cp /usr/lib64/libjpeg*.so* "
            "/usr/lib64/libpng*.so* /usr/lib64/libtiff*.so* "
            "/usr/lib64/libwebp*.so* /usr/lib64/liblcms2*.so* "
            "/usr/lib64/libfreetype*.so* build/lib || true",
            'echo "Flattening site-packages to root python directory"',
            "cp -r build/python/lib/python*/site-packages/. build/python/ || true",
            'echo "Removing nested lib directory after flattening"',
            "rm -rf build/python/lib || true",
            'echo "Final cleanup after flattening"',
            "rm -rf build/python/boto* || true",
            "rm -rf build/python/*boto* || true",
            "rm -rf build/python/dateutil* || true",
            "rm -rf build/python/jmespath* || true",
            "rm -rf build/python/s3transfer* || true",
            "rm -rf build/python/six* || true",
            'echo "Zipping layer"',
            "cd build && zip -qr layer.zip python lib || true",
            'echo "Layer zip contents:"',
            "unzip -l build/layer.zip | head -n 20",
            'echo "Done building layer for Python version ${PYTHON_VERSION}"',
        ]

        artifacts = {"files": ["layer.zip"]}
    else:
        build_commands = [
            'echo "Building in multi-version mode"',
            'echo "NEEDS_PILLOW=${NEEDS_PILLOW}"',
            "rm -rf build && mkdir -p build",
        ]
        for v in versions:
            build_commands.extend(
                [
                    f'echo "Building for Python {v}..."',
                    (
                        "mkdir -p "
                        f"build/python{v.replace('.', '')}/python/lib/python{v}"
                        "/site-packages"
                    ),
                    'if [ -d "dependencies" ]; then '
                    'echo "Found local dependencies, building them first..."; '
                    "mkdir -p dep_wheels; "
                    "for dep_dir in dependencies/*; do "
                    'if [ -d "$dep_dir" ]; then '
                    'dep_name=$(basename "$dep_dir"); '
                    'echo "  - Building $dep_name"; '
                    'cd "$dep_dir"; '
                    "python3 -m build --wheel --outdir ../../dep_wheels/; "
                    "cd ../../; "
                    "fi; "
                    "done; "
                    'echo "Installing local dependency wheels..."; '
                    f"python{v} -m pip install --no-cache-dir dep_wheels/*.whl "
                    f"-t build/python{v.replace('.', '')}/python/lib/"
                    f"python{v}/site-packages || true; "
                    "fi",
                    'echo "Building main package wheel"',
                    "cd source && python3 -m build --wheel --outdir ../dist/ && cd ..",
                    'echo "Installing wheel with optimization exclusions for Lambda layer"',
                    "WHEEL_FILE=$(ls dist/*.whl | head -1)",
                    (
                        f"python{v} -m pip install --no-cache-dir "
                        "--find-links dep_wheels "
                        f'"$WHEEL_FILE{extras}" '
                        f"-t build/python{v.replace('.', '')}/python/lib/"
                        f"python{v}/site-packages || "
                        '{ echo "First pip install attempt failed, retrying..."; '
                        f"python{v} -m pip install --no-cache-dir "
                        "--find-links dep_wheels "
                        f'"$WHEEL_FILE{extras}" '
                        f"-t build/python{v.replace('.', '')}/python/lib/"
                        f"python{v}/site-packages; }}"
                    ),
                    'if [ "$NEEDS_PILLOW" = "True" ]; then ',
                    'echo "Installing Pillow before flattening"; ',
                    (
                        f"python{v} -m pip install --no-cache-dir Pillow "
                        f"-t build/python{v.replace('.', '')}/python/lib/"
                        f"python{v}/site-packages; "
                    ),
                    "fi",
                    (
                        "rm -rf build/python{0}/python/lib/"
                        "python*/site-packages/boto* || true"
                    ).format(v.replace(".", "")),
                    (
                        "rm -rf build/python{0}/python/lib/"
                        "python*/site-packages/*boto* || true"
                    ).format(v.replace(".", "")),
                    (
                        "rm -rf build/python{0}/python/lib/"
                        "python*/site-packages/dateutil* || true"
                    ).format(v.replace(".", "")),
                    (
                        "rm -rf build/python{0}/python/lib/"
                        "python*/site-packages/jmespath* || true"
                    ).format(v.replace(".", "")),
                    (
                        "rm -rf build/python{0}/python/lib/"
                        "python*/site-packages/s3transfer* || true"
                    ).format(v.replace(".", "")),
                    (
                        "rm -rf build/python{0}/python/lib/"
                        "python*/site-packages/six* || true"
                    ).format(v.replace(".", "")),
                    'echo "Cleaning up unnecessary files from all packages"',
                    (
                        "find build -type d -name '__pycache__' -exec rm -rf {} + "
                        "2>/dev/null || true"
                    ),
                    (
                        "find build -type d -name 'tests' -o -name 'test' -exec rm -rf "
                        "{} + 2>/dev/null || true"
                    ),
                    (
                        "find build -type f -name '*.pyc' -o -name '*.pyo' -exec rm -f "
                        "{} + 2>/dev/null || true"
                    ),
                    (
                        "find build -type f \\( -name '*.md' -o -name '*.yml' "
                        "-o -name '*.yaml' -o -name '*.rst' \\) "
                        "-not -path '*/dist-info/*' -exec rm -f {} + 2>/dev/null || true"
                    ),
                    (
                        "find build -type f -name '*.dist-info/RECORD' -exec sed -i "
                        "'/\\.pyc/d' {} + 2>/dev/null || true"
                    ),
                    'echo "Flattening site-packages to root python directory"',
                    (
                        f"cp -r build/python{v.replace('.', '')}/python/lib/"
                        f"python*/site-packages/. "
                        f"build/python{v.replace('.', '')}/python/ || true"
                    ),
                    'echo "Removing nested lib directory after flattening"',
                    (
                        f"rm -rf build/python{v.replace('.', '')}/python/lib || true"
                    ),
                ]
            )
        build_commands.append('echo "Done building for all Python versions"')
        artifacts = {
            "files": ["python*/**/*"],
            "base-directory": "build",
        }

    return {
        "version": 0.2,
        "phases": {
            "pre_build": {
                "commands": [
                    "echo Checking ARG_MAX",
                    "ARG_LIMIT=$(getconf ARG_MAX)",
                    'echo "ARG_MAX is $ARG_LIMIT"',
                ]
            },
            "install": {
                "runtime-versions": {"python": primary},
                "commands": install_commands,
            },
            "build": {"commands": build_commands},
        },
        "artifacts": artifacts,
    }


def docker_image_buildspec(
    *,
    build_args: Dict[str, str],
    platform: str,
    lambda_function_name: Optional[str],
    debug_mode: bool,
    base_image_uri: Optional[str] = None,  # Resolved string value (not pulumi.Output)
) -> Dict[str, Any]:
    """Buildspec for CodeBuild Docker image builds."""
    build_args_str = " ".join(
        [f"--build-arg {k}={v}" for k, v in build_args.items()]
    )
    platform_flag = f"--platform {platform}" if platform else ""
    
    # Add BASE_IMAGE_URI as a build arg if provided
    if base_image_uri:
        # Note: base_image_uri will be resolved by Pulumi before creating the buildspec
        # and will be available as an environment variable in CodeBuild
        build_args_str += " --build-arg BASE_IMAGE_URI=$BASE_IMAGE_URI"

    return {
        "version": 0.2,
        "phases": {
            "pre_build": {
                "commands": [
                    "echo Logging in to Amazon ECR...",
                    (
                        "aws ecr get-login-password --region $AWS_DEFAULT_REGION | "
                        "docker login --username AWS --password-stdin $ECR_REGISTRY"
                    ),
                    "echo Listing current directory...",
                    "ls -la",
                    "echo Entering context directory...",
                    "cd context",
                    "ls -la",
                ]
            },
            "build": {
                "commands": [
                    "echo Build started on `date`",
                    "echo Building Docker image with multi-stage caching...",
                    "export DOCKER_BUILDKIT=1",
                    "export CACHE_BUST=$(date +%s)",
                    f"docker build {platform_flag} {build_args_str} "
                    f"--cache-from $ECR_REGISTRY/$REPOSITORY_NAME:cache "
                    f"--build-arg BUILDKIT_INLINE_CACHE=1 "
                    f"--build-arg CACHE_BUST=$CACHE_BUST "
                    f"-t $ECR_REGISTRY/$REPOSITORY_NAME:$IMAGE_TAG "
                    f"-t $ECR_REGISTRY/$REPOSITORY_NAME:latest "
                    f"-f Dockerfile .",
                    "echo Build completed on `date`",
                ]
            },
            "post_build": {
                "commands": [
                    "echo Pushing Docker image to ECR...",
                    "docker push $ECR_REGISTRY/$REPOSITORY_NAME:$IMAGE_TAG",
                    "docker push $ECR_REGISTRY/$REPOSITORY_NAME:latest",
                    (
                        "docker tag $ECR_REGISTRY/$REPOSITORY_NAME:latest "
                        "$ECR_REGISTRY/$REPOSITORY_NAME:cache"
                    ),
                    "docker push $ECR_REGISTRY/$REPOSITORY_NAME:cache || true",
                    "echo Getting image digest...",
                    (
                        "IMAGE_DIGEST=$(docker inspect --format='{{index .RepoDigests 0}}' "
                        "$ECR_REGISTRY/$REPOSITORY_NAME:latest | cut -d'@' -f2)"
                    ),
                    (
                        '[ -n "$IMAGE_DIGEST" ] || '
                        '{ echo "ERROR: image digest missing"; exit 1; }'
                    ),
                    "IMAGE_URI=$ECR_REGISTRY/$REPOSITORY_NAME@$IMAGE_DIGEST",
                    "echo Image URI: $IMAGE_URI",
                    (
                        'if [ -n "$LAMBDA_FUNCTION_NAME" ]; then '
                        'echo "Checking if Lambda function $LAMBDA_FUNCTION_NAME exists..." && '
                        'if aws lambda get-function --function-name "$LAMBDA_FUNCTION_NAME" '
                        ">/dev/null 2>&1; then "
                        'echo "Updating existing Lambda function..." && '
                        "aws lambda update-function-code --function-name "
                        '"$LAMBDA_FUNCTION_NAME" --image-uri "$IMAGE_URI" >/dev/null && '
                        'echo "✅ Lambda function updated"; else '
                        'echo "Lambda function does not exist - will be created by Pulumi"; '
                        "fi; fi"
                    ),
                    "echo Push completed on `date`",
                ]
            },
        },
    }
