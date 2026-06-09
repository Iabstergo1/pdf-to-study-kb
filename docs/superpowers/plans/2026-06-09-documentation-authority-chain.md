# 文档权威链整理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把项目文档收敛成单一权威链，删除会把实现拖回旧架构的过时文档，让后续 P0–P7 不被旧文档/旧代码路径误导。

**Architecture:** 设计唯一真值 = `docs/superpowers/specs/2026-06-08-claude-code-wiki-redesign-design.md`；决策记录 = `docs/adr/`；构建计划 = `docs/superpowers/plans/`；agent 指令 = 根 `CLAUDE.md`；人读概览 = `README.md`；领域语言 = `docs/agents/domain.md`。本计划只动**文档**，不动 `scripts/` 代码（旧代码删除属 P4）。

**Tech Stack:** Markdown 文档；`rg`（ripgrep，经 Grep 工具）做验证；git 频繁提交。

**Git 工作流：** 当前在 `main`，先开分支再提交。提交是本计划内的开发提交（与运行时"默认不 git 快照"无关）。合并/PR 由用户在计划完成后决定。

> **执行期调整（2026-06-09，用户决定）**：`docs/agents/issue-tracker.md` 与 `docs/agents/triage-labels.md` 含旧迁移期措辞且该 GitHub Issues 流程不使用，**一并删除**；故 `CLAUDE.md` 重写**不含** "Agent skills"（issue tracker / triage labels）段，权威链已覆盖 `domain.md` 与 `docs/adr/`。Task 7 删除清单从 2 份扩为 4 份。

---

## File Structure

- **删除**：`docs/semantic-pdf-to-obsidian-implementation-guide.md`、`docs/llm-wiki-borrowings-and-output-redesign.md`
- **新增**：`docs/adr/0001-drop-langgraph-adopt-claude-code-wiki.md`
- **重写**：`docs/agents/domain.md`、`CLAUDE.md`、`README.md`
- **标注修改**：`requirements.txt`、spec 顶部"取代"注（去除指向被删文件的引用）
- **不动**：`docs/agents/issue-tracker.md`、`docs/agents/triage-labels.md`、`docs/superpowers/specs/`（spec 本体）、`scripts/`

引用顺序约束：先建 ADR、改 domain/CLAUDE/README（移除对旧指南的引用），**再**删旧指南（届时已无人引用）。

---

### Task 1: 开工分支

**Files:** （无文件改动）

- [ ] **Step 1: 从 main 建分支**

Run:
```
git checkout -b docs/authority-chain
```
Expected: `Switched to a new branch 'docs/authority-chain'`

- [ ] **Step 2: 确认工作树干净起点**

Run:
```
git status --short
```
Expected: 只显示本会话已产生的未跟踪/改动（spec、plan），无意外文件。

---

### Task 2: 新增 ADR 0001（记录"舍弃 LangGraph"决策）

**Files:**
- Create: `docs/adr/0001-drop-langgraph-adopt-claude-code-wiki.md`

- [ ] **Step 1: 写 ADR 文件**

写入 `docs/adr/0001-drop-langgraph-adopt-claude-code-wiki.md`：

```markdown
# ADR 0001: 舍弃 LangGraph，采用 Claude-Code 维护的 wiki + 确定性 CLI

- 日期：2026-06-09
- 状态：Accepted
- 关联：`docs/superpowers/specs/2026-06-08-claude-code-wiki-redesign-design.md`

## 背景

旧管线用 LangGraph 编排每个 unit 的 author→review→revise LLM 循环（DeepSeek），用 checkpointer SQLite 做断点续跑，另有业务 SQLite。输出是按 PDF 章节结构组织的 per-book vault，读起来像原文转写。

目标改为 llm-wiki 模式：LLM 增量构建并维护一个持久、互联、多领域的 Obsidian wiki。生成/审校/合并循环搬进 Claude Code（`/ingest`），它是唯一的 LLM，且**人工触发**（可用的 Claude key 禁止无人值守自动化）。

## 决策

1. **移除 LangGraph**（StateGraph、checkpointer、`langgraph-checkpoint-sqlite` 依赖）。LLM 循环进了 Claude Code 后，CLI 侧只剩预处理、收尾两段确定性直线（无循环/分支/LLM 节点），LangGraph 只剩重量。
2. **单一业务 SQLite + source 级状态机**取代双库；checkpointer 的恢复职责由"CLI 阶段级幂等 + `/ingest` window 级进度"替代。
3. **不做 LLM 语义 unit 规划**（移除 `plan-units` / `validate-unit-plan` / `review-unit-plan`）；长源用确定性 processing windows 读取。
4. **编排 = 确定性 Python CLI + SQLite 状态跟踪**（`pipeline status` / `next`）。

## 后果

- 恢复粒度是阶段级（CLI）+ window 级（`/ingest`），不是 LangGraph 节点级——可接受。
- 唯一 LLM 成本在人工触发的 `/ingest`，规避 key 的自动化限制。
- 设计唯一真值是上面关联的 spec；本 ADR 记录"为什么不用 LangGraph"，供未来 agent 直接看到，无需通读 spec。
```

- [ ] **Step 2: 验证文件存在且无占位符**

Run:
```
rg -n "TODO|TBD|占位" docs/adr/0001-drop-langgraph-adopt-claude-code-wiki.md
```
Expected: no matches.

- [ ] **Step 3: 提交**

```
git add docs/adr/0001-drop-langgraph-adopt-claude-code-wiki.md
git commit -m "docs: add ADR 0001 (drop LangGraph; Claude-Code wiki + deterministic CLI)"
```

---

### Task 3: 重写 `docs/agents/domain.md`

**Files:**
- Modify (full rewrite): `docs/agents/domain.md`

- [ ] **Step 1: 用新内容整体替换 `docs/agents/domain.md`**

写入（覆盖全文）：

```markdown
# Domain Docs

本仓库从多种来源文档（PDF / DOCX / PPTX / Markdown）编译一个**多领域、LLM 维护的 Obsidian 学习 wiki**（llm-wiki 模式）。

## 权威链（按序阅读）

1. **设计唯一真值**：`docs/superpowers/specs/2026-06-08-claude-code-wiki-redesign-design.md`。代码与该 spec 冲突时以 spec 为准（除非更新的 spec/ADR 取代）。
2. **决策记录**：`docs/adr/`（如 `0001` 舍弃 LangGraph）。
3. **构建计划/进度**：`docs/superpowers/plans/`（P0–P7）。
4. **Agent 指令**：根 `CLAUDE.md`。
5. **人读概览**：`README.md`。

## 领域术语（现行架构）

- `source`：一个被 ingest 的文档（PDF/DOCX/PPTX/MD）。取代旧的 `section` / `semantic unit`。
- `source-convert`：确定性阶段，把任意来源转成干净 `source.md` + 抽取的 `assets/` + 难页 PNG。
- `processing window`：长源的确定性读取切片（按 TOC/标题/页码/token 预算），仅为喂模型；输出里不可见，不是知识结构单位。
- `work order`：每个 source 的契约（`pipeline-workspace/staging/<source>/workorder.yaml`），定义写入边界、registry 快照、页面快照、失败处理。
- `/ingest`：唯一的 LLM 步骤——一个交互式 Claude Code slash 命令，读一个 source 并织进 wiki。**人工触发，非自动化**。
- `canonical concept`：去重后的概念，命名空间化 `canonical_id`（`concept.<domain>.<slug>`）；真值在概念页 frontmatter；`concepts/_registry.yaml` 与 `aliases.md` 为派生。
- `two-stage publish`：`/ingest` 写 `status: proposed` 页；确定性收尾门禁把通过的页 promote 成 `published`（并入 `index.generated.md`），失败回滚 + 落 `Review-Queue/` proposal。
- `business SQLite`（`pipeline-workspace/state/study-kb.sqlite`）：单库，含状态机表 `sources / source_stage_runs / artifacts / work_orders / source_locks / review_proposals / ingest_progress`。
- `managed_by: pipeline`：frontmatter 标记，允许 pipeline 覆盖某生成页（已升级为 snapshot+hash 覆盖守卫）。
- `Review-Queue`：未过收尾门禁内容的暂存区。

## 工作规则

1. spec 是权威；**不要重新引入 LangGraph、`plan-units`、双 SQLite、逐 unit 孤立生成**——这些是被刻意移除的（见 `docs/adr/0001`）。
2. 预处理与收尾是确定性 Python CLI（零 LLM）；唯一 LLM 是人工触发的 `/ingest`。
3. 按 `docs/superpowers/plans/` 逐期构建；P0（状态底座 + 文档同步）是硬前置。
4. 先确定性底座、后 LLM 行为；实现同时加聚焦测试。
5. 未过收尾门禁的内容不 promote，转 `Review-Queue/`。
6. 覆盖已存在页前须：在 work-order snapshot 中、`managed_by != human`、磁盘 hash 一致；否则不覆盖、出 proposal。
7. 旧管线代码（`langgraph_worker.py`、`plan-units` 等）在其删除期（P4）前仍在仓库；**不要在旧路径上加新功能**。
```

- [ ] **Step 2: 验证无 legacy 残留、无指向被删指南的引用**

Run:
```
rg -n "section-manifest|source-slice|unit graph|semantic-pdf-to-obsidian-implementation-guide" docs/agents/domain.md
```
Expected: no matches.

- [ ] **Step 3: 提交**

```
git add docs/agents/domain.md
git commit -m "docs: rewrite domain.md to new architecture + authority chain"
```

---

### Task 4: 重写根 `CLAUDE.md`

**Files:**
- Modify (full rewrite): `CLAUDE.md`

- [ ] **Step 1: 用新内容整体替换 `CLAUDE.md`**

写入（覆盖全文）：

````markdown
# PDF to Study KB - Claude Code 项目指令

把多来源文档（PDF/DOCX/PPTX/MD）编译进一个**不断长大的、多领域、LLM 维护的 Obsidian 学习知识库**（llm-wiki 模式）。

## 权威链（按序）

1. **设计唯一真值**：`docs/superpowers/specs/2026-06-08-claude-code-wiki-redesign-design.md`。代码与本 spec 冲突时以 spec 为准（除非更新的 spec/ADR 取代）。
2. **决策**：`docs/adr/`（如 0001 舍弃 LangGraph）。
3. **构建进度/计划**：`docs/superpowers/plans/`（P0–P7）。
4. **领域语言**：`docs/agents/domain.md`。

> 本文描述**目标架构**。旧的 LangGraph/section/plan-units 管线正按计划逐期替换。**不要在旧路径上加新功能，也不要重新引入 LangGraph / 双 SQLite / plan-units / 逐 unit 孤立生成**（见 ADR-0001）。

## 架构（一句话）

确定性 Python CLI 做预处理 + 后置门禁 + 索引 + 状态跟踪（**零 LLM**）；唯一的 LLM 是**人工触发**的交互式 Claude Code `/ingest`，它读整源、写并合并 wiki、跨页归一概念。

```text
CLI 预处理（零 LLM）：add-source → profile → source-convert → windows → work order
      ↓ 人工触发
Claude Code /ingest（唯一 LLM）：读 source.md/难页图 → 写 status:proposed 页 + 概念归一
      ↓ 人工触发
CLI 收尾（零 LLM）：确定性 lint → 门禁 promote(proposed→published)/失败回滚+Review-Queue → 重建索引
```

## 核心约束

1. **预处理/收尾零 LLM**；唯一 LLM 是人工触发的 `/ingest`（不做无人值守自动化）。
2. **不拆分**：不让 LLM 做语义 unit 规划/审批；长源用确定性 processing windows（TOC/标题/页码/token 滑窗）读取。
3. **概念去重**：所有 concept 创建/更新走单一 `resolve_or_create_concept`，命中 `canonical_id` 则合并、**绝不新建重复页**；`_registry.yaml`/`aliases.md` 由概念页 frontmatter 派生，`/ingest` 不直接写派生文件。
4. **两阶段发布**：`/ingest` 写 `status: proposed`；收尾门禁通过才 promote 到 `published` 并入 index，失败回滚（`pipeline-workspace/snapshots/`，**默认非 git**）+ 进 Review-Queue。
5. **覆盖保护**：写已存在页须在 work-order snapshot 中、`managed_by != human`、hash 一致，否则不覆盖、出 proposal。
6. **单一业务 SQLite** 承载 source 级状态机（见 spec §3.3）。

## Vault 结构（输出）

单一 vault：`wiki/domains/<domain>/{lessons,concepts}`、`concepts/`（仅 shared，含 `_registry.yaml`）、`topics/`、`comparisons/`、`synthesis/`、`sources/`、`assets/`、`overview.md`、`index.generated.md`、`log.md`、`aliases.md`、`Review-Queue/`。**概念/主题为主，lessons 跟随源 TOC 为辅**。详见 spec §4/§8。

## Windows 工具选择

Claude Code 的 Bash 工具底层是 Git Bash (MSYS2)，处理含中文的 Windows 路径会崩溃。

1. **优先用原生工具**：Glob、Grep、Read、Edit —— 不经过 Bash，无路径问题。
2. **需要执行命令时**：直接调用 `pwsh`（PowerShell 7），不要通过 Git Bash 调用 PowerShell。
3. **禁止**：不要用 Bash 工具执行 `powershell -Command "..."` 或 `Select-String` 等 PowerShell 命令。

## 报告写入约定

执行报告、修复报告、审阅报告写入项目文件（如 `pipeline-workspace/reports/` 或 `docs/`），不在对话中复制大段输出；对话中只说一句指引用户读本地文件。

## Agent skills

- **Issue tracker**：工作跟踪在 GitHub Issues `Iabstergo1/pdf-to-study-kb`，见 `docs/agents/issue-tracker.md`。
- **Triage labels**：默认 triage 标签词表，见 `docs/agents/triage-labels.md`。
- **Domain docs**：领域语言与术语见 `docs/agents/domain.md`；ADR 在 `docs/adr/`。
````

- [ ] **Step 2: 验证无旧架构残留**

Run:
```
rg -n "LangGraph|plan-units|双 SQLite|run-book|surya|DeepSeek|books/<book-id>" CLAUDE.md
```
Expected: no matches（"LangGraph"仅可出现在 ADR/spec，不在 CLAUDE.md 正文，除"不要重新引入 LangGraph"那句——若该句命中，人工确认仅此一处语义为"禁止"，可接受）。

- [ ] **Step 3: 提交**

```
git add CLAUDE.md
git commit -m "docs: rewrite CLAUDE.md to new (no-LangGraph) architecture + authority chain"
```

---

### Task 5: 重写 `README.md`

**Files:**
- Modify (full rewrite): `README.md`

- [ ] **Step 1: 用新内容整体替换 `README.md`**

写入（覆盖全文）：

````markdown
# 📚 PDF → Study KB

把多来源文档（PDF / DOCX / PPTX / Markdown）编译进一个**不断长大的、多领域的本地 Obsidian 学习知识库**——按概念/主题导航，而不是线性翻原文。采用 [llm-wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) 模式：LLM 增量维护一个持久、互联的 wiki。

> **状态**：本仓库正从旧的 LangGraph/section 管线迁移到新架构。**设计唯一真值**是 [`docs/superpowers/specs/2026-06-08-claude-code-wiki-redesign-design.md`](docs/superpowers/specs/2026-06-08-claude-code-wiki-redesign-design.md)；构建进度见 [`docs/superpowers/plans/`](docs/superpowers/plans/)；关键决策见 [`docs/adr/`](docs/adr/)。旧管线代码仍在过渡期保留。

## 架构

确定性 Python CLI 做预处理 + 后置门禁 + 索引 + 状态跟踪（**零 LLM**）；唯一的 LLM 是**人工触发**的交互式 Claude Code `/ingest`，它读整源、写并合并 wiki、跨页归一概念。

```text
CLI 预处理（零 LLM）        add-source → profile → source-convert → windows → work order
        ↓ 人工触发
Claude Code /ingest（唯一 LLM）  读 source.md / 难页图 → 写 status:proposed 页 + 概念归一
        ↓ 人工触发
CLI 收尾（零 LLM）          确定性 lint → 门禁 promote / 失败回滚+Review-Queue → 重建索引
```

设计要点：
- **不拆分**：不让 LLM 做语义切分；长源用确定性 processing windows（TOC/页码/token 滑窗）读取。
- **概念去重**：canonical concept + 别名归一，同一概念一页、跨来源累积。
- **两阶段发布**：未过门禁的内容不进正式 index，进 `Review-Queue/`。
- **非文字内容**：分层后端（pymupdf4llm / marker / docling），难页交 Claude 多模态读图、公式 KaTeX、源页截图可核对。

## Vault 结构（输出）

```text
wiki/
  domains/<domain>/{lessons, concepts}   # 讲义（跟随源 TOC）+ 领域私有概念
  concepts/        # 仅 shared（跨域提升后），含 _registry.yaml（派生）
  topics/ comparisons/ synthesis/        # 综合层（一等产物）
  sources/  assets/  Review-Queue/
  overview.md      # living synthesis，入口
  index.generated.md  log.md  aliases.md # 派生
```

## 现状与运行

- 新架构的命令面（`add-source` / `profile` / `source-convert` / `windows` / `workorder` / `lint` / `promote` / `status` / `next` + `/ingest`）随 `docs/superpowers/plans/` 逐期落地，详见 spec §3、§9。
- 旧 CLI（`scripts/pipeline.py` 的 `init-book` / `plan-units` / `run-book` 等）及本地 Web 前端（`scripts/serve.py`）在迁移期仍可运行，但**不代表目标架构**，将按计划替换。
- 依赖见 `requirements.txt`（`langgraph*` 为过渡期保留，将在旧代码删除时一并移除）。

## 在 Obsidian 中阅读

Obsidian → `Open folder as vault` → 选 `wiki/` → 从 `overview.md` 开始。所有生成笔记 frontmatter 为 Dataview 友好（`type`/`canonical_id`/`domain`/`status`/`source_refs`…）。

## 文档导航

| 文档 | 用途 |
|------|------|
| `docs/superpowers/specs/2026-06-08-…design.md` | 设计唯一真值 |
| `docs/adr/` | 架构决策记录 |
| `docs/superpowers/plans/` | 分期实现计划（P0–P7） |
| `docs/agents/domain.md` | 领域术语 |
| `CLAUDE.md` | Agent 指令 |
````

- [ ] **Step 2: 验证无旧架构作为"现行"描述、无指向被删文件的链接**

Run:
```
rg -n "semantic-pdf-to-obsidian-implementation-guide|llm-wiki-borrowings" README.md
```
Expected: no matches.

- [ ] **Step 3: 提交**

```
git add README.md
git commit -m "docs: rewrite README to new architecture + authority chain"
```

---

### Task 6: 标注 `requirements.txt`（不删 langgraph，标记 P4 删）

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: 用新内容整体替换 `requirements.txt`**

写入（覆盖全文）：

```text
# PDF to Study KB 依赖
# 架构见 docs/superpowers/specs/2026-06-08-claude-code-wiki-redesign-design.md

# 核心（保留）
pymupdf>=1.23.0   # PDF 解析 / 页渲染
pyyaml>=6.0       # YAML

# DEPRECATED：新架构不用 LangGraph（见 docs/adr/0001）。
# 仅为过渡期旧管线（scripts/langgraph_worker.py）可运行而保留；
# 随旧代码在 P4 一并删除。
langgraph>=0.2.0
langgraph-checkpoint-sqlite>=3.0.1

# 非文字内容后端：新架构在 P1 引入 pymupdf4llm / marker / docling（届时补此处）。
# surya-ocr 过渡期保留，P1 后由难页 Claude 读图 + marker/docling 取代。
surya-ocr>=0.20.0

# 测试
pytest>=7.0
```

- [ ] **Step 2: 验证标注存在**

Run:
```
rg -n "DEPRECATED|adr/0001|P1|P4" requirements.txt
```
Expected: 至少命中 `DEPRECATED` 与 `adr/0001`。

- [ ] **Step 3: 提交**

```
git add requirements.txt
git commit -m "docs: annotate requirements (langgraph deprecated, removed in P4)"
```

---

### Task 7: 删除过时文档 + 清理 spec 的"取代"引用

**Files:**
- Delete: `docs/semantic-pdf-to-obsidian-implementation-guide.md`
- Delete: `docs/llm-wiki-borrowings-and-output-redesign.md`
- Modify: `docs/superpowers/specs/2026-06-08-claude-code-wiki-redesign-design.md`（仅顶部"取代"注一行）

- [ ] **Step 1: 先确认两份待删文档已无被引用**

Run:
```
rg -n "semantic-pdf-to-obsidian-implementation-guide|llm-wiki-borrowings-and-output-redesign" --glob '!docs/superpowers/specs/2026-06-08-claude-code-wiki-redesign-design.md'
```
Expected: no matches（Task 3/4/5 已移除引用）。若仍有命中，先回到对应文件清除再继续。

- [ ] **Step 2: 改 spec 顶部"取代"注，去掉指向即将删除文件的路径**

在 `docs/superpowers/specs/2026-06-08-claude-code-wiki-redesign-design.md` 中替换：

- old: `> 取代 \`docs/llm-wiki-borrowings-and-output-redesign.md\` 的初版思路（那版假设单一来源、且过度依赖 OCR/evidence 硬门禁）。`
- new: `> 取代初版分析思路（假设单一来源、过度依赖 OCR/evidence 硬门禁；该初稿已随本 spec 定稿删除）。`

- [ ] **Step 3: 删除两份过时文档**

Run:
```
git rm docs/semantic-pdf-to-obsidian-implementation-guide.md docs/llm-wiki-borrowings-and-output-redesign.md
```
Expected: 两文件被 stage 为删除。

- [ ] **Step 4: 验证删除且全仓库无悬挂引用**

Run:
```
rg -n "semantic-pdf-to-obsidian-implementation-guide|llm-wiki-borrowings-and-output-redesign"
```
Expected: no matches（含 spec 顶部注已改）。

- [ ] **Step 5: 提交**

```
git add docs/superpowers/specs/2026-06-08-claude-code-wiki-redesign-design.md
git commit -m "docs: delete obsolete implementation guide and superseded borrowings doc"
```

---

### Task 8: 全文档权威链一致性终检

**Files:** （无改动，纯验证）

- [ ] **Step 1: 文档层无旧架构作为"现行"的残留**

Run（只扫文档/权威文件，不扫 scripts/ 代码与 ADR/spec 的刻意提及）：
```
rg -n "plan-units|run-book|双 SQLite|LangGraph checkpointer|section-manifest" CLAUDE.md README.md docs/agents/
```
Expected: no matches。

- [ ] **Step 2: 权威指针齐全**

Run:
```
rg -n "2026-06-08-claude-code-wiki-redesign-design" CLAUDE.md README.md docs/agents/domain.md
```
Expected: 三个文件都命中（都指向 spec）。

- [ ] **Step 3: docs/ 树符合预期**

Run:
```
rg --files docs
```
Expected：含 `docs/adr/0001-drop-langgraph-adopt-claude-code-wiki.md`、`docs/agents/{domain,issue-tracker,triage-labels}.md`、`docs/superpowers/specs/2026-06-08-...md`、本 plan；**不含**两份已删文档。

- [ ] **Step 4: 终检提交（若 Step 2 改 spec 注未单独提交则在此收尾）**

```
git status --short
```
Expected: 工作树干净（所有改动已提交）。

---

## Self-Review

- **Spec/目标覆盖**：删除过时文档（Task 7）、新增 ADR（Task 2）、重写 domain/CLAUDE/README（Task 3–5）、标注 requirements（Task 6）、终检（Task 8）——覆盖"整理权威链 + 删旧文档 + 不被旧路径拉回"目标。✓
- **占位符**：各 Task 含完整文件正文，无 TODO/TBD。✓
- **一致性**：权威链表述在 domain.md / CLAUDE.md / README.md 三处一致（同一 spec 路径、同一 ADR 目录）；术语（source / processing window / work order / canonical / two-stage publish / 单一业务 SQLite）与 spec §3.1/§3.3/§6/§9 一致。✓
- **顺序安全**：先移除引用（Task 3–5）再删文件（Task 7），无悬挂引用窗口。✓
- **范围**：只动文档，不动 `scripts/`（旧代码删除属 P4）；`requirements.txt` 仅标注不删除（避免断旧管线）。✓

## 完成后

本计划完成 = 权威链收敛，可安全进入 **P0（状态底座 + 文档同步）** 的实现计划。
