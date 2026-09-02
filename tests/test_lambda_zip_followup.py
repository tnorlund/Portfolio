"""Post-Chroma zip-Lambda eligibility: Dockerfile classifier + size budget."""

from pathlib import Path

import scripts.lambda_zip_budget as budget


def test_every_infra_dockerfile_is_classified() -> None:
    rows = budget.classify_repository()
    paths = {row.relative_path for row in rows}
    discovered = {
        str(path.relative_to(budget.REPOSITORY_ROOT))
        for path in budget.iter_dockerfiles()
    }
    assert paths == discovered
    assert rows, "expected infra Dockerfiles"


def test_chroma_installers_are_blocked_from_zip() -> None:
    blocked = {
        row.relative_path
        for row in budget.classify_repository()
        if row.bucket == budget.BUCKET_CHROMA_BLOCKED
    }
    assert "infra/upload_images/container_ocr/Dockerfile" in blocked
    assert "infra/merge_receipt_lambda/lambdas/Dockerfile" in blocked
    assert (
        "infra/routes/word_similarity_cache_generator/lambdas/Dockerfile"
        in blocked
    )


def test_layoutlm_and_sagemaker_stay_on_images() -> None:
    by_path = {row.relative_path: row for row in budget.classify_repository()}
    layoutlm = by_path[
        "infra/routes/layoutlm_inference_cache_generator/lambdas/Dockerfile"
    ]
    sagemaker = by_path["infra/sagemaker_training/Dockerfile"]
    assert layoutlm.bucket == budget.BUCKET_STAY_IMAGE
    assert sagemaker.bucket == budget.BUCKET_NOT_LAMBDA


def test_already_slim_images_have_no_chroma() -> None:
    slim = [
        row
        for row in budget.classify_repository()
        if row.bucket == budget.BUCKET_ALREADY_SLIM
    ]
    slim_paths = {row.relative_path for row in slim}
    assert "infra/upload_images/container_upload/Dockerfile" in slim_paths
    assert "infra/trigger_reocr_lambda/lambdas/Dockerfile" in slim_paths
    for row in slim:
        text = (budget.REPOSITORY_ROOT / row.relative_path).read_text(
            encoding="utf-8"
        )
        assert "receipt_chroma" not in text
        assert "chromadb" not in text.lower()


def test_post_chroma_fat_path_fits_zip_ceiling() -> None:
    assert budget.chroma_stack_exceeds_zip()
    assert budget.remaining_fat_fits_zip()
    assert budget.REMAINING_FAT_UNZIPPED_MB < budget.ZIP_UNZIPPED_LIMIT_MB


def test_classify_tmp_chroma_dockerfile(tmp_path: Path) -> None:
    docker = tmp_path / "infra" / "example" / "Dockerfile"
    docker.parent.mkdir(parents=True)
    docker.write_text(
        "FROM public.ecr.aws/lambda/python:3.13\n"
        "COPY receipt_chroma/ /tmp/receipt_chroma/\n"
        "RUN pip install /tmp/receipt_chroma\n",
        encoding="utf-8",
    )
    row = budget.classify_dockerfile(docker, root=tmp_path)
    assert row.bucket == budget.BUCKET_CHROMA_BLOCKED
