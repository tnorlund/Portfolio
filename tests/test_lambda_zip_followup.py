"""Budget checks must account for the actual function and layer contents."""

from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from scripts import lambda_zip_budget as budget


def archive(path, size):
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as output:
        output.writestr("package/payload.bin", b"0" * size)
    return path


@pytest.mark.parametrize(
    ("size", "project_ok", "aws_ok"),
    [
        (19, True, True),
        (20, False, True),
        (24, False, True),
        (26, False, False),
    ],
)
def test_strict_project_budget_and_separate_hard_limit(
    tmp_path, monkeypatch, size, project_ok, aws_ok
):
    # Scale the thresholds so real ZIP fixtures stay small.
    monkeypatch.setattr(budget, "PROJECT_BUDGET_BYTES", 20)
    monkeypatch.setattr(budget, "AWS_LIMIT_BYTES", 25)
    result = budget.measure_archives(
        [archive(tmp_path / "function.zip", size)]
    )
    assert result["unzipped_bytes"] == size
    assert result["within_project_budget"] is project_ok
    assert result["within_aws_limit"] is aws_ok


def test_layers_count_toward_the_same_budget(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(budget, "PROJECT_BUDGET_BYTES", 20)
    function = archive(tmp_path / "function.zip", 12)
    layer = archive(tmp_path / "layer.zip", 12)
    assert budget.main([str(function)]) == 0
    assert budget.main([str(function), str(layer)]) == 1
    assert '"unzipped_bytes": 24' in capsys.readouterr().out


def test_compressed_size_is_not_the_budget(tmp_path):
    path = archive(tmp_path / "compressed.zip", 100_000)
    assert path.stat().st_size < 1000
    assert budget.measure_archives([path])["unzipped_bytes"] == 100_000


@pytest.mark.parametrize("kind", ["empty", "invalid", "missing"])
def test_invalid_artifacts_cannot_pass(tmp_path, kind):
    path = tmp_path / "function.zip"
    if kind == "empty":
        with ZipFile(path, "w"):
            pass
    elif kind == "invalid":
        path.write_bytes(b"not a zip")
    with pytest.raises(SystemExit) as error:
        budget.main([str(path)])
    assert error.value.code == 2


def test_default_budget_keeps_fifty_mib_of_headroom():
    assert budget.PROJECT_BUDGET_BYTES == 200 * 1024 * 1024
    assert budget.AWS_LIMIT_BYTES == 250 * 1024 * 1024
