"""干净正文的确定性文本规则（spec §10/§11 原语；纯函数、无 I/O；门禁组装在 P6）。"""
from __future__ import annotations

import re

# 裸 E-ID：旧管线的内联证据标记，正文里一律不许出现（L1）
_BARE_EVIDENCE = re.compile(r"\[E-[A-Za-z0-9_.\-]+\]")
# 脚注引用 [^e1]（行内）；(?!:) 排除定义行
_FOOTNOTE_REF = re.compile(r"\[\^([A-Za-z0-9_\-]+)\](?!:)")
# 脚注定义行 [^e1]: …
_FOOTNOTE_DEF = re.compile(r"^\[\^([A-Za-z0-9_\-]+)\]:", re.MULTILINE)


def find_bare_evidence_ids(body: str) -> list[str]:
    return _BARE_EVIDENCE.findall(body)


def footnote_refs(body: str) -> set[str]:
    return set(_FOOTNOTE_REF.findall(body))


def footnote_defs(body: str) -> set[str]:
    return set(_FOOTNOTE_DEF.findall(body))


def missing_footnote_defs(body: str) -> set[str]:
    return footnote_refs(body) - footnote_defs(body)
