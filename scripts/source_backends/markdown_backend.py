"""Markdown 后端（Spec 1）：原文即 source.md（顺读视图）；按 _sections 出 section-level 块。

复刻现有 Markdown 行为，不引入新解析策略。heading_path 与 windowing._sections 一致
（直接标题、不嵌套），保证 block 窗 ≈ char 窗等价。section 块的 text = 该段完整 Markdown
（heading 行 + 正文），绝不拆走正文；source_md[char_start:char_end] == block.text。

本地图片引用（标准 ![alt](path) / Obsidian ![[path]]）：先用 source_profile.mask_markdown_code_spans
排除围栏代码块/行内代码里的假引用，再按原始 md 文件所在目录解析相对路径。命中的本地文件、且
扩展名在下游 `_sync_assets`（pipeline.py）/ `build_source_images`（wiki_gate.py）白名单
（png/jpg/jpeg）内的才复制进 out_dir/assets，把 asset_path 挂到**该图片所属 section 的既有块**上
——刻意不新建独立的 image 块：windowing.py 的 `_pack_blocks`/`_sections_from_blocks` 假设块列表
按文档顺序排列、互不重叠（与 mineru_backend 的产出形状一致），插入一个 char 区间嵌套在某个
section 块内部的额外块会让 `_pack_blocks` 把该 section 的窗口范围错误收缩到嵌套块的位置
（已用集成脚本实测复现），所以只挂字段、不新增块。`asset_path` 是单值字段，一个 section 有
多张本地图时只挂第一张（并打 `multiple-local-images` risk_flag，随块传进窗口证据），其余仍
复制字节（不丢）但计入 parse_report.warnings。目标文件名按解析后绝对路径的 sha256 前 12 位加
前缀，避免不同目录下同名文件互相覆盖、同时保持同一路径重复转换时的幂等命名。外链
（http/https/data:）不下载，原样留在正文；本地解析不到、或扩展名不在下游白名单内的引用同样
计入 warnings（不硬失败——来源作者自己的悬空链接不该挡住整份文档入库）。
"""
from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import source_profile
import chaptering
import windowing
import source_artifacts as sa

# 后端归一版本：本文件的图片解析/复制/挂载逻辑实质变化就 +1，折进 converted_input_hash
# （与 mineru_backend.MINERU_ADAPTER_VERSION 同规），使存量 md converted 缓存在逻辑升级后
# 自动失效、强制重转换（否则只改这里、不动 source_profile.py 时容易忘记版本要跟着变）。
# v1: 本地图片资产复制 + 挂载到所属 section 块（代码块过滤、括号文件名、防碰撞命名、格式白名单）。
MARKDOWN_BACKEND_VERSION = "1"

# 与 pipeline._sync_assets / wiki_gate.build_source_images 的下游同步白名单保持一致——
# 复制下游根本不认的格式只会造成"staging 里看着可追溯、实际到不了 wiki/assets"的假象。
_SYNCED_EXTENSIONS = {".png", ".jpg", ".jpeg"}

# target：默认吃到闭括号前的一切（含空格、引号 title），但遇到一层平衡括号
# （`fig(1).png` 这类常见文件名）整体消费，不在其内部的 `)` 处提前截断。
_IMAGE_STD = re.compile(r"!\[[^\]]*\]\(((?:\([^()]*\)|[^)])+)\)")
_IMAGE_EMBED = re.compile(r"!\[\[([^\]]+)\]\]")
_EXTERNAL_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://|^data:")
_TITLE_SUFFIX = re.compile(r"""^(\S+)(?:\s+["'].*["'])?$""")


def _image_target(raw: str) -> str:
    """标准语法捕获组可能带 `path "title"` 形式的标题，剥离后只留路径。"""
    raw = raw.strip()
    m = _TITLE_SUFFIX.match(raw)
    return (m.group(1) if m else raw).strip()


def _find_image_refs(md: str) -> list:
    """扫描标准 + Obsidian 两种图片语法，按出现顺序返回 (start, end, target)。
    调用方应传入已用 source_profile.mask_markdown_code_spans 处理过的文本，排除代码块假引用；
    掩码保持字符位置不变，故这里返回的 start 对原始 md 文本同样有效。"""
    found = []
    for m in _IMAGE_STD.finditer(md):
        found.append((m.start(), m.end(), _image_target(m.group(1))))
    for m in _IMAGE_EMBED.finditer(md):
        target = m.group(1).split("|", 1)[0].split("#", 1)[0].strip()
        found.append((m.start(), m.end(), target))
    found.sort(key=lambda t: t[0])
    return found


def _asset_dst_name(candidate: Path) -> str:
    """确定性、防碰撞的资产文件名：解析后绝对路径 sha256 前 12 位 + 原始 basename。
    不同目录下同名文件（如两处都叫 fig.png）不会互相覆盖；同一路径重复转换产出同名文件（幂等）。"""
    digest = hashlib.sha256(str(candidate.resolve()).encode("utf-8")).hexdigest()[:12]
    return f"{digest}-{candidate.name}"


def _resolve_and_copy_local_images(md: str, src_dir: Path, assets_out_dir: Path):
    """本地图片引用 → 复制字节进 assets_out_dir。

    返回 (list[(start, asset_rel_path)], list[warning])，按出现顺序。外链跳过、不计
    warning；本地路径解析不到文件、或扩展名不在下游同步白名单内，计入 warning。"""
    copied = []
    warnings = []
    masked = source_profile.mask_markdown_code_spans(md)
    for start, _end, target in _find_image_refs(masked):
        if _EXTERNAL_SCHEME.match(target):
            continue
        candidate = Path(target)
        if not candidate.is_absolute():
            candidate = src_dir / candidate
        if not candidate.is_file():
            warnings.append(f"markdown image reference not found locally: {target}")
            continue
        if candidate.suffix.lower() not in _SYNCED_EXTENSIONS:
            allowed = "/".join(sorted(e.lstrip(".") for e in _SYNCED_EXTENSIONS))
            warnings.append(
                f"markdown image reference uses an extension not synced downstream "
                f"(only {allowed} reach wiki/assets): {target}")
            continue
        assets_out_dir.mkdir(parents=True, exist_ok=True)
        dst = assets_out_dir / _asset_dst_name(candidate)
        shutil.copy2(candidate, dst)
        copied.append((start, f"assets/{dst.name}"))
    return copied, warnings


def convert(src_path, *, out_dir, input_hash: str):
    src_path = Path(src_path)
    out_dir = Path(out_dir)
    md = src_path.read_text(encoding="utf-8")
    image_count = source_profile.count_markdown_image_refs(md)
    pages = [source_profile.profile_page(1, md, image_count=image_count)]
    chapters = chaptering.chapters_from_toc([], n_pages=1)
    blocks = []
    heading_count = 0
    for i, (path, s, e) in enumerate(windowing._sections(md)):
        seg = md[s:e]
        first = seg.splitlines()[0] if seg.strip() else ""
        m = windowing._HEADING.match(first)
        if m:
            heading_count += 1
        block_id = f"b{i + 1:06d}"
        blocks.append(sa.SourceBlock(
            block_id=block_id, type="heading" if m else "text", text=seg,
            page=1, char_start=s, char_end=e,
            text_level=(len(m.group(1)) if m else None), heading_path=path,
            risk_flags=[], source_ref=sa.block_source_ref(1, block_id)))

    local_images, image_warnings = _resolve_and_copy_local_images(
        md, src_path.parent, out_dir / "assets")
    linked_sections: set = set()
    for start, asset_path in local_images:
        owner = next((b for b in blocks if b.char_start <= start < b.char_end), None)
        if owner is None:
            continue
        if owner.block_id in linked_sections:
            image_warnings.append(
                f"multiple local images in section '{owner.heading_path}': "
                f"{asset_path} copied but not linked (asset_path already set)")
            if "multiple-local-images" not in owner.risk_flags:
                owner.risk_flags.append("multiple-local-images")
            continue
        owner.asset_path = asset_path
        if "image" not in owner.risk_flags:
            owner.risk_flags.append("image")
        linked_sections.add(owner.block_id)

    advice = sa.RoutingAdvice(recommended_backend="markdown",
                              structured_reparse_recommended=False)
    report = sa.build_parse_report("markdown", input_hash=input_hash, routing_advice=advice,
                                   section_count=len(blocks), heading_count=heading_count,
                                   block_count=len(blocks), warnings=image_warnings)
    return sa.BackendResult(source_md=md, blocks=blocks, chapters=chapters,
                            pages=pages, report=report, needs_vision_pages=[])
