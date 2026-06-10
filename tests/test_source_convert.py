from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


source_profile = _load("source_profile")


def test_needs_vision_high_formula():
    page = {"text_len": 800, "formula_symbols": 25, "image_count": 0}
    assert source_profile.needs_vision(page) is True


def test_needs_vision_blank_image_page():
    page = {"text_len": 10, "formula_symbols": 0, "image_count": 3}
    assert source_profile.needs_vision(page) is True


def test_plain_text_page_no_vision():
    page = {"text_len": 1500, "formula_symbols": 1, "image_count": 0}
    assert source_profile.needs_vision(page) is False


def test_formula_symbol_count_detects_latex_and_greek():
    n = source_profile.count_formula_symbols(r"设 $\alpha$ 与 $\sum_{i} x_i^2$，则 ∫ f dx ≥ 0")
    assert n >= 4


def test_profile_source_md_single_page(tmp_path):
    src = tmp_path / "n.md"
    src.write_text("# T\n\nbody\n", encoding="utf-8")
    pages = source_profile.profile_source(src, fmt="md")
    assert len(pages) == 1 and pages[0]["page"] == 1 and "needs_vision" in pages[0]
