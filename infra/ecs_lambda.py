#!/usr/bin/env python3
"""
ecs_lambda.py

Hybrid Lambda Function component similar to lambda_layer.py, but for Lambda code.

Goals:
- Fast `pulumi up` (async by default) by offloading packaging to AWS CodeBuild
- Simple architecture: S3 (source), CodePipeline → CodeBuild → update Lambda code
- Optionally wait in sync mode (useful in CI) to ensure function updated
- Supports including local `receipt-*` monorepo packages into the build
- Avoids Step Functions/SQS; keeps infra simple and debuggable

Usage pattern mirrors `LambdaLayer` but targets function packaging and updates.
"""

import base64
import glob
import hashlib
import json
import os
import shlex
from typing import Any, Dict, List, Optional

import pulumi
import pulumi_aws as aws
import pulumi_command as command
from pulumi import AssetArchive, ComponentResource, Output, ResourceOptions, StringAsset
from utils import _find_project_root


PROJECT_DIR = _find_project_root()


class EcsLambda(ComponentResource):
    """AWS-offloaded Lambda function builder/deployer using CodeBuild/CodePipeline.

    This component handles:
    - Zipping your sources (with optional local monorepo deps) and uploading to S3
    - Running CodeBuild to assemble `function.zip` (pip installs, optional wheels)
    - Updating the Lambda code with `aws lambda update-function-code`

    By default, `pulumi up` is fast (async). For CI/CD, enable sync to wait.
    """

    def __init__(
        self,
        name: str,
        *,
        package_dir: str,
        handler: str,
        python_version: str = "3.12",
        description: Optional[str] = None,
        role_arn: Optional[Output[str] | str] = None,
        timeout: int = 30,
        memory_size: int = 512,
        environment: Optional[Dict[str, str]] = None,
        layers: Optional[List[Output[str] | str]] = None,
        package_extras: Optional[str] = None,  # e.g., "lambda" to install `pkg[lambda]`
        sync_mode: Optional[bool] = None,
        opts: Optional[ResourceOptions] = None,
    ) -> None:
        super().__init__(f"ecs-lambda:{name}", name, {}, opts)

        self.name = name
        self.package_dir = package_dir
        self.handler = handler  # e.g., "my_module.lambda_handler"
        self.python_version = python_version
        self.description = description or f"AWS-built Lambda function for {name}"
        self.role_arn = role_arn
        self.timeout = timeout
        self.memory_size = memory_size
        self.environment = environment or {}
        self.layers = layers or []
        self.package_extras = package_extras

        # Configure build mode
        if sync_mode is not None:
            self.sync_mode = sync_mode
        else:
            config = pulumi.Config("ecs-lambda")
            if config.get_bool("sync-mode"):
                self.sync_mode = True
            elif os.getenv("CI") or os.getenv("GITHUB_ACTIONS"):
                self.sync_mode = True
            else:
                self.sync_mode = False

        # Additional config flags
        config = pulumi.Config("ecs-lambda")
        self.force_rebuild = config.get_bool("force-rebuild") or False
        self.debug_mode = config.get_bool("debug-mode") or False

        # Validate input source directory and compute change hash
        self._validate_package_dir()
        self.package_path = os.path.join(PROJECT_DIR, self.package_dir)
        package_hash = self._calculate_package_hash()

        if self.sync_mode:
            pulumi.log.info(
                f"🔄 Building function '{self.name}' in SYNC mode (will wait)"
            )
        else:
            pulumi.log.info(
                f"⚡ Function '{self.name}' in ASYNC mode (fast pulumi up)"
            )
            if self.force_rebuild:
                pulumi.log.info("   🔨 Force rebuild enabled - will trigger build")
            else:
                pulumi.log.info(
                    f"   📦 Hash: {package_hash[:12]}... - will build only if changed"
                )

        # Infra setup
        (
            build_bucket,
            upload_cmd,
            pipeline,
            publish_project,
            codebuild_role,
        ) = self._setup_pipeline(package_hash)

        # Create the Lambda function (with small placeholder code), then let pipeline update code
        self.function_name = f"{self.name}-{pulumi.get_stack()}"
        placeholder_code = self._make_placeholder_code(self.handler)

        # Lambda role: allow passing explicit role; otherwise create a basic one
        if self.role_arn is None:
            assume = json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "lambda.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }
            )
            role = aws.iam.Role(
                f"{self.name}-lambda-role",
                assume_role_policy=assume,
                opts=pulumi.ResourceOptions(parent=self),
            )
            aws.iam.RolePolicyAttachment(
                f"{self.name}-lambda-basic-exec",
                role=role.name,
                policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
                opts=pulumi.ResourceOptions(parent=self),
            )
            self.role_arn = role.arn

        # Create function with placeholder code; ignore code updates from Pulumi afterwards
        self.function = aws.lambda_.Function(
            f"{self.name}-function",
            name=self.function_name,
            runtime=f"python{self.python_version}",
            role=self.role_arn,
            handler=self.handler,
            code=placeholder_code,
            timeout=self.timeout,
            memory_size=self.memory_size,
            environment=aws.lambda_.FunctionEnvironmentArgs(variables=self.environment)
            if self.environment
            else None,
            layers=self.layers if self.layers else None,
            description=self.description,
            opts=pulumi.ResourceOptions(
                parent=self,
                ignore_changes=["code", "image_uri", "package_type"],
                depends_on=[upload_cmd, pipeline],
            ),
        )

        # Optionally trigger pipeline in async mode
        if not self.sync_mode:
            trigger_script = pipeline.name.apply(
                lambda pn: f"""#!/usr/bin/env bash
set -e
echo "🔄 Changes detected, starting CodePipeline execution for {self.name}"
EXEC_ID=$(aws codepipeline start-pipeline-execution --name {pn} --query pipelineExecutionId --output text)
echo "Triggered pipeline: $EXEC_ID"
"""
            )
            command.local.Command(
                f"{self.name}-trigger-pipeline",
                create=trigger_script,
                update=trigger_script,
                triggers=[package_hash],
                opts=pulumi.ResourceOptions(
                    parent=self, depends_on=[upload_cmd, pipeline]
                ),
            )
        else:
            # Sync: run pipeline and wait for completion before finishing
            sync_script = pipeline.name.apply(
                lambda pn: f"""#!/usr/bin/env bash
set -e
echo "🔄 Sync: Starting CodePipeline execution for {self.name}"
EXEC_ID=$(aws codepipeline start-pipeline-execution --name {pn} --query pipelineExecutionId --output text)
echo "Execution ID: $EXEC_ID"
sleep 2
while true; do
  STATUS=$(aws codepipeline get-pipeline-execution --pipeline-name {pn} --pipeline-execution-id $EXEC_ID --query "pipelineExecution.status" --output text)
  echo "🔄 Pipeline status: $STATUS"
  if [ "$STATUS" = "Succeeded" ]; then
    echo "✅ Pipeline completed successfully"
    break
  elif [ "$STATUS" = "Failed" ] || [ "$STATUS" = "Superseded" ]; then
    echo "❌ Pipeline failed with status: $STATUS"
    exit 1
  fi
  sleep 10
done
"""
            )
            command.local.Command(
                f"{self.name}-sync-pipeline",
                create=sync_script,
                update=sync_script,
                triggers=[package_hash],
                opts=pulumi.ResourceOptions(
                    parent=self, depends_on=[upload_cmd, pipeline]
                ),
            )

        # Export useful outputs
        self.arn = self.function.arn
        self.invoke_arn = self.function.invoke_arn

    # ---------------------------- Helpers ---------------------------- #

    def _validate_package_dir(self) -> None:
        src_path = os.path.join(PROJECT_DIR, self.package_dir)
        if not os.path.exists(src_path):
            raise ValueError(f"Package directory {src_path} does not exist")
        # Accept any Python files; `pyproject.toml` and/or `requirements.txt` are optional
        py_files = glob.glob(os.path.join(src_path, "**/*.py"), recursive=True)
        if not py_files:
            raise ValueError(f"Package directory {src_path} contains no Python files")

    def _calculate_package_hash(self) -> str:
        h = hashlib.sha256()
        src_path = os.path.join(PROJECT_DIR, self.package_dir)
        files_to_hash: List[str] = []
        for root, _, files in os.walk(src_path):
            for f in files:
                if f.endswith(".py") or f in ("pyproject.toml", "requirements.txt"):
                    files_to_hash.append(os.path.join(root, f))
        for fp in sorted(files_to_hash):
            with open(fp, "rb") as fin:
                h.update(fin.read())
            relp = os.path.relpath(fp, src_path)
            h.update(relp.encode())
        return h.hexdigest()

    def _get_local_dependencies(self) -> List[str]:
        """Find monorepo local packages referenced in pyproject optional extras.

        Matches `receipt-*` and converts to `_` folder names (e.g., receipt-label → receipt_label).
        """
        src_path = os.path.join(PROJECT_DIR, self.package_dir)
        pyproject_path = os.path.join(src_path, "pyproject.toml")
        local_deps: List[str] = []
        if os.path.exists(pyproject_path):
            try:
                try:
                    import tomllib  # pyright: ignore[reportMissingImports]

                    with open(pyproject_path, "rb") as f:
                        data = tomllib.load(f)
                except Exception:
                    import toml  # type: ignore

                    with open(pyproject_path, "r") as f:
                        data = toml.load(f)

                deps = data.get("project", {}).get("dependencies", [])
                if self.package_extras:
                    optional = data.get("project", {}).get("optional-dependencies", {})
                    deps.extend(optional.get(self.package_extras, []))

                for dep in deps:
                    dep_name = (
                        dep.split("[")[0]
                        .split(">")[0]
                        .split("<")[0]
                        .split("=")[0]
                        .strip()
                    )
                    if dep_name.startswith("receipt-"):
                        dir_name = dep_name.replace("-", "_")
                        local_path = os.path.join(PROJECT_DIR, dir_name)
                        if os.path.exists(local_path):
                            local_deps.append(dir_name)
                            pulumi.log.info(f"📦 Found local dependency: {dir_name} for {self.name}")
            except Exception as e:  # pylint: disable=broad-exception-caught
                pulumi.log.warn(f"Could not parse pyproject.toml for deps: {e}")
        return local_deps

    def _encode(self, script: str) -> str:
        return base64.b64encode(script.encode("utf-8")).decode("utf-8")

    def _generate_upload_script(self, bucket: str, package_hash: str) -> str:
        safe_bucket = shlex.quote(bucket)
        safe_src = shlex.quote(self.package_path)
        safe_project_root = shlex.quote(str(PROJECT_DIR))
        local_deps = self._get_local_dependencies()
        return f"""#!/usr/bin/env bash
set -e

BUCKET={safe_bucket}
SRC={safe_src}
HASH="{package_hash}"
NAME="{self.name}"
FORCE_REBUILD="{self.force_rebuild}"
LOCAL_DEPS="{' '.join(local_deps)}"
BASE_DIR={safe_project_root}

echo "📦 Checking if source upload needed for function '$NAME'..."
STORED_HASH=$(aws s3 cp "s3://$BUCKET/$NAME/hash.txt" - 2>/dev/null || echo '')
if [ "$STORED_HASH" = "$HASH" ] && [ "$FORCE_REBUILD" != "True" ]; then
  echo "✅ Source up-to-date. Skipping upload."
  exit 0
fi

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/source"
cp -r "$SRC"/* "$TMP/source/"

if [ -n "$LOCAL_DEPS" ]; then
  echo "Including local dependencies: $LOCAL_DEPS"
  mkdir -p "$TMP/dependencies"
  for dep in $LOCAL_DEPS; do
    DEP_PATH="$BASE_DIR/$dep"
    if [ -d "$DEP_PATH" ]; then
      mkdir -p "$TMP/dependencies/$dep"
      cp -r "$DEP_PATH"/* "$TMP/dependencies/$dep/"
    fi
  done
fi

cd "$TMP"
if [ -d dependencies ]; then
  zip -qr source.zip source dependencies
else
  zip -qr source.zip source
fi
cd - >/dev/null

aws s3 cp "$TMP/source.zip" "s3://$BUCKET/$NAME/source.zip"
echo -n "$HASH" | aws s3 cp - "s3://$BUCKET/$NAME/hash.txt"
echo "✅ Uploaded source.zip"
"""

    def _make_placeholder_code(self, handler: str) -> AssetArchive:
        # Create module file path and function name for the configured handler
        # Lambda handler is usually "module.function"; support dotted module path
        parts = handler.split(".")
        if len(parts) < 2:
            module_path = "index"
            func_name = handler
        else:
            module_path = "/".join(parts[:-1])
            func_name = parts[-1]
        file_path = f"{module_path}.py" if module_path else "index.py"
        content = (
            "import json\n"
            "def {}(event, context):\n"
            "    return {{'statusCode': 200, 'body': json.dumps({{'message': 'placeholder'}})}}\n".format(
                func_name
            )
        )
        return AssetArchive({file_path: StringAsset(content)})

    def _buildspec(self) -> Dict[str, Any]:
        v = self.python_version
        cmds: List[str] = []
        cmds.extend(
            [
                "echo Preparing function build...",
                "rm -rf build && mkdir -p build/package",
                # Debug
                '[ "$DEBUG_MODE" = "True" ] && echo "DEBUG: Listing source" && ls -la || true',
                # Build local dependencies first if present
                (
                    'if [ -d "dependencies" ]; then '
                    'echo "Building local dependency wheels..."; '
                    "mkdir -p dep_wheels; "
                    f"for d in dependencies/*; do if [ -d \"$d\" ]; then cd \"$d\"; python{v} -m build --wheel --outdir ../../dep_wheels/; cd - >/dev/null; fi; done; "
                    "fi"
                ),
                # Main install strategy: pyproject wheel if exists, else requirements.txt
                (
                    'if [ -f "source/pyproject.toml" ]; then '
                    'echo "Building source wheel"; '
                    f'cd source && python{v} -m build --wheel --outdir ../dist/ && cd ..; '
                    # Install built wheel with optional extras and local wheels first
                    f"WHEEL=$(ls dist/*.whl | head -1); "
                    f"python{v} -m pip install --no-cache-dir --find-links dep_wheels \"$WHEEL{f'[{self.package_extras}]' if self.package_extras else ''}\" -t build/package || "
                    f"python{v} -m pip install --no-cache-dir --find-links dep_wheels dist/*.whl -t build/package; "
                    'elif [ -f "source/requirements.txt" ]; then '
                    'echo "Installing from requirements.txt (using local wheels if present)"; '
                    f"python{v} -m pip install --no-cache-dir --find-links dep_wheels -r source/requirements.txt -t build/package; "
                    'fi'
                ),
                # Copy source files last to ensure they override site-packages collisions if any
                'echo "Copying source files"',
                'cp -r source/. build/package/',
                # Cleanup common large libs provided by Lambda runtime or not needed
                "find build/package -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true",
                # Zip
                'cd build/package && zip -qr ../function.zip . && cd - >/dev/null',
                # Upload to S3 and update Lambda
                'echo "Uploading function.zip to S3"',
                'aws s3 cp build/function.zip s3://$BUCKET_NAME/$PACKAGE_NAME/combined/function.zip',
                'echo "Updating Lambda function code"',
                'aws lambda update-function-code --function-name "$FUNCTION_NAME" --s3-bucket "$BUCKET_NAME" --s3-key "$PACKAGE_NAME/combined/function.zip" >/dev/null',
                'echo "Lambda code update completed"',
            ]
        )
        return {
            "version": 0.2,
            "phases": {
                "install": {
                    "runtime-versions": {"python": v},
                    "commands": [
                        f"python{v} -m pip install --upgrade pip build",
                        "set -e",
                    ],
                },
                "build": {"commands": cmds},
            },
            "artifacts": {"files": ["build/function.zip"]},
        }

    def _setup_pipeline(self, package_hash: str):
        # Artifact bucket
        build_bucket = aws.s3.Bucket(
            f"{self.name}-fn-artifacts",
            force_destroy=True,
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Explicit versioning resource (ordering)
        bucket_versioning = aws.s3.BucketVersioning(
            f"{self.name}-fn-artifacts-versioning",
            bucket=build_bucket.id,
            versioning_configuration=aws.s3.BucketVersioningVersioningConfigurationArgs(
                status="Enabled"
            ),
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Upload source command (idempotent thanks to hash tracking)
        upload_cmd = command.local.Command(
            f"{self.name}-fn-upload-source",
            create=build_bucket.bucket.apply(
                lambda b: self._generate_upload_script(b, package_hash)
            ),
            update=build_bucket.bucket.apply(
                lambda b: self._generate_upload_script(b, package_hash)
            ),
            triggers=[package_hash],
            opts=pulumi.ResourceOptions(parent=self, delete_before_replace=True),
        )

        # IAM role for CodeBuild/CodePipeline
        codebuild_role = aws.iam.Role(
            f"{self.name}-fn-codebuild-role",
            assume_role_policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "codebuild.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        },
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "codepipeline.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        },
                    ],
                }
            ),
            opts=pulumi.ResourceOptions(parent=self),
        )

        aws.iam.RolePolicy(
            f"{self.name}-fn-codebuild-policy",
            role=codebuild_role.id,
            policy=pulumi.Output.all(build_bucket.arn, self.name).apply(
                lambda args: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "logs:CreateLogGroup",
                                    "logs:CreateLogStream",
                                    "logs:PutLogEvents",
                                ],
                                "Resource": [
                                    f"arn:aws:logs:{aws.config.region}:{aws.get_caller_identity().account_id}:log-group:/aws/codebuild/*",
                                    f"arn:aws:logs:{aws.config.region}:{aws.get_caller_identity().account_id}:log-group:/aws/codebuild/*:*",
                                ],
                            },
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "s3:GetObject",
                                    "s3:GetObjectVersion",
                                    "s3:PutObject",
                                ],
                                "Resource": f"{args[0]}/*",
                            },
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "s3:GetBucketAcl",
                                    "s3:GetBucketLocation",
                                    "s3:ListBucket",
                                ],
                                "Resource": args[0],
                            },
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "lambda:UpdateFunctionCode",
                                    "lambda:UpdateFunctionConfiguration",
                                    "lambda:GetFunctionConfiguration",
                                    "lambda:ListTags",
                                ],
                                "Resource": "*",
                            },
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "codebuild:StartBuild",
                                    "codebuild:BatchGetBuilds",
                                ],
                                "Resource": f"arn:aws:codebuild:{aws.config.region}:{aws.get_caller_identity().account_id}:project/*",
                            },
                        ],
                    }
                )
            ),
            opts=pulumi.ResourceOptions(parent=self),
        )

        pipeline_role = aws.iam.Role(
            f"{self.name}-fn-pipeline-role",
            assume_role_policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "codepipeline.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }
            ),
            opts=pulumi.ResourceOptions(parent=self),
        )

        aws.iam.RolePolicy(
            f"{self.name}-fn-pipeline-s3",
            role=pipeline_role.id,
            policy=build_bucket.arn.apply(
                lambda arn: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "s3:ListBucket",
                                    "s3:GetBucketLocation",
                                    "s3:GetBucketVersioning",
                                ],
                                "Resource": arn,
                            },
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "s3:GetObject",
                                    "s3:GetObjectVersion",
                                    "s3:PutObject",
                                ],
                                "Resource": f"{arn}/{self.name}/*",
                            },
                        ],
                    }
                )
            ),
            opts=pulumi.ResourceOptions(parent=self),
        )

        aws.iam.RolePolicy(
            f"{self.name}-fn-pipeline-codebuild",
            role=pipeline_role.id,
            policy=Output.all(aws.config.region, aws.get_caller_identity().account_id).apply(
                lambda args: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "codebuild:StartBuild",
                                    "codebuild:BatchGetBuilds",
                                ],
                                "Resource": f"arn:aws:codebuild:{args[0]}:{args[1]}:project/*",
                            }
                        ],
                    }
                )
            ),
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Single CodeBuild project does both build and update
        publish_project = aws.codebuild.Project(
            f"{self.name}-fn-publish-{pulumi.get_stack()}",
            service_role=codebuild_role.arn,
            source=aws.codebuild.ProjectSourceArgs(
                type="CODEPIPELINE",
                buildspec=json.dumps(self._buildspec()),
            ),
            artifacts=aws.codebuild.ProjectArtifactsArgs(type="CODEPIPELINE"),
            environment=aws.codebuild.ProjectEnvironmentArgs(
                type="ARM_CONTAINER",
                compute_type="BUILD_GENERAL1_SMALL",
                image="aws/codebuild/amazonlinux-aarch64-standard:3.0",
                environment_variables=[
                    aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                        name="FUNCTION_NAME", value=f"{self.name}-{pulumi.get_stack()}"
                    ),
                    aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                        name="PACKAGE_NAME", value=self.name
                    ),
                    aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                        name="BUCKET_NAME", value=build_bucket.bucket
                    ),
                    aws.codebuild.ProjectEnvironmentEnvironmentVariableArgs(
                        name="DEBUG_MODE", value=str(self.debug_mode)
                    ),
                ],
            ),
            logs_config=aws.codebuild.ProjectLogsConfigArgs(
                cloudwatch_logs=aws.codebuild.ProjectLogsConfigCloudwatchLogsArgs(
                    status="ENABLED",
                ),
            ),
            opts=pulumi.ResourceOptions(parent=self),
        )

        # CodePipeline
        pipeline = aws.codepipeline.Pipeline(
            f"{self.name}-fn-pipeline-{pulumi.get_stack()}",
            role_arn=pipeline_role.arn,
            artifact_stores=[
                aws.codepipeline.PipelineArtifactStoreArgs(
                    type="S3",
                    location=build_bucket.bucket,
                )
            ],
            stages=[
                aws.codepipeline.PipelineStageArgs(
                    name="Source",
                    actions=[
                        aws.codepipeline.PipelineStageActionArgs(
                            name="Source",
                            category="Source",
                            owner="AWS",
                            provider="S3",
                            version="1",
                            output_artifacts=["SourceArtifact"],
                            configuration={
                                "S3Bucket": build_bucket.bucket,
                                "S3ObjectKey": f"{self.name}/source.zip",
                            },
                            run_order=1,
                        )
                    ],
                ),
                aws.codepipeline.PipelineStageArgs(
                    name="BuildAndDeploy",
                    actions=[
                        aws.codepipeline.PipelineStageActionArgs(
                            name="Publish",
                            category="Build",
                            owner="AWS",
                            provider="CodeBuild",
                            version="1",
                            input_artifacts=["SourceArtifact"],
                            run_order=1,
                            configuration={
                                "ProjectName": publish_project.name,
                                "PrimarySource": "SourceArtifact",
                            },
                        )
                    ],
                ),
            ],
            opts=pulumi.ResourceOptions(parent=self, depends_on=[bucket_versioning]),
        )

        return build_bucket, upload_cmd, pipeline, publish_project, codebuild_role

