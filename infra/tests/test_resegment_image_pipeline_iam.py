"""Tests for the resegment image-pipeline IAM wiring.

Runs the ``ResegmentReceiptLambda`` component (and therefore the shared
``CodeBuildDockerImage`` component) under Pulumi mocks and asserts that every
cross-resource reference is derived from the same resource objects:

- the pipeline role's ``codebuild:StartBuild`` policy covers exactly the
  CodeBuild project the pipeline's BuildAndPush stage references,
- the policy is attached to the role the pipeline assumes,
- the Lambda image URI is derived from the ECR repository resource,
- only ONE generation of pipeline/builder/repo resources is produced,
- the component's URN does not depend on the Python import path.

The mocks mint a random physical-name suffix per resource, so these
assertions can only pass when references flow from the resource outputs
(``.arn`` / ``.name`` / ``.repository_url``) — a hardcoded name literal in
the component would not carry the random suffix and would fail.
"""

import importlib.util
import json
import os
import sys
import uuid
from pathlib import Path

_REPO_ROOT = Path(__file__).parents[2]
_INFRA_DIR = Path(__file__).parents[1]
for _path in (str(_REPO_ROOT), str(_INFRA_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

# The infrastructure module requires portfolio:OPENAI_API_KEY at import time.
os.environ.setdefault(
    "PULUMI_CONFIG",
    json.dumps(
        {
            "portfolio:OPENAI_API_KEY": "sk-test",
            "aws:region": "us-west-2",
        }
    ),
)

import pulumi
import pulumi.runtime

_RECORDS: list[dict] = []


class _Mocks(pulumi.runtime.Mocks):
    def new_resource(self, args: pulumi.runtime.MockResourceArgs):
        # Random suffix per resource: mimics AWS auto-naming and defeats any
        # name-literal wiring in the component under test.
        phys = f"{args.name}-{uuid.uuid4().hex[:7]}"
        outputs = dict(args.inputs)
        outputs.setdefault("name", phys)
        outputs["arn"] = (
            f"arn:aws:mock:us-west-2:123456789012:{args.typ}/{phys}"
        )
        if args.typ == "aws:ecr/repository:Repository":
            outputs["repositoryUrl"] = (
                f"123456789012.dkr.ecr.us-west-2.amazonaws.com/{phys}"
            )
        if args.typ.startswith("aws:s3/"):
            outputs.setdefault("bucket", phys)
        record = {
            "type": args.typ,
            "logical_name": args.name,
            "inputs": args.inputs,
            "physical_name": outputs["name"],
            "arn": outputs["arn"],
            "id": f"{phys}-id",
        }
        _RECORDS.append(record)
        return record["id"], outputs

    def call(self, args: pulumi.runtime.MockCallArgs):
        if "getCallerIdentity" in args.token:
            return {"accountId": "123456789012"}
        return {}


pulumi.runtime.set_mocks(
    _Mocks(), project="portfolio", stack="dev", preview=False
)

# Load the module under a deliberately DIFFERENT module name than the Pulumi
# program uses (``resegment_receipt_lambda.infrastructure``): the component's
# type token — and with it every child URN — must not depend on the Python
# import path.
_MODULE_PATH = (
    _INFRA_DIR / "resegment_receipt_lambda" / "infrastructure.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "_resegment_infra_under_test", _MODULE_PATH
)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

_COMPONENT: dict = {}


@pulumi.runtime.test
def _run_program():
    component = _MODULE.ResegmentReceiptLambda(
        "resegment-receipt",
        dynamodb_table_name="tbl",
        dynamodb_table_arn=(
            "arn:aws:dynamodb:us-west-2:123456789012:table/tbl"
        ),
        raw_bucket_name="raw-bucket",
        site_bucket_name="site-bucket",
        image_bucket_name="image-bucket",
        chromadb_bucket_name="chroma-bucket",
        chromadb_bucket_arn="arn:aws:s3:::chroma-bucket",
    )
    return component.urn.apply(
        lambda urn: _COMPONENT.setdefault("urn", urn)
    )


# Executes the mocked deployment synchronously and drains all outstanding
# resource registrations before the assertions below run.
_run_program()


def _only(logical_name: str, type_: str | None = None) -> dict:
    matches = [
        r
        for r in _RECORDS
        if r["logical_name"] == logical_name
        and (type_ is None or r["type"] == type_)
    ]
    assert len(matches) == 1, (
        f"expected exactly one '{logical_name}' resource, "
        f"found {len(matches)}"
    )
    return matches[0]


def _build_action_config() -> dict:
    pipeline = _only(
        "resegment-receipt-img-pipeline",
        "aws:codepipeline/pipeline:Pipeline",
    )
    build_stages = [
        s for s in pipeline["inputs"]["stages"] if s["name"] == "BuildAndPush"
    ]
    assert len(build_stages) == 1
    (action,) = build_stages[0]["actions"]
    return action["configuration"]


def test_pipeline_role_policy_covers_referenced_builder() -> None:
    """The StartBuild policy must target the builder the pipeline triggers,
    resolved through resource references (not name literals)."""
    project_name = _build_action_config()["ProjectName"]

    # The pipeline references the builder via the Project resource's output
    # (the mock-minted physical name carries a random suffix).
    builders = [
        r
        for r in _RECORDS
        if r["type"] == "aws:codebuild/project:Project"
        and r["physical_name"] == project_name
    ]
    assert len(builders) == 1, (
        "pipeline BuildAndPush stage does not reference the CodeBuild "
        "project resource created by the same component"
    )
    builder = builders[0]

    policy_record = _only(
        "resegment-receipt-img-pl-cb", "aws:iam/rolePolicy:RolePolicy"
    )
    policy = json.loads(policy_record["inputs"]["policy"])
    (statement,) = policy["Statement"]
    assert "codebuild:StartBuild" in statement["Action"]
    assert statement["Resource"] == builder["arn"], (
        "pipeline role StartBuild policy does not cover the builder "
        "project referenced by the pipeline"
    )


def test_pipeline_role_policy_attached_to_pipeline_role() -> None:
    """The StartBuild policy must attach to the role the pipeline assumes."""
    pipeline = _only(
        "resegment-receipt-img-pipeline",
        "aws:codepipeline/pipeline:Pipeline",
    )
    role = _only("resegment-receipt-img-pl-role", "aws:iam/role:Role")
    assert pipeline["inputs"]["roleArn"] == role["arn"]

    policy_record = _only(
        "resegment-receipt-img-pl-cb", "aws:iam/rolePolicy:RolePolicy"
    )
    assert policy_record["inputs"]["role"] == role["id"]


def test_single_generation_of_image_resources() -> None:
    """A fresh program must converge to exactly one generation of the
    pipeline / builder / repo / role resources."""
    for logical_name in (
        "resegment-receipt-img-repo",
        "resegment-receipt-img-builder",
        "resegment-receipt-img-pipeline",
        "resegment-receipt-img-pl-role",
        "resegment-receipt-img-pl-cb",
        "resegment-receipt-img-function",
    ):
        _only(logical_name)


def test_lambda_image_uri_derives_from_repo_resource() -> None:
    """The Lambda must point at the SAME ECR repository resource the
    pipeline's builder pushes to."""
    repo = _only(
        "resegment-receipt-img-repo", "aws:ecr/repository:Repository"
    )
    function = _only(
        "resegment-receipt-img-function", "aws:lambda/function:Function"
    )
    expected_uri = (
        "123456789012.dkr.ecr.us-west-2.amazonaws.com/"
        f"{repo['physical_name']}:latest"
    )
    assert function["inputs"]["imageUri"] == expected_uri

    # And the builder pushes to the same repository (by resource reference).
    builder = _only(
        "resegment-receipt-img-builder", "aws:codebuild/project:Project"
    )
    env_vars = {
        var["name"]: var["value"]
        for var in builder["inputs"]["environment"]["environmentVariables"]
    }
    assert env_vars["REPOSITORY_NAME"] == repo["physical_name"]


def test_component_urn_is_import_path_independent() -> None:
    """The component type token is pinned; importing the module under a
    different name must not re-key the URN (which would mint a second
    physical generation of every child resource)."""
    urn = _COMPONENT["urn"]
    assert (
        "resegment_receipt_lambda.infrastructure-resegment-receipt" in urn
    ), urn
    assert "_resegment_infra_under_test" not in urn, urn
