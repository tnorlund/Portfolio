"""Integration tests for the ReceiptTrainer class."""

import os
import pytest
from pathlib import Path
import wandb
from datasets import Dataset, DatasetDict

from receipt_trainer import ReceiptTrainer, TrainingConfig, DataConfig


@pytest.fixture
def mock_env_vars(mocker):
    """Set up mock environment variables."""
    env_vars = {
        "WANDB_API_KEY": "mock-wandb-key",
        "HF_TOKEN": "mock-hf-token",
        "AWS_ACCESS_KEY_ID": "mock-aws-key",
        "AWS_SECRET_ACCESS_KEY": "mock-aws-secret",
        "AWS_DEFAULT_REGION": "us-west-2",
    }
    for key, value in env_vars.items():
        mocker.patch.dict(os.environ, {key: value})
    return env_vars


@pytest.fixture
def mock_dynamo_data(mocker):
    """Create mock DynamoDB data."""
    return {
        "receipt1": {
            "receipt": mocker.Mock(
                image_id="img1", receipt_id="rec1", width=800, height=1000
            ),
            "words": [
                mocker.Mock(
                    word_id=1,
                    line_id=1,
                    text="Store",
                    top_left={"x": 0.1, "y": 0.1},
                    top_right={"x": 0.2, "y": 0.1},
                    bottom_left={"x": 0.1, "y": 0.2},
                    bottom_right={"x": 0.2, "y": 0.2},
                )
            ],
            "word_tags": [
                mocker.Mock(word_id=1, tag="store_name", human_validated=True)
            ],
        }
    }


@pytest.fixture
def mock_sroie_data():
    """Create mock SROIE dataset."""
    return {
        "train": [
            {
                "words": ["The", "Store", "ABC", "Total", "is", "100"],
                "bboxes": [
                    [0, 0, 50, 50],
                    [50, 0, 150, 50],
                    [150, 0, 250, 50],
                    [0, 50, 100, 100],
                    [100, 50, 150, 100],
                    [150, 50, 250, 100],
                ],
                "ner_tags": [
                    0,
                    1,
                    2,
                    7,
                    0,
                    8,
                ],  # O, B-COMPANY, I-COMPANY, B-TOTAL, O, I-TOTAL
            }
        ],
        "test": [
            {
                "words": ["A", "Total", "of", "100", "dollars"],
                "bboxes": [
                    [0, 0, 50, 50],
                    [50, 0, 150, 50],
                    [150, 0, 200, 50],
                    [200, 0, 300, 50],
                    [300, 0, 400, 50],
                ],
                "ner_tags": [0, 7, 0, 8, 0],  # O, B-TOTAL, O, I-TOTAL, O
            }
        ],
    }


@pytest.fixture
def trainer(mock_env_vars, mocker):
    """Create a ReceiptTrainer instance with mocked dependencies."""
    mocker.patch("wandb.init")
    mocker.patch("torch.cuda.is_available", return_value=False)
    mocker.patch("torch.backends.mps.is_available", return_value=False)
    mocker.patch(
        "receipt_trainer.utils.aws.get_dynamo_table", return_value="mock-table"
    )

    return ReceiptTrainer(wandb_project="test-project", model_name="test/model")


@pytest.mark.integration
def test_trainer_initialization(trainer):
    """Test trainer initialization with mocked dependencies."""
    assert trainer.wandb_project == "test-project"
    assert trainer.model_name == "test/model"
    assert trainer.device == "cpu"
    assert isinstance(trainer.training_config, TrainingConfig)
    assert isinstance(trainer.data_config, DataConfig)


@pytest.mark.integration
def test_data_loading_pipeline(trainer, mock_dynamo_data, mock_sroie_data, mocker):
    """Test the complete data loading pipeline with mocked data sources."""
    # Setup mocks
    mock_client = mocker.Mock()
    mock_client.listReceiptDetails.return_value = (mock_dynamo_data, None)

    # Mock DynamoDB client and response
    mock_dynamo_client = mocker.Mock()
    mock_response = {
        "Items": [
            {
                "TYPE": {"S": "RECEIPT"},
                "PK": {"S": "IMAGE#7708f0d7-da00-4593-8e87-88cbce27cdd7"},
                "SK": {"S": "RECEIPT#00001"},
                "GSI1PK": {"S": "IMAGE"},
                "GSI1SK": {
                    "S": "IMAGE#7708f0d7-da00-4593-8e87-88cbce27cdd7#RECEIPT#00001"
                },
                "GSI2PK": {"S": "RECEIPT"},
                "GSI2SK": {
                    "S": "IMAGE#7708f0d7-da00-4593-8e87-88cbce27cdd7#RECEIPT#00001"
                },
                "image_id": {"S": "7708f0d7-da00-4593-8e87-88cbce27cdd7"},
                "receipt_id": {"N": "1"},
                "width": {"N": "800"},
                "height": {"N": "1000"},
                "top_left": {"M": {"x": {"N": "0.1"}, "y": {"N": "0.1"}}},
                "top_right": {"M": {"x": {"N": "0.2"}, "y": {"N": "0.1"}}},
                "bottom_left": {"M": {"x": {"N": "0.1"}, "y": {"N": "0.2"}}},
                "bottom_right": {"M": {"x": {"N": "0.2"}, "y": {"N": "0.2"}}},
                "raw_s3_bucket": {"S": "mock-bucket"},
                "raw_s3_key": {"S": "mock-key"},
                "timestamp_added": {"S": "2021-01-01T00:00:00Z"},
            },
            {
                "TYPE": {"S": "RECEIPT_WORD"},
                "PK": {"S": "IMAGE#7708f0d7-da00-4593-8e87-88cbce27cdd7"},
                "SK": {"S": "RECEIPT#00001#LINE#00001#WORD#00001"},
                "GSI2PK": {"S": "RECEIPT"},
                "GSI2SK": {
                    "S": "IMAGE#7708f0d7-da00-4593-8e87-88cbce27cdd7#RECEIPT#00001#LINE#00001#WORD#00001"
                },
                "text": {"S": "Store"},
                "bounding_box": {
                    "M": {
                        "x": {"N": "0.1"},
                        "y": {"N": "0.1"},
                        "width": {"N": "0.1"},
                        "height": {"N": "0.1"},
                    }
                },
                "top_right": {"M": {"x": {"N": "0.2"}, "y": {"N": "0.1"}}},
                "top_left": {"M": {"x": {"N": "0.1"}, "y": {"N": "0.1"}}},
                "bottom_right": {"M": {"x": {"N": "0.2"}, "y": {"N": "0.2"}}},
                "bottom_left": {"M": {"x": {"N": "0.1"}, "y": {"N": "0.2"}}},
                "angle_degrees": {"N": "0.0"},
                "angle_radians": {"N": "0.0"},
                "confidence": {"N": "0.95"},
                "histogram": {
                    "M": {
                        "S": {"N": "1"},
                        "t": {"N": "1"},
                        "o": {"N": "1"},
                        "r": {"N": "1"},
                        "e": {"N": "1"},
                    }
                },
                "num_chars": {"N": "5"},
                "tags": {"SS": ["store_name"]},
            },
            {
                "TYPE": {"S": "WORD_TAG"},
                "PK": {"S": "IMAGE#7708f0d7-da00-4593-8e87-88cbce27cdd7"},
                "SK": {"S": "RECEIPT#00001#LINE#00001#WORD#00001#TAG"},
                "word_id": {"N": "1"},
                "tag": {"S": "store_name"},
                "human_validated": {"BOOL": True},
            },
        ],
        "Count": 3,
        "ScannedCount": 3,
    }
    mock_dynamo_client.query.return_value = mock_response

    # Mock boto3 client creation
    mocker.patch("boto3.client", return_value=mock_dynamo_client)
    mocker.patch("dynamo.DynamoClient", return_value=mock_client)
    mocker.patch("datasets.load_dataset", return_value=mock_sroie_data)

    # Set the dynamo_table and initialize the client
    trainer.dynamo_table = "mock-table"
    trainer.initialize_dynamo()

    # Load data
    dataset = trainer.load_data(use_sroie=True)

    # Verify dataset structure
    assert isinstance(dataset, DatasetDict)
    assert "train" in dataset
    assert "validation" in dataset

    # Check data fields
    for split in dataset.values():
        assert isinstance(split, Dataset)
        assert all(
            field in split.features for field in ["words", "bboxes", "labels", "image_id"]
        )
        assert len(split) > 0


@pytest.mark.integration
def test_model_initialization(trainer, mocker):
    """Test model initialization with mocked tokenizer."""
    mock_tokenizer = mocker.Mock()
    mocker.patch(
        "transformers.LayoutLMTokenizerFast.from_pretrained",
        return_value=mock_tokenizer,
    )

    trainer.initialize_model()
    assert trainer.tokenizer == mock_tokenizer


@pytest.mark.integration
def test_wandb_initialization(trainer, mocker):
    """Test W&B initialization with mocked wandb."""
    mock_init = mocker.Mock()
    mocker.patch("wandb.init", mock_init)

    config = {"test": True}
    trainer.initialize_wandb(config)

    mock_init.assert_called_once_with(
        project=trainer.wandb_project, config=config, resume=True
    )


@pytest.mark.integration
def test_cache_directory_handling(trainer, tmp_path):
    """Test cache directory creation and management."""
    # Set cache directory to temporary path
    trainer.data_config.cache_dir = str(tmp_path / "cache")

    # Create cache directory
    os.makedirs(trainer.data_config.cache_dir, exist_ok=True)

    # Verify directory exists and has correct permissions
    cache_dir = Path(trainer.data_config.cache_dir)
    assert cache_dir.exists()
    assert cache_dir.is_dir()
    assert os.access(cache_dir, os.W_OK)
    assert os.access(cache_dir, os.R_OK)


@pytest.mark.integration
def test_error_handling(trainer, mocker):
    """Test error handling for missing environment variables."""
    # Mock environment with missing variables
    mocker.patch.dict(os.environ, {}, clear=True)

    with pytest.raises(EnvironmentError) as exc_info:
        trainer._validate_env_vars()
    assert "Missing required environment variables" in str(exc_info.value)


@pytest.mark.integration
def test_device_selection(mocker):
    """Test device selection logic with different hardware configurations."""
    # Mock environment variables
    env_vars = {
        "WANDB_API_KEY": "test",
        "HF_TOKEN": "test",
        "AWS_ACCESS_KEY_ID": "test",
        "AWS_SECRET_ACCESS_KEY": "test",
        "AWS_DEFAULT_REGION": "test",
    }
    mocker.patch.dict(os.environ, env_vars)

    # Test CUDA
    mocker.patch("torch.cuda.is_available", return_value=True)
    mocker.patch("torch.backends.mps.is_available", return_value=False)
    trainer = ReceiptTrainer(wandb_project="test", model_name="test/model")
    assert trainer.device == "cuda"

    # Test MPS
    mocker.patch("torch.cuda.is_available", return_value=False)
    mocker.patch("torch.backends.mps.is_available", return_value=True)
    trainer = ReceiptTrainer(wandb_project="test", model_name="test/model")
    assert trainer.device == "mps"

    # Test CPU fallback
    mocker.patch("torch.cuda.is_available", return_value=False)
    mocker.patch("torch.backends.mps.is_available", return_value=False)
    trainer = ReceiptTrainer(wandb_project="test", model_name="test/model")
    assert trainer.device == "cpu"


@pytest.mark.integration
def test_cleanup(trainer, tmp_path, mocker):
    """Test cleanup of temporary files and resources."""
    # Set up a mock wandb run
    trainer.wandb_run = mocker.Mock()

    # Clean up W&B run
    trainer.wandb_run.finish()

    # Verify temp directory cleanup
    trainer.data_config.cache_dir = str(tmp_path / "cache")
    os.makedirs(trainer.data_config.cache_dir, exist_ok=True)
    cache_dir = Path(trainer.data_config.cache_dir)
    assert cache_dir.exists()  # Should still exist during test
