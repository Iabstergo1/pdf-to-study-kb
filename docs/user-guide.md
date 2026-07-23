# PDF to Study KB — 用户使用说明（User Usage Guide）

> 本文面向**使用者**，告诉你如何在 Windows + PowerShell 下正确使用本项目。
> 所有命令均经源码核对；命令与行为以项目当前实现为准。
> 更深的实现细节见配套的[**开发实现说明**](developer-guide.md)。

---

## 1. 项目是做什么的

这是一个**对话式 agent 驱动的知识库编译器**。你把 **PDF / DOCX / PPTX / Markdown** 文档放进项目，
在 **Claude Code 或 Codex**（二选一）里用一句自然语言说"把这本书加进知识库"，背后的 LLM 就会自动跑完：

**预处理（解析+双审+切窗）→ 读整本书写学习笔记 → 同名概念合并 → 收尾校验发布**

最终在一个本地 **Obsidian vault（`wiki/` 目录）**里得到一个**按概念导航、跨领域互联、越长越互联**的学习
知识库（llm-wiki 模式）。它**不是** PDF 翻译器、**不是**单篇摘要工具、**不是**无人值守批量转换器——
唯一的 LLM 是你**手动触发**的对话；"总结一下/解释一下/翻译一下"这类只读请求**不会**写库。

**关键特性：**

- 同一概念走唯一入口**合并更新，绝不重复建页**；多本书跨领域积累。
- 公式/图表难页**渲染整页 PNG**，由 LLM 读图保真（公式写 KaTeX）。
- **两阶段发布 + 覆盖保护**：先写 `proposed`，过校验门才 `published`，失败回滚进 Review-Queue，
  **绝不静默覆盖你手改的页面**。
- 每本书入库是一次**需付费的 LLM 操作**，项目交付时是**空库**。

---

## 2. 使用前准备

| 必需 | 说明 | 证据 |
|------|------|------|
| Python 3.12+ | 跑确定性 CLI | `requirements.txt`、README §安装 |
| PyMuPDF + PyYAML | 唯一必需的 Python 依赖（pytest 供测试） | `requirements.txt` |
| Claude Code 或 Codex | 对话接口，**二选一即可**（行为对等） | README §安装 |
| 项目本体 | 克隆到本地（示例：`D:\pdf-to-study-kb`） | — |
| Obsidian（可选） | 阅读成品 vault | README §安装 |
| MinerU（可选） | PDF 严格验收的复核后端 + 扫描/低文本 PDF、DOCX/PPTX 的主解析器 | `requirements.txt` 末尾、README §安装 |

**路径要求：**

- 默认状态库、staging、vault 都锚定在**仓库根目录**下（`pipeline-workspace/`、`wiki/`）。
- 用 **PowerShell 7（pwsh）**，不要用 Windows PowerShell 5.1，也不要用 Git Bash 驱动 PowerShell。
- CJK（中文）源/路径前**务必设** `$env:PYTHONUTF8=1`。
- 建议用专用 conda/venv 环境，**勿污染共享环境**。

---

## 3. 首次安装与配置

```powershell
# ① 克隆并进入项目
git clone https://github.com/Iabstergo1/pdf-to-study-kb.git
cd pdf-to-study-kb

# ② 建专用环境（推荐 conda；也可用 venv）
conda create -y -n study-kb python=3.12
conda activate study-kb

# ③ 装依赖
python -m pip install -r requirements.txt

# ④ 自检：核心依赖就位（应打印 PyMuPDF 与 PyYAML 版本）
python -c "import fitz, yaml; print('PyMuPDF', fitz.VersionBind, '| PyYAML', yaml.__version__)"

# ⑤ CJK 源/路径必设（每个新 PowerShell 会话都要设，或写进 $PROFILE）
$env:PYTHONUTF8=1
```

**可选：安装 MinerU（PDF 严格验收 / 扫描件 / DOCX / PPTX 需要）**

```powershell
python scripts/install_mineru.py            # 装 mineru[core]，按 nvidia-smi 自动换匹配 CUDA torch；无 GPU 留 CPU
python scripts/install_mineru.py --dry-run  # 先看将执行的命令，不实际安装
```

> MinerU 只用 `pipeline` 后端，低显存 GPU（约 4GB）即可。未装时，born-digital PDF 仍可走轻量 PyMuPDF
> 路径（dev），但产物标记为 `degraded / 未双审`，**不算生产严格验收**。

**用 Claude Code / Codex 打开项目：** 装好后用任一 agent 打开项目根目录即可进入对话流程。
你**只需装其中一个**（两者读各自的项目真值 `CLAUDE.md` / `AGENTS.md` 与各自的 skill 树，但调同一套 CLI、
操作同一个 `wiki/`，行为一致）。

---

## 4. 基础使用流程（端到端，按正确顺序）

> 日常使用**全程用自然语言对话**，模型自动调用对应 skill，你无需记命令。下面给出每步的**目的、对话/命令、
> 预期产出、确认方式、常见失败**，并附等价的底层 CLI（高级排障用）。

### 第 0 步：初始化空库（一次性）

- **目的**：建 `wiki/` 脚手架。
- **做法**：对 agent 说"初始化知识库"，或手动：

```powershell
python scripts/pipeline.py init-vault
```

- **预期输出**：`[OK] seeded overview.md` / `log.md` / `_meta/purpose.md` / `.obsidian/...` + `[OK] vault skeleton at ...\wiki`。
- **生成文件**：`wiki/` 目录 + 上述种子文件。
- **确认成功**：`wiki/overview.md` 存在。
- **常见失败**：无（幂等，已存在不覆盖）。

### 第 1 步（可选但推荐）：填学习目标

- **目的**：让产出贴合你的需求。
- **做法**：编辑 **`wiki/_meta/purpose.md`**，写下学习目标、当前重点、偏好讲解风格
  （应试 vs 研究、偏直觉 vs 偏推导、哪些章节是重点）。
- **说明**：这是整个 vault 里**唯一需要你手写的文件**；`ingest` 会读取它。填不填都能跑，填了产出更准。

### 第 2 步：放入来源文件

- **目的**：把原始文档交给项目。
- **做法**：把文件放进 `books/<name>/input/`（`books/` 不入版本控制，放哪本书只存在于你本地）。

```powershell
New-Item -ItemType Directory -Force -Path "books\game-theory\input" | Out-Null
Copy-Item "C:\downloads\博弈论.pdf" "books\game-theory\input\博弈论.pdf"
```

### 第 3 步：一句话入库（ingest）

- **目的**：跑完整端到端流程并发布。
- **做法**：对 agent 说（`<...>` 替换为你的文件与领域）：

```text
你：把 books/game-theory/input/博弈论.pdf 加进知识库，领域 game-theory
```

- **预期行为**（ingest skill 自动）：
  1. 与你确认 `source_id` 与 `domain`；
  2. 跑预处理：`add-source → profile → source-convert → source-audit →`（有分歧则 agent 仲裁 →
     `arbitration-apply`）`→ windows → workorder → preflight-eval`；
  3. 读 `chapters.json` 建全书理解，按章判断内容路由（理论/方法/案例/参考/观点，只是推荐取向、
     不强制），按章读整源/难页图，写 `status: proposed` 页（正文按"装置预算"克制使用推导折叠/
     案例解剖/定位段/具名命题等阅读兴趣写法，默认零装置、除自测外一页至多再用一种）；
  4. 经 `resolve-concept` 归一同名概念；
  5. 阶段 E 写综合层（overview/topic/comparison/synthesis）；
  6. 跑收尾 `lint`（两段事务隔离）：先 vault preflight 复检全库已发布页的渲染安全旧伤——发现即阻断
     发布并登记 `Review-Queue/vault-health-*.md`，但**不回滚当前批**（修旧页后直接重跑）；随后检查
     当前批，通过则 promote 进 index；**当前批违规**才回滚（**回滚会连同被就地修改的页一起还原**，
     报告会列出被还原的文件清单，修复违规后须重新应用这些页的改动）+ 写 Review-Queue 并告诉你怎么修；
  7. 汇报：发布了哪些页 / 哪些进了复核队列。
- **生成文件**：`pipeline-workspace/staging/<src>/` 全套预处理产物 + `wiki/` 下各类页 + `wiki/assets/<src>/` 难页图。
- **确认成功**：终端见 `[OK] lint passed: promoted N pages; ... source published`；`wiki/index.generated.md` 出现。
- **常见失败**：lint 未过（进 Review-Queue）、覆盖冲突、MinerU 未装而 PDF 走 strict、扫描件未装 MinerU。见 §8。

> **零成本先验**：想先不花 LLM 钱验证预处理能否跑通，对 agent 说"先 source-preflight 这个 PDF"
> （只跑确定性预处理链 + 验收，不写库）。

### 第 4 步：在 Obsidian 阅读成品

- **目的**：浏览知识库。
- **做法**：Obsidian → **Open folder as vault** → 选项目里的 `wiki/` 目录。
- **从哪开始**：`overview.md` → 主题导航（topic）→ 概念（concept）三层入口；打开关系图视图（已随库配好按 type 着色）；
  用浏览器打开 `knowledge-graph.generated.html` 看力导向知识图谱（点击节点直接跳转对应 Obsidian 笔记）。
- **日常复习**：打开 `wiki/quiz-index.generated.md`（自测题库总索引）——不想系统读书时抽几题自答，
  再点链接回原页展开折叠答案核对；`wiki/propositions.generated.md`（命题总表）是"这个库断言了哪些事"
  的资产清单，做研究时可检索引用。

---

## 5. README 中每个操作的用户指南

> 日常无需手敲这些命令——对话即可。下表供你理解每个操作、以及需要手动排障时使用。

### 5.1 入库相关（对话触发）

| 操作 | 作用 / 何时用 | 对话怎么说 | 会创建/改什么 | 如何查看结果 | 常见错误与修复 |
|------|--------------|-----------|---------------|--------------|----------------|
| `ingest` | 把新来源端到端编进知识库 | "把 \<文件\> 加进知识库，领域 X" | staging 产物 + `wiki/` 各页 + assets | 终端汇报 + `wiki/index.generated.md` + Obsidian | lint 失败→看 Review-Queue 修后说"重跑 lint"；详见 §8 |
| `source-preflight` | **只**跑预处理链 + 验收，**不写库**（零成本先验） | "先预处理这个 PDF / 看看能不能 ingest" | 仅 `staging/<src>/` 产物 | `preflight_eval.json` | MinerU 未装而 strict→装 MinerU 或降级 |
| `source-xray` | 对**已发布**来源做拆书阅读笔记/综合候选 | "给这个已发布来源做拆书阅读笔记" | 默认只写 `pipeline-workspace/reports/source-xray/` | 该 reports 目录 | 来源未发布→先 ingest |

### 5.2 查询与保存（对话触发）

| 操作 | 作用 / 何时用 | 对话怎么说 | 会创建/改什么 | 查看 | 错误与修复 |
|------|--------------|-----------|---------------|------|-----------|
| `kb-query` | **只读**查询知识库 + 持久化查询会话 | "知识库里关于 X 怎么说" | `pipeline-workspace/query-sessions/<id>/`（不写 vault） | 终端回答 | 无 |
| `kb-save` | 把查询会话的对比/结论存成 proposed（有准入门槛） | "把刚才那个对比存进 wiki" | `synthesis/` 等 proposed 页 | lint 后入 index | 准入不达标→门会拦（见 `docs/skill-runtime/save-back-policy.md`） |
| `kb-review` | 逐条处理 Review-Queue / 复核项 | "处理一下复核队列" | 据你定夺改 | `wiki/Review-Queue/` | — |
| `wiki-lint-semantic` | 语义体检（对比维度/跨页矛盾/Q2 价值），只出 proposal | "给知识库做个语义体检" | Review-Queue proposal（不改页） | Review-Queue | — |
| `kb-qa` | QA/审计覆盖率（只读不改库） | "给知识库做次 QA / 审计覆盖率" | 报告 + Review-Queue proposal | reports | — |
| `skill-evolve` | 把反复踩的坑沉淀成对 skill 的有界改进 | "把这次踩的坑沉淀进 skill" | skill 候选提案（不写 vault） | `skill-evolution/` | gate 不过→修候选 |
| `kb-postmortem` | **发布后验后复盘**：一本书入库完，看看这次跑得怎么样 | "复盘这次入库 / 这本书 ingest 得怎么样" | 一份报告 `reports/postmortem/<书>-<日期>.md`（只出建议，**不改任何东西**） | 该报告文件 | 源没发布→先收尾 ingest |
| `pipeline-doctor` | **流水线卡住了找它**：只用安全命令修，绝不手改数据库 | "流水线卡住了 / 锁释放不掉 / 不让我重跑某一步" | 视诊断结果而定（都走已有安全命令） | 终端诊断结果 | 无固定配方时会如实告诉你、不瞎猜 |

### 5.3 底层 CLI 操作（高级排障 / 手动重跑）

> 所有 skill 背后都是 `python scripts/pipeline.py <command>`。下面按生命周期分组，标注**必填/可选参数**。
> 共 **46 个**子命令（完整实现映射见开发文档 §3；含 `vault-lint` 全库渲染安全健康门禁、`lint --source kb-save --session <run_id>` 会话发布路径与 `retract-source` 证据先行撤库）。

**状态与维护：**

| 命令 | 作用 | 必填参数 | 可选参数 | 示例 |
|------|------|----------|----------|------|
| `status` | 看每源阶段/状态 + 锁（`[STALE]` 标崩溃锁） | — | — | `python scripts/pipeline.py status` |
| `next` | 看每源下一步动作 | — | — | `python scripts/pipeline.py next` |
| `init-vault` | 建 `wiki/` 脚手架（幂等） | — | — | `python scripts/pipeline.py init-vault` |
| `unlock` | 回收 stale vault 锁（活锁拒绝） | — | `--ttl 1800` | `python scripts/pipeline.py unlock` |
| `fail` | 把崩溃残留的 running 阶段标 failed | `--source --stage --error` | — | `... fail --source X --stage converted --error "原因"` |
| `rebuild-registry` | 从概念页重建 `_registry.yaml`（aliases.md 已退休，残留自动清理） | — | — | `... rebuild-registry` |
| `rebuild-graph` | 重建知识图谱 v2.0（graph-data + 力导向 HTML，fail-hard） | — | — | `... rebuild-graph` |
| `graph-lint` | 校验知识图谱产物（errors→exit 2） | — | — | `... graph-lint` |
| `rebuild-quiz` | 从 published 页的 `[!question]` 重建自测题库索引 `quiz-index.generated.md`（题干+回链、不含答案；收尾 lint 自动重建） | — | — | `... rebuild-quiz` |
| `rebuild-propositions` | 从 published 页的具名命题（`**命题（名）**：…`）重建命题总表 `propositions.generated.md`（全库结论清单+回链；收尾 lint 自动重建） | — | — | `... rebuild-propositions` |
| `apply-obsidian-style` | 落地学习库 CSS 观感片段（纯配置，幂等） | — | — | `... apply-obsidian-style` |

**预处理（零 LLM，顺序固定，幂等跳过）：**

| 命令 | 作用 | 必填 | 可选 |
|------|------|------|------|
| `add-source` | 注册来源 | `--source --domain --path --fmt {pdf,md,docx,pptx}` | — |
| `profile` | 逐页 profile + needs_vision | `--source` | — |
| `source-convert` | 转 Markdown + 块 + 难页图 + 章节 | `--source` | `--backend {auto,pymupdf,mineru}` `--mineru-policy {conservative,aggressive}` `--force` |
| `source-audit` | PDF 双审 → reconciliation/evidence/queue | `--source` | `--strict` |
| `arbitration-status` | 看仲裁队列状态 | `--source` | — |
| `arbitration-apply` | 物化仲裁裁决（须在 windows 前） | `--source` | — |
| `arbitration-resolve` | 改判 needs_human 页 | `--source --page --decision {render,ignore} --reason` | — |
| `windows` | 生成 processing windows | `--source` | `--dev-bypass` |
| `workorder` | 生成 ingest 事务契约 | `--source` | — |
| `preflight-eval` | L4 确定性验收（**13 项**结构检查） | `--source` | `--strict` `--json <path>` |

**ingest 会话支撑（通常 skill 内部调用）：**

| 命令 | 作用 | 必填 | 可选 |
|------|------|------|------|
| `ingest-start` / `ingest-done` | 开工取锁 / 收工释放锁 | `--source` | — |
| `show-window` | 打印某窗源文本（含难页资产头） | `--source --window` | `--plain` `--verbose` |
| `window-start` / `window-done` / `window-fail` | 窗口记账 | `--source --window`（done 另 `--writes/--proposals`；fail 另 `--error`；start 另 `--hash`） | — |
| `resolve-concept` | 概念归一唯一入口（`--mention` 用中文规范名；英文/缩写放 `--alias`，`canonical_id` 才有稳定 ASCII 去重键） | `--mention --domain` | `--alias`（可重复）`--ref-source --ref-sections` |
| `check-write` | 写前守卫；既有页 ALLOW 时自动保存首份写前基线（必须先检查、后编辑） | `--source --path` | — |
| `snapshot-page` | 兼容命令：幂等确认首份基线，不能为已经发生的编辑补票 | `--source --path` | — |

**收尾、提升、查询、增量、自进化：**

| 命令 | 作用 | 必填 | 可选 |
|------|------|------|------|
| `lint` | 收尾门禁（两段事务隔离）：vault preflight 复检全库 published 渲染旧伤——发现即阻断但**不回滚当前批**；当前批过则 promote，**当前批违规**才回滚+Review-Queue。kb-save 会话发布必须带 `--session` | `--source`（kb-save 模式为 `--source kb-save`） | `--session <run_id>`（kb-save 必填） |
| `vault-lint` | 全库渲染安全健康门禁（published∪proposed，只读可 CI；违规非零退出） | — | — |
| `reopen` | 重开已收尾来源做增量补充 | `--source` | — |
| `sync-assets` | 把难页 PNG 同步进 `wiki/assets/<src>/` | `--source` | — |
| `promotion-candidates` | 检测跨域提升候选 | — | `--propose` |
| `promote-concept` | 机械提升一个概念为 shared | `--id concept.<domain>.<slug>` | — |
| `check-session` | query-session 目录契约检查 | `--id <run_id>` | `--saved` |
| `skill-mine` / `skill-gate` / `skill-stage` / `skill-adopt` | skill 自进化四步 | gate/stage/adopt 需 `--candidate` | `--base HEAD` |
| `ingest-stats` | 只读"体检单"：窗口/返工、窗口账本估算 `pages_estimate`，以及按 vault `source_refs` 重建的精确交付清单 `page_inventory`（报告总页数只认后者；不含 token/费用） | `--source` | `--json` |
| `proposals-resolve` | **给已修复的错误销账**（不然 `skill-mine` 的 backlog 会越攒越脏）；**默认只列清单不改库**，看清楚了再加 `--apply` | `--id <行号>` 或 `--signature <类型>` | `--source`（配合 `--signature` 限定某源）`--all-matching`（批量落库必须加）`--apply` |
| `reset-source` | **状态机"倒带键"**：某一步卡死重跑不了时，安全回退到更早的阶段。**默认只打印计划不改库**，确认后加 `--apply` | `--source --to {registered,profiled,converted,windowed,workorder_ready}` | `--apply` |
| `retract-source` | **证据先行撤库**：把一本已入库的书连页带账本安全卸下。**默认只打印计划不改库**——加 `--apply` 才会先导出证据包（页字节 + SHA256 manifest + 全部账本行）并核验，再删该源独占页、清账本、重置状态、重建派生层；共享页与人工页只报告不删 | `--source` | `--to {workorder_ready,registered}` `--apply` |
| `staging-clean` | **清理一本书处理时留下的临时文件**（可能几百 MB）。**默认只列清单不删**；`--apply` 前会自动检查"这本书是否已发布""图片是否已同步进 vault"，两条不满足直接拒绝执行 | `--source` | `--apply` |

### 5.4 主流程之外、你仍会接触的场景

> §4 是**首次入库**的主路径；下面这些是入库后**日常会用到、但不在那条线上**的功能，同样全程走对话。

| 场景 / 何时用 | 对话怎么说 | 产出 / 查看 |
|--------------|-----------|-------------|
| **查知识库**（读到一半想查某概念） | "知识库里关于纳什均衡怎么说" | 只读回答 + 一个 query-session（**不写库**） |
| **把查询结论沉淀回库**（承接上一步） | 查完接着说"把刚才那个纳什均衡 vs 帕累托最优的对比存进 wiki" | `comparison`/`synthesis` proposed 页 → lint 后入库。**有准入门槛**：一次性事实、原样复述会被门拦下（见 `docs/skill-runtime/save-back-policy.md`） |
| **处理复核队列**（lint 失败 / 有待人工决策项） | "处理一下复核队列 / 看看待复核的项" | 逐条分析 lint 失败项、跨域提升候选、被覆盖保护拒绝的改动；**最终采纳与否你定夺** |
| **语义体检**（想查质量而非结构） | "给知识库做个语义体检 / 查有没有跨页矛盾" | 检查对比页维度是否完整、跨页结论是否自相矛盾、近期保存是否有增量价值 → 只出 Review-Queue proposal，**不改页** |
| **QA / 覆盖率审计** | "给知识库做次 QA / 审计覆盖率 / 抽查证据" | 覆盖率报告 + 概念污染检查 → `pipeline-workspace/reports/` + Review-Queue proposal |
| **拆书阅读笔记**（对已发布来源） | "给博弈论这本书做拆书阅读笔记" | 只写 `pipeline-workspace/reports/source-xray/`，**不动 vault** |
| **给已入库的书增量补充** | "继续补充博弈论这本书" | `reopen` 重开 → 逐窗补写 → lint；**旧发布内容不回滚** |
| **多本书之后：跨域概念提升** | "检测下有没有该提升成跨域的概念"，确认后"把 纳什均衡 提升为 shared" | 私有概念移入 `concepts/`、全库链接自动重写。**注**：单本书触发不了——需至少两个领域出现同一概念 |

### 5.5 每本书发布后的"收尾三件事"（2026-07-09 新增，小白向说明）

> 这三件事**不是必须做**，但强烈建议每发布完一本书都走一遍——就像打扫完房间随手扔一下垃圾桶。
> 三件事互不依赖，想做哪个就说哪个。

**① 复盘这次入库做得怎么样**

对 agent 说"复盘这次入库"或"这本书 ingest 得怎么样"，会得到一份人话报告，告诉你：这本书写了多少页、
中间返工了几次、踩了哪些坑、这些坑值不值得让 skill 自己学一学（见下面第③件事）。**只出报告，不改任何东西**，
看完自己判断要不要采纳建议。

**② 把已经修好的错误"销账"**

背景：每次发布时如果有格式错误（比如链接指向一个不存在的页面），系统会记一笔"待办账"。这笔账**默认永远
挂着**——就算你后来把错误改好了，账本上也不会自动划掉。时间长了账本会越堆越乱，分不清哪些是真问题、
哪些早修好了。

复盘报告里会列出"这次哪些错误已经确认修好了，可以销账"，附上现成的命令。**执行前一定先不带
`--apply` 跑一遍**——它只会打印出要销的账目清单，不会真的改任何东西，你看清楚清单里的内容确实是已修复
的旧问题，再补上 `--apply` 真正执行。

**③ 清理这本书处理时留下的临时文件**

一本书从 PDF 到发布，中间会在电脑上留下不少"草稿纸"（有的书能到 200 多 MB），发布完这些草稿纸大多用不上了。
对 agent 说"清理一下这本书的临时文件"（或直接跑 `staging-clean --source <书> --apply`）即可。它自动分三类处理：

- **有用的证据、以后续跑要用的**（比如双审比对记录、正文原文）—— 永远保留，不会碰。
- **纯草稿、可以随时重新生成的**（比如 MinerU 的原始解析产物）—— 这部分才会被删。
- **看不懂是什么的文件** —— 一律保留、单独列出来给你看，不会因为"不认识"就乱删。

而且删之前会自动检查"这本书是不是真的已经发布了""里面的图片是不是已经复制进最终的知识库了"，
两条有一条不满足就直接拒绝执行——不用担心手滑删到还在用的东西。

**④（进阶，非必须）把反复踩的坑教给 AI**

如果①的复盘报告里提到"某个错误反复出现好几次"，可以说"把这次踩的坑沉淀进 skill"，让 AI 自己去改写
它自己的工作说明书（也就是 skill 文件），下次入库时就会自动避开这个坑。这一步涉及改 AI 的"工作手册"，
所以有额外的安全网：AI 改完会先自己跑一遍全部测试确认没搞坏别的东西，然后把改动**注册成一个待批准的
提案**，真正生效（`skill-adopt`）还是要你点头才行。

**其他格式（DOCX / PPTX / 扫描 PDF）入库：** 放法、说法和 PDF **完全一样**——放进 `books/<name>/input/`，说"把这个 X 加进知识库，领域 Y"。唯一区别在后端：`docx`/`pptx`/扫描件由 **MinerU 主解析**（需先装 MinerU，见 §3），只有 born-digital PDF 才走轻量 PyMuPDF + MinerU 双审。对话流程无任何差别；未装 MinerU 时这些格式会 fail-closed 并提示你去装。

### 5.6 「内容路由」和「写作装置」到底是什么（2026-07-08 新增，小白向说明）

第 3 步入库流程里提过一句"按章判断内容路由"、"按装置预算克制使用写作装置"——这两个词听着抽象，
但你不需要做任何事，**它们是写作 LLM 自己内部的判断依据，完全不需要你操心**。这里展开讲讲它们
到底在干什么，好让你看得懂生成的笔记为什么长这样。

**内容路由：不同章节，用不同的"写法"**

一本书不是每一章都该用同一种笔记写法——讲定理推导的章节和讲案例故事的章节，理想的笔记形态天然不同。
所以 ingest 会先通读全书目录，把每一章大致归到下面 5 类里的一类，再决定这一章倾向用哪种写法（**这一步
纯粹是 LLM 的判断，不查任何代码，机器也不会检查判断得对不对**）：

| 章节类型 | 长什么样的章节 | 笔记会怎么写 |
|---|---|---|
| 理论型 | 定义、定理、模型、推导为主 | 多写"概念页"和"对比页"，长推导收进可折叠的框里，重要结论会被起个短名字（"命题"） |
| 方法型 | 讲步骤、流程、怎么操作 | 写成"主题页"，但重点讲**为什么这么做**（背后的判断逻辑），不是照抄操作步骤 |
| 案例型 | 举例子、讲故事、复盘案例 | 摘一段场景描述，把里面对应到模型的关键词高亮标出，点开能跳回对应的概念页 |
| 参考型 | 像字典/规范手册一样的公式表、术语表 | 写成紧凑的速查卡片，表格为主、文字为辅，方便你快速查找 |
| 观点型 | 作者自己的主张、评论、论证 | 明确标出"这是作者自己的看法"还是"这是学界公认的结论"，避免你误把个人观点当定论 |

纯粹是目录、日程表、打鸡血式过渡内容不进这套分类，直接按你在 `purpose.md` 里写的偏好从简处理。

**关键一点**：这套分类只是**建议**，不是规定。如果某一章实际内容不符合它被归到的类型，LLM 会直接按
实际内容写，不会为了凑"应该长什么样"硬套模板——这种"跑偏"会被记一笔（`[routing-deviation]`），
留作以后判断"要不要修一下这套分类规则本身"的证据，不算错误。

**写作装置：让笔记更好读的几种"招式"，但不是每页都用**

除了照实转写内容，ingest 还准备了几种可选的"写作招式"，用来让笔记更好读、更好记——但**硬性规定**是
"默认什么招式都不用，一页最多同时用一种"（推导折叠是个例外，鼓励多用，见下）。防止的是"每页都花里胡哨、
反而看得累"。五种招式：

- **推导折叠**（唯一鼓励多用的招式）：一步步的数学推导过程收进一个可以点开展开的框里，**框外先给出结论**——
  你不想细看证明时，扫一眼结论就走，想核对严谨性时再点开。
- **案例解剖**：摘一小段现实场景的描述，把对应到某个模型/概念的关键词用高亮标出，点击能跳回那个概念的
  笔记页——训练你"在真实场景里一眼认出这是哪个模型"。
- **定位段**：只在**容易让人迷路的深层概念**页面开头，用一句斜体交代"这个概念是从哪个更基础的概念来的、
  要解决什么问题、后面会通向哪"，帮你不至于打开一堆概念页却不知道自己在知识网络的哪个位置。基础概念不需要这个。
- **具名命题**：把库里真正"扛得住"的重要结论起一个 2-8 字的短名字（比如"先发优势"），格式固定为
  `**命题（先发优势）**：一句话结论`。以后别的页要引用这个结论，直接说"由命题（先发优势）……"，不用重复
  论证一遍。全库这些命题会自动汇总进 `propositions.generated.md`（见 §7 的表）。
- **阅读兴趣设计**：包括"先猜再看"（抛一个问题让你先预测答案，再揭晓）、"找错题"（一段故意留了一处错误的
  推导，让你先挑错）、真实误区提醒（只针对真正容易搞混的一对概念，不是随手加的提示）、有强因果关系时的
  "下一步"悬念链接。这些都是"锦上添花"，缺了不影响笔记的正确性。

**为什么要跟你说这些**：读笔记时如果看到有的页很朴素（没有任何"招式"）、有的页用了折叠框或高亮，这不是
写得不用心/用心程度不一样，而是**协议本身就要求"宁缺毋滥"**——大多数页什么装置都不用才是正常状态，只有
内容确实需要时才会出现某种装置，全部由当次生成时的模型判断力决定。

---

## 6. 处理真实文档（Windows PowerShell + 含空格路径）

**含空格 / 中文路径**：用**双引号**包住整个路径。

```powershell
# 设 UTF-8（中文源必做）
$env:PYTHONUTF8=1

# 放入带空格路径的文件
New-Item -ItemType Directory -Force -Path "books\micro econ\input" | Out-Null
Copy-Item "C:\Users\Lenovo\Downloads\微观 经济学 第3版.pdf" "books\micro econ\input\micro.pdf"

# 手动跑预处理（如不用对话，演示含空格路径的 --path 写法）
python scripts/pipeline.py add-source --source micro-econ --domain economics --fmt pdf `
    --path "books\micro econ\input\micro.pdf"
python scripts/pipeline.py profile --source micro-econ
python scripts/pipeline.py source-convert --source micro-econ
```

**建议**：实际入库**走对话**（"把 books\micro econ\input\micro.pdf 加进知识库，领域 economics"），
让 ingest skill 自动编排全部步骤；上面的手敲命令仅用于排障或脚本化。

**PowerShell 续行**用反引号 `` ` ``（如上例），不要用 `\`。

---

## 7. 理解生成输出

| 路径 | 含义 | 是否手动编辑 | 如何连到下一步 |
|------|------|--------------|----------------|
| `wiki/overview.md` | vault 入口"活综合页"（概念地图 + 学习路线） | **否**（`managed_by: pipeline`，ingest 维护） | Obsidian 从这里读起 |
| `wiki/domains/<d>/concepts/*.md` | 领域私有概念页 | 否（除非你接管成 human 页） | 关系图/overview 链入 |
| `wiki/domains/<d>/lessons/*.md` | 讲义（降级可选层：主题命名，非章节复述） | 否 | 按主题整理的学习笔记 |
| `wiki/topics/*.md` | 主题综合页（概念之上的导航分类层） | 否 | overview → topic → concept |
| `wiki/comparisons/*.md` | 横向对比页 | 否 | 主题内对比差异维度 |
| `wiki/synthesis/*.md` | 深度综合页 | 否 | — |
| `wiki/sources/<src>.md` | 每来源一页摘要（"来过哪些书"台账） | 否 | — |
| `wiki/assets/<src>/p*.png` | 难页（公式/矢量图/表/标题）整页截图 | 否（确定性渲染） | 仅供 LLM 阅读，**published 正文禁嵌源图**（D-1） |
| `wiki/concepts/_registry.yaml` | 概念派生索引 | **否**（手改会被覆盖） | 收尾 CLI 重建 |
| `wiki/index.generated.md` | 内容目录（只收 published） | **否**（同上） | — |
| `wiki/graph-data.generated.json` | 知识图谱数据（节点/边/Louvain 社区） | 否 | `rebuild-graph` 重建 |
| `wiki/knowledge-graph.generated.html` | 力导向交互知识图谱（浏览器打开） | 否 | 点击节点跳 `obsidian://` 打开对应 Obsidian 笔记 |
| `wiki/quiz-index.generated.md` | 全库自测题索引（按领域分组，只列题干+回链，不泄露答案） | **否**（收尾重建，手改会被覆盖） | 复习入口：抽几题自答 → 点回链到原页展开折叠答案核对 |
| `wiki/propositions.generated.md` | 命题总表：全库承重结论清单（`**命题（名）**：…` 自动汇总，结论句+回链） | **否**（收尾重建，手改会被覆盖） | "这个库断言了哪些事"的资产清单；做研究时可检索引用 |
| `wiki/Review-Queue/*.md` | 未过门禁 / 待人工决策项 | 你处置（用 kb-review） | 修复后重跑 lint |
| `wiki/_meta/purpose.md` | **你手写**的学习目标 | **是**（唯一你维护的输入文件） | ingest 读取 |
| `wiki/log.md` | 操作日志（append-only） | 否 | 回溯 |
| `pipeline-workspace/staging/<src>/` | 预处理中间产物（source.md/blocks/windows/workorder/assets...） | 否 | ingest 读取；可清理后重跑预处理 |
| `pipeline-workspace/state/study-kb.sqlite` | 状态机数据库 | **否**（机器状态真值） | status/next 读取 |

> **重要提醒**：`published` ≠ 已核对。它只代表通过了**结构门禁**（断链/孤儿/重复 canonical_id/源图禁嵌正文/
> frontmatter 完整/占位符残留/表格内 wikilink 竖线转义（`table-wikilink-pipe`）/overview 非种子占位
> （`overview-seed`）/来源台账页存在（`source-page-missing`）等），**不代表内容已被核实**。公式由 LLM
> 原生重建 KaTeX，复杂公式可能有误——应对照 staging 里的难页源图核实。
> **源图只作 LLM 阅读证据，published 正文绝不嵌入**（`source-image-embed` 会阻断发布）。
> 综合层（overview/topic/comparison/synthesis）是 LLM 的归纳，作为线索而非定论。

> **写作质量的机制边界（以自测题为例）**：项目对"写得好不好"分三层管控，别把三层混为一谈——
> ① **机器硬门禁**：callout 类型必须在白名单内（`note tip info important warning question example
>    abstract summary quote success todo`，含嵌套层如折叠答案），未知类型直接阻断发布（`scripts/wiki_gate.py`
>    `callout-unknown`）；但**是否使用 callout 本身不强制**。
> ② **文档要求 + 软性机器提醒（2026-07-08 起，非阻断）**：`ingest/references/write-pages.md` 规定"自测必须
>    配答案 / 提示 / 回链，禁止抛出无解问题"——写作时靠 LLM 自觉遵守；`lint` 现在会额外扫一遍，对"有题无解"
>    的自测题打 `[warn]`（**不阻断发布**，只是提醒，避免逼模型为了过检硬凑格式）。
> ③ **纯模型能力发挥**：折叠语法用得漂不漂亮、问题设计得刁不刁钻、跟正文呼应得巧不巧，项目文档从未规定到
>    这个颗粒度，完全取决于当次跑 ingest 用的模型能力——同一套协议下换模型，效果会有明显差异。
>
> **同样的三层框架也适用于"内容路由"（2026-07-08 新增）**：路由表（按章判断理论/方法/案例/参考/观点、
> 推荐写法取向）只是文档层的建议，**没有对应机器校验**——模型可以偏离路由表按实际内容写，机器不检查
> "写的是否符合路由预判"。这不是漏洞：路由本来就该服从内容本身，偏离记录（`[routing-deviation]`）反而
> 是有意保留的证据，用来判断以后要不要修订路由表本身（这条修订走 skill-evolve，不是随手改）。

---

## 8. 故障排查

> 仅列**仓库源码/测试/README 有依据**的问题。

| 现象 | 根因 | 修复 | 依据 |
|------|------|------|------|
| `ModuleNotFoundError: fitz`（或 yaml） | 没装依赖 / 用错解释器 | `conda activate study-kb` 后 `pip install -r requirements.txt`；自检 `python -c "import fitz, yaml"` | `requirements.txt`、`pymupdf_backend.convert` 的 `BackendUnavailable` |
| 跑命令报"no state db yet" 或路径不对 | 不在仓库根目录跑 | `cd` 到仓库根再跑；或设 `$env:STUDY_KB_ROOT` 指向库根 | `pipeline._workspace_root` |
| 中文乱码 / 中文路径报错 | 没设 UTF-8 | 先 `$env:PYTHONUTF8=1` | CLAUDE.md §8、README §测试 |
| `scanned_source / requires_ocr ... 整本扫描件不适合 PyMuPDF` | 扫描件走了轻量路径 | 装 MinerU（`python scripts/install_mineru.py`）走 `--backend auto`；或确要 PyMuPDF 渲染加 `--force`（慎用） | `cmd_source_convert`、`is_scanned_source` |
| `dual-audit fail-closed: ...` / strict 验收不过 | PDF 严格验收需 MinerU，但未装/失败 | 装 MinerU；或去掉 `--strict`（产物标 degraded，不算生产验收） | `source_audit.DualAuditUnavailable`、`check_dual_audit` |
| `MinerU 未安装：--backend mineru 需要 MinerU` | 强制 mineru 但未装 | `python scripts/install_mineru.py`；或改 `--backend pymupdf`/`auto` 轻量路径 | `mineru_backend.convert` 的 `BackendUnavailable` |
| `windows` 报"PDF 源未完成 source-audit（缺 ...）" | PDF 未先双审就构窗 | 先跑 `source-audit`（+仲裁+`arbitration-apply`）；dev 可加 `--dev-bypass`（产物降级） | `cmd_windows` 闸门 B |
| `windows` 报"未闭环双审分歧，拒绝构窗" | 有候选页未仲裁/未物化 | 读 `arbitration/queue.json` 仲裁→写 `decisions.json`→`arbitration-apply`；或 `--dev-bypass` | `cmd_windows` 闸门 A、`arbitration.windows_blockers` |
| `vault lock held by <other> since ...` | 另一 ingest 持锁（或崩溃残留） | 等待；若 `status` 显示 `[STALE]` 则 `python scripts/pipeline.py unlock`（默认 heartbeat 超 1800s 才允许） | `locks`、`cmd_unlock` |
| `stale registry: disk _registry.yaml != work order hash` | registry 派生文件与 workorder 记录不一致 | 重跑 `workorder` 再 `ingest-start` | `ingest_guards.registry_fresh`、`cmd_ingest_start` |
| `check-write` 输出 `DENY ...` | 路径越界 / 目标是 human 页 / hash 不符 | 不在 write_scope→走对应目录；human 页→改走 Review-Queue proposal（**不会静默覆盖**） | `ingest_guards.can_overwrite` |
| `lint failed: N violations -> Review-Queue/...` | 收尾门禁未过（断链/孤儿/重复 canonical_id/**正文嵌了源图**/占位符残留/表内裸竖线/frontmatter 缺项/综合层或 topic 缺失/概念未被 topic 收编等；**注：正文小节标题已不再是门禁**） | 看 `wiki/Review-Queue/<src>-lint-<date>.md` 逐条修，再说"重跑 lint" | `cmd_lint`、`wiki_gate.lint_pages` |
| `InvalidTransition: ... not allowed` | 阶段顺序不对 / 想跳步 | 按 `next` 提示的下一步走；崩溃残留 running 用 `fail` 标记后重跑该阶段 | `state_store._allowed_next` |
| `cannot reopen ... at <stage>` | 对未收尾来源用了 reopen | 只有已收尾（lint 终态 / ingested-proposed）才能 reopen；进行中请续跑，预处理中请直接续预处理 | `state_store.reopen_source` |
| 空输出 / 没生成页 | 只跑了预处理没跑 ingest；或 source-preflight（不写库） | 预处理只产 staging；要写库须走 `ingest`（付费 LLM 操作） | README §使用须知 |
| 续跑脚本静默空转 | 调度任务用了未装依赖的解释器 | 注册时 `-Python "<装齐依赖的 python 绝对路径>"`，或设 `STUDY_KB_PYTHON` | `resume-ingest.ps1`、README §中断续跑前提条件 4 |
| Obsidian 关系图过密 | 汇总页对每个概念都建 wikilink 形成中心 hub | 写页规范已要求只连强关系；可在 Obsidian 调图谱设置 | README §在 Obsidian 中阅读 |
| 概念文件名/`canonical_id` 变成纯字母数字（如 `ai.md`、`20.md`），不是中文 | **已于 2026-07-07 修复**：`slugify()` 现在只要名字含任何非 ASCII 字符就整名保留（去空白），不再抓局部 ASCII 残片。仅旧版本会出现此症状 | 旧版本 workaround：删掉误生成的文件，换纯中文 `--mention` 重新 `resolve-concept`，英文/数字放 `--alias` | `scripts/concept_store.py::slugify` + `tests/test_concept_store.py` 回归用例 |

> **提示**：无人值守续跑脚本 `scripts/resume-ingest.ps1` 支持 `-MaxWindows`（默认 4），限定单次触发最多处理
> 几个 window，处理完干净退出、剩余留给下次触发——避免单次长会话因模型不可用（断连 / 限流 / 额度冻结）整体失败。
> 注册调度任务时可按需调整，例如 `-MaxWindows 6`。

---

## 9. 快速命令索引（按正常使用顺序）

```powershell
# ── 0. 每个新会话先设 UTF-8 ──
$env:PYTHONUTF8=1

# ── 1. 一次性安装 ──
conda create -y -n study-kb python=3.12
conda activate study-kb
python -m pip install -r requirements.txt
python -c "import fitz, yaml; print('PyMuPDF', fitz.VersionBind, '| PyYAML', yaml.__version__)"
python scripts/install_mineru.py            # 可选：PDF 严格验收 / 扫描件 / DOCX / PPTX

# ── 2. 建空库 + 填学习目标 ──
python scripts/pipeline.py init-vault
#   然后编辑 wiki\_meta\purpose.md（唯一需你手写的文件）

# ── 3. 放入来源（含空格路径用双引号） ──
New-Item -ItemType Directory -Force -Path "books\game-theory\input" | Out-Null
Copy-Item "C:\downloads\博弈论.pdf" "books\game-theory\input\博弈论.pdf"

# ── 4. 入库：推荐走对话（在 Claude Code / Codex 里说） ──
#   "把 books/game-theory/input/博弈论.pdf 加进知识库，领域 game-theory"
#   想零成本先验预处理：说 "先 source-preflight 这个 PDF"

# ── 5. 随时查看进度 / 排障（底层 CLI） ──
python scripts/pipeline.py status
python scripts/pipeline.py next
python scripts/pipeline.py show-window --source game-theory --window w0001
python scripts/pipeline.py preflight-eval --source game-theory --strict
python scripts/pipeline.py unlock                       # 仅当 status 显示 [STALE]

# ── 6. 收尾失败后修复重跑 ──
#   看 wiki\Review-Queue\<src>-lint-<date>.md 逐条修，再：
python scripts/pipeline.py lint --source game-theory

# ── 7. 给已发布来源增量补充 ──
python scripts/pipeline.py reopen --source game-theory
#   再走 ingest-start → 逐窗写 → ingest-done → lint（或直接对话"继续补充这本书"）

# ── 8. 维护派生层 ──
python scripts/pipeline.py rebuild-registry             # 重建 _registry.yaml（清旧 aliases.md）
python scripts/pipeline.py rebuild-graph                # 重建知识图谱 v2.0（graph-data + 力导向 HTML）
python scripts/pipeline.py graph-lint                   # 校验知识图谱产物
python scripts/pipeline.py rebuild-quiz                 # 重建自测题库索引 quiz-index.generated.md
python scripts/pipeline.py rebuild-propositions         # 重建命题总表 propositions.generated.md
python scripts/pipeline.py apply-obsidian-style         # 可选：学习库 CSS 观感

# ── 9. 每本书发布后的收尾三件事（对话说即可，命令仅供参考） ──
python scripts/pipeline.py ingest-stats --source game-theory            # ①看这本书的体检单
python scripts/pipeline.py proposals-resolve --signature broken-link `
    --source game-theory --all-matching                                # ②先 dry-run 看清单
python scripts/pipeline.py proposals-resolve --signature broken-link `
    --source game-theory --all-matching --apply                        # 确认无误后销账
python scripts/pipeline.py staging-clean --source game-theory           # ③先看清理清单
python scripts/pipeline.py staging-clean --source game-theory --apply   # 确认后真正清理

# ── 10. 测试（开发者） ──
$bt="$PWD\tmp\pt-$(Get-Random)"
python -m pytest tests -q -m fast --basetemp=$bt                          # 日常层（十几秒；计数以 pytest --collect-only 为准）
python -m pytest tests -q --basetemp=$bt                                  # 全量门禁（约 3 分钟）

# ── 11. 在 Obsidian 阅读 ──
#   Obsidian → Open folder as vault → 选项目里的 wiki/ 目录 → 从 overview.md 读起
```

---

> 更深的实现细节（模块职责、数据契约、命令实现映射、测试分层）见配套的[**开发实现说明**](developer-guide.md)。
