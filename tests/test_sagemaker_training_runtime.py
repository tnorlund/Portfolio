"""Static contracts for the shared SageMaker training/evaluation image."""

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = REPOSITORY_ROOT / "infra" / "sagemaker_training" / "Dockerfile"
EPOCH_EVAL_COMPONENT = (
    REPOSITORY_ROOT / "infra" / "sagemaker_epoch_eval" / "component.py"
)


def test_training_image_uses_python313_for_package_install() -> None:
    """Local packages must install after Python 3.13 becomes active."""
    dockerfile = DOCKERFILE.read_text()

    create_env = dockerfile.index('"python=${PYTHON_VERSION}" pip wheel')
    activate_env = dockerfile.index('ENV PATH="${PYTHON_ENV}/bin:${PATH}"')
    install_dynamo = dockerfile.index("/opt/ml/code/receipt_dynamo")
    install_layoutlm = dockerfile.index(
        '"/opt/ml/code/receipt_layoutlm[training]"'
    )

    assert "ARG PYTHON_VERSION=3.13" in dockerfile
    assert " AS training-runtime" in dockerfile
    assert create_env < activate_env < install_dynamo < install_layoutlm


def test_training_image_installs_matching_cuda_torch_wheel() -> None:
    """The replacement interpreter must receive the pinned CUDA build."""
    dockerfile = DOCKERFILE.read_text()

    assert "ARG PYTORCH_VERSION=2.6.0" in dockerfile
    assert (
        "ARG PYTORCH_INDEX_URL=" "https://download.pytorch.org/whl/cu124"
    ) in dockerfile
    assert "torch.version.cuda == '12.4'" in dockerfile
    assert "sys.version_info[:2] == (3, 13)" in dockerfile
    assert "python -m pip check" in dockerfile
    assert (
        "import receipt_dynamo, receipt_layoutlm, seqeval, sklearn, torch"
        in (dockerfile)
    )


def test_epoch_eval_reuses_the_training_image() -> None:
    """Epoch evaluation must inherit the corrected training runtime."""
    component = EPOCH_EVAL_COMPONENT.read_text()

    assert (
        "This reuses the *existing* LayoutLM training container image"
        in component
    )
    assert '"ECR_IMAGE_URI": f"{args[0]}:latest"' in component
    assert '"ImageUri": os.environ["ECR_IMAGE_URI"]' in component
