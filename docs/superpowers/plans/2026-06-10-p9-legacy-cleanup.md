# P9 旧管线下线清理 + 文档终态同步 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: 用 superpowers:executing-plans **Inline** 执行（与 P0–P8 同）。Steps 用 checkbox（`- [ ]`）跟踪。

**Goal:** 按 spec §12 删除清单下线旧管线（LangGraph/unit/双库/surya 硬管线代码、旧测试、旧 Web 前端、旧模板、旧 CLI 命令、过渡期依赖），并把 README/CLAUDE/domain 文档同步到终态（P0–P8 已完成）——完成后整个重构收官。

**Architecture:** 纯删除 + 文档同步，零新功能。安全前提已核实：旧模块的 import 只在旧模块之间（langgraph_worker→business_db/memory_store/llm_provider；unit_context→ocr_surya/pdf_profile/unit_plan；web_ops→unit_plan；surya_smoke→ocr_surya），新架构代码（state_store/concept_store/wiki_gate/...）零依赖旧模块。**保留**：`books/`（用户旧产物数据）、`tools/`（用户本地 llama.cpp GPU 工具链，与管线无关）、`tmp/`（环境遗留）。先加"旧物已除"守卫测试（TDD：先失败后删除使其通过）。

**Tech Stack:** git rm、pytest。无新增依赖。

**权威链：** spec §12（删除清单）、ADR-0001、requirements.txt 注释（"随旧代码一并删除"）。

**运行环境：** 测试用 `D:\miniconda3\envs\pythonProject\python.exe -m pytest`；命令用 `pwsh`。

**Git：** 从 `feat/p8-query-saveback` 开 `feat/p9-legacy-cleanup`。验证与提交用 `&&` 链接。

---

## 删除清单（spec §12 对照）

- **旧脚本（16）**：`scripts/` 下 `langgraph_worker.py`、`unit_plan.py`、`unit_context.py`、`run_book.py`、`business_db.py`、`llm_provider.py`、`pdf_profile.py`、`ocr_surya.py`、`surya_smoke.py`、`evidence_verifier.py`、`review_gate.py`、`obsidian_indexes.py`、`memory_store.py`、`cost_guard.py`、`web_ops.py`、`serve.py`。
- **旧测试（13）**：`tests/` 下 `test_pdf_profile.py`、`test_unit_context.py`、`test_unit_graph.py`、`test_langgraph_worker.py`、`test_pipeline_phase1.py`、`test_unit_plan.py`、`test_llm_provider.py`、`test_run_book_semantic.py`、`test_business_db.py`、`test_obsidian_indexes.py`、`test_memory_store.py`、`test_evidence_verifier.py`、`test_web_ops.py`。
- **旧前端/模式/模板**：`webapp/`（整目录）、`schemas/`（执行时先查看内容，确属旧 unit-plan/事件 schema 则整目录删；若含与新架构相关文件则保留该文件并在报告记录）、`templates/review-report.template.md`、`templates/section-lesson.template.md`。
- **`scripts/pipeline.py` 旧命令面**：`find_book_root`/`_ensure_dirs`/`cmd_init_book`/`cmd_profile_pdf`/`cmd_plan_units`/`cmd_validate_unit_plan`/`cmd_review_unit_plan`/`cmd_run_book` 函数、对应 argparse 注册块、commands dict 条目、顶层 `import yaml`（F5）；模块 docstring 换新。
- **`requirements.txt`**：删 `langgraph>=0.2.0`、`langgraph-checkpoint-sqlite>=3.0.1`、`surya-ocr>=0.20.0` 及其过渡期注释。
- **保留**：`books/`、`tools/`、`tmp/`、全部新架构代码与测试。

## File Structure

- Create `tests/test_legacy_removed.py` — 守卫测试（旧物不存在、pipeline.py 无旧命令）。
- Delete 上述清单。
- Modify `scripts/pipeline.py`、`requirements.txt`、`README.md`、`CLAUDE.md`、`docs/agents/domain.md`。

---

### Task 1: 开工分支

- [ ] **Step 1:** Run `git checkout -b feat/p9-legacy-cleanup` → Expected 切到新分支。

---

### Task 2: 守卫测试 + 删除旧代码/测试/前端/模板

**Files:** Create `tests/test_legacy_removed.py`、Delete 删除清单全部文件

- [ ] **Step 1: 写失败的守卫测试**

Create `tests/test_legacy_removed.py`:

```python
"""旧管线下线守卫（spec §12 / ADR-0001）：确保被删除的旧路径不再回来。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

LEGACY_SCRIPTS = [
    "langgraph_worker.py", "unit_plan.py", "unit_context.py", "run_book.py",
    "business_db.py", "llm_provider.py", "pdf_profile.py", "ocr_surya.py",
    "surya_smoke.py", "evidence_verifier.py", "review_gate.py",
    "obsidian_indexes.py", "memory_store.py", "cost_guard.py", "web_ops.py", "serve.py",
]


def test_legacy_scripts_gone():
    leftovers = [n for n in LEGACY_SCRIPTS if (ROOT / "scripts" / n).exists()]
    assert leftovers == [], f"legacy scripts still present: {leftovers}"


def test_legacy_dirs_and_templates_gone():
    assert not (ROOT / "webapp").exists()
    assert not (ROOT / "templates" / "section-lesson.template.md").exists()
    assert not (ROOT / "templates" / "review-report.template.md").exists()


def test_pipeline_has_no_legacy_commands_and_no_toplevel_yaml():
    text = (ROOT / "scripts" / "pipeline.py").read_text(encoding="utf-8")
    for legacy in ["plan-units", "run-book", "init-book", "profile-pdf",
                   "validate-unit-plan", "review-unit-plan", "langgraph"]:
        assert legacy not in text, f"legacy command remains: {legacy}"
    assert "\nimport yaml" not in text  # status/next 等保持 stdlib-only（F5）


def test_requirements_free_of_legacy_deps():
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    for dep in ["langgraph", "surya"]:
        assert dep not in req, f"legacy dependency remains: {dep}"
```

- [ ] **Step 2:** Run `python -m pytest tests/test_legacy_removed.py -q` → Expected 4 FAIL（旧物仍在）。

- [ ] **Step 3: 删除旧脚本与旧测试**

```
git rm scripts/langgraph_worker.py scripts/unit_plan.py scripts/unit_context.py scripts/run_book.py scripts/business_db.py scripts/llm_provider.py scripts/pdf_profile.py scripts/ocr_surya.py scripts/surya_smoke.py scripts/evidence_verifier.py scripts/review_gate.py scripts/obsidian_indexes.py scripts/memory_store.py scripts/cost_guard.py scripts/web_ops.py scripts/serve.py
git rm tests/test_pdf_profile.py tests/test_unit_context.py tests/test_unit_graph.py tests/test_langgraph_worker.py tests/test_pipeline_phase1.py tests/test_unit_plan.py tests/test_llm_provider.py tests/test_run_book_semantic.py tests/test_business_db.py tests/test_obsidian_indexes.py tests/test_memory_store.py tests/test_evidence_verifier.py tests/test_web_ops.py
git rm -r webapp
git rm templates/review-report.template.md templates/section-lesson.template.md
```

- [ ] **Step 4: 检查并处置 `schemas/`**

Run `Get-ChildItem schemas -Recurse -Name`，逐个判断：全部属旧 unit-plan/事件/审批模式 → `git rm -r schemas`；如有与新架构相关者保留该文件、其余删除，并在执行报告记录。

- [ ] **Step 5:** Run `python -m pytest tests/test_legacy_removed.py -q` → Expected：前两个测试 PASS，后两个仍 FAIL（pipeline/requirements 未改，Task 3/4 处理）。

---

### Task 3: `pipeline.py` 收口（删除旧命令面 + yaml 下沉）

**Files:** Modify `scripts/pipeline.py`

- [ ] **Step 1: 删除旧函数**：`find_book_root`、`_ensure_dirs`、`cmd_init_book`、`cmd_profile_pdf`、`cmd_plan_units`、`cmd_validate_unit_plan`、`cmd_review_unit_plan`、`cmd_run_book` 整体删除；顶层 `import yaml` 删除（`cmd_check_write` 已用局部 `import yaml as _yaml`，不受影响）。

- [ ] **Step 2: 删除对应 argparse 注册块**（init-book / profile-pdf / plan-units / validate-unit-plan / review-unit-plan / run-book 六块）与 commands dict 六条目。

- [ ] **Step 3: 换新模块 docstring**：

```python
#!/usr/bin/env python3
"""PDF to Study KB 流水线 CLI（新架构：确定性预处理 + 收尾门禁 + 状态跟踪，零 LLM）

预处理：add-source → profile → source-convert → windows → workorder
/ingest 会话支撑：ingest-start/done、window-start/done/fail、show-window、
                resolve-concept、check-write、snapshot-page
收尾：lint（promote 或 回滚+Review-Queue）、rebuild-registry
vault 与维护：init-vault、status、next、fail、promotion-candidates、
              promote-concept、check-session

用法：python scripts/pipeline.py <command> [options]
架构真值：docs/superpowers/specs/2026-06-08-claude-code-wiki-redesign-design.md
"""
```

- [ ] **Step 4:** Run `python -m pytest tests/test_legacy_removed.py tests/test_pipeline_status.py tests/test_p1_cli.py -q` → Expected：守卫第 3 测试 PASS，新 CLI 回归 PASS（第 4 测试仍 FAIL，Task 4 处理）。
- [ ] **Step 5:** Commit

```
git add -A && git commit -m "Remove legacy pipeline (LangGraph/unit/dual-DB/surya), slim CLI to new architecture" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: `requirements.txt` 终态

**Files:** Modify `requirements.txt`

- [ ] **Step 1: 整文件替换为：**

```
# PDF to Study KB 依赖
# 架构见 docs/superpowers/specs/2026-06-08-claude-code-wiki-redesign-design.md

pymupdf>=1.23.0   # PDF 解析 / 页渲染（source-convert 默认文本后端）
pyyaml>=6.0       # YAML（registry / workorder / frontmatter）

# 可选转换适配器（spec §5 分层后端；按需安装，缺失时 source-convert 自动降级/标 needs_vision）：
# pymupdf4llm / marker-pdf / docling / pandoc

# 测试
pytest>=7.0
```

- [ ] **Step 2:** Run `python -m pytest tests/test_legacy_removed.py -q` → Expected PASS（4）。
- [ ] **Step 3:** Commit

```
git add requirements.txt && git commit -m "Drop legacy deps (langgraph*, surya-ocr); document optional converter adapters" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: 文档终态同步（README / CLAUDE / domain，含 F4）

**Files:** Modify `README.md`、`CLAUDE.md`、`docs/agents/domain.md`

- [ ] **Step 1: README.md** —— ① 状态行替换：

旧：`> **状态**：本仓库正从旧的 LangGraph/section 管线迁移到新架构。**设计唯一真值**是 ...；构建进度见 ...；关键决策见 ...。旧管线代码仍在过渡期保留。`
新：`> **状态**：新架构 P0–P8 已全部落地，旧 LangGraph/section 管线已删除（见 docs/adr/0001）。**设计唯一真值**是 [`docs/superpowers/specs/2026-06-08-claude-code-wiki-redesign-design.md`](docs/superpowers/specs/2026-06-08-claude-code-wiki-redesign-design.md)；构建记录见 [`docs/superpowers/plans/`](docs/superpowers/plans/)；关键决策见 [`docs/adr/`](docs/adr/)。`

② "## 现状与运行" 三个 bullet 替换为：

```markdown
- 预处理（零 LLM）：`add-source` → `profile` → `source-convert` → `windows` → `workorder`。
- 人工触发 `/ingest <source_id>`（唯一 LLM；Claude Code 显式命令，含 rolling digest、写入守卫、window 级续跑）；查询/保存走 `/kb-query`、`/kb-save`，复核走 `/kb-review`、`/wiki-lint-semantic`。
- 收尾（零 LLM）：`lint`（通过 promote 入 index，失败回滚 + Review-Queue）；维护：`status` / `next` / `fail` / `init-vault` / `rebuild-registry` / `promotion-candidates` / `promote-concept` / `check-session`。
- 依赖见 `requirements.txt`（PyMuPDF + PyYAML + pytest；重转换后端为可选适配器）。
```

③ 文档导航表 `P0–P7` → `P0–P8 + 清理期`。

- [ ] **Step 2: CLAUDE.md** —— ① 删除过渡期 blockquote（"本文描述**目标架构**。旧的 LangGraph/section/plan-units 管线正按计划逐期替换。…"）替换为：`> 旧 LangGraph/section/plan-units 管线已删除（ADR-0001）。**不要重新引入 LangGraph / 双 SQLite / plan-units / 逐 unit 孤立生成**。`；② 权威链第 3 条 `（P0–P7）` → `（P0–P8 + 清理期）`。

- [ ] **Step 3: domain.md** —— ① 权威链第 3 条 `（P0–P7）` → `（P0–P8 + 清理期）`；② 工作规则第 3 条 `P0（状态底座 + 文档同步）是硬前置` 保留；③ 工作规则第 7 条替换：旧 `7. 旧管线代码（langgraph_worker.py、plan-units 等）在其删除期（P4）前仍在仓库；不要在旧路径上加新功能。` → 新 `7. 旧管线代码已在清理期删除（tests/test_legacy_removed.py 守卫）；不要重新引入（见 docs/adr/0001）。`

- [ ] **Step 4:** Run `python -m pytest -q --ignore=tmp` → Expected 全 PASS。
- [ ] **Step 5:** Commit

```
git add README.md CLAUDE.md docs/agents/domain.md && git commit -m "Sync docs to final state: P0-P8 complete, legacy pipeline removed (fixes P0-P7 stale refs)" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: 终验收

- [ ] **Step 1:** Run `python -m pytest -q --ignore=tmp` → Expected 全 PASS（旧测试已删，新套件完整）。
- [ ] **Step 2: spec §14 终态核对**：命令面齐全（预处理 5 + ingest 支撑 8 + 收尾 2 + 维护 8 + 5 个 slash command）；两阶段发布/锁/续跑/守卫/提升/Q1 各有测试；守卫测试锁死旧路径不回归。
- [ ] **Step 3:** Run `git status --short` → Expected 干净（报告目录未跟踪可忽略）。

---

## Self-Review

- **Spec §12 覆盖**：删除列全部命中（plan-units 系/LangGraph 系/双库 business_db/surya 硬管线/旧 Web/旧模板/旧 CLI/过渡依赖）；保留列（PyMuPDF、目录约定、单库）原样 ✓；`books/`、`tools/` 非 spec 删除项，保留并说明 ✓。
- **占位符扫描**：Task 2 Step 4 的 schemas 处置是"执行时检查 + 两分支处置规则"，非 TBD。✓
- **类型一致性**：守卫测试文件名/路径与删除清单一致；`import yaml as _yaml`（P4 cmd_check_write 局部导入）已核实存在，顶层删除不影响。✓
- **风险**：删除量大但全部 git rm 可恢复；新套件不依赖旧模块（已 grep 核实）。✓

## 完成后

P9 完成 = **重构全部收官**：spec §15 P0–P8 + §12 清理全部落地，仓库内只剩新架构。
