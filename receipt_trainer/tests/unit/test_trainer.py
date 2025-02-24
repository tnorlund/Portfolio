"""Integration tests for the ReceiptTrainer class."""

import os
import pytest
from pathlib import Path
import wandb
from datasets import Dataset, DatasetDict
import json
import torch
from transformers import (
    LayoutLMForTokenClassification,
    LayoutLMTokenizerFast,
    TrainingArguments,
    Trainer,
    LayoutLMConfig,
)

from receipt_trainer import ReceiptTrainer, TrainingConfig, DataConfig


@pytest.fixture
def mock_env_vars(monkeypatch):
    """Set up mock environment variables."""
    env_vars = {
        "WANDB_API_KEY": "mock-wandb-key",
        "HF_TOKEN": "mock-hf-token",
        "AWS_ACCESS_KEY_ID": "mock-aws-key",
        "AWS_SECRET_ACCESS_KEY": "mock-aws-secret",
        "AWS_DEFAULT_REGION": "us-west-2",
    }
    for key, value in env_vars.items():
        monkeypatch.setenv(key, value)
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
def trainer(mock_env_vars, monkeypatch, mocker):
    """Create a ReceiptTrainer instance with mocked dependencies."""
    monkeypatch.setattr("wandb.init", mocker.Mock())
    monkeypatch.setattr("torch.cuda.is_available", lambda: False)
    monkeypatch.setattr("torch.backends.mps.is_available", lambda: False)
    monkeypatch.setattr(
        "receipt_trainer.utils.aws.get_dynamo_table", lambda *args: "mock-table"
    )

    return ReceiptTrainer(
        wandb_project="test-project", model_name="/path/to/dummy_model"
    )


@pytest.mark.integration
def test_trainer_initialization(trainer):
    """Test trainer initialization with mocked dependencies."""
    assert trainer.wandb_project == "test-project"
    assert trainer.model_name == "/path/to/dummy_model"
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
            field in split.features for field in ["words", "bbox", "labels", "image_id"]
        )
        assert len(split) > 0


@pytest.mark.integration
def test_model_initialization(trainer, monkeypatch, mocker):
    """Test model initialization with mocked tokenizer."""
    mock_tokenizer = mocker.Mock()
    monkeypatch.setattr(
        "transformers.LayoutLMTokenizerFast.from_pretrained",
        lambda *args: mock_tokenizer,
    )

    trainer.initialize_model()
    assert trainer.tokenizer == mock_tokenizer


@pytest.mark.integration
def test_wandb_initialization(trainer, monkeypatch, mocker):
    """Test W&B initialization with mocked wandb."""
    mock_init = mocker.Mock()
    monkeypatch.setattr("wandb.init", mock_init)

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
def test_error_handling(trainer, monkeypatch):
    """Test error handling for missing environment variables."""
    monkeypatch.delenv("WANDB_API_KEY", raising=False)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)

    with pytest.raises(EnvironmentError) as exc_info:
        trainer._validate_env_vars()
    assert "Missing required environment variables" in str(exc_info.value)


@pytest.mark.integration
def test_device_selection(monkeypatch, mocker, mock_env_vars):
    """Test device selection logic with different hardware configurations."""
    # Test CUDA
    monkeypatch.setattr("torch.cuda.is_available", lambda: True)
    monkeypatch.setattr("torch.backends.mps.is_available", lambda: False)
    trainer = ReceiptTrainer(wandb_project="test")
    assert trainer.device == "cuda"

    # Test MPS
    monkeypatch.setattr("torch.cuda.is_available", lambda: False)
    monkeypatch.setattr("torch.backends.mps.is_available", lambda: True)
    trainer = ReceiptTrainer(wandb_project="test")
    assert trainer.device == "mps"

    # Test CPU fallback
    monkeypatch.setattr("torch.cuda.is_available", lambda: False)
    monkeypatch.setattr("torch.backends.mps.is_available", lambda: False)
    trainer = ReceiptTrainer(wandb_project="test")
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


@pytest.fixture
def mock_dataset():
    """Create a mock dataset for testing."""
    train_data = Dataset.from_dict(
        {
            "words": [["word1", "word2"], ["word3", "word4"]],
            "bbox": [[[1, 2, 3, 4], [5, 6, 7, 8]], [[9, 10, 11, 12], [13, 14, 15, 16]]],
            "labels": [["O", "B-total"], ["I-total", "O"]],
            "image_id": ["id1", "id2"],
        }
    )
    val_data = Dataset.from_dict(
        {
            "words": [["word5", "word6"]],
            "bbox": [[[17, 18, 19, 20], [21, 22, 23, 24]]],
            "labels": [["B-total", "I-total"]],
            "image_id": ["id3"],
        }
    )
    return DatasetDict({"train": train_data, "validation": val_data})


@pytest.fixture
def mock_tokenizer(mocker):
    """Create a mock tokenizer for testing."""
    tokenizer = mocker.Mock(spec=LayoutLMTokenizerFast)
    tokenizer.from_pretrained.return_value = tokenizer
    return tokenizer


@pytest.fixture
def mock_model(mocker):
    """Create a mock model for testing."""
    model = mocker.Mock(spec=LayoutLMForTokenClassification)
    model.to.return_value = model
    return model


def test_configure_training(trainer, mock_dataset, mock_tokenizer, tmp_path, mocker):
    """Test configure_training method."""
    trainer.dataset = mock_dataset
    trainer.tokenizer = mock_tokenizer
    output_dir = str(tmp_path / "checkpoints")

    mock_model = mocker.Mock()
    mock_model_init = mocker.patch(
        "transformers.LayoutLMForTokenClassification.from_pretrained",
        return_value=mock_model,
    )

    # Test with custom output directory
    trainer.configure_training(output_dir=output_dir)

    # Verify output directory was created
    assert os.path.exists(output_dir)

    # Verify model initialization
    assert trainer.model is not None
    assert trainer.num_labels == 3  # O, B-total, I-total
    assert set(trainer.label_list) == {"O", "B-total", "I-total"}

    # Verify training arguments
    assert isinstance(trainer.training_args, TrainingArguments)
    assert trainer.training_args.output_dir == output_dir
    assert trainer.training_args.num_train_epochs == trainer.training_config.num_epochs


def make_dummy_layoutlm_model(num_labels=3):
    """
    Return a small-but-real LayoutLMForTokenClassification instance
    so that HF Trainer won't crash calling named_children().
    """
    config = LayoutLMConfig(
        hidden_size=32,
        num_attention_heads=2,
        num_labels=num_labels,
        intermediate_size=64,
        max_position_embeddings=128,
        vocab_size=30522,
    )
    return LayoutLMForTokenClassification(config)


def test_save_model(trainer, mock_model, mock_tokenizer, tmp_path, mocker):
    """Test save_model method."""
    trainer.model = mock_model
    trainer.tokenizer = mock_tokenizer
    trainer.label_list = ["O", "B-total", "I-total"]
    trainer.label2id = {"O": 0, "B-total": 1, "I-total": 2}
    trainer.id2label = {0: "O", 1: "B-total", 2: "I-total"}
    trainer.num_labels = 3
    output_path = str(tmp_path / "saved_model")

    mocker.patch("transformers.AutoModel.from_pretrained")

    # Test successful save
    trainer.save_model(output_path)

    # Verify directories and files were created
    assert os.path.exists(output_path)
    assert os.path.exists(os.path.join(output_path, "label_config.json"))
    assert os.path.exists(os.path.join(output_path, "config.json"))

    # Verify model and tokenizer were saved
    trainer.model.save_pretrained.assert_called_once_with(output_path)
    trainer.tokenizer.save_pretrained.assert_called_once_with(output_path)

    # Verify config files content
    with open(os.path.join(output_path, "label_config.json")) as f:
        label_config = json.load(f)
        assert label_config["num_labels"] == 3
        assert set(label_config["label_list"]) == {"O", "B-total", "I-total"}


def test_save_model_errors(trainer, tmp_path, mocker):
    """Test save_model error handling."""
    output_path = str(tmp_path / "failed_save")

    # Test without model
    trainer.model = None
    trainer.tokenizer = None
    with pytest.raises(ValueError) as exc_info:
        trainer.save_model(output_path)
    assert "Model and tokenizer must be initialized" in str(exc_info.value)

    # Mock model and tokenizer
    mock_model = mocker.Mock()
    mock_tokenizer = mocker.Mock()
    trainer.model = mock_model
    trainer.tokenizer = mock_tokenizer
    trainer.label_list = ["O", "B-total", "I-total"]
    trainer.label2id = {"O": 0, "B-total": 1, "I-total": 2}
    trainer.id2label = {0: "O", 1: "B-total", 2: "I-total"}
    trainer.num_labels = 3

    # Test save failure
    mock_model.save_pretrained.side_effect = Exception("Failed to save model")
    with pytest.raises(Exception) as exc_info:
        trainer.save_model(output_path)
    assert "Failed to save model" in str(exc_info.value)

    # Reset side effect for next test
    mock_model.save_pretrained.side_effect = None

    # Test validation failure
    mock_auto_model = mocker.patch("transformers.AutoModel.from_pretrained")
    mock_auto_model.side_effect = Exception("Validation failed")
    with pytest.raises(ValueError) as exc_info:
        trainer.save_model(output_path)
    assert "Saved model validation failed" in str(exc_info.value)
