"""Receipt LayoutLM training package."""


# Lazy attribute access to avoid import errors if optional deps aren't installed
def __getattr__(name):
    if name == "DataConfig" or name == "TrainingConfig":
        from .config import DataConfig, TrainingConfig  # type: ignore

        return {"DataConfig": DataConfig, "TrainingConfig": TrainingConfig}[
            name
        ]
    if name == "ReceiptLayoutLMTrainer":
        from .trainer import ReceiptLayoutLMTrainer  # type: ignore

        return ReceiptLayoutLMTrainer
    raise AttributeError(name)
