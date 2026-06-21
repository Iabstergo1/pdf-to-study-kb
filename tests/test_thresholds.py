"""集中阈值 + env 覆盖 + 缓存指纹的测试（确定性）。"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _fresh_thresholds():
    # 每次新 load 以重读环境变量（模块级常量在 import 时读 env）。
    spec = importlib.util.spec_from_file_location("thresholds_fresh", ROOT / "scripts" / "thresholds.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_defaults_match_calibrated_values():
    t = _fresh_thresholds()
    assert t.VECTOR_FIGURE_DRAW == 12 and t.FORMULA_STRONG == 12
    assert t.LOW_TEXT_MEAN == 100 and t.DENSE_RATIO == 0.30
    assert t.OCR_LOW_CONF_MIN == 0.60 and t.OCR_LOW_CONF_MEAN == 0.85
    assert t.TOPIC_THRESHOLD == 6 and t.LESSON_MIN_BODY == 80


def test_env_override_int_and_float(monkeypatch):
    monkeypatch.setenv("STUDY_KB_VECTOR_FIGURE_DRAW", "25")
    monkeypatch.setenv("STUDY_KB_DENSE_RATIO", "0.5")
    t = _fresh_thresholds()
    assert t.VECTOR_FIGURE_DRAW == 25 and t.DENSE_RATIO == 0.5


def test_invalid_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("STUDY_KB_VECTOR_FIGURE_DRAW", "not-a-number")
    t = _fresh_thresholds()
    assert t.VECTOR_FIGURE_DRAW == 12        # 非法值回退默认，不崩


def test_fingerprint_stable_then_changes_on_override(monkeypatch):
    fp_default = _fresh_thresholds().fingerprint()
    assert fp_default.startswith("th-")
    assert _fresh_thresholds().fingerprint() == fp_default      # 默认下恒定
    monkeypatch.setenv("STUDY_KB_VECTOR_FIGURE_DRAW", "25")
    assert _fresh_thresholds().fingerprint() != fp_default      # 覆盖检测阈值 → 指纹变（失效缓存）


def test_observability_threshold_not_in_cache_fingerprint(monkeypatch):
    # 纯观测项（DETECT_RATIO_HIGH）改动不应使 profile/convert 缓存失效。
    fp_default = _fresh_thresholds().fingerprint()
    monkeypatch.setenv("STUDY_KB_DETECT_RATIO_HIGH", "0.5")
    assert _fresh_thresholds().fingerprint() == fp_default


def test_source_profile_reads_threshold_at_call_time(monkeypatch):
    sp = _load("source_profile")
    page = {"n_draw": 15, "formula_symbols": 0, "text_len": 500, "image_count": 0,
            "n_tables": 0, "is_code": False, "eq_lines": 0, "has_caption": False,
            "has_matrix_word": False, "numeric_grid": False}
    assert "vector-figure" in sp.needs_vision_reasons(page)         # 默认 12，n_draw=15 命中
    monkeypatch.setattr(sp.thresholds, "VECTOR_FIGURE_DRAW", 20)    # 调高阈值
    assert "vector-figure" not in sp.needs_vision_reasons(page)     # 同页不再命中（确实读阈值）
