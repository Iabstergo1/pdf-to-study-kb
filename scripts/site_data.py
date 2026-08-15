"""Deterministic association and aggregate data for the offline site.

Classification rule for Explorer/breadcrumb:

* ``overview.md`` is the permanent overview branch.
* ``type`` in ``topic | comparison | synthesis`` always belongs to the single
  ``跨域综合`` branch, even when a source happens to carry a ``domain`` field.
  Synthesis-layer pages are cross-domain by design, and this rule guarantees a
  page type is never split across both a real domain and a top-level pseudo
  domain.
* every other page belongs to its domain frontmatter branch.

Graph data is loaded from the existing ``graph-data.generated.json`` only; this
module never recalculates communities or edges. The site copy strips
``generated_at`` and drops audit/learning-path/insight fields that the site
views do not consume, while preserving node/edge/community identity and counts.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import page_rules
import site_render


OVERVIEW_BRANCH = "overview"
CROSS_DOMAIN_BRANCH = "cross-domain"
CROSS_DOMAIN_LABEL = "跨域综合"
CROSS_DOMAIN_TYPES = frozenset({"topic", "comparison", "synthesis"})


def classify_page(page: dict) -> dict:
    """Return the stable Explorer branch for one page."""
    rel = str(page.get("rel") or "")
    ptype = str(page.get("type") or "")
    if rel == "overview.md":
        return {"key": OVERVIEW_BRANCH, "label": "总览", "kind": "overview"}
    if ptype in CROSS_DOMAIN_TYPES:
        return {"key": CROSS_DOMAIN_BRANCH, "label": CROSS_DOMAIN_LABEL,
                "kind": "cross-domain"}
    domain = str(page.get("domain") or "其他")
    return {"key": f"domain:{domain}", "label": domain, "kind": "domain"}


def build_explorer_tree(pages: list[dict]) -> list[dict]:
    """Group pages into overview / cross-domain / domain -> type -> page."""
    branches: dict[str, dict] = {}
    for page in pages:
        branch = classify_page(page)
        ptype = str(page.get("type") or "")
        item = branches.setdefault(branch["key"], {
            "domain": branch["label"],
            "domain_key": branch["key"],
            "types": {},
        })
        item["types"].setdefault(ptype, []).append({
            "path": page["rel"],
            "title": page["title"],
        })

    def branch_order(item):
        key, value = item
        if key == OVERVIEW_BRANCH:
            return (0, "")
        if key == CROSS_DOMAIN_BRANCH:
            return (1, "")
        return (2, value["domain"])

    tree: list[dict] = []
    for _key, branch in sorted(branches.items(), key=branch_order):
        types = [
            {
                "type": ptype,
                "pages": sorted(pages_in_type, key=lambda item: item["path"]),
            }
            for ptype, pages_in_type in sorted(branch["types"].items())
        ]
        tree.append({
            "domain": branch["domain"],
            "domain_key": branch["domain_key"],
            "types": types,
        })
    return tree


def ordered_paths(pages: list[dict]) -> list[str]:
    out: list[str] = []
    for branch in build_explorer_tree(pages):
        for ptype in branch["types"]:
            out.extend(page["path"] for page in ptype["pages"])
    return out


def adjacent_paths(path: str, ordered: list[str]) -> tuple[str | None, str | None]:
    try:
        index = ordered.index(path)
    except ValueError:
        return None, None
    previous = ordered[index - 1] if index > 0 else None
    following = ordered[index + 1] if index + 1 < len(ordered) else None
    return previous, following


def breadcrumb(page: dict) -> list[dict]:
    branch = classify_page(page)
    ptype = str(page.get("type") or "")
    return [
        {"label": branch["label"], "kind": "domain",
         "target": branch["key"]},
        {"label": ptype, "kind": "type",
         "target": f"{branch['key']}|type:{ptype}"},
        {"label": page["title"], "kind": "page", "target": page["rel"]},
    ]


def build_backlinks(pages: list[dict], page_set: set[str] | None = None) -> dict[str, list[dict]]:
    """Reverse wikilink index: target path -> sorted source page identities."""
    known = set(page_set) if page_set is not None else {page["rel"] for page in pages}
    backlinks: dict[str, set[str]] = {}
    for page in pages:
        targets = set()
        for raw_target, _alias in site_render.extract_wikilinks(page["body"]):
            resolved = site_render._resolve_target(raw_target, known)
            if resolved is not None and resolved != page["rel"]:
                targets.add(resolved)
        for target in targets:
            backlinks.setdefault(target, set()).add(page["rel"])
    titles = {page["rel"]: page["title"] for page in pages}
    return {
        target: [
            {"path": source, "title": titles.get(source, Path(source).stem)}
            for source in sorted(sources)
        ]
        for target, sources in sorted(backlinks.items())
    }


_NODE_FIELDS = ("aliases", "community_id", "id", "label", "path", "summary", "type", "weight")
_EDGE_FIELDS = ("id", "relation", "source", "target", "weight")
_COMMUNITY_FIELDS = ("id", "label", "node_ids", "representative_node_ids", "weight")


def sanitize_graph_data(data: dict) -> dict:
    """Build the deterministic compact graph payload used by B2-B4.

    ``generated_at`` is removed because it would break byte determinism. Audit
    evidence, source attribution and insights are not consumed by the site graph
    views and are therefore omitted. Community representatives and learning-path
    node IDs are retained so the embedded graph honours the same degradation
    contract as ``knowledge-graph.generated.html``.
    """
    return {
        "nodes": sorted(
            (
                {field: node.get(field, [] if field == "aliases" else 0 if field == "weight" else "")
                 for field in _NODE_FIELDS}
                for node in data.get("nodes", [])
            ),
            key=lambda node: str(node["id"]),
        ),
        "edges": sorted(
            (
                {field: edge.get(field, 0 if field == "weight" else "")
                 for field in _EDGE_FIELDS}
                for edge in data.get("edges", [])
            ),
            key=lambda edge: str(edge["id"]),
        ),
        "communities": sorted(
            (
                {field: community.get(
                    field,
                    [] if field in {"node_ids", "representative_node_ids"}
                    else 0 if field == "weight" else ""
                )
                 for field in _COMMUNITY_FIELDS}
                for community in data.get("communities", [])
            ),
            key=lambda community: str(community["id"]),
        ),
        "learning_paths": sorted(
            (
                {"id": path.get("id", ""),
                 "node_ids": path.get("node_ids", [])}
                for path in data.get("learning_paths", [])
            ),
            key=lambda path: str(path["id"]),
        ),
    }


def build_quiz_items(
    pages: list[dict],
    render_answer,
    *,
    vault: str | Path | None = None,
    with_images: bool = False,
) -> list[dict]:
    page_set = {page["rel"] for page in pages}
    vault_path = Path(vault) if vault is not None else Path(".")
    items: list[dict] = []
    for page in pages:
        for card in page_rules.extract_question_cards(page["body"]):
            items.append({
                "rel": page["rel"],
                "title": page["title"],
                "stem": card["stem"],
                "answer_html": render_answer(
                    card["answer"], page_set, vault_path, with_images
                ),
            })
    return items


def build_proposition_items(pages: list[dict]) -> list[dict]:
    items: list[dict] = []
    for page in pages:
        for name, statement in page_rules.extract_propositions(page["body"]):
            items.append({
                "rel": page["rel"],
                "title": page["title"],
                "name": name,
                "statement": statement,
            })
    return items
