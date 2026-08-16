"""Static site exporter facade — one self-contained offline HTML for the published wiki.

P4 Batch A splits the P3 single module by responsibility:

* :mod:`site_render` owns Markdown/callout/image/math rendering and KaTeX assets.
* :mod:`site_data` owns Explorer classification, ordering, breadcrumbs,
  backlinks, graph sanitization, quiz/proposition items, and filter options.
* :mod:`site_layout` owns the HTML/CSS/JS reading shell and calls the shared
  :mod:`site_data` model for navigation structure.
* :mod:`site_media` owns P2 source-page media parsing, staging, thumbnails, and
  the source-pages panel.
* This module owns vault discovery, asset orchestration, and the pure
  ``build_site`` helper plus the filesystem-writing ``write_site`` API used by
  ``pipeline.py``.

``export-site`` remains a manual distribution action, not a vault derived layer:
it is not in any ``_DERIVED`` set, not in ``derived_violations``, not wired into
the publish hook, and not in ``retract-source``'s derived rebuild tuple.
"""
from __future__ import annotations

import copy
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mdpage
import graph_html
import site_data
import site_layout
import site_media
import site_render
import wiki_gate


def _string_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _source_refs(value) -> list[str]:
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    out = []
    for item in items:
        if isinstance(item, dict) and item.get("source"):
            out.append(str(item["source"]))
        else:
            out.append(str(item))
    return out


def _vault_pages(vault: Path) -> list[dict]:
    """Published non-derived pages, sorted by vault-relative path."""
    out: list[dict] = []
    for f in sorted(vault.rglob("*.md")):
        rel = f.relative_to(vault).as_posix()
        if rel in wiki_gate._DERIVED or rel.split("/")[0] in wiki_gate._EXCLUDE_TOP:
            continue
        meta, body = mdpage.read_page(f)
        if meta.get("status") != "published":
            continue
        title = str(meta.get("title") or meta.get("canonical_name") or Path(rel).stem)
        domain = str(meta.get("domain") or rel.split("/")[0])
        ptype = str(meta.get("type") or "")
        out.append({"rel": rel, "title": title, "domain": domain,
                    "type": ptype, "body": body,
                    "obsidian_uri": wiki_gate.obsidian_uri(vault, rel),
                    "aliases": _string_list(meta.get("aliases")),
                    "source_refs": _source_refs(meta.get("source_refs"))})
    return out


class SiteResult:
    def __init__(self, path: Path, page_count: int):
        self.path = path
        self.page_count = page_count


def _graph_payload(vault: Path) -> dict:
    path = vault / "graph-data.generated.json"
    if not path.is_file():
        return {"nodes": [], "edges": [], "communities": []}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return site_data.sanitize_graph_data(raw)


def _graph_view_html(vault: Path, graph_payload: dict) -> str:
    if not graph_payload.get("nodes"):
        return ""
    # R5: keep the same MAX_HTML_NODES / MAX_HTML_EDGES degradation contract as
    # knowledge-graph.generated.html. This HTML is meant to be shared, so it must
    # not be more aggressive than the standalone graph.
    view_payload = copy.deepcopy(graph_payload)
    for node in view_payload.get("nodes", []):
        node["obsidian_uri"] = wiki_gate.obsidian_uri(vault, node["path"])
    return graph_html.to_html(
        view_payload,
        site_embed=True,
    )


def _source_panel_rels(pages: list[dict]) -> set[str]:
    knowledge_types = {"concept", "topic", "comparison", "synthesis"}
    return {page["rel"] for page in pages if page.get("type") in knowledge_types}


def _page_asset_mapping(vault: Path, workspace: Path) -> dict[str, list[str]]:
    markdown = wiki_gate.build_source_images(vault, workspace)
    return site_media.load_page_assets(markdown)


def _reset_site_assets(site_dir: Path) -> None:
    root = site_dir.resolve()
    assets_dir = (root / "assets").resolve()
    if assets_dir.parent != root:
        raise RuntimeError(f"unexpected assets directory: {assets_dir}")
    if assets_dir.exists():
        shutil.rmtree(assets_dir)


def _render_html(pages: list[dict], vault: Path, with_images: bool,
                 katex_assets: dict,
                 source_media: dict[str, list[dict]] | None = None) -> str:
    graph_payload = _graph_payload(vault)
    quiz_items = site_data.build_quiz_items(
        pages, site_render.render_page_body, vault=vault, with_images=with_images
    )
    proposition_items = site_data.build_proposition_items(pages)
    return site_layout.render_html(
        pages,
        vault=vault,
        with_images=with_images,
        katex_assets=katex_assets,
        render_page_body=site_render.render_page_body,
        graph_payload=graph_payload,
        graph_view_html=_graph_view_html(vault, graph_payload),
        quiz_items=quiz_items,
        proposition_items=proposition_items,
        source_media=source_media,
    )


def build_site(vault: str | Path, *,
               vendor_dir: str | Path | None = None,
               katex_assets: dict | None = None) -> str:
    """Build the default self-contained HTML without touching the output tree.

    This is the pure rendering helper used by tests and byte-determinism checks.
    Directory-mode media is only assembled by :func:`write_site`; a pure HTML
    builder must not return relative image links to files it did not stage.
    """
    vault = Path(vault)
    pages = _vault_pages(vault)
    if katex_assets is None:
        katex_assets = site_render.load_katex(vendor_dir)
    return _render_html(
        pages, vault, False, katex_assets
    )


def write_site(vault: str | Path, workspace: str | Path | None = None, *,
               with_images: bool = False, vendor_dir: str | Path | None = None,
               katex_assets: dict | None = None) -> SiteResult:
    """Write the default single file or the ``--with-images`` directory.

    Both forms are deterministic and write UTF-8 with LF-only newlines. This
    module is the final byte-encoding boundary for the site; renderers above it
    operate on Unicode text only.
    """
    vault = Path(vault)
    workspace = Path(workspace) if workspace is not None else vault.parent
    pages = _vault_pages(vault)
    if katex_assets is None:
        katex_assets = site_render.load_katex(vendor_dir)
    out = workspace / "pipeline-workspace" / "exports" / "site" / "study-kb.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    source_media = None
    if with_images:
        _reset_site_assets(out.parent)
        mapping = _page_asset_mapping(vault, workspace)
        source_media = site_media.stage_image_files(
            vault,
            out.parent,
            mapping,
            only_rels=_source_panel_rels(pages),
        )
    else:
        _reset_site_assets(out.parent)
    html = _render_html(
        pages, vault, with_images, katex_assets, source_media=source_media
    )
    out.write_text(html, encoding="utf-8", newline="\n")
    return SiteResult(path=out, page_count=len(pages))
