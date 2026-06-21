"""MinerU structured backend（Spec 2）：optional，subprocess 调 MinerU CLI（`-b pipeline`）。

把复杂源（扫描/低文本 PDF、DOCX、PPTX、复杂表格公式图片）归一成项目 artifact 契约
（source.md + blocks.jsonl + chapters.json + parse_report.json + assets/）。
不引入 RAGFlow；不 import MinerU 内部 API（只 subprocess CLI）；为适配低显存 GPU（如 4GB 级）→
默认仅 pipeline 后端，禁 vlm/hybrid。
"""
from __future__ import annotations

import shutil
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import source_artifacts as sa  # noqa: F401（C2/C3 归一用）
import thresholds  # OCR 低置信阈值单一真值（env 可覆盖）
from source_backends import BackendUnavailable

# adapter 版本：归一逻辑实质变化就 +1，折进 converted 缓存键（与 PROFILER/ARTIFACT/WINDOWING 同规）。
# v2：middle.json 深解析（per-page 识别置信度 → ocr_low_confidence 旗标 + parse_report.pages）。
# v3：table 块并入 caption/footnote + 复制表区域源图 asset（HTML 可能有误，留源图供 LLM 核验）。
# v4：table→t{n} / image·chart→f{n} 稳定 element_id；相邻跨页表片段共享 element_id（保守链接）。
MINERU_ADAPTER_VERSION = "4"
DEFAULT_TIMEOUT_SECONDS = 1800


class MineruRunFailed(RuntimeError):
    """MinerU 子进程失败（非零/超时/输出缺失）。dispatcher/pipeline 据此 fail-closed，不静默回退。"""


def mineru_available() -> bool:
    """MinerU 是否可用：读包元数据（不 import MinerU、不依赖 CLI/PATH，保持主进程隔离）。
    MINERU_DISABLE=1 可强制禁用（测试/运维逃生开关）。"""
    import os
    if os.environ.get("MINERU_DISABLE") == "1":
        return False
    try:
        import importlib.metadata as _md
        _md.version("mineru")
        return True
    except Exception:
        return False


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


def _join_caption(cap) -> str:
    """MinerU caption/footnote（list 或 str）→ 单行字符串（空 → ""）。"""
    if isinstance(cap, list):
        return " ".join(str(c) for c in cap if c)
    return str(cap or "")


def normalize_content_list(items, *, assets_src_dir, assets_out_dir):
    """MinerU content_list.json items（按阅读顺序）→ (list[SourceBlock], discarded_count)。

    page = page_idx + 1（1-based）；block_id = b{seq:06d}；source_ref = p{page:04d}#{block_id}。
    heading（text + text_level）更新「当前标题路径」，其后正文块继承之（标题与正文同段，防分裂）。
    header/footer/page_number/discarded 丢弃并计数；char_start/char_end 由 render_source_md 写。
    """
    blocks, discarded, seq, current_path = [], 0, 0, ""
    table_seq, figure_seq, prev_table = 0, 0, None  # prev_table=(page, element_id) 若上一 append 块是表
    for it in items:
        t = (it.get("type") or "").lower()
        if t in _DISCARD_TYPES:
            discarded += 1
            continue                              # 页眉/页脚/页码 跳过，不打断跨页续表
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
            prev_table = None
        elif t in ("text", "list"):
            blocks.append(sa.SourceBlock(type="text", text=it.get("text", ""), risk_flags=[], **common))
            prev_table = None
        elif t == "table":
            body = it.get("table_body") or it.get("html") or it.get("text") or ""
            cap = _join_caption(it.get("table_caption"))
            foot = _join_caption(it.get("table_footnote"))
            text = "\n".join(p for p in (cap, body, foot) if p)   # caption + HTML + footnote
            # 跨页续表（保守链接，非合并）：上一 append 块是表且本表在其下一页相邻 → 共享 element_id。
            if prev_table is not None and page == prev_table[0] + 1:
                element_id = prev_table[1]
            else:
                table_seq += 1
                element_id = f"t{table_seq:04d}"
            # 表区域原图（HTML 结构可能有误）→ 复制为 asset，留源图供 LLM 核验
            asset_path = _copy_asset(it.get("img_path"), assets_src_dir, assets_out_dir)
            blocks.append(sa.SourceBlock(type="table", text=text, risk_flags=["table"],
                                         asset_path=asset_path, element_id=element_id, **common))
            prev_table = (page, element_id)
        elif t in ("equation", "formula"):
            latex = it.get("text") or it.get("latex") or ""
            blocks.append(sa.SourceBlock(type="equation", text=latex, risk_flags=["equation"], **common))
            prev_table = None
        elif t in ("image", "figure", "chart"):   # MinerU 3.x：chart 与 image 同类（ContentType.CHART='chart'）
            cap_text = _join_caption(it.get("img_caption") or it.get("image_caption")
                                     or it.get("chart_caption"))
            asset_path = _copy_asset(it.get("img_path"), assets_src_dir, assets_out_dir)
            figure_seq += 1
            blocks.append(sa.SourceBlock(type="image", text=cap_text, risk_flags=["image"],
                                         asset_path=asset_path, element_id=f"f{figure_seq:04d}", **common))
            prev_table = None
        else:
            blocks.append(sa.SourceBlock(type="text", text=it.get("text", ""), risk_flags=[], **common))
            prev_table = None
    return blocks, discarded


# ── middle.json 结构信号（深解析）：per-page 识别置信度 + 块类型/discarded ──────────
# MinerU 不把 per-page parse 方法(txt/ocr)写进输出，且 span score 在 OCR/文本层页范围重叠，
# 故不做"per-page OCR 二值"；改据 span score 算每页识别置信度，低分页打 ocr_low_confidence。
_TEXT_SPAN_TYPES = ("text", "inline_equation")


def per_page_signals(pdf_info, *, low_conf_min=None, low_conf_mean=None):
    """middle.json 的 pdf_info（逐页）→ per-page 结构信号（纯函数，无 IO）。

    每页产 {page(1-based), block_types, discarded, text_spans, mean_score, min_score,
    low_confidence}。low_confidence = 有文本 span 且 (min<low_conf_min 或 mean<low_conf_mean)。
    阈值缺省取 thresholds（env 可覆盖）；显式传参优先。
    """
    low_conf_min = thresholds.OCR_LOW_CONF_MIN if low_conf_min is None else low_conf_min
    low_conf_mean = thresholds.OCR_LOW_CONF_MEAN if low_conf_mean is None else low_conf_mean
    out = []
    for p in pdf_info or []:
        page = int(p.get("page_idx", 0)) + 1
        btypes, scores = {}, []
        for blk in p.get("para_blocks", []):
            t = blk.get("type")
            btypes[t] = btypes.get(t, 0) + 1
            for ln in blk.get("lines", []):
                for sp in ln.get("spans", []):
                    if sp.get("type") in _TEXT_SPAN_TYPES and isinstance(sp.get("score"), (int, float)):
                        scores.append(float(sp["score"]))
        n = len(scores)
        mean_s = sum(scores) / n if n else None
        min_s = min(scores) if n else None
        low = bool(n and (min_s < low_conf_min or mean_s < low_conf_mean))
        out.append({
            "page": page,
            "block_types": btypes,
            "discarded": len(p.get("discarded_blocks", [])),
            "text_spans": n,
            "mean_score": round(mean_s, 4) if mean_s is not None else None,
            "min_score": round(min_s, 4) if min_s is not None else None,
            "low_confidence": low,
        })
    return out


def _find_middle_json(raw_dir):
    matches = sorted(Path(raw_dir).rglob("*middle*.json"))
    return matches[0] if matches else None


def parse_middle_json(raw_dir, **kw):
    """读 MinerU middle.json → per_page_signals；缺失返回 None（middle.json 可选，不阻塞）。"""
    import json
    mj = _find_middle_json(raw_dir)
    if mj is None:
        return None
    d = json.loads(Path(mj).read_text(encoding="utf-8"))
    return per_page_signals(d.get("pdf_info", []), **kw)


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
            if b.asset_path:                       # 表区域源图随 HTML 一并入 source view
                body += f"\n![table]({b.asset_path})"
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
                        consumed_by_auto_router=False, warnings=None, page_signals=None):
    """MinerU parse_report：selected_backend=mineru、mineru_status=used、pipeline、各类 counts。

    page_signals（middle.json 深解析，可选）→ 报告附 `pages`(逐页结构信号) + `low_confidence_pages`。
    """
    counts = {}
    for b in blocks:
        counts[b.type] = counts.get(b.type, 0) + 1
    advice = routing_advice or sa.RoutingAdvice(recommended_backend="mineru",
                                                structured_reparse_recommended=False)
    structural = {}
    if page_signals is not None:
        structural["pages"] = page_signals
        structural["low_confidence_pages"] = sorted(s["page"] for s in page_signals
                                                    if s["low_confidence"])
    return sa.build_parse_report(
        "mineru", input_hash=input_hash, routing_advice=advice,
        consumed_by_auto_router=consumed_by_auto_router, warnings=warnings,
        mineru_status="used", mineru_backend="pipeline", mineru_version=mineru_version,
        page_count=max((b.page for b in blocks), default=0), block_count=len(blocks),
        text_block_count=counts.get("text", 0), heading_count=counts.get("heading", 0),
        table_count=counts.get("table", 0), equation_count=counts.get("equation", 0),
        image_count=counts.get("image", 0), discarded_count=discarded_count,
        ocr_used=bool(ocr_used), scan_suspected=bool(scan_suspected), **structural)


def _run_mineru(src_path, raw_dir, *, timeout):
    """用当前解释器在**隔离子进程**里跑 mineru_runner.py（do_parse backend=pipeline，禁 vlm/hybrid）。
    主进程不 import MinerU。失败（非零/超时/调用异常）抛 MineruRunFailed（不静默回退）。"""
    import subprocess
    import sys
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    runner = str(Path(__file__).with_name("mineru_runner.py"))
    cmd = [sys.executable, runner, "--input", str(src_path),
           "--output", str(raw_dir), "--backend", "pipeline"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise MineruRunFailed(f"mineru runner timeout after {timeout}s") from e
    except Exception as e:
        raise MineruRunFailed(f"mineru runner invocation failed: {e}") from e
    if proc.returncode != 0:
        raise MineruRunFailed(
            f"mineru runner exited {proc.returncode}: {(proc.stderr or '').strip()[:500]}")
    return raw_dir


def _find_content_list(raw_dir):
    matches = sorted(Path(raw_dir).rglob("*content_list*.json"))
    if not matches:
        raise MineruRunFailed("mineru output missing *_content_list.json")
    return matches[0]


def _mineru_version():
    """读已安装 mineru 包版本（不依赖 CLI）；失败 → "unknown"。"""
    try:
        import importlib.metadata as _md
        return _md.version("mineru")
    except Exception:
        return "unknown"


def failed_report(input_hash, reason: str) -> dict:
    """MinerU 失败时的最小 parse_report（dispatcher 落盘以审计；不静默回退）。"""
    advice = sa.RoutingAdvice(recommended_backend="mineru", structured_reparse_recommended=True)
    return sa.build_parse_report("mineru", input_hash=input_hash, routing_advice=advice,
                                 mineru_status="failed", mineru_backend="pipeline",
                                 mineru_failed=True, failure_reason=str(reason),
                                 warnings=[f"mineru_failed: {reason}"])


def convert(src_path, *, out_dir, input_hash, timeout=DEFAULT_TIMEOUT_SECONDS):
    if not mineru_available():
        raise BackendUnavailable(
            "MinerU 未安装：--backend mineru 需要 MinerU（本项目仅用 pipeline 后端）。"
            "一键安装：python scripts/install_mineru.py（按机型选 torch；见 requirements.txt 可选段）；"
            "未安装时请用 --backend pymupdf，或 --backend auto 的轻量路径。")
    import json
    out_dir = Path(out_dir)
    raw_dir = out_dir / "mineru_raw"
    _run_mineru(src_path, raw_dir, timeout=timeout)          # 失败抛 MineruRunFailed（不静默回退）
    content_list_path = _find_content_list(raw_dir)
    items = json.loads(content_list_path.read_text(encoding="utf-8"))
    blocks, discarded = normalize_content_list(
        items, assets_src_dir=content_list_path.parent, assets_out_dir=out_dir / "assets")
    # middle.json 深解析（可选）：低识别置信度页 → 给该页 blocks 追加 ocr_low_confidence 风险旗标
    page_signals = parse_middle_json(raw_dir)
    if page_signals:
        low_pages = {s["page"] for s in page_signals if s["low_confidence"]}
        for b in blocks:
            if b.page in low_pages and "ocr_low_confidence" not in b.risk_flags:
                b.risk_flags.append("ocr_low_confidence")
    source_md = render_source_md(blocks)
    page_count = max((b.page for b in blocks), default=0)
    chapters = build_chapters(blocks, page_count)
    report = build_mineru_report(blocks, input_hash=input_hash, discarded_count=discarded,
                                 mineru_version=_mineru_version(), page_signals=page_signals)
    needs_vision_pages = sorted({b.page for b in blocks if b.risk_flags})
    return sa.BackendResult(source_md=source_md, blocks=blocks, chapters=chapters,
                            pages=[], report=report, needs_vision_pages=needs_vision_pages)
