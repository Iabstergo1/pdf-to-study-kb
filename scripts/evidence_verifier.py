"""Evidence and formula publication gates."""

from __future__ import annotations

import re
from typing import Any


EVIDENCE_REF_RE = re.compile(r"E-[A-Za-z0-9_.-]+")


def extract_evidence_refs(draft: str) -> set[str]:
    return set(EVIDENCE_REF_RE.findall(draft or ""))


def extract_core_claims(draft: str) -> list[str]:
    claims = []
    lines = (draft or "").splitlines()
    in_frontmatter = bool(lines and lines[0].strip() == "---")
    for idx, raw_line in enumerate(lines):
        line = raw_line.strip()
        if in_frontmatter:
            if line == "---" and idx > 0:
                in_frontmatter = False
            continue
        if not line or line.startswith("#") or line.startswith("---"):
            continue
        if "Claim" in line or "结论" in line or "命题" in line:
            claims.append(line)
    if not claims and (draft or "").strip():
        in_frontmatter = bool(lines and lines[0].strip() == "---")
        for idx, raw_line in enumerate(lines):
            line = raw_line.strip()
            if in_frontmatter:
                if line == "---" and idx > 0:
                    in_frontmatter = False
                continue
            if line and not line.startswith("#") and not line.startswith("---"):
                claims.append(line[:200])
                break
    return claims


def extract_formula_risks(draft: str, context: dict[str, Any]) -> list[str]:
    risk_flags = set(context.get("risk_flags", []))
    has_formula_like_text = any(token in (draft or "") for token in ["∑", "\\sum", "\\frac", "="])
    if ("ocr_unavailable" in risk_flags or context.get("formula_risk") == "high") and has_formula_like_text:
        if "[公式缺失]" not in draft:
            return ["formula may have been reconstructed without OCR evidence"]
    return []


def verify_note(draft: str, context: dict[str, Any]) -> dict[str, Any]:
    claims = extract_core_claims(draft)
    available_ids = {
        item.get("evidence_id")
        for item in context.get("evidence_candidates", [])
        if item.get("evidence_id")
    }
    missing = []
    if claims and not available_ids:
        missing = claims
    elif claims:
        missing = [
            claim
            for claim in claims
            if not extract_evidence_refs(claim).intersection(available_ids)
        ]

    formula_risks = extract_formula_risks(draft, context)
    risk_flags = []
    if missing:
        risk_flags.append("evidence_missing")
    if formula_risks:
        risk_flags.append("formula_loss_risk")
    return {
        "passed": not risk_flags,
        "risk_flags": risk_flags,
        "missing_claims": missing,
        "formula_risks": formula_risks,
    }
