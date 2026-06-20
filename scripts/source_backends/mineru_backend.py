"""MinerU structured backend（Spec 2）：optional，subprocess 调 MinerU CLI（`-b pipeline`）。

把复杂源（扫描/低文本 PDF、DOCX、PPTX、复杂表格公式图片）归一成项目 artifact 契约
（source.md + blocks.jsonl + chapters.json + parse_report.json + assets/）。
不引入 RAGFlow；不 import MinerU 内部 API（只 subprocess CLI）；硬件 RTX 3050 Ti 4GB →
默认仅 pipeline 后端，禁 vlm/hybrid。
"""
from __future__ import annotations

import shutil
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import source_artifacts as sa  # noqa: F401（C2/C3 归一用）
from source_backends import BackendUnavailable

# adapter 版本：归一逻辑实质变化就 +1，折进 converted 缓存键（与 PROFILER/ARTIFACT/WINDOWING 同规）。
MINERU_ADAPTER_VERSION = "1"
DEFAULT_TIMEOUT_SECONDS = 1800


class MineruRunFailed(RuntimeError):
    """MinerU 子进程失败（非零/超时/输出缺失）。dispatcher/pipeline 据此 fail-closed，不静默回退。"""


def mineru_available() -> bool:
    """是否可调用 MinerU CLI（subprocess 探测，不 import 内部 API）。"""
    return shutil.which("mineru") is not None


# header/footer/page_number/discarded：不进正文块，仅计数（避免污染 source view 与窗口）。
_DISCARD_TYPES = {"header", "footer", "page_number", "page_footnote", "discarded"}


def _copy_asset(img_path, assets_src_dir, assets_out_dir):
    """把 MinerU 图片复制进 staging assets/，返回 staging 相对路径 `assets/<name>`（无则 None）。"""
    if not img_path:
        return None
    src = Path(assets_src_dir) / img_path
    if not src.exists():
        src = Path(assets_src_dir) / Path(img_path).name   # 有些版本 img_path 已是 basename
    out = Path(assets_out_dir)
    out.mkdir(parents=True, exist_ok=True)
    dst = out / Path(img_path).name
    if src.exists():
        shutil.copy2(src, dst)
    return f"assets/{dst.name}"


def normalize_content_list(items, *, assets_src_dir, assets_out_dir):
    """MinerU content_list.json items（按阅读顺序）→ (list[SourceBlock], discarded_count)。

    page = page_idx + 1（1-based）；block_id = b{seq:06d}；source_ref = p{page:04d}#{block_id}。
    heading（text + text_level）更新「当前标题路径」，其后正文块继承之（标题与正文同段，防分裂）。
    header/footer/page_number/discarded 丢弃并计数；char_start/char_end 由 render_source_md 写。
    """
    blocks, discarded, seq, current_path = [], 0, 0, ""
    for it in items:
        t = (it.get("type") or "").lower()
        if t in _DISCARD_TYPES:
            discarded += 1
            continue
        page = int(it.get("page_idx", 0)) + 1
        seq += 1
        block_id = f"b{seq:06d}"
        is_heading = (t == "text" and it.get("text_level"))
        if is_heading:
            current_path = (it.get("text", "") or "").strip()
        common = dict(block_id=block_id, page=page, char_start=0, char_end=0,
                      heading_path=current_path, source_ref=sa.block_source_ref(page, block_id))
        if is_heading:
            blocks.append(sa.SourceBlock(type="heading", text=it.get("text", ""),
                                         text_level=int(it.get("text_level")), risk_flags=[], **common))
        elif t in ("text", "list"):
            blocks.append(sa.SourceBlock(type="text", text=it.get("text", ""), risk_flags=[], **common))
        elif t == "table":
            body = it.get("table_body") or it.get("html") or it.get("text") or ""
            blocks.append(sa.SourceBlock(type="table", text=body, risk_flags=["table"], **common))
        elif t in ("equation", "formula"):
            latex = it.get("text") or it.get("latex") or ""
            blocks.append(sa.SourceBlock(type="equation", text=latex, risk_flags=["equation"], **common))
        elif t in ("image", "figure"):
            cap = it.get("img_caption") or it.get("image_caption") or []
            cap_text = " ".join(cap) if isinstance(cap, list) else str(cap or "")
            asset_path = _copy_asset(it.get("img_path"), assets_src_dir, assets_out_dir)
            blocks.append(sa.SourceBlock(type="image", text=cap_text, risk_flags=["image"],
                                         asset_path=asset_path, **common))
        else:
            blocks.append(sa.SourceBlock(type="text", text=it.get("text", ""), risk_flags=[], **common))
    return blocks, discarded


def render_source_md(blocks) -> str:
    """从归一 blocks 渲染统一 source view（三后端形态一致），并就地写每块 char_start/char_end。

    每块前置 `<!-- block:bId page:N type:T -->` 注释 + 可读正文；block.text 是语义内容
    （标题文本/LaTeX/表 HTML/图注），是该切片的子串，char span 才是定位真值（供窗口/show-window）。
    """
    parts, pos = [], 0
    for b in blocks:
        comment = f"<!-- block:{b.block_id} page:{b.page} type:{b.type} -->\n"
        if b.type == "heading":
            body = ("#" * (b.text_level or 1)) + " " + (b.text or "")
        elif b.type == "table":
            body = b.text or ""
        elif b.type == "equation":
            body = "$$\n" + (b.text or "") + "\n$$"
        elif b.type == "image":
            cap = f" {b.text}" if b.text else ""
            body = f"![figure]({b.asset_path or ''})" + cap
        else:
            body = b.text or ""
        seg = comment + body + "\n\n"
        b.char_start, b.char_end = pos, pos + len(seg)
        parts.append(seg)
        pos += len(seg)
    return "".join(parts)


def build_chapters(blocks, page_count):
    """据 heading blocks 切章（chaptering 复用）；无稳定层级则退化整书一章，不阻塞入库。"""
    import chaptering
    toc = [[b.text_level or 1, (b.heading_path or b.text or "").strip(), b.page]
           for b in blocks if b.type == "heading" and b.text_level]
    return chaptering.chapters_from_toc(toc, max(1, int(page_count)))


def build_mineru_report(blocks, *, input_hash, discarded_count, mineru_version="unknown",
                        ocr_used=False, scan_suspected=False, routing_advice=None,
                        consumed_by_auto_router=False, warnings=None):
    """MinerU parse_report：selected_backend=mineru、mineru_status=used、pipeline、各类 counts。"""
    counts = {}
    for b in blocks:
        counts[b.type] = counts.get(b.type, 0) + 1
    advice = routing_advice or sa.RoutingAdvice(recommended_backend="mineru",
                                                structured_reparse_recommended=False)
    return sa.build_parse_report(
        "mineru", input_hash=input_hash, routing_advice=advice,
        consumed_by_auto_router=consumed_by_auto_router, warnings=warnings,
        mineru_status="used", mineru_backend="pipeline", mineru_version=mineru_version,
        page_count=max((b.page for b in blocks), default=0), block_count=len(blocks),
        text_block_count=counts.get("text", 0), heading_count=counts.get("heading", 0),
        table_count=counts.get("table", 0), equation_count=counts.get("equation", 0),
        image_count=counts.get("image", 0), discarded_count=discarded_count,
        ocr_used=bool(ocr_used), scan_suspected=bool(scan_suspected))


def convert(src_path, *, out_dir, input_hash, timeout=DEFAULT_TIMEOUT_SECONDS):
    if not mineru_available():
        raise BackendUnavailable(
            "MinerU 未安装：--backend mineru 需要 MinerU（本项目仅用 pipeline 后端）。"
            "安装见 requirements-mineru.txt（pip install -r requirements-mineru.txt）；"
            "未安装时请用 --backend pymupdf，或 --backend auto 的轻量路径。")
    # 子进程调用 + content_list 归一在 C2/C3 落地。
    raise NotImplementedError("mineru convert 归一在 C2/C3 实现")
