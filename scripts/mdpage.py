"""Markdown 页读写：YAML frontmatter + 正文（确定性 round-trip；spec §6 真值在 frontmatter）。"""
from __future__ import annotations

from pathlib import Path

import yaml


def read_page(path) -> tuple[dict, str]:
    text = Path(path).read_text(encoding="utf-8")
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            meta = yaml.safe_load(text[4:end + 1]) or {}
            return meta, text[end + 5:]
    return {}, text


def write_page(path, meta: dict, body: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fm = yaml.safe_dump(meta, allow_unicode=True, sort_keys=True, default_flow_style=False)
    p.write_text(f"---\n{fm}---\n{body}", encoding="utf-8")
