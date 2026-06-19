"""source-convert：把来源转成 staging/<source>/source.md + 逐页 profile + 难页 PNG。

后端：md 直通；pdf 用 PyMuPDF 抽纯文本 + 逐页 profile。

公式保真走 route B（读图兜底）：PyMuPDF 纯文本会把上/下标与分数拍平失真（公式密集书严重），
故每个公式风险页（needs_vision）始终渲染整页 PNG，由 ingest 时 LLM 读图写 KaTeX 保真
（lint 硬规则强制 lesson 内嵌源图）。不依赖任何重型 OCR/ML 后端（marker/surya 已评估，4GB 显存太慢弃用）。
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
        sig = source_profile.visual_signals(page)
        prof = source_profile.profile_page(i + 1, text, image_count=sig["image_count"],
                                           n_draw=sig["n_draw"], n_tables=sig["n_tables"])
        pages.append(prof)
        parts.append(f"\n\n<!-- page {i + 1} -->\n\n{text.strip()}\n")
        if prof["needs_vision"]:
            assets_dir.mkdir(parents=True, exist_ok=True)
            pix = page.get_pixmap(matrix=fitz.Matrix(3, 3))
            pix.save(str(assets_dir / f"p{i + 1:04d}.png"))
    doc.close()
    n_vision = sum(1 for p in pages if p.get("needs_vision"))
    if n_vision:
        print(f"[info] source-convert：本源约 {n_vision} 个难页（公式 / 矢量图 / 表 / 图表标题）"
              f"已渲染整页 PNG（assets/pXXXX.png），由 ingest 读图保真（route B）。"
              f"纯文本抽取会拍平上/下标与分数、且看不见矢量图与无框线表，故以源图为准。",
              file=sys.stderr)
    return "".join(parts).strip() + "\n", pages
