"""Offline contract for the Core AI exporter's pinned converter API."""

import importlib.util
import json
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "receipt_layoutlm"
INPUT_NAMES = ["input_ids", "attention_mask", "bbox", "token_type_ids"]


class _AIProgram:
    """The current API can save without exposing a separate optimize step."""

    def save_asset(self, path: Path) -> None:
        assert isinstance(path, Path)
        path.mkdir()


@pytest.mark.parametrize("converter_rejects_graph", [False, True])
def test_export_preserves_the_named_coreai_interface(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    converter_rejects_graph: bool,
) -> None:
    """Exercise bundle creation without PyTorch, native Core AI, or AWS."""
    checkpoint = tmp_path / "checkpoint"
    checkpoint.mkdir()
    (checkpoint / "config.json").write_text("{}")
    (checkpoint / "vocab.txt").write_text("[PAD]\n[UNK]\n")
    bundle = tmp_path / "bundle"

    model = Mock(
        config=SimpleNamespace(
            id2label={0: "O"}, label2id={"O": 0}, num_labels=1
        )
    )
    tokenizer = Mock(vocab_size=2)
    wrapper = Mock()
    exported = Mock()
    graph = object()
    decomposition = object()
    exported.run_decompositions.return_value = graph
    convert = Mock(return_value=_AIProgram())
    add_graph = Mock(
        side_effect=(
            TypeError("rejected graph") if converter_rejects_graph else None
        )
    )

    monkeypatch.syspath_prepend(str(PACKAGE))
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(
            no_grad=nullcontext,
            export=SimpleNamespace(export=Mock(return_value=exported)),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "receipt_layoutlm.model_wrappers",
        SimpleNamespace(LayoutLMWrapper=Mock(return_value=wrapper)),
    )
    monkeypatch.setitem(
        sys.modules,
        "transformers",
        SimpleNamespace(
            LayoutLMForTokenClassification=SimpleNamespace(
                from_pretrained=Mock(return_value=model)
            ),
            LayoutLMTokenizerFast=SimpleNamespace(
                from_pretrained=Mock(return_value=tokenizer)
            ),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "coreai_torch",
        SimpleNamespace(
            get_decomp_table=Mock(return_value=decomposition),
            TorchConverter=Mock(
                return_value=SimpleNamespace(
                    add_exported_program=add_graph, to_coreai=convert
                )
            ),
        ),
    )

    spec = importlib.util.spec_from_file_location(
        "_coreai_export_contract",
        PACKAGE / "receipt_layoutlm/export_coreai.py",
    )
    assert spec is not None and spec.loader is not None
    exporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(exporter)
    monkeypatch.setattr(exporter, "_sample_inputs", lambda *_: ())

    if converter_rejects_graph:
        with pytest.raises(exporter.CoreAIExportError, match="rejected graph"):
            exporter.export_coreai(str(checkpoint), str(bundle))
        convert.assert_not_called()
        assert not (bundle / "LayoutLM.aimodel").exists()
    else:
        assert exporter.export_coreai(str(checkpoint), str(bundle)) == str(
            bundle
        )
        convert.assert_called_once_with()
        assert (bundle / "LayoutLM.aimodel").is_dir()
        assert (bundle / "vocab.txt").read_text() == "[PAD]\n[UNK]\n"
        assert (
            json.loads((bundle / "config.json").read_text())["num_labels"] == 1
        )
        assert json.loads((bundle / "label_map.json").read_text())[
            "labels"
        ] == ["O"]
        provenance = json.loads((bundle / "coreai_export.json").read_text())
        assert provenance["inputs"] == INPUT_NAMES
        assert provenance["outputs"] == ["logits"]

    exported.run_decompositions.assert_called_once_with(decomposition)
    add_graph.assert_called_once_with(
        graph, input_names=INPUT_NAMES, output_names=["logits"]
    )
