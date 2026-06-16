# 📚 PDF → Study KB

> 把 **PDF / DOCX / PPTX / Markdown** 多来源文档，用**对话**增量编译进一个**不断长大、跨领域、按概念导航的本地 Obsidian 学习知识库**。

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white">
  <img alt="Tests" src="https://img.shields.io/badge/tests-passing-success">
  <img alt="Pipeline" src="https://img.shields.io/badge/pipeline-zero--LLM-blueviolet">
  <img alt="Agents" src="https://img.shields.io/badge/agents-Claude%20Code%20%2B%20Codex-orange">
  <img alt="Output" src="https://img.shields.io/badge/output-Obsidian%20vault-7C3AED">
</p>

这是一个 **对话式 agent 驱动的知识库编译器**：你在 **Claude Code 或 Codex**（任选其一）里用自然语言说“把这本书加进知识库”，背后的 LLM 就会自己跑完**预处理 → 写笔记 → 概念归一 → 收尾发布**全流程。两个 agent 共享同一套确定性 CLI 与同一个 vault，行为一致、可互换。不是“按章节翻译原文”，而是 [llm-wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) 模式：相同概念**合并更新**，新内容**新增页面**，库越长越互联。

> [!NOTE]
> **项目真值**：Claude Code 看 [`CLAUDE.md`](CLAUDE.md)，Codex 看 [`AGENTS.md`](AGENTS.md)（两者对等、调同一套 CLI）。skill 运行时协议在 [`docs/skill-runtime/`](docs/skill-runtime/)。

---

## 目录

**快速上手**

- [✨ 它解决什么](#-它解决什么)
- [🚀 安装（克隆后三步）](#-安装克隆后三步)
- [💬 如何使用（端到端工作流）](#-如何使用端到端工作流)

**深入：架构与组件**

- [🏗️ 架构](#️-架构)
- [🧩 对话式 skills 全表](#-对话式-skills-全表)
- [🛠️ 底层：确定性 CLI（高级控制与排障接口）](#️-底层确定性-cli高级控制与排障接口)
- [📂 Vault 结构与产物来源](#-vault-结构与产物来源)
- [🔄 状态机与故障恢复](#-状态机与故障恢复)
- [⏸️ 中断续跑（上下文上限 / 订阅限额）](#️-中断续跑上下文上限--订阅限额)
- [👓 在 Obsidian 中阅读](#-在-obsidian-中阅读)
- [🧪 开发与测试](#-开发与测试)
- [📚 文档导航](#-文档导航)

---

## ✨ 它解决什么

| 痛点 | 本项目的做法 |
|------|------|
| 多本书各存一份笔记，概念重复、互不连通 | 单一 vault、按领域分区；同一概念**走唯一入口合并**，绝不重复建页 |
| 笔记是线性翻译，越读越像目录 | **概念/主题为主**组织，lessons 跟随源 TOC 只作线性辅助层 |
| 要记一堆命令、手动跑流水线 | **对话式**：一句“把这本书加进知识库”，skill 自己编排全流程 |
| 公式/图表在 PDF 里转写易碎 | 文本走 PyMuPDF，**难页渲染整页 PNG** 交 Claude 多模态读图，写成 KaTeX |
| 自动写库直接覆盖手改内容 | **两阶段发布** + 覆盖保护：先写 `proposed`，过门禁才 `published`，失败回滚进 Review-Queue |

---

## 🚀 安装（克隆后三步）

**前置：** [Python](https://www.python.org/) 3.12+、[Claude Code](https://claude.com/claude-code) 或 **Codex**（任选其一作为对话接口）、[Obsidian](https://obsidian.md/)（可选，用来阅读成品）。

```bash
# ① 克隆并进入项目
git clone https://github.com/Iabstergo1/pdf-to-study-kb.git
cd pdf-to-study-kb

# ② 安装依赖（建议用虚拟环境 / Conda 环境，避免污染全局）
python -m pip install -r requirements.txt

# ③ 自检：核心依赖就位（应打印 PyMuPDF 与 PyYAML 版本）
python -c "import fitz, yaml; print('PyMuPDF', fitz.VersionBind, '| PyYAML', yaml.__version__)"
```

装好后，用 **Claude Code 或 Codex** 打开本项目根目录，即可进入下一节的对话流程。

> [!NOTE]
> **Claude Code 与 Codex 完全对等、二选一即可**：两者各读自己的项目真值（[`CLAUDE.md`](CLAUDE.md) / [`AGENTS.md`](AGENTS.md)）与各自的 skill 树（[`.claude/skills/`](.claude/skills/) 与 [`.agents/skills/`](.agents/skills/)，两树**字节对等**），但**调用同一套 CLI、操作同一个 `wiki/`**，因此行为一致、可互换。你**无需两个都装**。

> [!NOTE]
> 必需依赖只有 **PyMuPDF + PyYAML**（见 [`requirements.txt`](requirements.txt)）。
> 公式保真走 route B：`source-convert` 用 PyMuPDF 抽文本，公式风险页渲染整页 PNG，由 ingest **读图写 KaTeX** 保真。不依赖任何重型 OCR/ML 后端。

---

## 💬 如何使用（端到端工作流）

打开项目后**全程用自然语言对话**，模型按意图自动调用对应 skill（也可手动输入 `/<skill>`）。你**无需记命令、也无需自己撰写笔记内容**——内容由模型在对话中生成。典型一本书的流程只有三步：

### ① 填学习目标 —— 唯一需要你手写的输入（可选但推荐）

第一次对话前，先初始化 vault 脚手架（若 agent 尚未自动建库，可直接说一句“初始化知识库”或手动跑 `python scripts/pipeline.py init-vault`），然后编辑 **`wiki/_meta/purpose.md`**：写下你的**学习目标、当前重点、偏好的讲解风格**（如应试导向 vs 研究导向、偏直觉 vs 偏推导、哪些章节是重点）。

这是 `init-vault` 落下的空模板，也是**整个 vault 里唯一为你准备、需要你手写的文件**；`ingest` 写库时会读取它来调整产出。其余所有内容页都由模型生成，你都不用碰——填不填都能跑，但填了产出更贴合你的需求。

### ② 一句话入库（ingest）

把 **PDF / DOCX / PPTX / Markdown** 放进 `books/<name>/input/`，然后对 agent 说一句话即可。下例用占位符 `<...>` 表示你自己的文件与领域：

```text
你：把 books/<name>/input/<your-file>.pdf 加进知识库，领域 <domain>

Claude / Codex（ingest skill）：
  → 与你确认 source_id 与 domain（由文件名 / 你的指定派生）
  → 跑预处理：add-source → profile → source-convert → windows → workorder
  → 按 processing windows 读整源 / 难页图，写 concepts/lessons（status: proposed）
  → 经 resolve-concept 归一同名概念（命中即合并，绝不重复建页）
  → 跑收尾 lint：通过则 promote 进 index；失败则回滚 + 写 Review-Queue 并告诉你怎么修
  → 汇报：发布了哪些页 / 哪些进了复核队列
```

`ingest` skill 端到端跑完**预处理 → 写 proposed 页 → 概念归一 → 收尾 lint 发布**，只在需人工决策时停下问你（lint 失败 / 覆盖冲突 / 跨域提升 / human 页）。`books/` 目录不入版本控制——放哪本书、产出什么内容，都只存在于你本地。

> [!NOTE]
> **每本书的入库是一次需付费的 LLM 操作**，并非“导入即用”。项目交付时为空库，内容通过运行 ingest 逐步生成。想先零成本验证预处理链，可只跑 `source-preflight`（见下表）。

### ③ 在 Obsidian 阅读成品

Obsidian → **Open folder as vault** → 选项目里的 `wiki/` 目录，从 `overview.md` 开始（阅读技巧见〈[在 Obsidian 中阅读](#-在-obsidian-中阅读)〉）。

> [!IMPORTANT]
> “总结这篇 / 解释这段 / 翻译一下 / 问个常识”这类只读请求**不会**触发写库——skill 的描述里写了负样本，模型会当普通问题回答。

---

## 🏗️ 架构

**对话编排层**（Claude Code / Codex skills，唯一 LLM）+ **确定性执行层**（Python CLI，零 LLM）。
skill 只是自然语言指令，通过 shell 调用 CLI；**所有业务逻辑、安全守卫都在 CLI 里**。

**为什么分两层**：把"可重复、可观测、可守卫"的工作（状态机、并发锁、lint 门禁、覆盖保护、索引重建）全部下沉到零-LLM 的确定性 CLI，由 `tests/` 当规格覆盖；只把唯一高价值、无法确定化的工作（读整源写笔记、跨页归并概念）留给人触发的 LLM 会话。好处是**安全性与"模型是否自动触发 skill"解耦**——即便误触发，写库仍要逐一过 CLI 守卫与两阶段门禁，越不过就回滚，不会污染已发布内容。

```text
你在 Claude Code 或 Codex 里说：“把这个 PDF 加进知识库，领域 X”
                         │
                         ▼  ingest skill 接管，端到端编排 ↓↓↓
┌──────────────────────────────────────────────────────────────────────┐
│  ① 确定性预处理（零 LLM，幂等可重跑）                                    │
│     add-source → profile → source-convert → windows → workorder        │
├──────────────────────────────────────────────────────────────────────┤
│  ② LLM 写库（同一会话）                                                  │
│     按 processing windows 读整源 / 难页图 → 写 status:proposed 页        │
│     → 概念归一 + 综合层（overview / topic / comparison / synthesis）     │
├──────────────────────────────────────────────────────────────────────┤
│  ③ 确定性收尾（零 LLM）                                                  │
│     lint 门禁 → promote(proposed→published) 或 回滚+Review-Queue         │
│     → 从 frontmatter 重建 index / registry / aliases                    │
└──────────────────────────────────────────────────────────────────────┘
                         │
                         ▼  只在需人工决策时停下问你
              （lint 失败 / 覆盖冲突 / 跨域提升 / human 页）
```

**四条核心约束：**

1. **不拆分** — 不让 LLM 做语义切分；长源用确定性 *processing windows*（TOC / 页码 / token 滑窗）读取，窗口只是“读取单位”，不决定 wiki 页面结构。
2. **概念去重** — 所有概念创建/更新走单一 `resolve-concept` 入口，命中合并、绝不重复建页；`_registry.yaml` / `aliases.md` 是**派生文件**。
3. **两阶段发布** — 先写 `status: proposed`；收尾 `lint` 过门禁才 promote 成 `published` 并入 index，失败回滚 + 进 `Review-Queue/`。
4. **覆盖保护** — 写已存在页须满足“在快照中 + `managed_by != human` + hash 一致”三条件，否则拒写、出 proposal。

> 自动触发不削弱安全：第 3、4 条由确定性 CLI 守卫强制执行，与“skill 是否被模型自动调用”正交。

---

## 🧩 对话式 skills 全表

在 Claude Code 或 Codex 中，**直接用自然语言描述即可**——模型会按意图自动调用对应 skill（也可手动输入 `/<skill>`）。两套 agent 各读自己的 skill 树（`.claude/skills/` 与 `.agents/skills/`），但**字节对等、调用同一套 CLI**，因此行为一致。
所有写库 skill 全程受确定性 CLI 守卫保护，只写 `status: proposed`。

| skill | 一句话说什么就触发 | 它做什么 |
|------|------|------|
| **`ingest`** | “把这本书 / 这个 PDF 加进知识库，领域 X” | ⭐端到端：预处理 → 写 proposed → 收尾 lint，只在需决策时停 |
| **`kb-query`** | “知识库里关于 X 怎么说” | 只读查询 + 持久化 query-session（**不写库**） |
| **`kb-save`** | “把刚才那个对比 / 结论存进 wiki” | 把 query-session 候选存为 proposed（有准入门槛） |
| **`kb-review`** | “处理一下复核队列” | 逐条过 Review-Queue，给建议、人工定夺 |
| **`wiki-lint-semantic`** | “给知识库做个语义体检” | 查对比维度 / 跨页矛盾，只出 proposal |
| **`kb-qa`** | “给知识库做次 QA / 审计覆盖率” | 体检已发布库或保存前候选，产出报告 + Review-Queue proposal（只读不改库） |
| **`source-preflight`** | “先预处理这个 PDF / 看看能不能 ingest” | 只跑确定性预处理链并验收 staging，不写语义页（**零-LLM 验收门，可零成本先验证**） |
| **`source-xray`** | “给这个已发布来源做拆书阅读笔记” | 基于已发布内容生成 xray 笔记 / synthesis 候选报告，默认只写 `reports/` |
| **`skill-evolve`** | “把这次踩的坑沉淀进 skill / 让 skill 自我改进” | skill 自进化：mine 反复失败 → 你提炼 bounded 编辑 → gate(pytest+双树) → 人 adopt（改的是 skill 自己，不写 vault） |

---

## 🛠️ 底层：确定性 CLI（高级控制与排障接口）

所有 skill 背后调用的都是 `python scripts/pipeline.py <command>`（零 LLM、可独立运行，**全部业务逻辑与安全守卫都在这里**）。日常对话无需手动输入；该接口面向**精细控制、问题排查、手动重跑某一阶段、无人值守脚本化**等高级场景。

命令按生命周期分五组：**状态与维护**（看清进度、崩溃自救）、**预处理**（把"读取与切窗"做成确定性可重跑链）、**ingest 会话支撑**（保证写库可断点续跑、不越界、不覆盖人工页）、**收尾与查询**（两阶段发布的门禁与提升）、**skill 自进化**（把反复失败沉淀成有界改进）。共 28 个子命令：

<details>
<summary><b>展开：完整 CLI 命令参考</b></summary>

### 状态与维护

> **为什么有这组**：ingest 是可中断的长任务，且同一 vault 受并发锁保护。这组命令让你不依赖 LLM 就能"看清现状 + 崩溃自救"——`status`/`next` 回答"每个来源走到哪一步、锁在谁手里、下一步该做什么"；`fail` 把崩溃残留的 `running` 阶段标记 `failed` 以便重跑；`unlock` 受控回收超时的 stale 锁（活锁拒绝，防误删）；`rebuild-registry` 从概念页 frontmatter 重建派生索引；`init-vault` 幂等搭起空脚手架。

| 命令 | 作用 | 关键参数 |
|------|------|------|
| `status` | 列出每个 source 的阶段/状态 + vault 锁持有者（`[STALE]` 标记崩溃残留锁） | — |
| `next` | 列出每个 source 的**下一步人工动作** + stale 锁清理建议 | — |
| `init-vault` | 建 `wiki/` 脚手架 + 种子文件（幂等，不覆盖） | — |
| `unlock` | 受控回收 stale vault 锁；活锁拒绝 | `--ttl 1800` |
| `fail` | 把崩溃残留的 `running` 阶段标记 `failed` | `--source --stage --error` |
| `rebuild-registry` | 从概念页 frontmatter 重建 `_registry.yaml` + `aliases.md` | — |

### 预处理（零 LLM，顺序固定，幂等跳过）

> **为什么有这组**：把"读取与切窗"做成**确定性、可重跑**的固定链，LLM 才不必做语义拆分（核心约束①不拆分）。顺序固定、每步幂等：失败重跑不会污染状态，已完成的步骤自动跳过。最终产出 `source.md`（干净文本）+ `windows.jsonl`（确定性读取单位）+ `workorder.yaml`（写入边界与 registry 快照）三件套，作为 LLM 写库的**唯一输入契约**——LLM 只能在 workorder 划定的范围内写。

| 命令 | 作用 | 输入 → 产出 | 关键参数 |
|------|------|------|------|
| `add-source` | 注册来源到状态库 | 原始文件 → `sources` 记录 | `--source --domain --path --fmt {pdf,md,docx,pptx}` |
| `profile` | 逐页 profile + `needs_vision` 判定 | raw → `staging/<src>/pages.jsonl` | `--source` |
| `source-convert` | 转干净 Markdown，难页渲染 PNG | raw → `staging/<src>/source.md` + `assets/` | `--source` |
| `windows` | 生成确定性 processing windows | source.md → `windows.jsonl` | `--source` |
| `workorder` | 生成 ingest 事务契约 | → `staging/<src>/workorder.yaml` | `--source` |

### `ingest` 会话支撑（通常由 skill 内部调用）

> **为什么有这组**：保证 LLM 写库这一步"**可断点续跑 + 不越界 + 不覆盖人工页**"。`ingest-start`/`ingest-done` 取/释放并发锁并校验 registry 是否过期；`window-start`/`window-done`/`window-fail` 做窗级记账（中断后能从下一个未完成 window 续跑，并维持锁心跳）；`resolve-concept` 是概念去重的**唯一入口**（命中合并、未命中新建，核心约束②）；`check-write` + `snapshot-page` 在写已存在页前强制覆盖保护（不在快照中 / 是 human 页 / hash 不符 → 拒写出 proposal，核心约束④）。

| 命令 | 作用 | 关键参数 |
|------|------|------|
| `ingest-start` / `ingest-done` | 开工（取锁 + stale registry 校验）/ 收工（释放锁） | `--source` |
| `show-window` | 打印指定 window 的源文本 | `--source --window` |
| `window-start` / `window-done` / `window-fail` | window 级记账（断点续跑 + 锁心跳） | `--source --window [--hash/--writes/--error]` |
| `resolve-concept` | 概念归一唯一入口：命中合并 / 未命中新建 | `--mention --domain [--alias --ref-source --ref-sections]` |
| `check-write` | 写前守卫：边界 + 覆盖保护（DENY 则 `exit 1`） | `--source --path` |
| `snapshot-page` | 就地 merge 前快照该页 | `--source --path` |

### 收尾、提升与查询

> **为什么有这组**：发布是**一道门**而非直接写盘（核心约束③两阶段发布）。`lint` 是收尾门禁——proposed 全部过检才 promote 成 published 并重建 index，任一不过（断链 / 缺必需小节 / 孤儿页 / 重复 canonical_id / 公式页缺源图）即回滚就地修改、把违规项写进 `Review-Queue/`；`promotion-candidates`/`promote-concept` 处理"领域私有概念何时升为跨域 shared"（人工确认后机械执行）；`check-session` 守 query-session 的只读目录契约。

| 命令 | 作用 | 关键参数 |
|------|------|------|
| `lint` | 收尾门禁：proposed 过则 promote、败则回滚 + Review-Queue | `--source` |
| `promotion-candidates` | 检测跨域提升候选（人工确认） | `--propose` |
| `promote-concept` | 机械提升一个概念为 shared | `--id concept.<domain>.<slug>` |
| `check-session` | query-session 目录契约检查（Q1） | `--id <run_id> [--saved]` |

### skill 自进化（零 LLM 命令；唯一 LLM 是人触发的 `skill-evolve` skill）

> **为什么有这组**：让**反复出现的 lint 失败**能被沉淀成对 skill 自身的有界改进，而不是同一个坑一踩再踩。`skill-mine` 把失败信号聚成 `backlog.yaml`；人触发的 `skill-evolve` skill 写出 bounded 编辑；`skill-gate` 当门禁（pytest + 双树字节对等 + 只许动 skill 两树，挡越权改 `tests/`）；`skill-stage` 登记候选；最终 `skill-adopt` 由**人**重跑门禁后才合并进双树。改的始终是 skill 自己，绝不写 vault。

| 命令 | 作用 | 关键参数 |
|------|------|------|
| `skill-mine` | 扫 `review_proposals` 失败信号 → 按规则聚类成 `backlog.yaml`（**`lint` 失败时自动刷新**，也可手动重扫） | — |
| `skill-gate` | 候选门：gate-integrity（只许动 skill 两树，挡 `tests/` 越权）+ `pytest`（含双树对等） | `--candidate [--base]` |
| `skill-stage` | gate 绿后登记候选提案（diff + audit），线上不动 | `--candidate [--base]` |
| `skill-adopt` | **人触发**：重跑 gate 兜底后把候选合并进双树（commit） | `--candidate [--base]` |

> 状态库默认锚定仓库根：`pipeline-workspace/state/study-kb.sqlite`。设环境变量 `STUDY_KB_ROOT` 可整体重定向（测试隔离 / 多库场景）。

</details>

---

## 📂 Vault 结构与产物来源

`init-vault` 先落一个**空脚手架**——下列目录加上 `overview.md` / `log.md` / `_meta/purpose.md` 三个种子文件（幂等，已存在绝不覆盖）；其余内容随 `ingest` 写库与收尾 `lint` 逐步生成。先看整体布局：

```text
wiki/
├── _meta/purpose.md     # ← 你手写：学习目标与偏好（ingest 读取）
├── domains/<domain>/
│   ├── lessons/         # 讲义：跟随源 TOC 的线性辅助层
│   └── concepts/        # 领域私有概念（默认归属）
├── concepts/            # 仅 shared（跨域提升后），含 _registry.yaml（派生）
├── topics/              # 跨章节/跨来源主题综合
├── comparisons/         # 横向对比页：多个并列对象同页比差异维度
├── synthesis/           # 深度综合/结晶化
├── sources/             # 所有来源摘要（统一台账）
├── assets/<src>/        # 源页截图：公式风险页(needs_vision)整页 PNG，供 route B 读图
├── Review-Queue/        # 未过门禁 / 需人工决策的 proposal
├── overview.md          # living synthesis，vault 入口（ingest 维护）
├── index.generated.md   # 内容目录（派生，只收录 published；首次 ingest 后出现）
├── aliases.md           # 别名视图（派生；首次 ingest 后出现）
└── log.md               # append-only（ingest / lint 追加）
```

**每一部分由谁生成、为什么需要：**

| 路径 | 由谁生成 | 作用 / 为什么需要 |
|------|------|------|
| `_meta/purpose.md` | **你手写**（init-vault 落空模板） | 你的学习目标 / 重点 / 偏好；ingest 读取以调整产出。**唯一需要你维护的输入文件**。 |
| `overview.md` | init-vault 种子 → ingest 增量重写 | vault 入口的“活综合页”（概念地图 + 学习路线），每次 ingest 增量更新；`managed_by: pipeline`，勿手改。 |
| `log.md` | ingest + 收尾 lint 追加（append-only） | 操作日志，记录每次入库 / 发布，便于回溯。 |
| `domains/<domain>/lessons/` | ingest（LLM），按源 TOC | 讲义：跟随源目录的线性辅助层，便于对照原书顺读。 |
| `domains/<domain>/concepts/` | ingest（LLM），经 `resolve-concept` | 领域私有概念页（默认归属）；同名概念命中即合并，绝不重复建页。 |
| `concepts/`（含 `_registry.yaml`） | 跨域提升后写入；registry 由收尾 CLI 派生 | 仅存被提升为 **shared** 的跨域概念；`_registry.yaml` 是概念派生索引。 |
| `topics/` | ingest（LLM） | 跨章节 / 跨来源的主题综合页，把散落的相关内容收拢到一处。 |
| `comparisons/` | ingest（LLM） | 把同一主题下**多个并列对象放一页做横向对比**（按差异维度），避免比较点散落各页。 |
| `synthesis/` | ingest（LLM） / `kb-save` | 深度综合、结论结晶化的页面。 |
| `sources/` | ingest（LLM） | 每个来源一页摘要，作为“来过哪些书”的统一台账。 |
| `assets/<src>/` | **`source-convert` 渲染并同步**（零 LLM） | 把被判定为**公式 / 排版风险页（`needs_vision`）**的源页整页渲成 PNG，供 ingest 读图把公式写成 KaTeX（route B）。**因此这里出现的图，正是该来源里上 / 下标、分数等在纯文本抽取下会失真的那些页**——数量、页码随每本书的公式密度而不同，由确定性判定自动选出，无需人工指定。 |
| `Review-Queue/` | 收尾 lint 失败时写入 | 未过门禁 / 需人工决策的 proposal；你用 `/kb-review` 逐条处置。 |
| `index.generated.md` · `aliases.md` | 收尾 CLI 从 frontmatter **派生重建**（首次 ingest 后出现） | 内容目录 / 别名视图，只收录 `published`。**派生文件，手改会被下次收尾覆盖**。 |

> **概念/主题为主，lessons 跟随源 TOC 为辅。** 三个派生文件（`index.generated.md` / `aliases.md` / `_registry.yaml`）一律由收尾 CLI 从 frontmatter 重建，写库 skill 绝不手写。

---

## 🔄 状态机与故障恢复

每个 source 走单向阶段流（单一业务 SQLite 记录）：

```text
registered → profiled → converted → windowed → workorder_ready
          → ingest_waiting → ingesting → ingested(proposed) → lint(published)
```

| 故障 | 现象 | 恢复 |
|------|------|------|
| **阶段崩溃** | 卡在 `running` | `pipeline.py fail --source X --stage <阶段> --error "原因"` → 重跑该阶段 |
| **lint 失败** | source 进 `lint/failed` | 自动回滚就地 merge、违规写 `Review-Queue/`；修复后重跑 `lint`，或重走 ingest |
| **孤儿 proposed 页** | 不归属任何 source | 阻断 lint（fail-closed）；按 Review-Queue 提示补归属后重跑 |
| **ingest 崩溃残留锁** | `status` 显示 `[STALE]` | `next` 给建议，`unlock` 回收（默认 heartbeat 超 1800s 才允许） |

故障发生时，`ingest` skill 会**停下来**把现象和修复建议告诉你，而不是硬闯。

---

## ⏸️ 中断续跑（上下文上限 / 订阅限额）

长文档的 ingest 是单次会话内的长任务，可能遇到两类中断：上下文窗口被压缩，或订阅用量达到限额。两类中断均不会丢失进度——确定性层提供持久化底座：窗级记账（`ingest_progress`，SQLite）、已落盘的 `status: proposed` 页、`digest.md` 外部记忆，以及幂等的 `ingest-start`（重入时返回 `resumed`）。从任意中断点重启，都会从下一个未完成的 processing window 继续。

### 上下文窗口压缩：自动恢复

Claude Code 的 auto-compact 与 SessionStart hook 配合完成自动恢复：`.claude/settings.json` 在 `compact` / `resume` 事件时调用 [`scripts/resume_hint.py`](scripts/resume_hint.py)，将 `pipeline.py next`（机器推导的下一步）与各 staging digest 顶部的 `## ⏩ RESUME` 块重新注入上下文。`ingest` skill 在每个 window 维护该 RESUME 块，因此恢复锚点对任意来源自动具备，不限于特定文档。

### 订阅限额（5 小时用量窗口）：需配置调度

限额冻结期间 agent 无法运行；用量窗口复位后，需由外部调度重新触发续跑。可选三种方式，自动化程度依次递增：

| 方式 | 自动化 | 说明 |
|------|------|------|
| 手动续跑 | 人工 | 用量复位后在会话中输入“继续”，hook 注入的 RESUME 块会引导其从下一个 window 继续。 |
| **OS 级调度（推荐用于无人值守）** | 全自动 | 用 Windows 任务计划程序或 cron，以 **大于 5 小时** 的间隔调度 [`scripts/resume-ingest.ps1`](scripts/resume-ingest.ps1)。该脚本仅在存在进行中的 ingest 时唤起所选 agent 的 headless 续跑，冻结期内空转退出、复位后正常完成；脚本头部附注册命令。 |
| 第三方 API key | 不适用 | 按 token 计费、无 5 小时窗口限制；额度充足时可一次完成，无需上述调度。 |

OS 级调度提供的是收敛式重试，而非“一次完成”的保证：每次 `claude -p` / `codex exec` 均为无记忆的新会话，进度持久化在磁盘（`ingest_progress`、proposed 页、`digest`），新会话通过 `pipeline.py next` 与 RESUME 块重新定位到下一个未完成 window。任何一次触发都不会丢失进度；将间隔设为大于 5 小时，可确保相邻两次触发不会同时落入同一冻结窗口——落在冻结期的一次空转退出，下一次成功——从而单调收敛至 `ingest` 与 `lint` 全部完成。

**前提条件**（任一项缺失将中断自动恢复，脚本头部有详细说明）：

1. 所选 agent（`claude` 或 `codex`）已登录并位于 `PATH` 中。
2. 非交互式权限。Claude headless 模式下 Bash 不会自动放行，脚本默认使用 `--dangerously-skip-permissions`（作用范围仅限本仓库与 gitignored 的 `wiki/` 运行时目录），或改用 `acceptEdits` 并在 `permissions.allow` 中放行 `Bash(python scripts/pipeline.py:*)`；Codex 默认用 `--sandbox workspace-write`（最小权限，仅写入 workspace），若沙箱阻止写入则在注册时附加 `-Bypass` 改用 `--dangerously-bypass-approvals-and-sandbox`。
3. 触发时设备处于唤醒状态（睡眠需配置唤醒定时器，笔记本需允许电池供电下运行；注册命令已包含相关设置）。

> **注意**：`ScheduleWakeup` 与会话级 cron 不适用于跨越 5 小时窗口——前者上限为 1 小时，后者需保持 REPL 常驻且在冻结期同样受限。仅 OS 级、独立进程的调度（如 `scripts/resume-ingest.ps1`）能可靠跨越用量复位窗口。
>
> **并发约束**：同一 vault 在同一时刻只允许一个 ingest，请勿为两个 agent 注册指向同一知识库的调度任务。每次触发结果会追加至 `tmp/resume.log` 以供核对。

### 克隆后是否可直接套用

可以。上述机制对任意领域、任意文档均生效，不限于示例文件：状态机、digest、`## ⏩ RESUME` 块与两个续跑脚本均随仓库分发，且与具体文档无关。个人偏好（如自动接受编辑的 `defaultMode`）置于 gitignored 的 `.claude/settings.local.json`，不影响其他使用者。如需无人值守运行，注册一次 `scripts/resume-ingest.ps1` 即可。

---

## 👓 在 Obsidian 中阅读

1. Obsidian → **Open folder as vault** → 选项目里的 `wiki/` 目录
2. 从 `overview.md` 开始

所有生成笔记的 frontmatter 都是 **Dataview 友好**的（`type` / `canonical_id` / `domain` / `status` / `source_refs` …），可用 Dataview 自定义检索视图。

> [!TIP]
> **frontmatter 是承重的**（Dataview 字段 + lint 全靠它），不能删。若觉得它显示在正文开头影响阅读：Obsidian → **Settings → Editor → "Properties in document" 选 "Hidden"**——文件照旧、阅读时不显示。
> **关系图（Graph）过于密集**通常源于"汇总页对每个概念都建立 wikilink"形成的中心化 hub；写页规范已要求仅连接真实的强关系（见 ingest skill 阶段 D），汇总页只保留核心的若干链接，其余以普通文本表述。

---

## 🧪 开发与测试

```bash
# 全量测试（确定性、零 LLM）
python -m pytest tests -q

# 快速冒烟：只检查能否收集
python -m pytest tests --collect-only -q
```

**测试即规格**：因为全部业务逻辑都在确定性 CLI（skill 不承载 Python），`tests/` 就是这套系统的**可执行规格**——改 pipeline 行为应先在这里立约，再改实现。除常规单元覆盖外，有三类"守卫"测试值得单独知道：

- 依赖见 [`requirements.txt`](requirements.txt)。
- [`tests/test_legacy_removed.py`](tests/test_legacy_removed.py) — 守卫**架构不回退**：**LangGraph / 双 SQLite / plan-units / surya 硬管线**一旦被重新引入即失败，防止"换个方向悄悄重写"。
- [`tests/test_command_docs.py`](tests/test_command_docs.py) — 守卫**文档与协议一致**：锁定各对话式 skill 的必备协议要素、`ingest` 的端到端编排，以及本 README 中续跑自动化的旗标措辞，确保文档不随实现漂移。
- 其余 `test_*.py` 覆盖状态机、并发锁、概念归一、覆盖保护、窗口切分、workorder、lint 门禁等**每一条核心约束**——绿表示六条约束都还成立。

---

## 📚 文档导航

| 文档 | 用途 |
|------|------|
| [`CLAUDE.md`](CLAUDE.md) | **Claude Code 项目真值**（架构 / 约束 / 协作约定） |
| [`AGENTS.md`](AGENTS.md) | **Codex 项目真值**（与 CLAUDE.md 对等） |
| [`docs/skill-runtime/`](docs/skill-runtime/) | skills 的运行时协议（routing / schema / 概念归一 / save-back 准入），skill 按需加载 |
| [`.claude/skills/`](.claude/skills/) · [`.agents/skills/`](.agents/skills/) | 9 个对话式 skill 的指令文件（Claude 读前者、Codex 读后者，两树字节对等） |
