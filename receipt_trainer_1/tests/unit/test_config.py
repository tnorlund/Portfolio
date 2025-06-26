"""Unit tests for configuration classes."""

import pytest

from receipt_trainer.config import DataConfig, TrainingConfig


def test_training_config_defaults():
    """Test TrainingConfig default values."""
    config = TrainingConfig()
    assert config.batch_size == 16
    assert config.learning_rate == 3e-4
    assert config.num_epochs == 20
    assert config.gradient_accumulation_steps == 32
    assert config.warmup_ratio == 0.2
    assert config.weight_decay == 0.01
    assert config.max_grad_norm == 1.0
    assert config.evaluation_steps == 100
    assert config.save_steps == 100
    assert config.logging_steps == 50
    assert config.bf16 is True
    assert config.early_stopping_patience == 5


def test_training_config_custom_values():
    """Test TrainingConfig with custom values."""
    config = TrainingConfig(
        batch_size=8,
        learning_rate=1e-4,
        num_epochs=10,
        gradient_accumulation_steps=16,
    )
    assert config.batch_size == 8
    assert config.learning_rate == 1e-4
    assert config.num_epochs == 10
    assert config.gradient_accumulation_steps == 16
    # Other values should remain default
    assert config.warmup_ratio == 0.2


def test_data_config_defaults():
    """Test DataConfig default values."""
    config = DataConfig()
    assert config.balance_ratio == 0.7
    assert config.use_sroie is True
    assert config.augment is True
    assert config.max_length == 512
    assert config.sliding_window_size == 50
    assert config.sliding_window_overlap == 10
    assert config.env == "dev"
    assert config.cache_dir is None


def test_data_config_custom_values():
    """Test DataConfig with custom values."""
    config = DataConfig(
        balance_ratio=0.8, use_sroie=False, env="prod", cache_dir="/tmp/cache"
    )
    assert config.balance_ratio == 0.8
    assert config.use_sroie is False
    assert config.env == "prod"
    assert config.cache_dir == "/tmp/cache"
    # Other values should remain default
    assert config.max_length == 512
