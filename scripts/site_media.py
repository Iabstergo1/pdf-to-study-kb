"""Directory-mode source-page media for the P4 offline site.

Page-level attribution comes exclusively from ``wiki_gate.build_source_images``,
the existing P2 implementation. This module parses that generated index's
``page 级`` section and stages original images plus deterministic PyMuPDF PNG
thumbnails under the export directory.

Thumbnail rendering uses fixed ``fitz.Matrix`` parameters and writes
``pixmap.tobytes("png")`` directly, so repeated exports produce byte-identical
files. Pillow is deliberately not used: PyMuPDF is already a project dependency.
"""
from __future__ import annotations

import re
import shutil
import sys
from html import escape as _html_escape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fitz


_PAGE_ENTRY = re.compile(r"^- \[\[([^\]|]+)(?:\|[^\]]*)?\]\]\s*→\s*(.+)$")
_ASSET_LINK = re.compile(r"\]\((assets/[^)]+)\)")
_PAGE_NUMBER = re.compile(r"^p(\d+)\.(?:png|jpg|jpeg)$", re.IGNORECASE)


def load_page_assets(markdown: str) -> dict[str, list[str]]:
    """Parse page-level entries from P2's generated index; ignore source-level."""
    merged: dict[str, set[str]] = {}
    mode = ""
    for line in markdown.splitlines():
        if line.startswith("## "):
            mode = ""
        elif line.startswith("**page 级**"):
            mode = "page"
        elif line.startswith("**source 级**"):
            mode = "source"
        elif mode == "page":
            match = _PAGE_ENTRY.match(line)
            if not match:
                continue
            assets = sorted(_ASSET_LINK.findall(match.group(2)))
            if assets:
                merged.setdefault(match.group(1), set()).update(assets)
    return {rel: sorted(assets) for rel, assets in merged.items()}


def _asset_label(rel: str) -> str:
    name = Path(rel).name
    page = _PAGE_NUMBER.match(name)
    return f"p.{int(page.group(1))}" if page else Path(name).stem


def _thumbnail_rel(rel: str) -> str:
    path = Path(rel)
    return (
        Path("assets") / "thumbs" / Path(*path.parts[1:]).with_suffix(".png")
    ).as_posix()


def describe_source_media(
    page_assets: dict[str, list[str]],
    *,
    only_rels: set[str] | None = None,
) -> dict[str, list[dict]]:
    """Return directory-mode media entries without writing files."""
    described: dict[str, list[dict]] = {}
    for rel in sorted(page_assets):
        if only_rels is not None and rel not in only_rels:
            continue
        entries = [
            {
                "original": asset_rel,
                "thumbnail": _thumbnail_rel(asset_rel),
                "label": _asset_label(asset_rel),
            }
            for asset_rel in sorted(page_assets[rel])
        ]
        if entries:
            described[rel] = entries
    return described


def stage_image_files(
    vault: str | Path,
    output_dir: str | Path,
    page_assets: dict[str, list[str]],
    *,
    only_rels: set[str] | None = None,
    thumb_max_dim: int = 320,
) -> dict[str, list[dict]]:
    """Copy originals and write deterministic thumbnails under ``output_dir``."""
    vault = Path(vault)
    output = Path(output_dir)
    described = describe_source_media(page_assets, only_rels=only_rels)
    staged: dict[str, list[dict]] = {}
    for rel, entries in sorted(described.items()):
        staged_entries: list[dict] = []
        for entry in entries:
            asset_rel = entry["original"]
            source = vault / asset_rel
            if not source.is_file():
                continue
            original_out = output / asset_rel
            original_out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, original_out)

            thumb_rel = entry["thumbnail"]
            thumb_out = output / thumb_rel
            thumb_out.parent.mkdir(parents=True, exist_ok=True)
            document = fitz.open(str(source))
            try:
                page = document[0]
                scale = min(1.0, thumb_max_dim / max(page.rect.width, page.rect.height))
                pixmap = page.get_pixmap(
                    matrix=fitz.Matrix(scale, scale),
                    alpha=False,
                )
                thumb_out.write_bytes(pixmap.tobytes("png"))
            finally:
                document.close()
            staged_entries.append(entry)
        if staged_entries:
            staged[rel] = staged_entries
    return staged


def render_source_panel(entries: list[dict]) -> str:
    """Render the reserved ``data-slot="source-pages"`` content."""
    if not entries:
        return ""
    thumbs = []
    for entry in entries:
        original = _html_escape(entry["original"], quote=True)
        thumbnail = _html_escape(entry["thumbnail"], quote=True)
        label = _html_escape(entry["label"])
        thumbs.append(
            f'<a class="source-page-thumb" href="{original}" target="_blank" '
            f'rel="noopener"><img src="{thumbnail}" alt="{label}" loading="lazy"></a>'
        )
    return (
        '<div class="source-pages-panel">'
        '<div class="panel-title"><span>原书难页</span></div>'
        '<p class="source-pages-note">这些图是写本页时所读窗口的难页原图，'
        '可能包含同窗邻近上下文，不等同于关于本页概念的插图。</p>'
        '<div class="source-pages-grid">' + "".join(thumbs) + "</div></div>"
    )
