"""Evidence and formula publication gates."""

from __future__ import annotations

import re
from typing import Any


EVIDENCE_REF_RE = re.compile(r"E-[A-Za-z0-9_.-]+")
# 整行被加粗/斜体包裹的行，如 **第1类：核心命题的完整证明** —— 是小标题而非结论
_WRAPPED_HEADING_RE = re.compile(r"^(\*\*|\*|__|_).+(\*\*|\*|__|_)$")
# 列表/有序号前缀：- * + / 1. 2) / a. (a) (1) 等，剥离后再判断行的实质内容
_LIST_PREFIX_RE = re.compile(r"^(?:[-*+]|\d+[.)]|[A-Za-z][.)]|\([0-9A-Za-z]+\))\s+")
_CLAIM_KEYWORDS = ("Claim", "结论", "命题")
# 论断忠实度三分类（对应 CLAUDE.md：原文压缩 / 学习解释 / 个人桥接）
#   source      = 对原文的压缩/复述，必须有 evidence_id 支撑
#   explanation = 作者对原文的学习化解释或推导
#   bridge      = 个人桥接/类比/联想，可无 evidence_id
VALID_CLAIM_TYPES = {"source", "explanation", "bridge"}


def extract_evidence_refs(draft: str) -> set[str]:
    return set(EVIDENCE_REF_RE.findall(draft or ""))


def normalize_claims(raw: Any) -> list[dict[str, Any]] | None:
    """把 author 输出的 claims 规整为 [{statement, evidence_ids, type}]。

    返回 ``None`` 表示「未提供结构化 claims」（旧草稿或模型漏输出）→ verify_note 退回正则
    散文扫描（仅作 advisory）。返回 ``[]`` 表示「提供了但无有效条目」。type 缺失/非法时按
    「有证据→source，无证据→explanation」推断，避免把没声明类型的论断默认成需证据的 source。"""
    if not isinstance(raw, list):
        return None
    claims: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        statement = item.get("statement") or item.get("text") or ""
        if not isinstance(statement, str) or not statement.strip():
            continue
        evidence_ids = item.get("evidence_ids", item.get("evidence_id", []))
        if isinstance(evidence_ids, str):
            evidence_ids = [evidence_ids]
        evidence_ids = [e for e in evidence_ids if isinstance(e, str) and e.strip()]
        claim_type = str(item.get("type", "")).strip().lower()
        if claim_type not in VALID_CLAIM_TYPES:
            claim_type = "source" if evidence_ids else "explanation"
        claims.append({"statement": statement.strip(), "evidence_ids": evidence_ids, "type": claim_type})
    return claims


def _is_structural_line(line: str) -> bool:
    """标题/小标题/分隔/表格/标签行等结构性文本，不是需要逐条证据的事实结论。

    作者常用 Markdown 标题（``#``）、整行加粗的小标题（``**...**``）、带列表前缀的加粗
    标签（``- **针对…的提问：**``）、以冒号结尾引出下文的标签行、列表/表格来组织讲义。
    这些行即便含“命题/结论”等字样也只是结构标签，本就不携带证据引用；若当成核心结论
    校验会产生假阳性，把有据的讲义错误挡进 Review-Queue。

    判定前先剥离列表/有序号前缀，使“``- **小标题**``”“``1. **小标题**``”与裸小标题
    走同一套规则——有原则的泛化，而非逐个措辞打补丁。"""
    if not line or line.startswith("#") or line.startswith("---"):
        return True
    if line.startswith("|") or line.startswith(">"):  # 表格行 / 引用块
        return True
    core = _LIST_PREFIX_RE.sub("", line).strip()
    if not core:  # 仅有列表符号、无实质内容
        return True
    if _WRAPPED_HEADING_RE.match(core):  # （剥前缀后）整行加粗/斜体的小标题
        return True
    if core.endswith(("：", ":")):  # 以冒号结尾、引出下文的纯标签行
        return True
    return False


def extract_core_claims(draft: str) -> list[str]:
    """抽取需要逐条核验证据的核心结论行（显式含“结论/命题/Claim”的句子）。

    只识别结构性行之外、含关键词的句子。不再用“无关键词时抓首行”的脆弱兜底——那会把
    章节标题/小标题误当结论；无显式结论时的证据完整性改由 verify_note 用“正文是否存在
    任意有效证据引用”来判断。"""
    claims = []
    lines = (draft or "").splitlines()
    in_frontmatter = bool(lines and lines[0].strip() == "---")
    for idx, raw_line in enumerate(lines):
        line = raw_line.strip()
        if in_frontmatter:
            if line == "---" and idx > 0:
                in_frontmatter = False
            continue
        if _is_structural_line(line):
            continue
        if any(keyword in line for keyword in _CLAIM_KEYWORDS):
            claims.append(line)
    return claims


def extract_formula_risks(draft: str, context: dict[str, Any]) -> list[str]:
    risk_flags = set(context.get("risk_flags", []))
    has_formula_like_text = any(token in (draft or "") for token in ["∑", "\\sum", "\\frac", "="])
    if ("ocr_unavailable" in risk_flags or context.get("formula_risk") == "high") and has_formula_like_text:
        if "[公式缺失]" not in draft:
            return ["formula may have been reconstructed without OCR evidence"]
    return []


def verify_note(draft: str, context: dict[str, Any], claims: Any = None) -> dict[str, Any]:
    """证据落地门禁。分三层，机械层确定性、语义层交给 reviewer：

    ① 幻觉证据（确定性、阻塞）：正文或 claims 引用了 evidence_candidates 里不存在的 id。
    ② 结构化 claims（确定性、阻塞）：author 声明 type=source 的论断必须有有效 evidence_id；
       explanation/bridge 类是学习解释/个人桥接，本就不需要原文证据，不拦。
    ③ 无结构化 claims 时（兼容旧草稿）：正则散文扫描降级为 advisory（不阻塞），把语义判断
       留给 LLM reviewer；仅保留「有可用证据却整篇零引用」这一确定性兜底。
    """
    available_ids = {
        item.get("evidence_id")
        for item in context.get("evidence_candidates", [])
        if item.get("evidence_id")
    }
    cited_in_draft = extract_evidence_refs(draft)
    valid_cited = cited_in_draft & available_ids

    structured = normalize_claims(claims)

    # ① 任何被引用却不存在的 id = 幻觉证据
    hallucinated = set(cited_in_draft) - available_ids

    source_missing: list[str] = []
    advisory_uncited: list[str] = []

    if structured is not None:
        # ② source 类论断必须有有效证据；claims 内引用的 id 也纳入幻觉检查
        for claim in structured:
            ids = set(claim.get("evidence_ids", []))
            hallucinated |= ids - available_ids
            if claim.get("type") == "source" and not (ids & available_ids):
                source_missing.append(claim.get("statement", ""))
    else:
        # ③ 退回正则扫描，仅作提示
        for line in extract_core_claims(draft):
            if not extract_evidence_refs(line) & available_ids:
                advisory_uncited.append(line)

    # 确定性兜底：没有可据以判断的结构化 claims（None=未输出，或 []=输出了空数组）时，
    # 整篇实质讲义却零有效引用 → 未落地来源。有非空结构化 claims 时以 per-claim 的 source
    # 检查为准（纯 explanation/bridge 单元不应被拦）。`not structured` 同时覆盖 None 与 []。
    if not structured and (draft or "").strip() and not valid_cited:
        source_missing.append("draft 无任何可用证据引用")

    formula_risks = extract_formula_risks(draft, context)
    risk_flags = []
    if hallucinated:
        risk_flags.append("evidence_hallucinated")
    if source_missing:
        risk_flags.append("evidence_missing")
    if formula_risks:
        risk_flags.append("formula_loss_risk")
    return {
        "passed": not risk_flags,
        "risk_flags": risk_flags,
        "missing_claims": source_missing,
        "hallucinated_evidence": sorted(hallucinated),
        "advisory_uncited": advisory_uncited,
        "formula_risks": formula_risks,
    }
