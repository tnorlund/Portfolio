"""Shared test configuration and fixtures."""

import os
import pytest
import logging
import tempfile
import json
from pathlib import Path
from unittest.mock import Mock


@pytest.fixture(autouse=True)
def setup_logging():
    """Configure logging for tests."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


def pytest_configure(config):
    """Add custom markers."""
    config.addinivalue_line("markers", "integration: mark test as an integration test")


@pytest.fixture(autouse=True)
def disable_wandb_logging():
    """Disable W&B logging for all tests."""
    os.environ["WANDB_MODE"] = "disabled"
    os.environ["WANDB_SILENT"] = "true"


@pytest.fixture(autouse=True)
def mock_aws_credentials(request):
    """Set mock AWS credentials for all tests except end-to-end tests."""
    # Skip mocking credentials for end-to-end tests
    if request.node.get_closest_marker("end_to_end"):
        return

    # Apply mock credentials for non-end-to-end tests
    os.environ.update(
        {
            "AWS_ACCESS_KEY_ID": "testing",
            "AWS_SECRET_ACCESS_KEY": "testing",
            "AWS_DEFAULT_REGION": "us-west-2",
        }
    )


@pytest.fixture
def mock_env_vars():
    """Set up mock environment variables."""
    env_vars = {
        "WANDB_API_KEY": "mock-wandb-key",
        "HF_TOKEN": "mock-hf-token",
        "AWS_ACCESS_KEY_ID": "mock-aws-key",
        "AWS_SECRET_ACCESS_KEY": "mock-aws-secret",
        "AWS_DEFAULT_REGION": "us-west-2",
    }
    with patch.dict(os.environ, env_vars):
        yield env_vars


@pytest.fixture
def mock_dynamo_data():
    """Mock DynamoDB receipt data."""
    return {
        "receipt1": {
            "receipt": Mock(image_id="img1", receipt_id="rec1", width=800, height=1000),
            "words": [
                Mock(
                    word_id=1,
                    line_id=1,
                    text="Store",
                    top_left={"x": 0.1, "y": 0.1},
                    top_right={"x": 0.2, "y": 0.1},
                    bottom_left={"x": 0.1, "y": 0.2},
                    bottom_right={"x": 0.2, "y": 0.2},
                )
            ],
            "word_tags": [Mock(word_id=1, tag="store_name", human_validated=True)],
        }
    }


@pytest.fixture
def mock_sroie_dataset():
    """Mock SROIE dataset."""
    return {
        "train": [
            {
                "words": ["Company", "ABC"],
                "bboxes": [[0, 0, 100, 50], [100, 0, 200, 50]],
                "ner_tags": ["B-COMPANY", "I-COMPANY"],
            }
        ],
        "test": [
            {
                "words": ["Total", "100"],
                "bboxes": [[0, 0, 100, 50], [100, 0, 200, 50]],
                "ner_tags": ["B-TOTAL", "I-TOTAL"],
            }
        ],
    }


@pytest.fixture(scope="session")
def temp_dir():
    """Create a temporary directory for test artifacts."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        yield tmp_dir


@pytest.fixture(scope="session")
def mock_sroie_data(temp_dir):
    """Create mock SROIE dataset files."""
    train_data = {
        "sroie_train_0": {
            "words": ["Company", "ABC", "Total", "100"],
            "bboxes": [
                [0, 0, 100, 50],
                [100, 0, 200, 50],
                [0, 100, 100, 150],
                [100, 100, 200, 150],
            ],
            "labels": [
                "B-store_name",
                "I-store_name",
                "B-total_amount",
                "I-total_amount",
            ],
            "image_id": "sroie_train_0",
        }
    }

    test_data = {
        "sroie_test_0": {
            "words": ["Store", "XYZ", "Amount", "200"],
            "bboxes": [
                [0, 0, 100, 50],
                [100, 0, 200, 50],
                [0, 100, 100, 150],
                [100, 100, 200, 150],
            ],
            "labels": [
                "B-store_name",
                "I-store_name",
                "B-total_amount",
                "I-total_amount",
            ],
            "image_id": "sroie_test_0",
        }
    }

    train_path = Path(temp_dir) / "sroie_train.json"
    test_path = Path(temp_dir) / "sroie_test.json"

    with open(train_path, "w") as f:
        json.dump(train_data, f)
    with open(test_path, "w") as f:
        json.dump(test_data, f)

    return {"train_path": train_path, "test_path": test_path}


@pytest.fixture
def trainer(mock_env_vars):
    """Create a ReceiptTrainer instance with mocked dependencies."""
    from receipt_trainer import ReceiptTrainer
    from unittest.mock import patch

    with patch("wandb.init"), patch(
        "torch.cuda.is_available", return_value=False
    ), patch("torch.backends.mps.is_available", return_value=False):
        trainer = ReceiptTrainer(
            wandb_project="test-project",
            model_name="test/model",
            dynamo_table="test-table",
        )
        return trainer
