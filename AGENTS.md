# PDF to Study KB — Codex 项目指令

> 本文件是 **Codex 理解本项目的唯一真值**（Claude Code 对应 `CLAUDE.md`，内容对等、调同一套 CLI）。
> 详细运行时协议在 `docs/skill-runtime/*`（skill 执行时按需加载）。其余历史设计文档已删除，**请勿参照已删除的文档开展工作**。

## 1. 本质

把多来源文档（PDF/DOCX/PPTX/MD）**用对话**增量编译进一个本地、跨领域、按概念导航、越长越互联的 Obsidian 学习知识库（llm-wiki 模式）。**确定性 Python CLI 保证可重复 / 可观测 / 安全；唯一的 LLM 是人触发的对话式 skill，负责高价值的写作与跨页归并。** 产出是"知识网"而非"翻译稿"：概念/主题为主，lessons 跟随源 TOC 为辅。

## 2. 架构（两层 + 两 agent）

- **确定性执行层** `scripts/pipeline.py`（零 LLM）：预处理 + 后置 lint 门禁 + 索引重建 + 单一业务 SQLite 状态机/锁。**全部业务逻辑在此**，由 `tests/` 覆盖。
- **对话编排层** `.agents/skills/<name>/SKILL.md`（Codex）/ Claude Code 侧 skill：自然语言指令 + 流程编排，**不承载业务 Python**，通过 shell 调同一 CLI。
- 两 agent 共享同一 `pipeline.py` 与同一 `wiki/` vault；Codex 读本文件，Claude Code 读 `CLAUDE.md`。

```text
ingest skill 编排预处理（零 LLM）：add-source → profile → source-convert → source-audit（PyMuPDF×MinerU 双审）→ windows → workorder
   ↓ 同一会话（唯一 LLM）：读 chapters.json 全书章节图 + source.md/难页图 → 按章写 status:proposed 页（难页按类型嵌原图）+ 概念归一 + 综合层
   ↓ 同一会话收尾（零 LLM）：确定性 lint → promote(proposed→published) 或 回滚+Review-Queue → 重建 index/registry/aliases
```

## 3. 六条核心约束

1. **预处理/收尾零 LLM**（确定性 CLI）；唯一 LLM 是人触发的对话式 skill，不做无人值守批处理。
2. **不拆分**：不让 LLM 做语义 unit 规划/审批；长源用确定性 processing windows（TOC/标题/页码/token 滑窗）读取。
3. **概念去重**：所有 concept 创建/更新走单一 `resolve-concept`，命中 `canonical_id` 即合并、**绝不新建重复页**；`_registry.yaml`/`aliases.md` 为派生文件，skill 不手写。
4. **两阶段发布**：skill 只写 `status: proposed`；收尾门禁过才 promote 到 `published` 并入 index，失败回滚（`pipeline-workspace/snapshots/`，默认非 git）+ 进 `Review-Queue/`。
5. **覆盖保护**：写已存在页须"在 work-order snapshot 中 + `managed_by != human` + hash 一致"，否则拒写、出 proposal。**绝不静默修改由 human 维护的页面。**
6. **fail-closed lint**：断链、缺必需小节、孤儿页（未记账归属）、重复 canonical_id、公式页缺源图——任一不过即阻断发布。

## 4. 命令层（skills 驱动，可自动触发）

LLM 能力 = `.agents/skills/{ingest,kb-query,kb-save,kb-review,kb-qa,wiki-lint-semantic,source-preflight,source-xray,skill-evolve}/SKILL.md`，**全部允许模型按 `description` 自动触发**（无 `disable-model-invocation`）。误触发靠 description 负样本压制（"总结这篇/解释/翻译"不进 wiki 流程）；数据安全由 CLI 守卫强制，与是否自动触发正交。其中 `skill-evolve` 是 **skill 自进化**：把反复出现的 lint 失败（`skill-mine` 在 lint 失败时自动聚成 `backlog.yaml`）沉淀成对某 skill 的有界改进，受 `skill-gate`（pytest+双树对等+gate-integrity，候选只许动 skill 两树）守门、人 `skill-adopt` 才合并进双树。详细协议：`docs/skill-runtime/{routing,schema,concept-resolution,save-back-policy}.md`（skill 按需加载）。

## 5. 双 agent 协作约定（Codex + Claude Code）

- **同一时刻同一 vault 只允许一个 ingest**（`source_locks` 强制）。Codex 与 Claude Code **不得同时对同一库 ingest**；崩溃残留锁用 `python scripts/pipeline.py unlock` 回收。
- **共享 CLI 是唯一契约**：两 agent 都只调 `scripts/pipeline.py`，**业务逻辑只改这里**，不在各自 skill 里重复实现；改了 CLI 行为要保证两边 skill 仍一致。
- **续跑锚点 = `pipeline.py next` + digest `## ⏩ RESUME` 块**（不依赖任何会话级 hook）：中断后（上下文压缩 / 模型不可用）说“继续”或由 `scripts/resume-ingest.ps1`（OS 定时器触发，prompt 自带定位逻辑）续跑，都从下一个未完成 window 接上。两 agent 的**共享契约始终是 `pipeline.py` + `digest.md`（含 RESUME 块）+ 字节对等的 skill 双树**，对 Codex / Claude 一致。
- **解释器统一用项目指定的 `study-kb` conda 环境**（`conda create -n study-kb python=3.12` 后安装 `requirements.txt`：PyMuPDF / PyYAML / pytest；可选 MinerU 结构化后端见 `requirements.txt` 末尾可选段 / `scripts/install_mineru.py`，非默认依赖）；**请勿改用其他解释器**。
- **生成物非 git**：`wiki/`、`pipeline-workspace/` 已 gitignore，不提交——它们是每机运行时状态。

## 6. 真实能力边界（开工前知悉）

- **视觉保真 / PDF 双审解析（PyMuPDF 抽取 + MinerU structural review）**：fast path——PDF 经 PyMuPDF 抽纯文本（快，作 profiling/extraction 路径），会拍平上/下标/分数、且看不见矢量图与无框线表；`source-convert` 把每个难页（`needs_vision` 高召回判定：公式页 / 矢量图页[`get_drawings`] / 表格页[`find_tables`] / 图表标题页）渲染为整页 PNG（route B），由 ingest 时 LLM **读图**保真（公式写 KaTeX；lint 硬规则强制 lesson 内嵌源图），`pages.jsonl` 记 `needs_vision_reason` 可审计。**MinerU 是 PDF 验收的必需 structural reviewer（不是可选回退）**：`source-audit` 跑 MinerU 复读同一 PDF、与 PyMuPDF 做确定性逐页互检 → `reconciliation.json`（哪后端给哪证据 / 对了哪些页 / 分歧 / 是否接受 / 是否降级）——因 PyMuPDF 的 `needs_vision` 阈值刻意宽、**不可作单一真值**。strict / 生产验收要求每个 PDF 都过双审，MinerU 不可用即 **fail-closed（不静默回退 PyMuPDF）**；non-strict / dev 可 PyMuPDF-only 但 reconciliation 显式标 `degraded / 未双审`、**不满足 strict 验收**。MinerU 亦作扫描 / 低文本 PDF、DOCX / PPTX 的 primary 结构化抽取，归一成同一套 `source.md + blocks.jsonl + chapters.json + parse_report.json + reconciliation.json + assets/`；低显存 GPU（约 4GB）→ 仅 MinerU `pipeline` 后端（CLI 恒 `-b pipeline`，禁 vlm/hybrid）。
- **格式覆盖**：`pdf` 经 PyMuPDF 抽取 + MinerU 双审（strict 必需，未装 fail-closed）；`md` 走 fast path；`docx`/`pptx` 与扫描/低文本 PDF 由 MinerU primary 结构化（`--backend auto` 自动路由，`--backend mineru` 强制；未装 fail-closed）。
- **每本书的入库都是一次需付费的 LLM 操作**，并非导入即用；项目交付时为空库，内容通过运行 ingest 逐步生成。
- **lint 硬规则**：wikilink 必须全 vault 相对路径（非 Obsidian basename）、必需小节标题逐字、非 source 页（topic/comparison/synthesis/overview）必须进某 window 的 `--writes` 记账——见 ingest skill 阶段 D 速查；未遵守将被门禁拦截。

## 7. Windows / PowerShell 工具约定

> 本项目在 Windows + PowerShell 7 环境下开发，本节面向 Windows 贡献者；在 macOS / Linux 上，agent 的原生 shell 即标准 Bash，以下 Git Bash 相关事项通常不适用。

在 Windows 上，Codex 的 Bash 工具底层是 Git Bash (MSYS2)，处理含中文的 Windows 路径可能出错。

1. **优先用原生工具**：Glob、Grep、Read、Edit —— 不经过 Bash，无路径问题。
2. **要执行命令时**：直接调 `pwsh`（PowerShell 7）+ study-kb 解释器，不要用 Git Bash 调 PowerShell。
3. **禁止**：用 Bash 执行 `powershell -Command "..."` 或 `Select-String` 等。
4. **UTF-8**：跑 Python 前设 `$env:PYTHONUTF8=1`（中文源/路径）。

## 8. 权威收敛与报告约定

- **本文件 = Codex 的项目真值**；`CLAUDE.md` = Claude Code 的（内容对等）。两者冲突时以行为更安全的一方为准并同步修正。
- `docs/skill-runtime/*` = skill 运行时协议（保持准确、按需加载），不是"背景文档"。
- 旧 `docs/`（spec / adr / agents）已删除——请勿参照已删除的文档开展工作；**请勿重新引入 LangGraph / 双 SQLite / plan-units / 逐 unit 孤立生成 / surya 硬管线**（`tests/test_legacy_removed.py` 守卫）。
- 执行/修复/审阅报告写入项目文件（如 `pipeline-workspace/reports/`），对话中只说一句指引，不复制大段输出。
