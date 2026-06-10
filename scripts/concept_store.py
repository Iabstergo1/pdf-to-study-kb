"""Canonical 概念模型（spec §6）：slug/canonical_id、registry 重建、resolve_or_create_concept。

真值在概念页 frontmatter；concepts/_registry.yaml 与 aliases.md 为派生（本模块重建，/ingest 不写）。
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mdpage

_ASCII_SLUG = re.compile(r"[^a-z0-9]+")
_SLUG_OK = re.compile(r"[a-z0-9][a-z0-9-]*")


def slugify(name: str) -> str:
    """确定性 slug：ASCII 名转 kebab；纯 CJK 名保留原字（去空白）。"""
    ascii_slug = _ASCII_SLUG.sub("-", name.strip().lower()).strip("-")
    if ascii_slug:
        return ascii_slug
    return re.sub(r"\s+", "", name.strip())


def canonical_id(domain: str, name: str, aliases=()) -> str:
    """concept.<domain>.<slug>；slug 依次试 name、各 alias，取第一个纯 ASCII 的（spec §6 示例规则）。"""
    for cand in (name, *aliases):
        s = slugify(cand)
        if _SLUG_OK.fullmatch(s):
            return f"concept.{domain}.{s}"
    return f"concept.{domain}.{slugify(name)}"
