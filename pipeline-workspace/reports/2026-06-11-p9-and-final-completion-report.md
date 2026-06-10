# P9 旧管线下线 + 重构收官总报告

- 日期：2026-06-11
- 分支：`feat/p9-legacy-cleanup`（基于 `feat/p8-query-saveback`，保留本地）
- 终态验证：`python -m pytest -q --ignore=tmp` → **133 passed**（217 − 88 个随旧管线删除的旧测试 + 4 个守卫测试，数目吻合，零回归）

## P9 提交清单

| 提交 | 内容 |
|---|---|
| 92979c7 | 删除旧管线：16 个旧脚本、13 个旧测试、`webapp/`、`schemas/`、2 个旧模板、`pipeline.py` 旧命令面（init-book/profile-pdf/plan-units/validate/review/run-book + 顶层 `import yaml`）；新增 `tests/test_legacy_removed.py` 守卫（−8486 行）；过程报告入库 |
| 5b48339 | requirements 终态：删 `langgraph*`/`surya-ocr`，PyMuPDF+PyYAML+pytest，重后端列为可选适配器 |
| 686cf16 | README/CLAUDE/domain 同步终态（修正 P0–P7 → P0–P8 + 清理期的陈旧引用，移除过渡期表述） |

保留：`books/`（用户旧产物数据）、`tools/`（用户本地 llama.cpp GPU 工具链）、`tmp/`（环境遗留）。

---

# 重构全程总览（goal：写计划→执行，直到全部重构完成）

## 分支链（全部本地保留、未 push，每期一计划一分支，TDD 逐任务提交）

```
main ← feat/p0-state-foundation（状态底座，23 测试）
     ← feat/p1-source-convert（转换+切窗+needs_vision，18 测试）
     ← feat/p2-canonical-concepts（canonical 概念+registry+归一协议，21 测试）
     ← feat/p3-page-templates（6+1 模板+干净正文规则，13 测试）
     ← feat/p4-ingest-command（work order 事务协议+/ingest+10 个支撑 CLI，23 测试）
     ← feat/p5-synthesis-layer（综合层骨架+init-vault+L5，5 测试）
     ← feat/p6-lint-gate（确定性门禁+promote/回滚+Review-Queue+index，11 测试）
     ← feat/p7-cross-domain-promotion（跨域提升候选+机械提升，5 测试）
     ← feat/p8-query-saveback（query/save-back 闭环+Q1+4 命令，10 测试）
     ← feat/p9-legacy-cleanup（旧管线下线+守卫+文档终态）
```

## 终态架构（spec §14 验收对照）

- **预处理（零 LLM）**：add-source → profile → source-convert → windows → workorder，状态机记账、幂等、崩溃可救（`fail`）。
- **唯一 LLM**：人工触发 `/ingest`（rolling digest、registry stale 硬守卫、写入边界+覆盖保护三条件、window 级续跑、vault 锁互斥、全部 proposed）。
- **收尾（零 LLM）**：`lint` 门禁（L1/L2/L3/L5/L6-代理/断链/公式邻接/脚注证据/重复 canonical）→ promote+派生重建+index（只收 published）或 回滚+Review-Queue+回流。
- **概念**：canonical_id 命名空间、resolve_or_create 唯一入口（命中合并绝不重复）、跨域提升人工确认。
- **学习反哺**：/kb-query（只读+session 持久化）→ /kb-save（准入门槛+decision+Q1 硬检查）→ lint promote；/kb-review、/wiki-lint-semantic 收口复核与语义体检。
- **守卫**：`tests/test_legacy_removed.py` 锁死旧路径不回归。

## 已知留尾（非阻塞，已在各报告声明）

1. L6 为代理实现（lesson 过短检查），精确判定需"源页→lesson"映射，待真实样本校准。
2. docx/pptx 转换是适配器 stub（spec §5 允许，按需装 pandoc/docling）。
3. source 生命周期（更新/删除/取代）按 spec §9.1 明确不在 P0–P8，留待后续单列。
4. 全部分支未合并未 push（遵守"全部完成后再 push"约束）——合并顺序：按分支链依次或直接把 p9 分支整体合回 main。
5. 后台 Codex review（审 P0+P1 时代的分支 diff）未返回结果，其结论到达后可能补充少量修复。
