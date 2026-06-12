"""source-convert：把来源转成 staging/<source>/source.md + 逐页 profile + 难页 PNG（spec §5）。

后端适配器：md 直通 / pdf 文本(PyMuPDF) 默认；marker/docling/pandoc/pymupdf4llm 作可选适配器
（availability check，不可用就降级或标 needs_vision）。本期不强制安装重后端。

公式保真：pymupdf 纯文本会把上/下标与分数拍平失真（公式密集书严重）。检测到高保真后端
（marker/pymupdf4llm）缺席且本源有公式风险页时，convert() 会发醒目降级告警；同时难页（needs_vision）
始终渲染整页 PNG 供 /ingest 读图兜底。装 marker 建议用独立 venv（避免污染共享环境的 torch/transformers）。
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import importlib.util as _ilu
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import source_profile

# 高保真 PDF 后端（公式可保真为 LaTeX / 更好的结构化 markdown）。缺席则降级到 pymupdf 纯文本。
_HIFI_PDF_BACKENDS = ("marker", "pymupdf4llm")


class BackendUnavailable(RuntimeError):
    pass


def available_hifi_backend() -> str | None:
    """返回第一个可用的高保真 PDF 后端名；都不可用返回 None（→ pymupdf 纯文本，公式会失真）。"""
    for name in _HIFI_PDF_BACKENDS:
        if _ilu.find_spec(name) is not None:
            return name
    return None


def _sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def convert(src_path, *, out_dir, fmt: str) -> dict:
    """返回 {source_md, sha256, assets_dir, pages:[profile...], needs_vision_pages:[int...]}。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    assets_dir = out_dir / "assets"
    if fmt == "md":
        md, pages = _convert_markdown(Path(src_path))
    elif fmt == "pdf":
        md, pages = _convert_pdf_text(Path(src_path), assets_dir)
    else:
        raise BackendUnavailable(f"no P1 backend for fmt={fmt} (docx/pptx 适配器后续期实现)")
    source_md = out_dir / "source.md"
    source_md.write_text(md, encoding="utf-8")
    return {
        "source_md": str(source_md),
        "sha256": _sha256_text(md),
        "assets_dir": str(assets_dir),
        "pages": pages,
        "needs_vision_pages": [p["page"] for p in pages if p.get("needs_vision")],
    }


def _convert_markdown(src: Path):
    text = src.read_text(encoding="utf-8")
    pages = [source_profile.profile_page(1, text, image_count=0)]
    return text, pages


def _convert_pdf_text(src: Path, assets_dir: Path):
    if _ilu.find_spec("fitz") is None:
        raise BackendUnavailable("pymupdf (fitz) not installed")
    import fitz  # PyMuPDF（已装）
    doc = fitz.open(str(src))
    parts, pages = [], []
    for i in range(len(doc)):
        page = doc[i]
        text = page.get_text()
        img_count = len(page.get_images())
        prof = source_profile.profile_page(i + 1, text, image_count=img_count)
        pages.append(prof)
        parts.append(f"\n\n<!-- page {i + 1} -->\n\n{text.strip()}\n")
        if prof["needs_vision"]:
            assets_dir.mkdir(parents=True, exist_ok=True)
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            pix.save(str(assets_dir / f"p{i + 1:04d}.png"))
    doc.close()
    n_formula = sum(1 for p in pages if p.get("needs_vision"))
    if n_formula and available_hifi_backend() is None:
        print(f"[warn] source-convert 使用 pymupdf 纯文本后端：本源约 {n_formula} 个公式风险页的"
              f"上/下标与分数会被拍平失真（例如 (a−c)q1²−bq1q2 会断行错位）。难页已渲染整页 PNG "
              f"供 /ingest 读图兜底；如需公式保真为 LaTeX，请在独立 venv 安装 marker-pdf 后重跑。",
              file=sys.stderr)
    return "".join(parts).strip() + "\n", pages
