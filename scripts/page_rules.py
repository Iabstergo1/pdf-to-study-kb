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


# 各页面类型的必需小节（spec §8；P6 门禁选择阻断子集：L2=concept、L3=topic、L5=overview）
REQUIRED_SECTIONS: dict[str, list[str]] = {
    "source": ["## 一句话总结", "## 核心观点", "## 关键概念",
               "## 与其他来源的关联", "## 精彩摘录", "## 相关页面"],
    "lesson": [],  # 干净散文，无强制小节；约束是无裸 E-ID + 脚注配对
    "concept": ["## 一句话", "## 直觉", "## 形式化", "## 各章如何处理",
                "## 与其他概念的关系", "## 自测"],
    "topic": ["## 核心综合", "## 各来源贡献", "## 未解决问题"],
    "comparison": ["## 结论", "## 对比维度", "## 适用场景", "## 相关概念"],
    "synthesis": ["## 核心洞见", "## 关键决策", "## 涉及概念", "## 待跟进"],
    "overview": ["## 核心概念地图", "## 推荐学习路线", "## 模型家族对比"],
}


def required_sections_for(page_type: str) -> list[str]:
    return list(REQUIRED_SECTIONS[page_type])  # 未知类型 KeyError 即报错


def missing_sections(body: str, required: list[str]) -> list[str]:
    present = {ln.strip() for ln in body.splitlines() if ln.lstrip().startswith("#")}
    return [s for s in required if s not in present]
