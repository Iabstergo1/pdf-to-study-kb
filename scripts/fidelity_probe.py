# -*- coding: utf-8 -*-
"""标识符来源命中探测（advisory-only；2026-07-19 mysql 内容忠实度返工 B 组）。

页面正文里反引号包裹的"代码型标识符"（参数名/结构体名/函数名）是外部知识混入时最常见的
携带物（实测：mysql 首轮 6 个 FAIL 页中 4 个可由此检出；其余三本已发布书零命中）。本模块
把这个探测固化为纯函数，由 `ingest-stats` 以软信号输出，供 kb-qa 做 triage 排序。

口径（变更需同步 tests/test_fidelity_probe.py）：
- token 语法：反引号 span 内形如 `[A-Za-z_][A-Za-z0-9_]*`、可带尾随 `()`；且必须含 `_`
  或以 `()` 结尾——排除普通英文单词；含 CJK / 空格的 span 不算。
- 大小写敏感：`MEMORY_BLOCK_READ_COST` 不因书里有 `memory_block_read_cost` 而算命中
  （大小写差异本身就是实测真阳性）。
- 语料：页面 `source_refs` 列出的每个 source 的 staging `source.md` 原文；任一命中即有据；
  语料缺失的 source 跳过、缺全则整页跳过（advisory 不误报）。
- **未命中 ≠ 违规**：演示 schema（`idx_a`/`user_id`）、原文跨行断字都合法。这是给人看的
  排查线索，**永不进 lint / 不影响 publish / 不改变任何退出码**。
"""
from __future__ import annotations

import re

_SPAN = re.compile(r"`([^`\n]{2,60})`")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\(\))?\Z")


def extract_idents(body: str) -> set[str]:
    """正文 → 代码型标识符 token 集合（口径见模块 docstring）。"""
    out: set[str] = set()
    for span in _SPAN.findall(body):
        s = span.strip()
        if _IDENT.fullmatch(s) and ("_" in s or s.endswith("()")):
            out.add(s)
    return out


def missing_idents(idents: set[str], corpus: str) -> list[str]:
    """在语料中查无的 token（大小写敏感；尾随 `()` 剥离后按子串匹配），排序返回。"""
    return sorted(i for i in idents if i.removesuffix("()") not in corpus)


def unsourced_identifiers(pages, corpora) -> list[tuple[str, list[str]]]:
    """[(rel_path, body, source_ids)] × {source_id: corpus} → [(rel_path, missing)]。

    页面的语料 = 其 source_refs 中每个有语料的 source 之并集；一个都没有则跳过该页
    （advisory 宁缺毋滥）。只返回确有未命中 token 的页。
    """
    out: list[tuple[str, list[str]]] = []
    for rel_path, body, source_ids in pages:
        parts = [corpora[s] for s in source_ids if s in corpora]
        if not parts:
            continue
        missing = missing_idents(extract_idents(body), "\n".join(parts))
        if missing:
            out.append((rel_path, missing))
    return out
