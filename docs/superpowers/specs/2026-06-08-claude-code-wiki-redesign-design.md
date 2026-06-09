# 设计 spec：Claude-Code 维护的多领域学习型 Obsidian 知识库

> 状态：设计待复核（brainstorming 产出）。日期 2026-06-08。
> 取代初版分析思路（假设单一来源、过度依赖 OCR/evidence 硬门禁；该初稿已随本 spec 定稿删除）。
> 实现将按本 spec 末尾的分期拆成多个 implementation plan，不在单个计划内一次做完。
> 2026-06-09 修订：舍弃 LangGraph，编排改为确定性 Python CLI + 单一业务 SQLite 状态跟踪（见 §3.2、§12）。
> 2026-06-09 修订：新增 Skill 化 Claude Code 命令层与 query/save-back 闭环（见 §3.4、§7.1）；不改 P0 状态底座计划。
> 2026-06-09 修订：命令层明确为显式 slash command（副作用命令不自动触发；若用 SKILL.md 则 `disable-model-invocation: true`）；`/kb-query-save` 拆为 `/kb-query` + `/kb-save <session>`；query-session 暂只落文件系统、不进 artifacts；新增 source 生命周期 stub（§3.4 / §7.1 / §9.1 / §11 / §15）。

---

## 1. 目标

把 PDF→Obsidian 流水线从"按章节忠实转写"改造为 **llm-wiki 式、可复利的多领域学习知识库**：

- 多来源、长期累积、跨领域；相同概念合并更新，新内容新增页面，库越长越互联。
- 读者打开 vault 第一眼看到的是"怎么学 / 核心概念 / 模型怎么比较"，不是章节清单。
- 工程化：**确定性 Python CLI**（普通脚本，非 LangGraph）负责可重复、可观测、低成本的预处理/后置门禁/索引/状态跟踪；Claude Code（交互式、人触发命令）负责高价值的 wiki 写作与跨页合并。
- Skill 化执行：Claude Code 的写作/审核不是一次性提示词，而是由**显式 slash command + scoped prompt 文档 + 模板 + 确定性脚本**驱动；复杂长任务的关键中间结果必须持久化、可恢复、可审计。

## 2. 决策记录（本次锁定）

**用户决策**
1. **单一 vault、按领域分区、共享 `concepts/` 有准入门槛**。概念默认归属领域，仅当确实跨领域复用才提升为 shared，避免 `utility`/`model`/`strategy` 跨域同名污染。
2. **公式页分层处理**：默认本地后端（pymupdf4llm/marker/局部 OCR）跑量，只有难页（marker/OCR 低置信、公式断裂、关键图表）才渲染整页 PNG 交 Claude 读图；笔记旁保留源页截图链接供肉眼核对。
3. **允许引入 marker**，原则"本地优先 + 可插拔外部后端"；Claude 读图作难页兜底。
4. **不拆分（精确定义见 §3.1）**：不让第三方 LLM 做语义 unit 规划、不做人工 unit 审批、不让每个 unit 独立生成孤立笔记。拿到 PDF 只做确定性预处理，再整源 ingest 更新进 wiki。长书仍按确定性 processing windows 读取（TOC/标题层级/页码范围/token 上限滑窗），那是机械预处理，不是知识结构规划。
5. **输出组织**：概念/主题为主（按理念组织），lessons 跟随源文档 TOC 为辅（线性阅读层），二者皆由 `/ingest` 在会话内产出，不彻底取消 lessons。
6. **命令层 + query/save-back**：副作用命令一律**用户显式调用**——主接口是 `.claude/commands/*` 显式 slash command（不参与模型自动触发、无命中率稀释）；若改用 `.claude/skills/<name>/SKILL.md`，写库相关 Skill 必须加 `disable-model-invocation: true`。不放大量 always-on 指令进 `CLAUDE.md`。`/ingest` 从 source 编译 wiki（写 proposed）；`/kb-query` 只读并持久化 query-session；`/kb-save <session>` 才把命中准入门槛的学习资产写 `status: proposed`，继续走两阶段发布和 Review-Queue。

**编排与 LLM 边界**：用户决定**舍弃 LangGraph**，改为**确定性 Python CLI 编排 + 单一业务 SQLite 状态跟踪**（理由见 §3.2）。CLI 骨架 **100% 确定性、零 LLM**；唯一的 LLM 是交互式 Claude Code `/ingest`（人工触发，非无人值守自动化），彻底规避第三方 key 的自动化限制。

**接受的 Codex 复审五点**（落地见对应章节）：①canonical 概念数据模型（§6）②综合层升为一等产物（§7）③不取消证据门禁、只清理正文（§10）④`/ingest` 事务协议（§9）⑤可执行学习质量 lint（§11）。

## 3. 总架构与数据流

```text
Python CLI 预处理（确定性，零 LLM，不碰 Claude key）
  init → profile-pdf → source-convert(分层后端 → 干净 source.md + 抽图 + 难页渲染 PNG)
       → 生成 processing windows（确定性 TOC/页码/token 滑窗）
       → 生成 1 个 source 级 ingest work order（写入边界 + 旧页 hash + registry 快照 + 失败落 Review-Queue）
        ↓ 文件交接（staging/<source>/{workorder.yaml, source.md, windows.jsonl, page-images/, registry 快照}）
Claude Code Skill 命令层（唯一 LLM；一次交互式会话，人手动触发 /ingest <source>，合规非自动化）
  按确定性 processing windows 读整源（§3.1）
  → 概念/主题为主织进 wiki：concept upsert（查 registry+aliases 归一）/ topic / comparison / synthesis / overview
  → lessons 跟随源 TOC 顺带产出（线性层，干净正文、证据进脚注）
  → 维护 overview（综合）+ 追加 log；别名只改概念页 frontmatter（index/aliases/registry 由收尾派生重建），保留 source traceability（源页截图链接）
        ↓ 文件交接（domains/**、concepts/**、topics/**、staging review 提案）
Python CLI 收尾（确定性，零 LLM）
  health → 确定性 lint（L1/L2/L3/L5/L6 + 断链 + 重复 canonical_id + 公式邻接 + claim 有证据）
         → duplicate-concept/门禁不达标 → 阻断发布并转 Review-Queue proposal
  → 从概念页 frontmatter 重建 _registry.yaml + aliases.md + index.generated.md / coverage / dashboards
  注：overview/topic/synthesis 等综合内容由 Claude 维护，收尾不改写；语义 lint（L4/矛盾）见 §11
```

边界：Python CLI 做确定性预处理 + 后置门禁 + 索引 + 状态跟踪（不调 LLM、无状态图）；Claude Code Skill 命令层做唯一的 LLM 工作——读整源、织 wiki、跨页合并、概念归一、必要时把 query 结果保存回 wiki。（状态机、两阶段发布、window 级续跑、并发锁见 §3.3；Skill 命令层见 §3.4。）

## 3.1 "不拆分"的精确定义与 processing windows

不拆分 = 取消"知识结构规划"，**不是**运行时永远把全文一次塞进上下文：

- **取消**：第三方 LLM 语义 unit 规划、人工 unit 审批、每 unit 独立生成孤立笔记（即删除 `plan-units` / `validate-unit-plan` / `review-unit-plan` / 逐 unit 图）。
- **保留（机械、确定性、零 LLM）**：长源按 **processing windows** 读取——按 PDF TOC、标题层级、页码范围、token 上限滑窗切片，带相邻窗口 overlap + 滚动 digest（借鉴 nashsu）。短源单遍读完。
- 关键区别：processing window 是"为把长文喂进模型"的**读取单位**，输出里不可见；它不决定 wiki 页面结构。wiki 结构由内容（概念/主题）和源 TOC（lessons）决定，不由窗口决定。

## 3.2 为什么不用 LangGraph（编排选型）

舍弃 LangGraph，改用**确定性 Python CLI（扩展现有 `scripts/pipeline.py`）+ 单一业务 SQLite 状态跟踪**。理由：

- LangGraph 的独占价值是**编排有状态、带循环、带条件分支的 LLM 流程**（旧设计的 author→review→revise 循环 + checkpointer 断点续跑）。该循环已**整体搬进 Claude Code `/ingest`**，CLI 这侧只剩 ①预处理、③收尾 两段**确定性直线**，无循环、无 LLM 节点分支。
- 三段（①CLI 预处理 → ②人工 + Claude Code → ③CLI 收尾）是**各自独立、靠文件交接**的阶段，不是"一个会 interrupt/resume 的图"。**人工触发 ≠ LangGraph 的 interrupt 功能**。
- "多来源、多阶段、人工把关"的**进度协调**是真实需求，但属于**状态跟踪**而非 LLM 编排——用业务 SQLite 的状态表 + 一个 `pipeline status` / `pipeline next` CLI 即可（告诉你每个 source 当前阶段与下一步人工动作），比 LangGraph 轻一个数量级。
- 仅当将来在 CLI 侧重建**自动化多步 LLM 循环**，或编排出现大量分支/重试/带依赖并行时，才重新评估 LangGraph。当前都不是。

## 3.3 状态模型、两阶段发布、续跑与并发（Codex 第二轮 P1/P2）

把 §3.2 的"SQLite 状态跟踪"从口号落成契约。

### 阶段状态机（每个 source）

```text
registered → profiled → converted → windowed → workorder_ready
  → ingest_waiting → ingesting → ingested → lint
```

- `current_stage`：业务阶段（`registered` 初始；profile 成功后才 `profiled`）。
- `current_status`：`pending | running | done | failed | proposed | published`。**失败 = `current_status=failed`（停在该 stage，不新建 `<stage>_failed`）**；`ingested` 完成后 `status=proposed`；`lint` 通过 `status=published`（终态）。
- **重试边**：`failed` 可重跑同 stage；`lint` 失败可回 `ingest_waiting`（修复后重 ingest）。
- **原子阶段 API（唯一入口，同一事务更新 `source_stage_runs` + `sources`，调用方不得手拼两表）**：`start_stage(source,stage,input_hash)` / `complete_stage(source,stage,output_hash)` / `fail_stage(source,stage,error)`；幂等 `should_run_stage` 命中同 `stage+input_hash` 的 `done` 记录则跳过。

### 业务 SQLite 表（取代旧 unit/run 结构）

- `sources(source_id PK, domain, format, added_at, current_stage, current_status)` — `current_status ∈ {pending,running,done,failed,proposed,published}`；初始 stage 为 `registered`
- `source_stage_runs(id, source_id, stage, status, started_at, finished_at, input_hash, output_hash, error)` — 阶段级幂等/恢复依据；只由原子阶段 API 写
- `artifacts(id, source_id, kind, path, sha256, created_at)` — source.md/windows.jsonl/workorder.yaml/page-images 等产物 hash
- `work_orders(source_id PK, path, registry_hash, write_scope_json, created_at)`
- `source_locks(scope, holder, pid, started_at, heartbeat_at)` — 见并发
- `review_proposals(id, source_id, target_path, kind, diff_path, reason, created_at, status)` — 门禁未过的改动
- `ingest_progress(source_id, window_id, input_hash, started_at, finished_at, status, write_set_json, proposal_set_json, error)` — `/ingest` window 级机器状态

`pipeline status`/`pipeline next` 从这些表派生（current_stage、下一步人工动作、锁持有者、stale lock 提示）。**`log.md` 仅由状态派生为人读摘要，不承担事务/checkpoint 职责。**

### 两阶段发布（让"阻断发布"真正成立）

`/ingest` 不直接产出"已发布"内容：
- 新建/改动页一律带 frontmatter `status: proposed`；写集记入 `ingest_progress.write_set`。
- **就地 merge 既有 published 页前**先存 pre-ingest 快照到 `pipeline-workspace/snapshots/<source>/<run_id>/`（文件副本 + sha256 + manifest），失败按 manifest 回滚。**默认不用 git**（自动 commit 会污染用户仓库历史、且违反"未明确要求不提交"约束）；仅用户显式开启时才用 `git commit`/`git stash`。promote 成功后清旧 snapshot。
- 收尾门禁只校验 proposed 集合：**通过 → promote**（`proposed`→`published`，纳入 `index.generated.md`/Dataview）；**失败 → 不 promote**（新页保持 proposed、不进 index；就地 merge 回滚到 pre-ingest 版本），失败 diff 落 `review_proposals` + `Review-Queue/`。
- `index.generated.md`/overview/dashboards **只收录 `status: published`**，故未过门禁内容不会"上线"。

### 续跑边界（明确替代 checkpointer 的范围）

- **CLI 阶段**：阶段级幂等重跑——失败回到该 `stage` 入口，靠 `source_stage_runs.input_hash` 决定是否跳过。
- **`/ingest`**：window 级续跑——读 `ingest_progress`，跳过 `status=finished` 且 `input_hash` 未变的 window，只重做未完成/失败 window。
- **不恢复** Claude 会话内部推理状态——这是与 LangGraph 节点级恢复的明确差异，可接受。

### 并发（v1 保守）

- **同一 vault 同时只允许一个 active `/ingest`**（最简实现）；`source_locks` 记 holder/pid/started_at/heartbeat。后续可放宽到"同一 domain + shared 写锁"。
- `pipeline status` 显示锁持有者与启动时间；heartbeat 超时判 stale，`pipeline next` 给清理建议。
- 杜绝两个 `/ingest` 同时改 `overview.md`/`log.md`/shared concept/registry 派生源。

## 3.4 命令层与路由（不是架构真值）

Claude Code 侧采用**命令化的工程工作流**：把"如何生成/审核 wiki"的提示词、模板、协议说明和脚本入口拆成可维护文件，按命令最小加载。**架构真值仍是本 spec，机器真值仍是 SQLite + wiki frontmatter + 派生产物。**

### 显式命令 vs 自动触发 Skill（关键区分）

- **主接口 = 显式 slash command**（`.claude/commands/*.md`）：用户敲 `/ingest` 才跑，**不参与模型自动触发、无命中率稀释**；**所有有副作用（写库）的命令一律设计为用户显式调用**。
- **自动触发 Skill**（`.claude/skills/<name>/SKILL.md`，模型按 `description` 自调）只在需要"自然语言自动进入知识库流程"时才考虑，且**写库相关 Skill 必须加 `disable-model-invocation: true`**，禁止模型擅自触发副作用流程，其作用退化为"被显式调用时装载上下文 + 路由到子命令"。
- "skills 过多→命中率下降"只对自动触发 Skill 成立；本项目以显式命令为主，默认不依赖自动触发。

推荐落地结构：

```text
.claude/
  commands/
    ingest.md              # /ingest <source_id>：整源 ingest，写 proposed
    kb-query.md            # /kb-query "<question>"：只读查询 + 持久化 query-session
    kb-save.md             # /kb-save <session_id>：把某 query-session 候选提升为 proposed
    kb-review.md           # /kb-review：处理 Review-Queue proposal
    wiki-lint-semantic.md  # /wiki-lint-semantic：语义 lint，产出 proposal
docs/skill-runtime/
  routing.md               # 命令选择树 + 正/负样本（见下）
  schema.md                # 页面类型、frontmatter、证据/状态规则
  concept-resolution.md    # resolve_or_create_concept 协议
  save-back-policy.md      # query/save-back 准入门槛
templates/
  source.md lesson.md concept.md topic.md comparison.md synthesis.md
```

### routing.md：决策树 + 正/负样本

- **决策树**：新外部来源 → `/ingest`；问已有知识 → `/kb-query`；query 后想留存 → `/kb-save <session>`；处理队列/复核 → `/kb-review`；语义体检 → `/wiki-lint-semantic`。
- **正例**：「把这个 PDF / 这本书加入知识库」「ingest <source>」→ `/ingest`；「知识库里关于 X 怎么说 / 查我的 wiki」→ `/kb-query`；「把刚才那个对比/结论存进 wiki / 形成 synthesis」→ `/kb-save`；「处理复核队列」→ `/kb-review`。
- **负例（绝不触发写库 / ingest）**：「总结这篇文章」「解释这段」「帮我配 Obsidian」「修这个代码 bug」「问个常识 / 翻译一下」——只做普通回答，不进 wiki 流程。

路由与持久化原则：

- **按命令加载最小上下文**：`/ingest` 读 schema、concept-resolution、work order；`/kb-query` 读 index/registry 和相关页面；`/kb-save` 额外读 save-back-policy；不要把所有规则塞进 `CLAUDE.md`（它只放指针）。
- **命令不绕过门禁**：任何写库命令只能写 `status: proposed`，随后由 CLI 确定性 lint/promote 或 Review-Queue 处理。
- **关键中间结果持久化**：`/ingest` 写 `ingest_progress`；`/kb-query` 与 `/kb-save` 写 `pipeline-workspace/query-sessions/<run_id>/`（question、answer、candidate_write_set、evidence_refs、decision）。**query-session 不登记进 `artifacts`**（P0 `artifacts.source_id` 为 `NOT NULL`，而 query-session 跨来源、无 source）——当前只落文件系统；P8 如确有查询统计需求再评估单独的 `query_sessions` 表。

## 4. Vault 结构

```text
wiki/
  _meta/
    purpose.md      # 用户维护：学习目标、偏好、当前重点
    schema.md       # 仅指针/派生：指向 docs/skill-runtime/schema.md（不手写副本以免漂移；如需 vault 内可读由脚本派生）
  domains/
    game-theory/   { lessons/  concepts/ }   # 讲义 + 领域私有概念（默认在此）
    math-econ/     { lessons/  concepts/ }
    programming/   { lessons/  concepts/ }
  concepts/        # 仅 shared（跨领域提升后的）概念
    _registry.yaml # canonical 索引（派生：由概念页 frontmatter 确定性重建）
  topics/          # 跨章节/跨来源主题综合
  comparisons/     # 横向对比页
  synthesis/       # 深度综合/结晶化
  sources/         # 所有来源摘要（统一来源台账，跨领域）
  assets/          # 本地图片、源页截图
  overview.md      # living synthesis，vault 入口（Claude 维护）
  index.generated.md  # 内容目录（派生：确定性重建，Claude 不写）
  log.md           # append-only（Claude 追加 ingest，收尾追加 lint）
  aliases.md       # 别名视图（派生：由概念页 frontmatter 重建，Claude 不写）
```

借鉴：sdyckjq-lab 分目录+模板；Karpathy index+log；SamurAIGPT overview=living synthesis。

## 5. 非文字内容分层方案（弃 surya 硬管线）

新增预处理 CLI 阶段 `source-convert`，按页选后端，产出每个 source 的 `source.md`，并把难页渲染成 PNG 存 `assets/`：

| 页类型（由 profile-pdf 判定） | 后端 | 输出 |
|---|---|---|
| 纯文本 PDF（Pro Git / Python Cookbook） | `pymupdf4llm`（快，本地） | Markdown，代码块/表格保真 |
| 公式密集但排版清晰 | `marker`（本地优先，GPU） | Markdown + 保留 LaTeX |
| 有结构化源（arXiv 等） | 结构化源（如 arxiv2md） | 跳过 PDF，直接拿 LaTeX |
| marker/OCR 低置信、公式断裂、关键图表页 | 标 `needs_vision`，渲染整页 PNG | Claude `/ingest` 时直接读图 → KaTeX `$$…$$` + 解释 |

- **图/图表**：抽到 `assets/<source>/`，笔记内 `![[assets/…png]]` 内嵌 + Claude 写图注，SHA-256 去重（同一图跨来源只描述一次）。借鉴 nashsu caption-first。
- **可验证性靠邻接 + 后置检查**（不是只靠邻接）：公式重的 lesson 必须在源页截图旁呈现，且 §10 的发布门禁会检查"公式页是否引用源页图"。
- MinerU（云 API）列为**可选** bulk 后端，默认不启用（保持本地优先/隐私）。

**来源格式路由（PDF 之外）**：`source-convert` 同时按**来源格式**路由，统一产出 `source.md` + `assets/`；**下游只认 `source.md`，与原始格式无关**，故扩展格式不改下游：

| 来源格式 | 后端 | 备注 |
|---|---|---|
| Markdown | 直通（几乎不转） | 本就是目标格式，顶多抽内嵌图 |
| Word `.docx` | `pandoc` / `docling`（保 OMML 公式→LaTeX） | 避免 `markitdown` 丢公式 |
| PPT `.pptx` | `docling` / `markitdown`（每页→小节，抽幻灯片图） | 原生数字，免 OCR |
| PDF | 上表分层后端（pymupdf4llm/marker/难页读图） | 不变 |

非 PDF 多为原生数字文本，基本不走 OCR/难页读图；唯一需专门处理的是 **Office 公式（OMML）保真**——用 pandoc/docling，别用 markitdown。

借鉴出处：分层后端 + 结构化源优先(SamurAIGPT)；caption-first + SHA 去重 + `media/`(nashsu)；media 本地化(sdyckjq-lab)；"先读文本再看图"(Karpathy)。

## 6. Canonical 概念数据模型（Codex #1 + 决策 #1）

**真值在概念页 frontmatter**；`concepts/_registry.yaml` 与业务 SQLite 为派生索引（供 ingest 时快速归一查询），由 frontmatter 重建。

```yaml
---
type: concept
canonical_id: concept.game-theory.signaling-game   # 命名空间化，跨域天然隔离
canonical_name: 信号博弈
aliases: [Signaling Game]
scope: domain            # domain | shared
domain: game-theory      # 拥有者领域；shared 时为 "shared"
source_refs: [{source: game-theory-whitepaper, sections: ["5.2", "12.2"]}]
page_path: domains/game-theory/concepts/signaling-game.md
---
```

- **唯一真值 = 概念页 frontmatter**。`aliases.md` 与 `concepts/_registry.yaml` 都是**派生产物**，由收尾 CLI 从所有概念页 frontmatter 确定性重建。`/ingest` **只写概念页 frontmatter 的 `aliases:`**，绝不直接写派生文件——杜绝"派生文件成写入真值"的自相矛盾。
- **归一走单一协议 `resolve_or_create_concept`**（所有 concept 创建/更新唯一入口）：查 registry + aliases，mention 命中 `canonical_id` 则 **merge 进既有页（绝不新建）**；未命中且过准入门槛才按命名空间 `concept.<domain>.<slug>` **create** 并登记。直接消除 `信号博弈`×2 这类分裂。
- **registry 必须完整、带 hash、可重建**：work order 携带覆盖"当前 domain + 全部 shared"的完整 registry 快照及其 hash（见 §9）；`/ingest` 开工先校验磁盘 registry hash 一致（stale 守卫），不一致则中止。去重保障 = 完整 registry + 强制走协议（防大多数重复）+ §11 阻断性 duplicate-concept lint（漏网的转 Review-Queue proposal）。
- **跨域提升门槛**：默认 `scope: domain`，放 `domains/<d>/concepts/`。第二个领域**语义上确实复用**（非仅同名）才提升 `scope: shared` 并移到顶层 `concepts/`，须经 Review-Queue 人工确认。同名异义（econ 的 utility vs CS 的 utility）保持各自 `canonical_id`，不合并。

## 7. 综合层作为一等产物（Codex #2）

overview/topics/comparisons/synthesis 由 `/ingest` 在会话内**随 ingest 增量产出与更新**，不是收尾聚合：

- `overview.md`：living synthesis，vault 入口。含"核心概念地图 / 推荐学习路线 / 模型家族对比"，**禁止退化成章节清单**（§11 lint L5 强制）。
- `topics/<主题>.md`：跨章节综合 + 各来源贡献表 + 未解决问题/矛盾。
- `comparisons/<对比>.md`：如"古诺 vs 伯特兰 vs 斯塔克尔伯格"。
- 收尾 CLI 只重建**确定性派生**部分（Dataview 表、coverage、dashboards、index.generated.md、_registry.yaml、aliases.md），**不改写 overview/topic/synthesis 等 Claude 维护的综合内容**。

## 7.1 Query/save-back：学习过程反向滋养 wiki

除 source ingest 外，wiki 还需要从用户的学习问题中增长。拆成**只读查询**与**显式保存**两步：

- `/kb-query "<question>"`：**只读**。读取 `index.generated.md`、registry、相关概念/主题/来源页回答问题；**不写 vault**；但持久化一份 query-session 到 `pipeline-workspace/query-sessions/<run_id>/`（question、answer、candidate_write_set、evidence_refs、related_pages），供事后保存与审计。
- `/kb-save <session_id>`：作用在**已有的 query-session** 上（看到答案后再决定）。只有命中准入门槛才写 `status: proposed` 的 topic/comparison/synthesis/concept 更新，并走 §3.3 两阶段发布。

save-back 准入门槛（至少满足一项，且不得缺证据）：

- 形成跨来源综合、模型对比、学习路线、常见误区或自测题；
- 解决一个会反复出现的学习困惑，并能链接到已有概念/主题；
- 发现重复概念、别名、跨域提升候选或页面矛盾；
- 用户明确要求“保存到 wiki / 形成笔记 / 加进 synthesis”。

默认不保存：

- 一次性事实查询、普通解释、没有来源支撑的推测、只复述已有页面的答案；
- 需要覆盖 `managed_by: human` 页或越过 write scope 的答案；
- 无法链接到现有 source_refs / concept_refs 的内容。

硬约束：概念写入仍走 `resolve_or_create_concept`（命中即合并、绝不新建重复）；Q2 语义 lint 判"是否真新增价值 vs 复述已有页"并可阻断。`/kb-save` 必须在该 session 目录留下 `decision.md`，说明"为什么保存 / 写了哪些页 / 引用了哪些证据 / 为什么没有污染已有概念"。query-session 当前只落文件系统、不进 `artifacts`（见 §3.4）。这让 query 变成可审计的学习行为，而不是聊天记录堆积。

## 8. 页面模板（套 sdyckjq-lab，治读感混乱）

5 类，frontmatter 全部带 Dataview 字段 + `managed_by: pipeline`：

- **source**（来源摘要）：一句话总结 / 核心观点(3–5) / 关键概念([[]]) / **与其他来源的关联（补充·反驳·扩展）** / 精彩摘录 / 相关页面。
- **lesson**（讲义）：**正文干净散文，无裸 E-ID**；证据进脚注 `[^e1]` + 链 Claims/源页截图；公式 KaTeX；难页内嵌源页图。
- **concept**（概念，最小结构强制）：一句话 / 直觉 / 形式化(KaTeX) / **各章如何处理** / 与其他概念关系([[]]) / 自测链接。
- **topic**：核心综合 / 各来源贡献表 / 未解决问题。
- **synthesis**：核心洞见 / 关键决策 / 涉及概念 / 待跟进。

## 9. `/ingest` 事务协议与 work order 契约（Codex #4，source 级）

整源 ingest 会触及多页、且部分页面在 ingest 中才发现，故写入边界用**目录/glob 作用域**而非穷举文件清单。预处理 CLI 后为每个 source 生成 `staging/<source>/workorder.yaml`：

```yaml
source_id: game-theory-whitepaper
domain: game-theory
write_scope:                          # 只能在这些作用域内写（派生文件不在内）
  - domains/game-theory/**            # 该源所属领域（lessons + 域内概念）
  - concepts/**                       # 仅在提升为 shared 时
  - topics/**
  - comparisons/**
  - synthesis/**
  - sources/game-theory-whitepaper.md
  - overview.md                       # Claude 维护的综合入口
  - log.md                            # append-only
registry:                             # 完整、带 hash、stale 守卫
  path: concepts/_registry.yaml
  hash: <sha256-of-registry>          # /ingest 开工先校验磁盘一致，不符则中止
  scope: [domain:game-theory, shared] # 覆盖当前 domain + 全部 shared 的 canonical
concept_pages_snapshot:               # 完整：domain + shared 全部 canonical 概念页（非"可能触及"）
  - { canonical_id: concept.game-theory.signaling-game, path: domains/game-theory/concepts/signaling-game.md, sha256: <hash>, managed_by: pipeline }
  # …全部
other_pages_snapshot:                 # 其它已存在目标页（lessons/topics/…）hash + 管理归属
  - { path: domains/game-theory/lessons/5.2.md, sha256: <hash>, managed_by: pipeline }
source:
  text_md: staging/game-theory-whitepaper/source.md
  page_images_dir: assets/game-theory/src/    # 仅 needs_vision 页
  processing_windows: staging/game-theory-whitepaper/windows.jsonl  # TOC/页码/token 滑窗
on_failure: route_to_review_queue
```

命令必须遵守：
- **stale registry 守卫**：开工先比对磁盘 `_registry.yaml` 的 hash 与 `registry.hash`；不符 → 中止并要求重新生成 work order（绝不用过期归一索引）。
- **写入边界**：只在 `write_scope` 内写，越界视为失败。派生文件（`index.generated.md` / `aliases.md` / `_registry.yaml`）**一律不由 `/ingest` 写**，收尾从 frontmatter 重建。
- **覆盖保护（默认拒写）**：写任一已存在页前，该页须 ① 在 snapshot 中、② `managed_by != human`、③ 磁盘 hash == snapshot hash，三者皆满足才可覆盖；**否则（不在 snapshot／hash 已变／human）一律不覆盖**，改动写成 `Review-Queue/` proposal。
- **概念归一走单一协议 `resolve_or_create_concept`**：所有 concept 创建/更新只经此函数——命中 canonical_id 则 merge 进既有页（**绝不新建**），未命中按 `concept.<domain>.<slug>` 新建登记；别名只写概念页 frontmatter `aliases:`。
- **幂等可重跑（机器状态，不靠 log.md）**：每个 window 写 `ingest_progress`（window_id/input_hash/started/finished/status/write_set/proposal_set/error，见 §3.3）；续跑跳过已完成且 input_hash 未变的 window。`log.md` 仅由此派生为人读摘要。
- **两阶段发布**：所有写出页带 `status: proposed`；就地 merge 既有 published 页前先存文件快照到 `pipeline-workspace/snapshots/<source>/<run_id>/`（默认非 git）；promote/回滚由收尾门禁负责（见 §3.3）。
- 追加 `log.md`：`## [YYYY-MM-DD] ingest | <source> | <created/updated 页列表>`。

`/kb-save <session_id>` 复用同一写入纪律，但没有 source work order：它读取既有 query-session 运行目录、当前 registry hash 和目标页快照；写入范围仅限 `topics/**`、`comparisons/**`、`synthesis/**`、相关 concept 页、`overview.md`、`log.md`；任何新增/修改仍为 `status: proposed`，并由收尾 CLI 门禁决定 promote、回滚或转 Review-Queue。

## 9.1 Source 生命周期（stub，后续阶段）

当前阶段只覆盖"新增 source → ingest → 两阶段发布"。**source 的更新、删除、取代及其派生页对账属于后续阶段问题，本 spec 暂不展开**，仅记录约束以免日后踩坑：

- **更新**（同一 source 新版本重 ingest）：需对账旧的 lesson/concept 贡献（`source_refs` 增删），不得留下指向已删章节的孤立内容。
- **删除**（移除一个 source）：回收其独有的 lesson/source 摘要，并从受影响 concept/topic 的 `source_refs` 移除该 source；仅由该 source 支撑的 concept 进 Review-Queue 人工决定去留（借鉴 nashsu source-lifecycle / source-delete-decision）。
- **取代**（source A 被 B 取代）：按"先 ingest B、再按删除流程处理 A"组合。
- **落地阶段**：建议在 P7（多领域结构落地）之后或单列一期实现；P0 的 `sources` / `source_refs`（概念页 frontmatter）/ `review_proposals` 已能承载该状态，**无需现在加表**。

## 10. 证据策略：干净正文 + 保留门禁（Codex #3）

- **正文**：无裸 E-ID。证据退到脚注、`Claims/`（claim→证据）、`Source-QA/`、源页截图链接。
- **发布前门禁（收尾 CLI 后置 pass，保留）**：
  - 每条核心 claim 在脚注/Claims 中有可追溯证据；
  - 公式重的 lesson/源段引用了源页截图；
  - 断链数、重复 canonical_id 数在阈值内；
  - 达标 → promote（`proposed`→`published`，纳入 index）；不达标 → 不 promote、就地 merge 回滚，失败 diff 落 `review_proposals` + `Review-Queue/`（见 §3.3 两阶段发布）。
- `evidence_verifier` 重构为解析"脚注/Claims/frontmatter"，不再要求内联 E-ID。

## 11. 学习质量 lint（Codex #5）

分两类执行。**收尾 CLI 只跑确定性检查（零 LLM）**；语义判断放进 `/ingest` 会话内 Claude 自检，或用户手动触发 `/wiki-lint-semantic`——不在收尾 CLI 引入任何模型：

| 规则 | 类型 | 执行处 | 检查 |
|---|---|---|---|
| L1 | 确定性 | 收尾 | lesson 正文无裸 `[E-...]` ID（正则） |
| L2 | 确定性 | 收尾 | concept 页含必需小节（直觉/形式化/各章如何处理/关系） |
| L3 | 确定性 | 收尾 | topic 页含跨章节贡献表（表格存在性） |
| L5 | 确定性 | 收尾 | overview 含综合节（核心概念/怎么学/对比），非纯链接清单 |
| L6 | 确定性 | 收尾 | cover/blank/toc 页（profile 标记）不生成学习页 |
| 结构 | 确定性 | 收尾 | 断链、重复 canonical_id、孤儿页、公式页引用源页图、claim 有证据引用 |
| Q1 | 确定性 | 收尾 | `/kb-save` 产物有 query-session 目录（文件系统）、decision、candidate write set、证据/相关页引用 |
| L4 | 语义 | /ingest 自检 或 /wiki-lint-semantic | comparison 是否真正覆盖关键差异维度 |
| 矛盾 | 语义 | /ingest 自检 或 /wiki-lint-semantic | 跨页结论矛盾 |
| Q2 | 语义 | /kb-save 自检 或 /wiki-lint-semantic | 保存内容是否真的新增学习价值，而不是重复已有页面 |

**阻断性**：duplicate-concept、核心 claim 无证据、公式页缺源页图等门禁项**不只报告**——对应内容不发布，转 `Review-Queue/` proposal 等人工处理。

## 12. 留用 vs 重建（迁移影响）

- **留用**：PyMuPDF 页渲染/`pdf_profile`、目录约定/Dataview、**单一业务 SQLite**（观测+cost，现兼作状态跟踪）。
- **删除**：`plan-units` / `validate-unit-plan` / `review-unit-plan` / 逐 unit 生成图 / DeepSeek planner（不再做语义 unit 规划）；**LangGraph StateGraph + checkpointer + checkpoint SQLite + `langgraph-checkpoint-sqlite` 依赖**（编排已无循环/分支，见 §3.2；CLAUDE.md 的"双 SQLite"描述需在实现时同步改为单库）。
- **新增**：`source-convert`（分层后端 + 难页渲染）、processing windows 生成器（确定性 TOC/页码/token 滑窗）、命令层（显式 slash command：`.claude/commands/*` + `docs/skill-runtime/*`）、`/ingest` 命令 + source 级 work order 生成器、`/kb-query`、`/kb-save`、canonical registry、learning-quality lint、后置 adjacency/断链/重复检查；**确定性 Python CLI 编排（普通顺序脚本，扩展 `scripts/pipeline.py`）+ 业务 SQLite 状态跟踪 + `pipeline status`/`pipeline next` CLI**。
- **重建**：surya-OCR 管线 → 分层后端 + 难页多模态读图；`langgraph_worker.py`（StateGraph + 调模型的 generate/review/revise 节点）→ 删除，生成搬进 `/ingest` 命令（唯一 LLM）；evidence/formula 硬门禁（内联） → 后置门禁（脚注/Claims/邻接）；`obsidian_indexes.py::can_overwrite()` 从"只认 `managed_by: pipeline`"升级为 snapshot+hash 模型（不在 snapshot 或 hash 变化即拒写、出 proposal）。
- **概念归一**：不设独立 LLM 预规划阶段，归一在 `/ingest` 内靠 registry+aliases 约束完成（见 §6）。
- **续跑边界**：单库替代 checkpointer 的范围是 **CLI 阶段级 + `/ingest` window 级**恢复，**不恢复 Claude 内部推理状态**（见 §3.3，取舍可接受）。
- **文档同步（不晚于 P0/P1）**：`CLAUDE.md`（双 SQLite / LangGraph / plan-units 描述）、`README.md`、`requirements.txt`（`langgraph*` 依赖）仍指向旧架构，须在动 P1 代码前或同期更新，否则后续 agent 按旧架构开工。

## 13. 风险与未决

- **一次会话 token/限流**：大书 `/ingest` 按 processing windows 逐窗处理（wiki 当外部记忆），幂等可分章分批触发；不假设一气呵成。
- **难页判定准确度**：`needs_vision` 误判会漏读公式或浪费图读；先用 marker 置信度 + profile 公式风险双信号，阈值可调。
- **跨域提升判断**：自动只给"候选"，提升一律人工确认，避免污染。
- **Claude key 合规**：唯一 LLM 是人触发的交互式 `/ingest`，非无人值守自动化；预处理全是确定性本地工具（零 LLM），不碰该 key。
- **registry 完整性/新鲜度**：去重不靠预规划，靠"完整 registry 快照 + hash 守卫 + 单一 resolve 协议 + 阻断性后置 lint"四件套；缺一件重复概念就可能绕过约束。
- **命令误触发/命中率下降**：副作用命令一律显式 slash command（不自动触发、无稀释）；若用 `SKILL.md` 则加 `disable-model-invocation: true`；配 `routing.md` 负样本、按命令最小加载上下文；不把 wiki 规则写成 always-on 全局指令。
- **query/save-back 污染知识库**：`/kb-query` 只读（仅持久化 query-session）；只有 `/kb-save <session>` 且满足 §7.1 准入门槛时才写 `status: proposed`；重复/低价值答案由 Q2 自检和 Review-Queue 拦截。

## 14. 验收标准

- 一本书走通 `profile → source-convert → 生成 windows + work order → /ingest → 收尾 lint`。
- 抽查 lesson：正文无裸 E-ID，公式 KaTeX 渲染，难页有源页截图。
- 概念：`信号博弈/Signaling Game` 合并为单页且 `source_refs` 累积；无跨域同名污染。
- overview 是综合入口而非章节清单（L5 通过）。
- 重跑 `/ingest` 不覆盖 `managed_by: human` 页、不产生重复概念页。
- 不达标内容正确进 Review-Queue。
- **两阶段发布**：门禁通过才 promote 并纳入 `index.generated.md`；失败内容保持 `status: proposed`、不在 index，就地 merge 已回滚到 pre-ingest 版本。
- **window 级续跑**：中断后重跑跳过已完成且 input_hash 未变的 window，不重复写、不丢未完成 window。
- **并发锁**：vault 已有 active `/ingest` 时第二个被拒；heartbeat 超时的 stale lock 可由 `pipeline next` 提示清理。
- **命令路由**：副作用命令均显式调用（slash command；若用 `SKILL.md` 则 `disable-model-invocation: true`）；`/kb-query` 只读、持久化 query-session、不写 vault；`/kb-save <session>` 只有命中准入门槛才写 proposed，并留下 query-session decision。
- **命令上下文瘦身**：`CLAUDE.md` 只指向命令层和 spec，不内联 schema/模板/所有规则；命令按需读取 `docs/skill-runtime/*`。

## 15. 实现分期（交 writing-plans 细化）

> 编排基线：全程确定性 Python CLI（扩展 `scripts/pipeline.py`）+ 单一业务 SQLite 状态跟踪 + `pipeline status`/`next` CLI；移除 LangGraph/checkpointer。各期都在此基线上做。

0. **P0 状态底座 + 文档同步（硬前置，不与 P1 业务逻辑混做）**：建业务 SQLite 状态机表（sources / source_stage_runs / artifacts / work_orders / source_locks / review_proposals / ingest_progress）+ `pipeline status`/`next` + 单 vault 锁 + **快照回滚机制**（`pipeline-workspace/snapshots/`，默认非 git）；同步 `CLAUDE.md`/`README.md`/`requirements.txt` 到新架构。先把这套可恢复/可诊断底座跑通，再做 source-convert。

1. **P1 source-convert + processing windows + 难页 vision 标记**（分层后端 + PNG 渲染 + 确定性 TOC/页码/token 滑窗；纯文本书端到端可跑）。
2. **P2 canonical 概念模型 + registry + 别名归一 + 概念 merge**（先在现有 vault 上做去重，立即可见收益）。
3. **P3 页面模板 + 正文清理（证据进脚注）**。
4. **P4 命令层（显式 slash command）+ `/ingest` + source 级 work order 事务协议**（核心重构；落地 `.claude/commands/ingest.md`、最小 `docs/skill-runtime/routing.md`、`schema.md`、`concept-resolution.md`、模板引用；仍只写 proposed。若用 `SKILL.md` 则 `disable-model-invocation: true`）。
5. **P5 综合层一等产物（overview/topic/comparison + lessons 跟随 TOC）**。
6. **P6 学习质量 lint + 后置门禁 + Review-Queue 回流**。
7. **P7 多领域结构落地 + 跨域提升流程**。
8. **P8 query/save-back 闭环 + review/semantic-lint 命令**（`/kb-query` 只读 + 持久化 query-session、`/kb-save <session>` 写 proposed、`/kb-review` 处理结构化 proposal、`/wiki-lint-semantic` 输出人工可审查建议；如确有查询统计需求再评估新增 `query_sessions` 表）。

> Source 生命周期（更新/删除/取代，见 §9.1）为后续阶段问题，建议 P7 之后或单列一期，不在当前 P0–P8 范围内强行实现。
