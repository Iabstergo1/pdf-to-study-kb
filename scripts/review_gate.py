"""Reviewer output parsing and mandatory gate checks."""

from __future__ import annotations

from typing import Any


VALID_DECISIONS = {"accept", "revise", "reject"}
VALID_CONFIDENCE = {"high", "medium", "low"}


def apply_review_gate(decision: dict[str, Any], report: str) -> dict[str, Any]:
    gated = dict(decision)
    gated.setdefault("decision", "reject")
    gated.setdefault("confidence", "low")
    gated.setdefault("warnings", [])
    gated.setdefault("risk_flags", [])

    if gated["decision"] not in VALID_DECISIONS:
        gated["decision"] = "reject"
    if gated["confidence"] not in VALID_CONFIDENCE:
        gated["confidence"] = "low"

    missing_tables = []
    if "证据对照表" not in report:
        missing_tables.append("missing_evidence_table")
    if "公式风险清单" not in report:
        missing_tables.append("missing_formula_risk_table")
    if missing_tables:
        gated["decision"] = "reject"
        gated["confidence"] = "low"
        gated["warnings"] = list(gated.get("warnings", [])) + missing_tables

    if "原文空白" in report and "补全公式" in report:
        risk_flags = set(gated.get("risk_flags", []))
        risk_flags.add("formula_loss_risk")
        gated["risk_flags"] = sorted(risk_flags)

    return gated
