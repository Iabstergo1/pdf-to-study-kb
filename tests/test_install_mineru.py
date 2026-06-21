"""install_mineru 的选版纯逻辑测试（无需 GPU / 网络）。

只测 candidate_cu_tags / select_wheel / index_url —— 实际 pip / nvidia-smi 调用是副作用，不在此覆盖。
"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


im = _load("install_mineru")


def test_index_url():
    assert im.index_url("130") == "https://download.pytorch.org/whl/cu130"


def test_candidate_tags_high_driver_prefers_newest():
    tags = im.candidate_cu_tags("13.1")
    assert tags[0] == "130"          # 最新优先
    assert "118" in tags             # 旧的也在候选
    assert tags == sorted(tags, key=lambda t: -int(t))


def test_candidate_tags_filtered_by_driver_cuda():
    tags = im.candidate_cu_tags("12.4")
    assert "130" not in tags and "128" not in tags and "126" not in tags
    assert tags[0] == "124"          # 不超过驱动 CUDA 的最新
    assert "121" in tags and "118" in tags


def test_candidate_tags_no_gpu_is_empty():
    assert im.candidate_cu_tags(None) == []   # 无 GPU → CPU，不选 CUDA wheel


def test_select_wheel_picks_newest_available():
    # prober 只认 cu128（如该版本号仅在 cu128 索引存在）
    prober = lambda tag, base: tag == "128"
    res = im.select_wheel("13.1", "2.12.1", prober)
    assert res == (im.index_url("128"), "128")


def test_select_wheel_none_when_unavailable():
    assert im.select_wheel("13.1", "2.12.1", lambda tag, base: False) is None


def test_select_wheel_none_without_gpu():
    assert im.select_wheel(None, "2.12.1", lambda tag, base: True) is None
