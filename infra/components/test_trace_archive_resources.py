"""Preserve the trace bucket URN without provisioning paid-export resources."""

import importlib.util
from pathlib import Path

import pulumi
from pulumi.runtime import MockResourceArgs, Mocks


class ArchiveMocks(Mocks):
    def __init__(self) -> None:
        self.resources = []

    def new_resource(self, args: MockResourceArgs) -> tuple:
        self.resources.append(args)
        return args.name, args.inputs

    def call(self, args: object) -> dict:
        return {}


@pulumi.runtime.test
def test_archive_keeps_bucket_identity_and_removes_paid_export_resources() -> (
    pulumi.Output
):
    mocks = ArchiveMocks()
    pulumi.runtime.set_mocks(mocks, project="portfolio", stack="test")
    path = Path(__file__).parent / "langsmith_bulk_export" / "__init__.py"
    spec = importlib.util.spec_from_file_location("trace_archive_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    archive = module.LangSmithBulkExport(
        "langsmith-export-test", project_name="archive"
    )

    def check(values: list) -> None:
        urn, force_destroy = values
        assert urn == (
            "urn:pulumi:test::portfolio::custom:langsmith-bulk-export:langsmith-export-test"
            "$aws:s3/bucket:Bucket::langsmith-export-test-export-bucket"
        )
        assert force_destroy is False
        assert not any(
            resource.typ.startswith(("aws:iam/", "aws:lambda/"))
            for resource in mocks.resources
        )

    return pulumi.Output.all(
        archive.export_bucket.urn, archive.export_bucket.force_destroy
    ).apply(check)
