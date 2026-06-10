"""source-convert：把来源转成 staging/<source>/source.md + 逐页 profile + 难页 PNG（spec §5）。

后端适配器：md 直通 / pdf 文本(PyMuPDF) 默认；marker/docling/pandoc/pymupdf4llm 作可选适配器
（availability check，不可用就降级或标 needs_vision）。本期不强制安装重后端。
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import importlib.util as _ilu
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import source_profile


class BackendUnavailable(RuntimeError):
    pass


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
    return "".join(parts).strip() + "\n", pages
