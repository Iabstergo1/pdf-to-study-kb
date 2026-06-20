from pathlib import Path
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import source_backends
from source_backends import mineru_backend as mb
from source_backends import BackendUnavailable


def test_mineru_convert_fail_closed_when_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(mb, "mineru_available", lambda: False)
    src = tmp_path / "x.pdf"
    src.write_text("dummy", encoding="utf-8")
    with pytest.raises(BackendUnavailable) as ei:
        mb.convert(src, out_dir=tmp_path / "o", input_hash="h")
    assert "requirements-mineru" in str(ei.value)


def test_get_backend_by_name_mineru():
    assert source_backends.get_backend_by_name("mineru") is mb
