import os
import json
import pytest

def test_save_model(trainer, mock_model, mock_tokenizer, tmp_path, mocker):
    """Test save_model method."""
    trainer.model = mock_model
    trainer.tokenizer = mock_tokenizer
    trainer.label_list = ["O", "B-total", "I-total"]
    trainer.label2id = {"O": 0, "B-total": 1, "I-total": 2}
    trainer.id2label = {0: "O", 1: "B-total", 2: "I-total"}
    trainer.num_labels = 3
    output_path = str(tmp_path / "saved_model")

    # Mock save_pretrained to create necessary files
    def mock_save_pretrained(path):
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, "config.json"), "w") as f:
            json.dump({"mock": "config"}, f)
    
    mock_model.save_pretrained.side_effect = mock_save_pretrained
    mock_tokenizer.save_pretrained.side_effect = mock_save_pretrained

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
    
    # Make save_pretrained create directory but not config.json
    def mock_save_pretrained(path):
        os.makedirs(path, exist_ok=True)
    mock_model.save_pretrained.side_effect = mock_save_pretrained
    mock_tokenizer.save_pretrained.side_effect = mock_save_pretrained

    with pytest.raises(ValueError) as exc_info:
        trainer.save_model(output_path)
    assert "Validation failed" in str(exc_info.value) 