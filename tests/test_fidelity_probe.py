# -*- coding: utf-8 -*-
"""fidelity_probe 纯函数层：反引号标识符的来源命中探测（advisory-only）。

口径（与模块 docstring 一致，2026-07-19 mysql 内容忠实度返工 B 组）：
- 只看反引号内形如代码标识符的 token：`[A-Za-z_][A-Za-z0-9_]*` 可带尾随 `()`，
  且必须含 `_` 或以 `()` 结尾（排除普通英文单词）；含 CJK / 空格的 span 不算。
- 大小写敏感（`MEMORY_BLOCK_READ_COST` ≠ 书里的 `memory_block_read_cost`——实测真阳性）。
- 多来源页：token 在任一 source 语料中命中即算有据。
- 未命中 ≠ 违规（演示 schema / 跨行断字均合法）——advisory triage 信号，永不进门禁。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import fidelity_probe


def test_extract_idents_picks_code_like_tokens_only():
    body = (
        "控制块（`buf_block_t`）由 `mtr_start()` 创建；参数 `innodb_deadlock_detect` 控制。\n"
        "普通词 `SELECT` 和 `simple` 不算；`含中文` 不算；`a b` 含空格不算；`x` 太短不算。"
    )
    assert fidelity_probe.extract_idents(body) == {
        "buf_block_t", "mtr_start()", "innodb_deadlock_detect"}


def test_match_is_case_sensitive():
    corpus = "系统变量 memory_block_read_cost 的默认值是 1.0"
    missing = fidelity_probe.missing_idents({"MEMORY_BLOCK_READ_COST"}, corpus)
    assert missing == ["MEMORY_BLOCK_READ_COST"]
    assert fidelity_probe.missing_idents({"memory_block_read_cost"}, corpus) == []


def test_trailing_parens_stripped_for_corpus_match():
    corpus = "调用 mtr_start 开启一个 mini-transaction"
    assert fidelity_probe.missing_idents({"mtr_start()"}, corpus) == []


def test_unsourced_identifiers_reports_only_missing_and_sorts():
    pages = [("concepts/a.md", "有 `real_param` 和 `fake_sym`、`another_fake()`", ["s1"])]
    corpora = {"s1": "书里只有 real_param 这一个"}
    out = fidelity_probe.unsourced_identifiers(pages, corpora)
    assert out == [("concepts/a.md", ["another_fake()", "fake_sym"])]


def test_multi_source_page_hits_any_corpus():
    pages = [("concepts/b.md", "`from_s2_param` 与 `nowhere_sym`", ["s1", "s2"])]
    corpora = {"s1": "无关文本", "s2": "含 from_s2_param 的另一本书"}
    out = fidelity_probe.unsourced_identifiers(pages, corpora)
    assert out == [("concepts/b.md", ["nowhere_sym"])]


def test_clean_pages_and_missing_corpus_are_silent():
    pages = [
        ("concepts/clean.md", "`covered_param` 而已", ["s1"]),
        ("concepts/no_corpus.md", "`whatever_sym`", ["ghost"]),  # 语料缺失→跳过不报（advisory）
    ]
    corpora = {"s1": "covered_param 在此"}
    assert fidelity_probe.unsourced_identifiers(pages, corpora) == []
