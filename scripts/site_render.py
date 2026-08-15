"""Markdown, callout, image, and math rendering for the offline static site.

Callout structure comes exclusively from :func:`page_rules.parse_callouts` (the
one and only callout parser); this module only converts the parsed node tree to
HTML.

Math is protected before Markdown rendering and restored afterwards. Restoring
must HTML-escape ``<`` / ``>`` / ``&`` (while preserving existing entities),
because the restored TeX is parsed as HTML before KaTeX reads it — an unescaped
``<`` would be swallowed as a tag start. This is the same class as P1-R4:
target-format escaping at restore time must not be forgotten.
"""
from __future__ import annotations

import base64
import re
import sys
from html import escape as _html_escape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import page_rules

try:
    from markdown_it import MarkdownIt
except ImportError:  # pragma: no cover - exercised only when the dependency is missing
    MarkdownIt = None


_QUOTE_PREFIX = re.compile(r"^ {0,3}((?:> ?)+)")
_WIKILINK = re.compile(r"\[\[([^\]\n]+)\]\]")
_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_MATH_BLOCK = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
_MATH_INLINE = re.compile(r"(?<!\$)\$(?!\s)(.+?)(?<!\$)\$(?!\d)", re.DOTALL)
_HTML_ENTITY = re.compile(r"&(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);")

_MD = None
if MarkdownIt is not None:
    _MD = MarkdownIt("commonmark")
    _MD.enable("table")


def _resolve_target(target: str, page_set: set[str]) -> str | None:
    target = target.split("#", 1)[0].replace("\\", "/")
    if target in page_set:
        return target
    if not target.endswith(".md") and target + ".md" in page_set:
        return target + ".md"
    if target.endswith(".md") and target[:-3] in page_set:
        return target[:-3]
    return None


def extract_wikilinks(text: str) -> list[tuple[str, str]]:
    """Return raw ``(target, alias)`` pairs from wikilinks.

    This is the single wikilink tokenizer used by Markdown rendering and by the
    B1 reverse-index, so the two layers cannot disagree about escaped table
    pipes or path separators.
    """
    pairs: list[tuple[str, str]] = []
    for match in _WIKILINK.finditer(text):
        inner = match.group(1)
        # Obsidian 表格里把 wikilink 的 `|` 分隔符写成 `\|`；先按该转义形式拆分。
        parts = inner.split("\\|", 1) if "\\|" in inner else re.split(
            r"(?<!\\)\|", inner, maxsplit=1)
        target = parts[0].strip().replace("\\", "/")
        alias = parts[1].strip() if len(parts) > 1 else ""
        pairs.append((target, alias))
    return pairs


def _replace_wikilinks(text: str, page_set: set[str]) -> str:
    out: list[str] = []
    cursor = 0
    for match in _WIKILINK.finditer(text):
        out.append(text[cursor:match.start()])
        (target, alias), = extract_wikilinks(match.group(0))
        resolved = _resolve_target(target, page_set)
        if resolved is None:
            out.append(alias or Path(target).stem)
        else:
            if not alias:
                alias = Path(resolved).stem
            out.append(f"[{alias}](#/{resolved})")
        cursor = match.end()
    out.append(text[cursor:])
    return "".join(out)


def _image_data_uri(vault: Path, src: str) -> str | None:
    if src.startswith(("http://", "https://", "data:")):
        return None
    candidate = (vault / src).resolve()
    if not candidate.is_file():
        return None
    ext = candidate.suffix.lower()
    mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "gif": "image/gif", "webp": "image/webp", "svg": "image/svg+xml"}.get(
        ext.lstrip("."), "application/octet-stream")
    b64 = base64.b64encode(candidate.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _replace_images(text: str, page_set: set[str], vault: Path, with_images: bool) -> str:
    def repl(match: re.Match) -> str:
        alt = match.group(1) or "图"
        if not with_images:
            return alt
        uri = _image_data_uri(vault, match.group(2))
        if uri is None:
            return alt
        return f'<img alt="{_html_escape(alt, quote=True)}" src="{uri}">'

    return _IMAGE.sub(repl, text)


def _protect_math(text: str) -> tuple[str, list[str]]:
    """Protect ``$$..$$`` and ``$..$`` before markdown rendering.

    markdown-it's inline rules would otherwise treat math ``*`` / ``\\`` /
    ``\\{`` as emphasis or escapes. Math must be completely opaque to Markdown,
    matching the protect -> process -> restore discipline used by Anki export.
    """
    protected: list[str] = []

    def protect(match: re.Match) -> str:
        protected.append(match.group(0))
        return f"@@KATEX{len(protected) - 1}@@"

    out = _MATH_BLOCK.sub(protect, text)
    out = _MATH_INLINE.sub(protect, out)
    return out, protected


def _restore_math(html: str, protected: list[str]) -> str:
    for i, raw in enumerate(protected):
        html = html.replace(f"@@KATEX{i}@@", _escape_math_html(raw))
    return html


def _escape_math_html(raw: str) -> str:
    """Apply the target HTML escaping contract when math is restored.

    Math is restored after markdown-it, so raw ``<`` / ``>`` / ``&`` cannot be
    inserted directly: the HTML parser would consume ``<`` as a tag start.
    Existing entities are preserved to avoid double-escaping.
    """
    protected: list[str] = []

    def keep(match: re.Match) -> str:
        protected.append(match.group(0))
        return f"\x00ENT{len(protected) - 1}\x00"

    out = _HTML_ENTITY.sub(keep, raw)
    out = out.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    for i, token in enumerate(protected):
        out = out.replace(f"\x00ENT{i}\x00", token)
    return out


def _render_markdown(text: str, page_set: set[str], vault: Path, with_images: bool) -> str:
    if _MD is None:
        raise RuntimeError("markdown-it-py is required for export-site")
    text = _replace_wikilinks(text, page_set)
    text = _replace_images(text, page_set, vault, with_images)
    text, protected = _protect_math(text)
    html = _MD.render(text)
    return _restore_math(html, protected)


def _quote_depth(line: str) -> int:
    match = _QUOTE_PREFIX.match(line)
    return match.group(1).count(">") if match else 0


def _strip_quote(line: str) -> str:
    match = _QUOTE_PREFIX.match(line)
    return line[match.end():] if match else line


def _compute_spans(lines: list[str], nodes: list[dict]) -> tuple[dict[int, int], dict[int, int]]:
    """Return (line -> node index, node index -> exclusive end line index)."""
    by_line = {node["line"] - 1: i for i, node in enumerate(nodes)}
    spans: dict[int, int] = {}
    for i, node in enumerate(nodes):
        start = node["line"] - 1
        end = len(lines)
        for idx in range(start + 1, len(lines)):
            if _quote_depth(lines[idx]) == 0:
                end = idx
                break
            other = by_line.get(idx)
            if other is not None and other != i and nodes[other]["depth"] <= node["depth"]:
                end = idx
                break
        spans[i] = end
    return by_line, spans


def _render_callout(index: int, lines: list[str], nodes: list[dict],
                    by_line: dict[int, int], spans: dict[int, int],
                    ctx: tuple[set[str], Path, bool]) -> str:
    node = nodes[index]
    start = node["line"] - 1
    end = spans[index]
    page_set, vault, with_images = ctx
    parts: list[str] = []
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            parts.append(_render_markdown("\n".join(buffer), page_set, vault, with_images))
            buffer.clear()

    idx = start + 1
    while idx < end:
        child = by_line.get(idx)
        if child is not None and child in node["children"]:
            flush()
            parts.append(_render_callout(child, lines, nodes, by_line, spans, ctx))
            idx = spans[child] - 1
        else:
            buffer.append(_strip_quote(lines[idx]))
        idx += 1
    flush()
    inner = "\n".join(parts)
    ctype = node["type"]
    if node["folded"]:
        summary = node["title"] or ctype
        return (f'<details class="callout callout-{ctype}" data-callout="{ctype}">'
                f'<summary>{_html_escape(summary)}</summary>'
                f'<div class="callout-body">{inner}</div></details>')
    label = node["title"] or ctype
    return (f'<div class="callout callout-{ctype}" data-callout="{ctype}">'
            f'<div class="callout-title">{_html_escape(label)}</div>'
            f'<div class="callout-body">{inner}</div></div>')


def render_page_body(body: str, page_set: set[str], vault: Path, with_images: bool) -> str:
    nodes, _errors = page_rules.parse_callouts(body)
    if not nodes:
        return _render_markdown(body, page_set, vault, with_images)
    lines = body.splitlines()
    by_line, spans = _compute_spans(lines, nodes)
    ctx = (page_set, vault, with_images)
    parts: list[str] = []
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            parts.append(_render_markdown("\n".join(buffer), page_set, vault, with_images))
            buffer.clear()

    idx = 0
    while idx < len(lines):
        node_idx = by_line.get(idx)
        if node_idx is not None:
            flush()
            parts.append(_render_callout(node_idx, lines, nodes, by_line, spans, ctx))
            idx = spans[node_idx] - 1
        else:
            buffer.append(lines[idx])
        idx += 1
    flush()
    return "\n".join(parts)


def load_katex(vendor_dir: str | Path | None = None) -> dict:
    """Read KaTeX vendor assets (js/css/auto-render/fonts base64)."""
    vd = Path(vendor_dir) if vendor_dir else Path(__file__).resolve().parent / "vendor" / "katex"
    js = (vd / "katex.min.js").read_text(encoding="utf-8")
    css = (vd / "katex.min.css").read_text(encoding="utf-8")
    auto_render_js = (vd / "auto-render.min.js").read_text(encoding="utf-8")
    fonts: dict[str, str] = {}
    fonts_dir = vd / "fonts"
    if fonts_dir.is_dir():
        for f in sorted(fonts_dir.glob("*.woff2")):
            fonts[f.name] = base64.b64encode(f.read_bytes()).decode("ascii")
    return {"js": js, "css": css, "auto_render_js": auto_render_js, "fonts": fonts}
