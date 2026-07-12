# PDF to Study KB — 开发实现说明（Developer Implementation Guide）

> 本文面向开发者，描述仓库 `D:\pdf-to-study-kb` 的架构、模块职责、数据契约、命令层与测试。
> 所有结论以**源码为准**。
> 面向使用者的操作说明见 [用户使用说明](user-guide.md)。
> 版本锚点：`main` 分支（2026-07-12 增量核对）；**45 个 CLI 子命令**（新增 `vault-lint`）/ 11 个技能。测试数量以 `pytest --collect-only -q` 为准——本轮两次证明精确计数写进文档当场就腐烂，故不再保留任何快照。
> 2026-07-11 六阶段重构后的新机制（§7 已更新，其余章节以源码为准）：统一 callout 解析器
> `page_rules.parse_callouts`（唯一语法入口，错误不吞节点）、渲染安全唯一实现 + **vault preflight
> 事务隔离**（published 旧伤阻断 promote 但不回滚当前批）、`vault-lint` 全库健康门禁、归属≠记账
> （`unaccounted-write`，frontmatter 归属优先/write_set 只回退认领无归属页）、`legacy-concept-scaffold`
> 防旧模板复辟、kb-save 会话发布路径（`lint --source kb-save --session <run_id>` + `save_session` 内容身份）。

---

## 1. 项目架构概览

### 1.1 一句话定位

把多格式来源文档（PDF / DOCX / PPTX / Markdown）**通过对话**增量编译进一个本地、跨领域、按概念导航的
Obsidian 学习知识库（llm-wiki 模式）。系统由**两层**构成：

- **确定性执行层（零 LLM）**：`scripts/pipeline.py` 及其模块。负责预处理、状态机、并发锁、收尾 lint
  门禁、覆盖保护、索引重建、运营维护（复盘指标/失败信号退场/状态机回退/磁盘清理）。**全部业务逻辑在此**，
  由 `tests/` 当作可执行规格覆盖。
- **对话编排层（唯一 LLM）**：`.claude/skills/<name>/SKILL.md`（Claude Code 读）与
  `.agents/skills/<name>/SKILL.md`（Codex 读）。两套 skill 树**字节对等**，只用自然语言编排，
  通过 shell 调用同一套 CLI，**不含任何业务 Python**。

### 1.2 主要入口

| 入口 | 文件 | 说明 |
|------|------|------|
| 唯一 CLI 入口 | `scripts/pipeline.py` → `main()` | argparse 分发 **45 个**子命令（见 §3；2026-07-11 新增 `vault-lint`）。 |
| LLM 编排入口 | `.claude/skills/ingest/SKILL.md`（+ `references/*`） | 端到端入库 skill，唯一的 LLM 写库步骤。 |
| 无人值守续跑 | `scripts/resume-ingest.ps1` | OS 调度触发的有界续跑脚本（PowerShell）。 |
| 可选后端安装 | `scripts/install_mineru.py` → `main()` | 按机型自动安装 MinerU + 匹配 CUDA torch。 |
| MinerU 子进程 | `scripts/source_backends/mineru_runner.py` → `main()` | 进程隔离地跑 MinerU `do_parse`。 |

### 1.3 核心模块（`scripts/`）

| 模块 | 职责 | 关键符号 |
|------|------|----------|
| `pipeline.py` | CLI 分发 + 各阶段编排（每个 `cmd_*` 一个子命令，共 45 个） | `main()`、`cmd_*` 函数族、`_workspace_root()`、`_vault_dir()`、`_staging_dir()` |
| `state_store.py` | 单一业务 SQLite 状态机（**7 张表**）+ 原子阶段 API | `STAGES`、`NEXT`、`start_stage/complete_stage/fail_stage`、`should_run_stage`、`reopen_source`、`reset_source`、`RESETTABLE_TARGETS`、`resolve_review_proposals`、`source_stats`、`*_window` |
| `locks.py` | 单 vault 写锁（scope 固定 `"vault"`） | `acquire/release/heartbeat/is_stale/break_stale` |
| `source_profile.py` | L1 逐页 profile + `needs_vision` 判定（公式/图/表/扫描信号） | `profile_source`、`profile_page`、`needs_vision_reasons`、`vision_tier`、`is_scanned_source`、`render_pages_png`、`PROFILER_VERSION="5"` |
| `source_convert.py` | L1 dispatcher：选后端 → 调后端 → 落 artifact | `convert`、`select_backend`、`classify_source`、`converted_input_hash` |
| `source_backends/` | 三个解析后端 | `pymupdf_backend`、`markdown_backend`、`mineru_backend`、`mineru_runner` |
| `source_artifacts.py` | L2 数据契约（blocks/parse_report/reconciliation 形状 + 序列化） | `SourceBlock`、`build_parse_report`、`build_reconciliation_report`、`ARTIFACT_VERSION="6"` |
| `chaptering.py` | L2/L3 确定性章节切分（PDF 书签 → chapters） | `chapters_from_toc`、`CHAPTERING_VERSION="1"` |
| `source_audit.py` | L1/L4 PyMuPDF×MinerU 双审互检 → reconciliation/evidence/queue | `audit`、`reconcile`、`DualAuditUnavailable`、`PDF_TYPES` |
| `arbitration.py` | L4 分歧仲裁的确定性半（证据模型/候选/物化/闭环门） | `build_evidence_model`、`select_candidates`、`materialize_*`、`windows_blockers`、`check_closure`、`assess_risks` |
| `windowing.py` | L3 确定性 processing windows（block-aware / char fallback） | `build_windows_from_blocks`、`build_windows`、`page_char_ranges`、`WINDOWING_VERSION="5"` |
| `workorder.py` | L3/L4 ingest 事务契约（写入边界 + registry hash + 页快照） | `build_workorder`、`write_workorder` |
| `preflight_eval.py` | L4 确定性验收门（**12 项** check） | `evaluate`、`check_*` 函数族（见 §8） |
| `concept_store.py` | 概念归一唯一入口 + registry/aliases 派生 | `resolve_or_create_concept`、`build_registry`、`canonical_id`、`scan_concept_pages` |
| `promotion.py` | 跨域提升（候选检测 + 人工确认后机械提升） | `find_candidates`、`promote_to_shared` |
| `wiki_gate.py` | 收尾 lint 门禁 + promote + index 重建 + **quiz/命题两个派生阅读层** | `lint_pages`、`collect_proposed`、`promote`、`write_index`、`lint_risk_traceability`、`build_quiz_index`/`write_quiz_index`、`collect_propositions`/`build_propositions_index`/`write_propositions_index`/`duplicate_proposition_names` |
| `page_rules.py` | 页正文确定性文本规则（纯函数原语） | `REQUIRED_SECTIONS`（键保留值已清空）、`REQUIRED_FRONTMATTER`、`missing_frontmatter`、`missing_sections`、`bare_pipe_wikilink_in_table`、`leading_h1_duplicates_filename`、`extract_question_stems`、`extract_propositions`、`footnote_*` |
| `mdpage.py` | Markdown 页 frontmatter 读写（round-trip） | `read_page`、`write_page` |
| `ingest_guards.py` | 写前守卫（写入边界 glob + 覆盖保护三条件 + registry 新鲜度） | `in_write_scope`、`can_overwrite`、`registry_fresh` |
| `snapshots.py` | 就地 merge 前的文件快照 + 回滚（非 git） | `take_snapshot`、`rollback`、`cleanup` |
| `graph_model.py` | Knowledge Graph v2.0：published 页 → 图模型（topic_membership 骨架） | `build_graph_model`、`GRAPH_VERSION=2` |
| `graph_analysis.py` | Knowledge Graph v2.0：Louvain 社区分析 | `analyze_graph` |
| `graph_data.py` | Knowledge Graph v2.0：graph-data.generated.json 契约 | `to_graph_data`、`GRAPH_DATA_FILE` |
| `graph_schema.py` | Knowledge Graph v2.0：GRAPH_VERSION schema 定义 | `GRAPH_VERSION=2` |
| `graph_html.py` | Knowledge Graph v2.0：力导向交互 HTML（点击跳 obsidian://） | `write_html`、`HTML_FILE` |
| `graph_lint.py` | Knowledge Graph v2.0：graph-data/HTML 校验（fail-hard + warn-only） | `validate_graph_data`、`validate_html`、`write_report` |
| `query_session.py` | query-session 目录契约（kb-query/kb-save 用） | `check_session` |
| `thresholds.py` | 集中阈值配置（**27 个** `STUDY_KB_*` 常量，18 个折进缓存指纹 + 9 个门禁/观测/审计专用不折进） | 各常量、`_CACHE_KEYED`、`fingerprint()` |
| `install_mineru.py` | 可选 MinerU 安装器（选 torch CUDA wheel） | `candidate_cu_tags`、`select_wheel`、`detect_driver_cuda`、`main` |

> **不在 `scripts/` 里的"内容路由"与"写作装置"**：这两个是 2026-07-08 引入的 advisory 写作协议，**零 CLI 参与**——
> 详见 §3.8 与 §8。

### 1.4 数据流（端到端）

```text
原始文件 books/<name>/input/<file>
  │  add-source            （登记 source + raw_source artifact）
  ▼
pages.jsonl                （profile：逐页 needs_vision）
  │  source-convert        （选后端 → 解析）
  ▼
source.md + blocks.jsonl + chapters.json + parse_report.json + assets/   （L1+L2）
  │  source-audit          （PDF 双审：MinerU 复核）
  ▼
reconciliation.json + evidence.json + arbitration/queue.json            （L1/L4）
  │  [agent 仲裁 → arbitration-apply]   （L4：补整页图 + 置 needs_vision）
  ▼
windows.jsonl              （windows：block-aware 读取单位）        （L3）
  │  workorder
  ▼
workorder.yaml             （写入边界 + registry 快照 + 页快照）     （L4）
  │  preflight-eval --strict   （12 项确定性验收）
  ▼  ───────────────────── 以上零 LLM ─────────────────────
ingest-start → 读 chapters.json 建全书理解 → 按章判断内容路由（advisory，写进 digest.md）
  → 逐窗 show-window / 写 proposed 页（按装置预算克制用写作装置） / resolve-concept / window-done   （LLM）
  │  ingest-done
  ▼
status: proposed 页（lessons / concepts / topics / comparisons / synthesis / sources / overview）
  │  lint                  （收尾门禁）                              （零 LLM）
  ├─ 旧页渲染旧伤 → vault preflight 阻断（当前批**不回滚**、不写 lint/failed），修旧页直接重跑
  ├─ 通过 → promote(proposed→published) + 重建 index/registry
  │          + 知识图谱 v2.0 / quiz-index.generated.md / propositions.generated.md（三者均 publish-isolated）
  └─ 当前批违规 → 回滚就地 merge + 写 Review-Queue/
  │
  ▼（可选，发布后）
kb-postmortem：先存旧 backlog 快照 → ingest-stats → skill-mine 重扫 → diff → 复盘报告 + 建议
  → 人工确认后 proposals-resolve 销账 / staging-clean 清理 / skill-evolve 沉淀（reset-source / pipeline-doctor 按需修复卡死状态机）
```

### 1.5 预处理 / 解析流程

四层确定性链路（README §四层；以下为实现层映射）：

- **L1 解析与双审**：`source_profile.classify_source` 派生 `source_type`（native_pdf / scanned_pdf /
  low_text_pdf / mixed_pdf / docx / pptx / markdown）；`select_backend` 据 fmt+policy 选后端；
  `source-audit` 跑 MinerU 复读 PDF 并产 `reconciliation.json`。
- **L2 结构还原与证据归一**：`source_artifacts.SourceBlock` 定义块契约（`page/block_id/type/
  heading_path/chapter_id/source_ref/risk_flags/element_id`）；`chaptering.chapters_from_toc` 据
  PDF 书签切章；`assess_risks` 逐页打证据风险旗标。
- **L3 读取窗口与导航**：`windowing.build_windows_from_blocks` 按 section 切窗、原子块（table/image/
  chart）整块打包**长表不切**，回挂块元数据。
- **L4 仲裁与验收**：`arbitration` 把分歧整理成队列、物化裁决、闭环门；`preflight_eval.evaluate`
  跑 12 项 check，`--strict` 遇 high/fail 退出码 2。

### 1.6 产物生成流程（LLM 写库）

`ingest` skill 在同一会话内：读 `chapters.json` 建全书理解 → **按章判断内容路由**（`ingest/references/
content-routing.md` 定义的 5 分类：理论型/方法型/案例型/参考型/观点型；纯 LLM 判断、零 CLI 参与，写进
`staging/<src>/digest.md` 的一张「路由表」；实际写法偏离路由标签时允许直接按内容写，但须在 digest 追加
`[routing-deviation] chapter=<ch> 推荐=<原类型> 实际=<实际写法> 原因=<一句话>`，这些偏离标记是后续
`skill-evolve` 修订路由表本身的证据来源）→ 按章逐窗 `show-window` 读取（难页带 `route-b-assets` 资产头）
→ 写 `status: proposed` 页（正文默认零装置，**推导折叠**不计预算、鼓励使用，其余装置——案例解剖/定位段/
具名命题/误区 warning/图——一页至多启用一种，详见 §8）→ 经 `resolve-concept` 归一概念 → 阶段 E 写综合层
（overview/topic/comparison/synthesis）。每次真实写盘前调 `check-write`（边界 + 覆盖保护）与
`snapshot-page`（就地 merge 前快照）。

### 1.7 校验 / 质量门流程

- **预处理验收门**：`preflight-eval --strict`（`check_dual_audit` + `check_evidence_bundle` 等）。
- **收尾发布门**：`lint`——fail-closed，两段事务隔离：vault preflight（published 渲染旧伤 → 阻断
  promote + Review-Queue 去重登记，**不回滚当前批**）→ batch lint（当前批违规才回滚 + Review-Queue；
  共 27 个违规标识，见 §7）。同一渲染安全扫描可用 `vault-lint` 独立跑。
- **写前守卫**：`check-write`（`ingest_guards.in_write_scope` + `can_overwrite`）。
- **概念去重门**：`resolve-concept`（唯一入口）+ lint 的 `duplicate-canonical`。
- **并发门**：`source_locks`（单 vault 锁）+ `ingest-start` 的 stale registry 校验。
- **内容路由 / 写作装置**：**没有对应机器校验**，纯 advisory（见 §1.6、§8）——机器只守秩序/安全/溯源
  三类底线，正文该长什么样不归 lint 管。

### 1.8 Fallback 行为

| 场景 | Fallback | 实现位置 |
|------|----------|----------|
| 无 `blocks.jsonl` 的旧 staging | windowing 退回 `build_windows`（char 窗，`mode="chars"`，`degraded=True`） | `pipeline.cmd_windows` / `windowing.py` |
| MinerU 不可用（dev、非 strict） | PyMuPDF-only 仍出，但 reconciliation 标 `degraded_no_review / dual_audited=False` | `source_audit.reconcile` |
| MinerU 不可用（strict / PDF） | **fail-closed**：抛 `DualAuditUnavailable`，CLI 非零退出，**不静默回退** | `source_audit.audit` |
| 整本扫描件走 PyMuPDF | `is_scanned_source` 命中即在 `source-convert` 阻断（除非 `--force`），引导用 MinerU | `pipeline.cmd_source_convert` |
| 无 PDF 书签 TOC | `chapters_from_toc` 退化为整书一章 `ch00-full` | `chaptering.py` |
| graph / quiz-index / propositions 重建失败 | **发布隔离**：三者各自 try/except，只 warn、保留旧产物，**绝不回滚已发布内容或改变 lint 退出码** | `pipeline.cmd_lint` 内三段独立 try/except |
| 缺 `chapters.json` | windowing 用空表，`chapter_title` 退化为 `""`，不报错 | `pipeline.cmd_windows` |

### 1.9 外部依赖

- **必需**：`pymupdf>=1.23.0`（`import fitz`）、`pyyaml>=6.0`、`pytest>=7.0`（测试）。
- **可选**：`mineru[core]`（PDF 严格验收的 structural reviewer + 扫描/低文本 PDF、DOCX/PPTX 的 primary
  解析）。安装通过 `scripts/install_mineru.py`。仅用 MinerU 的 `pipeline` 后端（禁 vlm/hybrid）。
- 标准库：`sqlite3`、`argparse`、`hashlib`、`json`、`subprocess`、`importlib.metadata`。

### 1.10 状态与输出文件位置

锚点由 `_workspace_root()` 决定：默认仓库根；环境变量 **`STUDY_KB_ROOT`** 可整体重定向。

| 路径 | 内容 | git |
|------|------|-----|
| `pipeline-workspace/state/study-kb.sqlite` | 业务状态机 SQLite（单库） | gitignore |
| `pipeline-workspace/staging/<src>/` | 每源预处理产物（source.md / blocks / windows / workorder / assets / evidence / arbitration / digest） | gitignore |
| `pipeline-workspace/snapshots/<src>/` | 就地 merge 前回滚快照 | gitignore |
| `pipeline-workspace/query-sessions/<run_id>/` | kb-query / kb-save 会话 | gitignore |
| `pipeline-workspace/skill-evolution/` | `backlog.yaml`（只计 open proposals，每簇带 `last_seen`）+ 候选提案 + audit | gitignore |
| `pipeline-workspace/reports/` | 执行/审计报告；`reports/postmortem/<src>-<date>.md` 是 `kb-postmortem` 的固定产物子目录 | gitignore |
| `wiki/` | 生成的 Obsidian vault（成品） | gitignore |
| `books/<name>/input/` | 用户放原始来源处 | gitignore（`books/*`） |
| `tmp/resume.log` | 续跑脚本日志 | gitignore |

---

## 2. 仓库结构地图

```text
pdf-to-study-kb/
├── CLAUDE.md                    # Claude Code 项目真值（架构/约束/协作）
├── AGENTS.md                    # Codex 项目真值（与 CLAUDE.md 内容对等）
├── README.md                    # 用户向总文档（中文）
├── LICENSE
├── requirements.txt             # 依赖：pymupdf + pyyaml + pytest（MinerU 注释为可选）
├── pytest.ini                   # 测试 marker 注册（fast/cli/slow/skill/realbook）
├── .gitignore                   # 忽略 wiki/ pipeline-workspace/ books/* tmp/ 等运行时态
├── .gitattributes
│
├── scripts/                     # ⭐ 全部业务逻辑（零 LLM）
│   ├── pipeline.py              # 唯一 CLI 入口（44 子命令）
│   ├── state_store.py           # 状态机 SQLite（7 张表：sources/source_stage_runs/artifacts/
│   │                            #   work_orders/source_locks/review_proposals/ingest_progress）
│   ├── locks.py                 # 单 vault 写锁
│   ├── source_profile.py        # L1 逐页 profile + needs_vision
│   ├── source_convert.py        # L1 dispatcher（选后端 + 持久化）
│   ├── source_backends/
│   │   ├── __init__.py          # 后端注册（get_backend / get_backend_by_name）
│   │   ├── pymupdf_backend.py   # fast path（route B：难页整页 PNG）
│   │   ├── markdown_backend.py  # Markdown 原文即 source.md
│   │   ├── mineru_backend.py    # 结构化后端（subprocess，pipeline-only）
│   │   └── mineru_runner.py     # MinerU 子进程入口（进程隔离）
│   ├── source_artifacts.py      # L2 blocks/parse_report/reconciliation 契约
│   ├── chaptering.py            # L2/L3 PDF 书签 → 章节
│   ├── source_audit.py          # L1/L4 双审互检
│   ├── arbitration.py           # L4 仲裁确定性半（证据/候选/物化/闭环）
│   ├── windowing.py             # L3 processing windows
│   ├── workorder.py             # L3/L4 ingest 事务契约
│   ├── preflight_eval.py        # L4 验收门（12 项 check）
│   ├── concept_store.py         # 概念归一唯一入口 + registry 派生（aliases.md 退休/清理）
│   ├── promotion.py             # 跨域提升
│   ├── wiki_gate.py             # 收尾 lint（order/safety/provenance）+ promote + index
│   │                            #   + quiz-index / propositions 两个派生阅读层
│   ├── page_rules.py            # 页正文规则原语（REQUIRED_SECTIONS 键保留值已清空；
│   │                            #   REQUIRED_FRONTMATTER 接棒溯源；quiz/命题提取原语）
│   ├── mdpage.py                # frontmatter 读写
│   ├── ingest_guards.py         # 写前守卫
│   ├── snapshots.py             # 快照 + 回滚
│   ├── graph_model.py           # ⭐ KG v2.0：published → 图模型（topic_membership 骨架）
│   ├── graph_analysis.py        # ⭐ KG v2.0：Louvain 社区分析
│   ├── graph_data.py            # ⭐ KG v2.0：graph-data.generated.json 契约
│   ├── graph_schema.py          # ⭐ KG v2.0：GRAPH_VERSION=2 schema
│   ├── graph_html.py            # ⭐ KG v2.0：力导向交互 HTML（点击节点跳 obsidian://）
│   ├── graph_lint.py            # ⭐ KG v2.0：graph-data/HTML 校验（fail-hard + warn-only）
│   ├── query_session.py         # query-session 契约
│   ├── thresholds.py            # 集中阈值（27 个 STUDY_KB_*，env 可覆盖）
│   ├── install_mineru.py        # 可选 MinerU 安装器
│   └── resume-ingest.ps1        # 无人值守续跑（OS 调度）
│
├── .claude/skills/<name>/SKILL.md   # 11 个对话式 skill（Claude 读）
├── .agents/skills/<name>/SKILL.md   # 同 11 个（Codex 读，与上者字节对等）
│       skill 列表（CLAUDE.md §5 顺序）：ingest / kb-query / kb-save / kb-review / kb-qa /
│                  kb-postmortem / pipeline-doctor / wiki-lint-semantic /
│                  source-preflight / source-xray / skill-evolve
│       只有 ingest 含 references/（其余 10 个技能都是单文件 SKILL.md）：
│           preflight / arbitrate / content-routing / write-pages / synthesis / finish-lint
│
├── docs/skill-runtime/          # skill 运行时协议（按需加载，5 个文件）
│   ├── routing.md               # ⚠ 命令路由决策树（把请求分派到哪个技能），与"内容路由"是同名不同物
│   ├── schema.md                # 页类型 + frontmatter 规则；两套运行时模板/两阶段发布/REQUIRED_SECTIONS 为空
│   ├── concept-resolution.md    # resolve_or_create_concept 协议
│   ├── save-back-policy.md      # kb-save 准入门（至少一项成立 + evidence_refs 非空）
│   └── skill-standard.md        # skill 工程标准（thin skill + thick CLI，九段契约）
├── docs/user-guide.md           # 用户使用说明（本文档的姊妹篇）
├── docs/developer-guide.md      # 本文档
│
├── templates/                   # ⚠ 只剩 2 个运行时会读的页型模板（2026-07-09 起，见 §5.3）
│   ├── concept.md                #   resolve-concept 建概念页骨架
│   └── overview.md               #   init-vault 落 overview.md 种子
│   #   （原 source/lesson/topic/comparison/synthesis 五个模板已删——D-4 后不再有强制小节标题，
│   #    这些模板早已只剩测试对象、无运行时读取者）
│
└── tests/                       # 确定性测试 = 可执行规格（46 个测试文件，
                                  #   含 test_graph_{model,analysis,data,html,lint,v2_e2e}
                                  #   + 运营层三件 test_ops_metrics_cli/test_doctor_cli/test_staging_clean_cli）
```

> **注**：`books/`、`wiki/`、`pipeline-workspace/` 在干净仓库中**不存在或为空**——它们是运行时按机生成的、
> gitignore 的状态目录。项目交付时为空库。

---

## 3. 命令实现映射表

> 每条命令 → 实现文件/函数、输入、产物、副作用、覆盖测试。末列"实现状态"标注实现与测试的完备度。

### 3.1 安装与环境

| 操作 | 用户命令/动作 | 实现文件 | 函数/类/入口 | 输入 | 输出/产物 | 副作用 | 测试/证据 | 实现状态 |
|---|---|---|---|---|---|---|---|---|
| 克隆 + 装依赖 | `pip install -r requirements.txt` | `requirements.txt` | — | — | 安装 pymupdf/pyyaml/pytest | 装包 | `requirements.txt` 第 10-18 行 | 准确 |
| 自检依赖 | `python -c "import fitz, yaml; ..."` | — | `fitz.VersionBind` | — | 打印版本 | 无 | README §安装 | 准确 |
| 装 MinerU（可选） | `python scripts/install_mineru.py [--dry-run]` | `install_mineru.py` | `main`、`select_wheel`、`detect_driver_cuda` | `nvidia-smi` | 装 `mineru[core]` + 匹配 CUDA torch | 装包/替换 torch | `tests/test_install_mineru.py` | 准确 |

### 3.2 状态与维护命令

| 操作 | 命令 | 实现函数 | 输入 | 输出/产物 | 副作用 | 测试 | 实现状态 |
|---|---|---|---|---|---|---|---|
| 列出各源阶段/状态 + 锁 | `status` | `cmd_status` → `state_store.status_rows` + `locks.get` | state db | 终端打印 | 无 | `test_pipeline_status.py` | 准确 |
| 列出各源下一步动作 | `next` | `cmd_next` → `state_store.next_actions` | state db | 终端打印 | 无 | `test_pipeline_status.py` | 准确 |
| 建 vault 脚手架 | `init-vault` | `cmd_init_vault` | `templates/overview.md` | `wiki/` 目录 + overview/log/purpose + `.obsidian/{graph,app}.json` | 写盘（幂等不覆盖） | `test_vault_init_cli.py` | 准确 |
| 回收 stale 锁 | `unlock [--ttl 1800]` | `cmd_unlock` → `locks.break_stale` | state db | 释放锁 | 删锁行（活锁拒绝） | `test_locks.py` | 准确 |
| 崩溃阶段标 failed | `fail --source --stage --error` | `cmd_fail` → `state_store.fail_stage` | state db | 标记 failed | 改状态 | `test_state_store.py` | 准确 |
| 重建 registry | `rebuild-registry` | `cmd_rebuild_registry` → `concept_store.build_registry/write_registry` + `remove_stale_aliases` | 概念页 frontmatter | `_registry.yaml`（aliases.md 已退休，若残留则删除） | 写派生文件 | `test_concept_store.py` | 准确 |
| 重建知识图谱 v2.0 | `rebuild-graph` | `cmd_rebuild_graph` → `graph_model/graph_analysis/graph_data/graph_html/graph_lint` | published 页 | `graph-data.generated.json` + `knowledge-graph.generated.html`（力导向、Louvain、点击跳 `obsidian://`） | fail-hard（退出 2） | `test_graph_v2_e2e.py` 等 6 个 | 准确 |
| 校验知识图谱 | `graph-lint` | `cmd_graph_lint` → `graph_lint.validate_graph_data` | graph-data + html | 报告 → `pipeline-workspace/reports/graph-lint-*.md` | errors → 退出 2 | `test_graph_lint.py` | 准确 |
| **重建自测题库索引** | `rebuild-quiz` | `cmd_rebuild_quiz` → `wiki_gate.build_quiz_index`/`write_quiz_index` | published 页 `[!question]` 题干 | `quiz-index.generated.md`（按 domain 分组、只列题干+回链、不含答案） | 写派生文件；lint 收尾自动重建、publish-isolated | `test_wiki_gate.py`、`test_lint_republish_cli.py`、`test_page_rules.py` | 准确 |
| **重建命题总表** | `rebuild-propositions` | `cmd_rebuild_propositions` → `wiki_gate.collect_propositions`/`build_propositions_index`/`write_propositions_index` | published 页 `**命题（名）**：…` | `propositions.generated.md`（按 domain 分组、名字即锚点、v1 不编号）；域内重名走 `duplicate_proposition_names` 软警告 | 写派生文件；lint 收尾自动重建、publish-isolated | 同上 | 准确 |

### 3.3 预处理命令

| 操作 | 命令 | 实现函数 | 输入 | 输出/产物 | 失败处理 | 测试 | 实现状态 |
|---|---|---|---|---|---|---|---|
| 注册来源 | `add-source --source --domain --path --fmt {pdf,md,docx,pptx}` | `cmd_add_source` → `state_store.register_source` + `record_artifact` | 原始文件 | `sources` 行 + `raw_source` artifact | — | `test_preprocessing_cli.py` | 准确 |
| 逐页 profile | `profile --source` | `cmd_profile` → `source_profile.profile_source` | raw | `staging/<src>/pages.jsonl` | 整本扫描件打 WARN（不阻断 profile） | `test_preprocessing_cli.py` | 准确 |
| 转 Markdown + 块 | `source-convert --source [--backend auto\|pymupdf\|mineru] [--mineru-policy conservative\|aggressive] [--force]` | `cmd_source_convert` → `source_convert.convert` | raw + pages.jsonl | `source.md`+`blocks.jsonl`+`chapters.json`+`parse_report.json`+`assets/` | 扫描件走 pymupdf 未 `--force` → fail-closed；MinerU 失败落最小 report 后抛 | `test_source_convert.py`、`test_conversion_backend_cli.py` | 已实现（含 `--force`） |
| PDF 双审 | `source-audit --source [--strict]` | `cmd_source_audit` → `source_audit.audit` | source.md/blocks + raw | `reconciliation.json`+`evidence.json`+`arbitration/queue.json` | strict + MinerU 不可用 → `DualAuditUnavailable` 非零退出 | `test_source_audit.py` | 准确 |
| 看仲裁队列 | `arbitration-status --source` | `cmd_arbitration_status` | evidence.json + decisions.json | 终端打印 | 缺 evidence → 报错 | `test_arbitration.py` | 准确 |
| 物化仲裁 | `arbitration-apply --source` | `cmd_arbitration_apply` → `arbitration.materialize_*` + `render_pages_png` | decisions.json | 补整页图 + 置 needs_vision + 改 blocks/pages | 缺 decisions → 报错 | `test_arbitration.py` | 准确 |
| 改判 needs_human | `arbitration-resolve --source --page --decision {render,ignore} --reason` | `cmd_arbitration_resolve` | decisions.json | 改 decisions + audit.jsonl | reason 必填、非 needs_human 拒 | `test_arbitration.py` | 准确 |
| 生成窗口 | `windows --source [--dev-bypass]` | `cmd_windows` → `windowing.build_windows_from_blocks` | source.md/blocks/chapters | `windows.jsonl` | PDF 未双审/分歧未闭环 → fail-closed（除非 `--dev-bypass`） | `test_windowing.py` | 准确 |
| 生成 work order | `workorder --source` | `cmd_workorder` → `workorder.build_workorder` | windows + vault | `workorder.yaml` + registry 快照 | 概念页损坏 → ValueError | `test_workorder.py` | 准确 |
| L4 验收 | `preflight-eval --source [--strict] [--json <path>]` | `cmd_preflight_eval` → `preflight_eval.evaluate` | staging 全产物 | `preflight_eval.json` | strict 遇 high/fail → exit 2 | `test_preflight_eval.py` | 已实现（`evaluate()` 跑 **12 项** check，见 §8） |

### 3.4 ingest 会话支撑命令

| 操作 | 命令 | 实现函数 | 输入 | 输出/产物 | 失败处理 | 测试 | 实现状态 |
|---|---|---|---|---|---|---|---|
| 开工取锁 | `ingest-start --source` | `cmd_ingest_start` → `locks.acquire` + `ingest_guards.registry_fresh` | workorder + 锁 | 锁 + stage→ingesting | 锁被他源持有/registry 过期 → 拒 | `test_ingest_orchestration_cli.py` | 准确（幂等 resume：返回已在 ingesting） |
| 收工释放锁 | `ingest-done --source` | `cmd_ingest_done` | 锁 | stage→ingested(proposed) + 释放锁 | 未持锁 → 拒 | `test_ingest_orchestration_cli.py` | 准确 |
| 打印窗口文本 | `show-window --source --window [--plain] [--verbose]` | `cmd_show_window` | windows + source.md + pages.jsonl | 终端（含 `route-b-assets` 资产头） | 窗不存在 → 报错 | `test_ingest_orchestration_cli.py` | 已实现（含 `--plain/--verbose`） |
| 窗口记账 | `window-start --source --window --hash` / `window-done [--writes\|--writes-file --proposals]` / `window-fail --error` | `cmd_window_*` → `state_store.*_window` | 锁 | `ingest_progress` 行 + 锁心跳 | 未持锁 → 拒；`--writes` 与 `--writes-file` 同给 → 报错互斥 | `test_ingest_progress.py`、`test_doctor_cli.py` | 准确（`--writes-file` 为 2026-07-09 新增，见 §3.7b） |
| 概念归一 | `resolve-concept --mention --domain [--alias --ref-source --ref-sections]` | `cmd_resolve_concept` → `concept_store.resolve_or_create_concept` | 概念页 | merge 或新建概念页 | 概念页损坏 → 报错 | `test_concept_store.py` | 准确 |
| 写前守卫 | `check-write --source --path` | `cmd_check_write` → `ingest_guards.in_write_scope` + `can_overwrite` | workorder | ALLOW / DENY(exit 1) | 越界/human 页/hash 不符 → DENY | `test_ingest_guards.py` | 准确 |
| 页快照 | `snapshot-page --source --path` | `cmd_snapshot_page` → `snapshots.take_snapshot` | vault 页 | `snapshots/<src>/<run>/` | — | `test_snapshots.py` | 准确 |

### 3.5 收尾、提升与查询命令

| 操作 | 命令 | 实现函数 | 输入 | 输出/产物 | 失败处理 | 测试 | 实现状态 |
|---|---|---|---|---|---|---|---|
| 收尾门禁 | `lint --source <src>`；kb-save 会话发布：`lint --source kb-save --session <run_id>` | `cmd_lint` → vault preflight（`wiki_gate.vault_render_safety`）→ `wiki_gate.lint_pages` + `promote` + `_rebuild_graph_artifacts` + `write_quiz_index` + `write_propositions_index` | proposed 页 | promote→published + 重建 index/registry + 知识图谱 v2.0 + quiz-index + propositions（后三者各自 publish-isolated） + log | preflight 旧伤 → 阻断不回滚（vault-health 队列）；当前批违规 → 回滚快照 + Review-Queue + exit 非零（27 个违规标识，见 §7） | `test_lint_republish_cli.py`、`test_wiki_gate.py` | 准确 |
| 全库渲染安全门禁 | `vault-lint` | `cmd_vault_lint` → `wiki_gate.vault_render_safety(published∪proposed)` | 全 vault | 违规清单，非零退出（可 CI） | 只读，零写入 | `test_lint_republish_cli.py` | 准确 |
| 跨域提升候选 | `promotion-candidates [--propose]` | `cmd_promotion_candidates` → `promotion.find_candidates` | registry | 终端 + (可选)Review-Queue | — | `test_promotion.py` | 准确 |
| 机械提升概念 | `promote-concept --id concept.<domain>.<slug>` | `cmd_promote_concept` → `promotion.promote_to_shared` | 概念页 | 移动到 `concepts/` + 全 vault 链接重写 | 目标冲突 → 中止 | `test_concept_promotion_cli.py` | 准确 |
| query-session 契约 | `check-session --id <run_id> [--saved]` | `cmd_check_session` → `query_session.check_session` | session 目录 | ok / 问题列表 | 缺文件 → exit 非零 | `test_query_session*.py` | 准确 |

### 3.6 skill 自进化命令

| 操作 | 命令 | 实现函数 | 输入 | 输出/产物 | 失败处理 | 测试 | 实现状态 |
|---|---|---|---|---|---|---|---|
| 聚类失败信号 | `skill-mine` | `cmd_skill_mine` → `_refresh_skill_backlog` | `review_proposals`（**只统计 `status='open'` 行**） | `skill-evolution/backlog.yaml`（每簇附 `last_seen` = 该簇 open 行 max `created_at`） | — | `test_skill_evolution.py`、`test_ops_metrics_cli.py` | 准确（lint 失败时自动刷新；已修复信号经 `proposals-resolve` 退休后不再计入） |
| 候选门 | `skill-gate --candidate [--base HEAD]` | `cmd_skill_gate` → `_skill_gate_check` | git diff | PASS / DENY(exit 1) | 越界改 tests/ 或 pytest 不过 → DENY | `test_skill_evolution.py` | 准确 |
| 登记候选提案 | `skill-stage --candidate [--base]` | `cmd_skill_stage` | git diff | `candidates/<n>/proposal.diff` + audit | — | `test_skill_evolution.py` | 准确 |
| 人工采纳 | `skill-adopt --candidate [--base]` | `cmd_skill_adopt` | git diff | 重跑 gate + commit 双树 | gate 不过 → 拒不提交 | `test_skill_evolution.py` | 准确 |

### 3.7 辅助命令（不在主流程，运维/增量用）

| 命令 | 实现函数 | 作用 | 测试 |
|---|---|---|---|
| `apply-obsidian-style` | `cmd_apply_obsidian_style` | 写 `wiki/.obsidian/snippets/study-kb.css` + merge `appearance.json` 启用片段（纯配置层，零内容改动，幂等） | 无专门测试 |
| `reopen` | `cmd_reopen` → `state_store.reopen_source` | 重开已收尾来源做增量补充（重建 workorder + 状态机回 `workorder_ready`） | `test_lint_republish_cli.py`、`test_state_store.py` |
| `sync-assets` | `cmd_sync_assets` → `_sync_assets` | 把本源 staging 难页 PNG 同步进 `wiki/assets/<src>/`（预处理/reopen 会自动调用） | `test_source_convert.py` 间接 |

### 3.7b 运营层四件套（2026-07-09 新增，`feat/ops-layer-postmortem` 分支，已并入 `main` commit `7731fc8`/`8cd4db0`）

> 背景：`review_proposals` 此前只有 INSERT、没有 UPDATE 路径，`skill-mine` 的 backlog 会被已修复的旧信号
> 单调污染；每本书发布后的复盘、状态机故障修复都靠手工/手写 SQL，无审计留痕。四件套补齐这条运营路径。
> **三个改状态/删文件的命令一律默认 dry-run**（不带 `--apply` 时只打印计划、不改任何东西）。

| 命令 | 实现函数 | 作用 | 测试 |
|---|---|---|---|
| `ingest-stats --source [--json]` | `cmd_ingest_stats` → `state_store.source_stats` | 只读代理指标：窗口成败/阶段耗时与重跑次数（同 stage 多行 = 重跑）/`lint_failures`（stage=lint 且 status=failed 的行数，≈回滚次数）/`pages_estimate`（finished 窗 write_set_json 去重计数）/`proposals_by_kind`（open/resolved 分布）。窗口耗时口径是**最后一次尝试**（`start_window` UPSERT 覆盖 `started_at`，非累计）；token/费用拿不到不伪造 | `test_ops_metrics_cli.py` |
| `proposals-resolve --id/--signature [--source] [--all-matching] [--apply]` | `cmd_proposals_resolve` → `state_store.resolve_review_proposals` | 失败信号退场：`--id`（可重复）精确选行，或 `--signature` 按 kind 批量（批量落库须显式 `--all-matching`，防误伤同类未修复的行）；只把 `status='open'` 的行改成 `'resolved'`；`--apply` 成功后自动重跑一次 `_refresh_skill_backlog` 让已修复簇立即退场 | `test_ops_metrics_cli.py` |
| `reset-source --source --to {registered,profiled,converted,windowed,workorder_ready} [--apply]` | `cmd_reset_source` → `state_store.reset_source` | forward-only 状态机的确定性回退出口：只删 `source_stage_runs` 中回退目标之后阶段的行（这是 `should_run_stage` 的缓存来源，不删则同 `input_hash` 永远 `[skip]`）+ 插一条 `stage='reset'` 审计行；**不动** `ingest_progress`/`artifacts`/`work_orders`/`review_proposals`/staging 文件；拒绝 `running` 状态或持锁的 source；回退目标限定在预处理段（`RESETTABLE_TARGETS`），ingest 段请用既有的 `reopen` | `test_doctor_cli.py` |
| `window-done ... --writes-file <path.json>` | `cmd_window_done`（新增分支） | 从 UTF-8 文件读 JSON 数组写入 `write_set_json`，绕开 Windows `conda run` 吞双引号导致 `--writes '[...]'` 变成非法 JSON 的老坑；与 `--writes` 显式互斥（同给报错，不静默优先） | `test_doctor_cli.py` |
| `staging-clean --source [--apply]` | `cmd_staging_clean` → `_classify_staging`/`_assets_synced` | staging 目录三分类治理：`keep`（审计件 + `arbitration/` + `assets/` + 续跑必需的 `source.md`/`blocks.jsonl`/`windows.jsonl` 等，永不删）/`regen`（`mineru_raw/`/`audit/`/`diag/`/`dump_*.txt`，`--apply` 才删）/`unknown`（**fail-safe 一律保留并列出**，防误删看不懂的文件）；`--apply` 双护栏：source 必须 `lint/published` + staging 的图片与 `wiki/assets/<src>/` 逐文件 sha256 对齐，两条有一条不满足直接拒绝 | `test_staging_clean_cli.py` |

对应两个新技能：`kb-postmortem`（编排 `status`→`ingest-stats`→**先存旧 backlog 快照再 `skill-mine` 再 diff**→读 `digest.md`→写报告，只出建议不代执行）、`pipeline-doctor`（症状→白名单 CLI 配方表，`SKILL.md` 明文禁手写 SQL / 禁直接改数据库文件）。

### 3.8 流程性叙述映射

| 操作 | 实现锚点 |
|---|---|
| 「填学习目标 `wiki/_meta/purpose.md`」 | `init-vault` 落空模板；ingest SKILL.md §2 读取；优先级高于内容路由/装置预算等 advisory 建议 |
| 「一句话入库（ingest）」 | `ingest` skill 编排 add-source→...→lint |
| 「按章内容路由（理论/方法/案例/参考/观点）」 | `.claude/skills/ingest/references/content-routing.md`；路由表写进 `staging/<src>/digest.md`；**零 CLI 校验**，纯 LLM 判断记录在纸面上；偏离走 `[routing-deviation]` 标记，供 skill-evolve 修订路由表本身的证据 |
| 「写作装置预算（推导折叠/案例解剖/定位段/具名命题）」 | `.claude/skills/ingest/references/write-pages.md`「Phase D」段；默认零装置，推导折叠不计预算、鼓励用，其余装置一页至多一种；**无对应 lint 规则**，纯写作纪律 |
| 「具名命题格式抽取」 | `page_rules.extract_propositions`（正则 `\*\*命题（[^）]{1,24}）\*\*[：:]\s*(.+)`）→ `rebuild-propositions` |
| 「自测题干抽取」 | `page_rules.extract_question_stems` → `rebuild-quiz` |
| 「在 Obsidian 阅读」 `wiki/` | overview.md + `.obsidian/graph.json` + `knowledge-graph.generated.html` + `quiz-index.generated.md` + `propositions.generated.md` |
| 「source-preflight 零成本验证」 | `source-preflight` skill（只跑预处理链 + 验收，不写库） |
| 「中断续跑：说『继续』」 | `pipeline.py next` + digest `## RESUME` 块（digest 由 LLM skill 维护，非 CLI）；`next --source <src> --resume-packet` 输出结构化 `RESUME_PACKET v1`（`resume_packet.py`，fail-closed：RESUME 过期/digest 或 workorder 缺失即拒绝出包） |
| 「无人值守续跑」 `resume-ingest.ps1` | OS 调度 + resume packet 落盘 `tmp/resume-packet.txt` + 单行 prompt 引用（多行参数会被 Windows `.cmd` shim 截断）；有界 `-MaxWindows` 默认 4；packet 拿不到则记日志退出，不唤起 agent |
| 「发布后复盘 / 失败信号销账 / 状态机卡死修复 / staging 清理」 | `kb-postmortem` / `proposals-resolve` / `pipeline-doctor`+`reset-source` / `staging-clean`（§3.7b） |
| 「STUDY_KB_ROOT 重定向」 | `_workspace_root()` 读 env |
| 「测试分层 fast/cli/slow/skill/realbook」 | `pytest.ini` + `tests/conftest.py`（准确） |
| 「概念去重唯一入口 resolve-concept」 | `concept_store.resolve_or_create_concept`（准确） |
| 「两阶段发布」 | proposed → lint → published（准确） |
| 「覆盖保护三条件」 | `ingest_guards.can_overwrite`（准确） |
| 「fail-closed lint（order/safety/provenance，共 27 个违规标识）」 | `wiki_gate.lint_pages`/`render_safety_violations` + `lint_risk_traceability` + `pipeline.cmd_lint` 自身；vault preflight 与当前批回滚事务隔离；**正文小节标题不是门禁**（D-4） |

---

## 4. 内部执行流程

### 4.1 环境 / setup 流程

1. `conda create -n study-kb python=3.12` → `conda activate study-kb` → `pip install -r requirements.txt`。
2. 解释器须能 `import fitz, yaml`。状态库锚点默认仓库根，`STUDY_KB_ROOT` 可改。
3. 可选 `python scripts/install_mineru.py`：先 `pip install -U mineru[core]`，再 `nvidia-smi` 探 CUDA，
   `select_wheel` 选 ≤ 驱动 CUDA 的最新 torch wheel，`--no-deps` 把 CPU torch 替换为 `+cuXXX`；无 GPU 留 CPU。

### 4.2 初始化流程（`init-vault`，幂等）

`cmd_init_vault` 建目录 `_meta / domains / concepts / topics / comparisons / synthesis / sources /
assets / Review-Queue / .obsidian`，落种子：`overview.md`（取自 `templates/overview.md`，
`status: published`）、`log.md`、`_meta/purpose.md`、`.obsidian/graph.json`（按页面 type 着色）、
`.obsidian/app.json`。**已存在文件绝不覆盖**（`if not target.exists()`）。`templates/` 目录现在**只有
`concept.md`（`resolve-concept` 建概念页时读）与 `overview.md`（这里读）两个运行时会读的模板**；原
source/lesson/topic/comparison/synthesis 五个模板已在 `c52d1ab` 删除（D-4 后不再有强制小节标题，这些
文件早已只剩测试对象、无运行时消费者）。

### 4.3 输入注册 / 添加流程（`add-source`）

`register_source` 向 `sources` 表 `INSERT OR IGNORE`（`current_stage="registered"`、
`current_status="done"`）；`record_artifact` 记 `raw_source`（含 raw 文件 sha256）。

### 4.4 文档 / PDF / Markdown 转换流程

1. `profile`：`profile_source` 按 fmt 分支——`md` 视为单页；`pdf` 用 PyMuPDF 逐页（先廉价预扫，整本扫描件早退）；
   `docx/pptx` 返回空 pages（无轻量后端，auto 据 fmt 直接选 mineru）。每页算 `formula_symbols / n_draw /
   n_tables / image_count / needs_vision_reason / vision_tier`。
2. `source-convert`：`select_backend(fmt, pages, backend, policy)` 选 `pymupdf / markdown / mineru`；
   `convert` 调后端 → `classify_source` 写 `source_type / backend_reason / dual_audit_required` →
   `_assign_chapter_ids` 给块映射章 → 落 `source.md / blocks.jsonl / chapters.json / parse_report.json`
   → `_sync_assets` 把难页 PNG 同步进 `wiki/assets/<src>/`。
   - **PyMuPDF 后端**：每页一个 `type=text` 块；难页 `get_pixmap(matrix=Matrix(3,3))` 渲整页 PNG + 写
     `asset_path/risk_flags`；`chapters_from_toc(doc.get_toc())` 切章。
   - **Markdown 后端**：原文即 source.md；按 `_sections` 出 section 块。
   - **MinerU 后端**：`_run_mineru` 在子进程跑 `mineru_runner`（`do_parse(backend="pipeline")`）→
     `normalize_content_list` 归一块（table→`t{n}`、image→`f{n}`，跨页续表共享 element_id）→
     `parse_middle_json` 算 per-page 识别置信度（低分页打 `ocr_low_confidence`）→ `render_source_md`
     生成统一 source view。

### 4.5 解析 / 预处理（双审 + 仲裁）流程

1. `source-audit`：`audit` 对 born-digital PDF 跑 MinerU 复读（`_default_mineru_review`，子进程）→
   `reconcile` 逐页比对 table/figure/formula 存在性 → 写 `reconciliation.json`。
   同批 `_write_evidence_and_queue`：`build_evidence_model` 出逐页证据 + 候选；`assess_risks` 打 hard/soft
   风险旗标；`apply_nonblocking_risk_flags` 把非阻断旗标并进 blocks；`build_packets` + 渲候选页图 → `arbitration/queue.json`。
2. **agent 仲裁**（在 skill 流里，唯一的预处理 LLM）：读 queue 包，写 `arbitration/decisions.json`
   （`render / ignore / needs_human`，结构化）。
3. `arbitration-apply`：`render_pages_png` 补整页图；`materialize_blocks/pages` 置 `asset_path` +
   `needs_vision`；`_apply_resolutions` 回写 evidence + audit。**须在 `windows` 之前跑**。
4. `windows`：两道 fail-closed 闸门——闸门 B（PDF 必须已 source-audit，reconciliation+evidence+queue 三件齐）、
   闸门 A（`arbitration.windows_blockers`：任一候选未仲裁 / render 未物化 / needs_human / ignore 缺因 → 拒构窗）。
   过闸后 `build_windows_from_blocks` 按 section 切窗、原子块整块打包。
5. `workorder`：`build_workorder` 重建 registry（保证新鲜）、算 `write_scope` glob、快照概念页/overview/log/source/lessons。
6. `preflight-eval --strict`：`evaluate` 跑 12 项 check，`check_dual_audit` + `check_evidence_bundle` 是 strict 关键门。

### 4.6 切块 / windowing 流程

`build_windows_from_blocks`：`_sections_from_blocks` 把连续同 `heading_path` 聚成 section；含原子块
（table/image/chart）的 section 走 `_pack_blocks`（**任何块不切到两窗，长表不切**），纯文本 section 走
`_slice_section`（token≈char 滑窗 + overlap）。每窗回挂 `block_ids / page_start-end / contains / assets /
risk_flags / source_refs / chapter_ids`，注入 `source_id` + 据 page_start 查 `chapter_title`。
无 blocks 时退回 `build_windows`（char 窗，`mode="chars"`，`degraded=True`）。

### 4.7 内容路由与写作装置流程（advisory，零 CLI，2026-07-08 新增）

1. LLM 读完 `chapters.json` 后，据 `ingest/references/content-routing.md` 的 5 分类（理论/方法/案例/
   参考/观点）逐章判断写法取向，写进 `staging/<src>/digest.md` 的「路由表」——这是**纯文档记录**，确定性层
   既不计算也不校验它。
2. 实际写窗口时若判断路由标签不合适，允许直接按内容写，**但须**在该窗口的 digest 条目追加
   `[routing-deviation] chapter=<ch> 推荐=<原类型> 实际=<实际写法> 原因=<一句话>`。
3. 偏离标记会被 `skill-mine` 后续可能纳入信号聚类（若同一原因反复出现），成为人工判断"要不要修订路由
   表本身"的证据——`content-routing.md` 自称"活文档、防固化条款"。
4. 写作装置预算（同一份文档 write-pages.md「Phase D」）与路由正交独立：路由管"这一章倾向什么写法"，
   装置预算管"这一页最多用几种修辞/结构手法"。两者都不接入 lint，`purpose.md` 的优先级高于两者。

### 4.8 测试与验证流程

见 §7。

### 4.9 输出查看流程

- `status` / `next`：看各源进度与下一步。
- `show-window --source --window`：看某窗源文本 + 难页资产头。
- `preflight-eval --json <path>`：导出验收 JSON。
- `ingest-stats --source [--json]`：看某源的运营代理指标（窗口/耗时/返工/违规分布）。
- Obsidian 打开 `wiki/`：从 `overview.md` → topic → concept 三层入口；浏览器打开
  `knowledge-graph.generated.html` 看力导向知识图谱；`quiz-index.generated.md` 做自测复习；
  `propositions.generated.md` 看全库承重结论清单。

---

## 5. 数据契约与生成产物

### 5.1 状态机 SQLite（`pipeline-workspace/state/study-kb.sqlite`）

由 `state_store.SCHEMA` 创建，**7 张表**。**谁创建**：`add-source` 起各 `cmd_*`；**稳定产物**（机器状态真值）。

| 表 | 字段 | 消费方 |
|----|------|--------|
| `sources` | `source_id, domain, format, added_at, current_stage, current_status` | status/next/各阶段守卫 |
| `source_stage_runs` | `id, source_id, stage, status(running/done/failed), started_at, finished_at, input_hash, output_hash, error` | `should_run_stage`（幂等跳过）、fail、`reset_source`（删下游行 + 插 reset 审计行） |
| `artifacts` | `id, source_id, kind, path, sha256, created_at` | `_raw_path`、workorder |
| `work_orders` | `source_id, path, registry_hash, write_scope_json, created_at` | ingest-start、check-write |
| `source_locks` | `scope, holder, pid, started_at, heartbeat_at` | 并发互斥、unlock、`reset-source` 拒绝持锁 source |
| `review_proposals` | `id, source_id, target_path, kind, diff_path, reason, created_at, status(open/resolved)` | kb-review、skill-mine（只统计 open）、`proposals-resolve`（open→resolved 唯一写路径） |
| `ingest_progress` | `id, source_id, window_id, input_hash, started_at, finished_at, status, write_set_json, proposal_set_json, error`，`UNIQUE(source_id,window_id)` | 断点续跑、lint 归属、`ingest-stats` 聚合窗口/页数代理指标 |

**阶段顺序**（`STAGES` / `NEXT`，单向）：`registered → profiled → converted → windowed → workorder_ready →
ingest_waiting → ingesting → ingested → lint`。完成 `ingested` 置 `proposed`，完成 `lint` 置 `published`。
`lint/failed` 可回 `ingest_waiting`（修复后重 ingest）；`reopen_source` 把已收尾源重置回 `workorder_ready`；
`reset_source` 把**预处理段**（`registered..workorder_ready`）任一 source 确定性回退到更早阶段（ingest 段
请用 `reopen`，见 §3.7b）。

### 5.2 预处理产物（`pipeline-workspace/staging/<src>/`）

| 产物 | 创建者 | 字段/形状 | 消费方 | 性质 |
|------|--------|-----------|--------|------|
| `pages.jsonl` | `profile` | 每行 `{page,text_len,formula_symbols,image_count,n_draw,n_tables,is_code,has_caption,eq_lines,numeric_grid,needs_vision_reason[],vision_tier,needs_vision}` | convert/audit/show-window | 中间产物 |
| `source.md` | `source-convert` | 主顺读文本（PyMuPDF 含 `<!-- page N -->` 标记）。**预处理绝不重写它** | ingest LLM（show-window） | 稳定产物 |
| `blocks.jsonl` | `source-convert` | `SourceBlock`：`block_id,type,text,page,char_start,char_end,text_level,heading_path,asset_path,risk_flags[],source_ref(p{NNNN}#{bid}),chapter_id,element_id` | windowing/preflight | 稳定产物（`ARTIFACT_VERSION=6`） |
| `chapters.json` | `source-convert` | `{index,chapter_id,title,level,page_start,page_end}` | windowing（chapter_title）、ingest（全书图 + 内容路由判断输入） | 稳定产物（导航脊柱） |
| `parse_report.json` | `source-convert` | `selected_backend,source_type,backend_reason,dual_audit_required,routing_advice,mineru_status,page_count,...counts,low_confidence_pages` | audit/preflight/workorder/lint | 建议性报告 |
| `assets/p{NNNN}.png` | `source-convert` / `arbitration-apply`（PyMuPDF 难页/仲裁补图）；MinerU 图片 | 整页 PNG（zoom=3）或 MinerU 抠图 | ingest 读图（route B）；同步进 `wiki/assets/` | 用户可见输出 |
| `reconciliation.json` | `source-audit` | `primary_backend,review_backend,review_status,dual_audited,production_accepted,degraded,disagreements[],pages_cross_checked[],missing_evidence[]` | `check_dual_audit` | 稳定产物（审计证据） |
| `evidence.json` | `source-audit` | `pages{},initial_needs_vision,reviewer_structural,candidates[],soft_risk_pages[],risk_flags_by_page,final_hard_pages` | 仲裁/`check_evidence_bundle` | 中间产物 |
| `arbitration/queue.json` | `source-audit` | `packets[]`（每候选页：disagreement_kinds/risk_flags/pymupdf_text_excerpt/mineru_structural/page_image） | agent 仲裁 | 中间产物 |
| `arbitration/decisions.json` | **agent 写**（skill 流） | `decisions[]`：`{page,decision(render/ignore/needs_human),reason}` | apply/windows 闸门/closure | 中间产物（唯一非 CLI 写的预处理文件） |
| `arbitration/audit.jsonl` | apply/resolve | append-only 裁决审计 | 人工核查 | 审计 |
| `windows.jsonl` | `windows` | 见 §4.6 字段 | ingest（show-window）/preflight | 稳定产物（`WINDOWING_VERSION=5`） |
| `workorder.yaml` | `workorder` | `write_scope[],registry{hash},concept_pages_snapshot[],other_pages_snapshot[],source{...}` | ingest-start/check-write | 稳定产物（事务契约） |
| `preflight_eval.json` | `preflight-eval` | `{checks[],summary{ok,warn,fail}}` | CI / 人工 | 建议性报告 |
| `digest.md` | **ingest skill（LLM）写** | 顶部含 `## RESUME` 块（完成后改名 `## DONE`）+ 一张「路由表」（内容路由，advisory；"弱化"行禁裸"跳过"须附理由）+ `[routing-deviation]` / `[window-skip]` 标记 + 跨窗滚动摘要 | 续跑定位（恢复三读=chapters.json+RESUME+write-pages.md）；`kb-postmortem` 读取偏离标记 | 外部记忆（非 CLI 产物） |

### 5.3 Vault 产物（`wiki/`，用户可见输出）

| 页/文件 | 创建者 | type/frontmatter | 备注 |
|---------|--------|------------------|-------------|
| `domains/<d>/lessons/*.md` | ingest（LLM） | `lesson` | 降级可选层；主题命名，非章节复述；须干净散文（无裸 E-ID、脚注配对） |
| `domains/<d>/concepts/*.md` | ingest 经 resolve-concept | `concept` | `templates/concept.md` 是散文式种子（占位指引+正确嵌套的自测示例；2026-07-11 起旧固定小节骨架已废除，成套复活会被 `legacy-concept-scaffold` 阻断） |
| `concepts/*.md`（shared） | promote-concept | `concept`(scope=shared) | 同上 |
| `concepts/_registry.yaml` | 收尾 CLI 派生 | — | `canonical_id → {canonical_name,aliases,scope,domain,page_path}` |
| `topics/*.md` | ingest（LLM） | `topic` | 概念之上的导航分类层；无运行时模板（已删），结构由 purpose.md + 内容决定 |
| `comparisons/*.md` | ingest（LLM） | `comparison` | 横向对比；同上，无运行时模板 |
| `synthesis/*.md` | ingest / kb-save | `synthesis` | 深度综合；同上，无运行时模板 |
| `sources/<src>.md` | ingest（LLM） | `source` | 每来源摘要；同上，无运行时模板；**必须存在**，缺失阻断发布（`source-page-missing`） |
| `overview.md` | init-vault 种子（`templates/overview.md`）→ ingest 增量 | `overview` | vault 入口综合页；仍含占位符时新概念产出会阻断发布（`overview-seed`） |
| `index.generated.md` | 收尾 CLI 派生 | — | 只收录 published，按 type 分组 |
| `graph-data.generated.json` | lint / rebuild-graph（graph_model/graph_data） | Knowledge Graph v2.0 | 节点/边/社区（Louvain）；topic_membership 骨架 |
| `knowledge-graph.generated.html` | lint / rebuild-graph（graph_html） | 力导向交互 HTML | 点击节点跳 `obsidian://` 对应笔记；publish-isolated（失败不阻断发布） |
| `quiz-index.generated.md` | lint / rebuild-quiz（`wiki_gate.write_quiz_index`） | 零 LLM 派生 | published 页 `[!question]` 题干 + 回链，按 domain 分组，不含答案；publish-isolated |
| `propositions.generated.md` | lint / rebuild-propositions（`wiki_gate.write_propositions_index`） | 零 LLM 派生 | published 页具名命题（`**命题（名）**：…`）+ 回链，按 domain 分组，名字即锚点、v1 不编号；publish-isolated |
| `log.md` | ingest + lint 追加 | append-only | 操作日志 |
| `Review-Queue/*.md` | lint 失败 / promotion | — | 未过门禁 / 待人工决策项 |
| `_meta/purpose.md` | **用户手写**（init-vault 落空模板） | — | 学习目标与偏好；ingest 读取，优先级高于内容路由/装置预算等 advisory |

**派生文件系列**（`_registry.yaml` / `index.generated.md` / `graph-data.generated.json` +
`knowledge-graph.generated.html` / `quiz-index.generated.md` / `propositions.generated.md`）一律由收尾
CLI 从 frontmatter/正文重建，skill **绝不手写**，手改会被下次收尾覆盖。**`aliases.md` 已退休**（别名保留
在概念页 `aliases:` frontmatter，`rebuild-registry` 主动清理残留）。

**页面正文的强制项已从"小节标题"转移到"frontmatter 字段"**：`page_rules.REQUIRED_SECTIONS` 七个页型键
（`source/lesson/concept/topic/comparison/synthesis/overview`）**仍然存在，但每个键的值都已清空为
`[]`**——即不再有任何强制的逐字小节标题（D-4），正文结构交给写作 LLM + `purpose.md` 自然决定。取而代之的
是 `page_rules.REQUIRED_FRONTMATTER` + `missing_frontmatter()`：非 source 内容页（topic/comparison/
synthesis/overview 等）**必须带 `source_refs`**（溯源责任从"小节里摘录来源"转移到"frontmatter 字段完整"，
lint 违规 `frontmatter-incomplete`）。

### 5.4 缓存键版本常量（改逻辑须 +1，否则 `should_run_stage` 会误 `[skip]`）

| 常量 | 当前值 | 折进哪个阶段缓存键 |
|------|--------|---------------------|
| `source_profile.PROFILER_VERSION` | `"5"` | profiled、converted |
| `source_artifacts.ARTIFACT_VERSION` | `"6"` | converted |
| `windowing.WINDOWING_VERSION` | `"5"` | windowed |
| `chaptering.CHAPTERING_VERSION` | `"1"` | （章节切分） |
| `mineru_backend.MINERU_ADAPTER_VERSION` | `"4"` | converted |
| `thresholds.fingerprint()` | 动态（18 个 `_CACHE_KEYED` 阈值的 sha256 短指纹） | profiled、converted（env 覆盖阈值即失效缓存） |

---

## 6. 后端与依赖行为

### 6.1 解析后端选择（`source_convert.select_backend`）

| fmt + 条件 | 选定后端 | 触发依据 |
|------------|----------|----------|
| 显式 `--backend pymupdf/mineru` | 该后端（`consumed=False`） | 用户指定 |
| `auto` + `md` | markdown | fmt |
| `auto` + `docx/pptx` | mineru | fmt |
| `auto` + pdf 扫描/低文本（`_scan_or_low_text`） | mineru | `is_scanned_source` 或 `mean_text<LOW_TEXT_MEAN(100)` 或 `scan_ratio≥DENSE_RATIO(0.30)` |
| `auto` + pdf + `aggressive` + 密集（`_dense`） | mineru | dense flag 比例≥0.30 |
| `auto` + 其余 pdf | pymupdf | 默认 born-digital |

### 6.2 可用性检测与 fail-closed

- `mineru_backend.mineru_available()`：读包元数据 `importlib.metadata.version("mineru")`（**不 import MinerU**，
  保持主进程隔离）；环境变量 **`MINERU_DISABLE=1`** 可强制禁用。
- 未装 MinerU 而选定 mineru → `BackendUnavailable`（fail-closed）。
- strict 双审 + MinerU 不可用/失败（born-digital PDF）→ `DualAuditUnavailable` → CLI 非零退出。
- **绝不静默回退 PyMuPDF**：MinerU 运行失败时 `source_convert.convert` 先落最小失败 report（审计）再抛。

### 6.3 子进程调用

- MinerU：`mineru_backend._run_mineru` 用 `sys.executable` 调 `mineru_runner.py`（`subprocess.run`，
  `timeout=1800s`）。子进程内 `from mineru.cli.common import do_parse`，`backend="pipeline"`（**拒 vlm/hybrid**）。
  Windows multiprocessing spawn 需 `__main__` guard + `freeze_support`。模型源默认 `MINERU_MODEL_SOURCE=modelscope`。
- skill-gate/adopt：`subprocess` 调 `git diff` + `pytest`。
- install_mineru：`subprocess` 调 `pip` / `nvidia-smi`。

### 6.4 环境变量（全部 env）

| 变量 | 作用 | 默认/说明 |
|------|------|-----------|
| `STUDY_KB_ROOT` | 重定向状态库/staging/vault 锚点 | 默认仓库根 |
| `STUDY_KB_PYTHON` | resume-ingest.ps1 优先用的解释器路径 | 留空则用 PATH 上的 python |
| `STUDY_KB_*`（**27 个**，见 `thresholds.py`） | 覆盖检测/路由/门禁阈值；其中 **18 个**折进 `_CACHE_KEYED`（缓存指纹）、**9 个**（`TOPIC_THRESHOLD`/`LESSON_MIN_BODY`/`CONTENT_MIN_BODY`/`DETECT_RATIO_HIGH`/`RECONCILE_PAGECOUNT_TOL`/`FRAGMENT_MIN_LINES`/`FRAGMENT_SHORTLINE_LEN`/`FRAGMENT_SHORTLINE_RATIO`/`GRAPH_DENSE_DEGREE`）不折进缓存、纯门禁/观测/审计期参数 | 各有默认值 |
| `MINERU_DISABLE=1` | 强制禁用 MinerU | 未设=按可用性探测 |
| `MINERU_MODEL_SOURCE` | MinerU 模型源 | 默认 `modelscope`（可设 HF 镜像） |
| `PYTHONUTF8=1` | CJK 源/路径必设 | 每个新 shell 会话需设 |

### 6.5 Windows 注意事项

- 写盘统一 `newline="\n"`（`concept_store.write_registry` 等）——否则 Windows 默认 `\r\n` 会让 hash
  守卫误报 stale registry。
- 测试每次给新 `--basetemp`，避免 `pytest-of-Lenovo` 临时目录被句柄锁住。
- 用 `pwsh`（PowerShell 7）+ study-kb 解释器直接跑，勿经 Git Bash 驱动 PowerShell。
- `--writes-file <path.json>`（`window-done`）：Windows 上经 `conda run` 调用时命令行双引号会被吞，
  `--writes '["a.md"]'` 变成非法 JSON `[a.md]`；改把数组写进文件走 `--writes-file` 可整体绕开这个坑。

### 6.6 CPU / GPU 行为

- 必需 CLI 全程 CPU（PyMuPDF 抽取 + 渲染）。
- MinerU `pipeline` 后端适配**低显存 GPU（约 4GB）**；无 GPU 时 CPU torch 亦可跑（更慢）。
- `install_mineru.py` 据 `nvidia-smi` 的 `CUDA Version: X.Y` 选 ≤ 驱动 CUDA 的最新 torch CUDA wheel
  （候选 cu130/128/126/124/121/118），找不到匹配 → fail-closed 保留 CPU 构建。

---

## 7. 测试与验证

### 7.1 分层（`pytest.ini` marker + `tests/_tiering.py` 唯一注册表 + `tests/conftest.py` 守卫）

五层：`fast`（正向白名单 = 日常层，纯函数/直接模块测试）/ `cli`（subprocess 起真实 CLI 的 wiring）/
`slow`（完整工作流，只进全量门禁）/ `skill`（双树协议与文档契约）/ `realbook`（预留层，当前无测试）。
命令（PowerShell）：

```powershell
$env:PYTHONUTF8=1; $bt="$PWD\tmp\pt-$(Get-Random)"
python -m pytest tests -q -m fast --basetemp=$bt          # 日常层（十几秒）
python -m pytest tests -q --basetemp=$bt                  # 全量门禁（约 3 分钟）
python -m pytest tests --collect-only -q --basetemp=$bt   # 只看分层收集
```

> 测试计数以 `pytest --collect-only -q` 为准（精确数随提交漂移，不在多份文档硬编码）。

文件→tier 的映射**只**存在于 `tests/_tiering.py` 的 `FILE_TIERS`（本文不内嵌快照，以该文件为唯一真值）。
`tests/conftest.py` 在 collection 期执行 **fail-closed 归层守卫**：新增测试文件未登记、注册表条目指向
已删文件、tier 名非法、`fast` 与重 tier 组合——任一违规直接中止收集。这样日常层是显式白名单，
新测试不可能静默掉出频繁反馈层（守卫判定本身由 `test_tiering_guard.py` 的 fast 单元测试覆盖）。

### 7.2 重要测试 → 它保护的功能

| 测试文件 | 保护的功能 |
|----------|-----------|
| `test_legacy_removed.py` | **架构不回退**：禁 LangGraph / 双 SQLite / plan-units / surya 硬 OCR；确认双审架构（source_audit/mineru_backend/check_dual_audit）在位 |
| `test_command_docs.py` | **文档与协议一致**：锁定各 skill 必备协议要素、ingest 端到端编排、双审接线、续跑脚本旗标措辞（`--sandbox workspace-write` / `--dangerously-bypass...`）；真跑 pwsh + `.cmd` shim 的烟测拆在 `test_resume_ingest_smoke.py` |
| `test_tiering_guard.py` | **fail-closed 归层守卫**：`tests/_tiering.py` 注册表 vs 磁盘漂移、非法/冲突 tier 判定（daily 白名单不静默漏测） |
| `test_skill_standard.py` | 九段合约（T1）、**双树字节对等**（T2）、卫生（T3）、协议关键词完好（T4，含 `kb-postmortem`/`pipeline-doctor` 词表）、source-xray guard（T5） |
| `test_state_store.py` | 状态机单向转换、幂等跳过、reopen、window 记账 |
| `test_locks.py` | 单 vault 锁获取/释放/stale 判定/受控破锁 |
| `test_preflight_eval.py` | 12 项 check 各自行为（最大测试文件之一；CLI wiring 拆在 `test_preflight_eval_cli.py`） |
| `test_source_convert.py`（747 行） | 后端选择 + 转换契约（最大测试文件） |
| `test_conversion_backend_cli.py`（518 行） | backend/policy CLI 路由 |
| `test_wiki_gate.py`（502 行） | lint 各规则（含新增的 `table-wikilink-pipe`/`overview-seed`）、quiz/命题索引构建 |
| `test_source_audit.py` | 双审互检、reconcile、fail-closed |
| `test_arbitration.py` | 证据模型/候选/物化/闭环门 |
| `test_windowing.py` | 切窗、长表不切、char fallback |
| `test_wiki_gate_callout.py` | callout 白名单 |
| `test_concept_store.py` / `test_concept_promotion_cli.py` | 概念归一、跨域提升 |
| `test_ingest_guards.py` | 写入边界 + 覆盖保护三条件 |
| `test_graph_model.py` / `test_graph_analysis.py` / `test_graph_data.py` / `test_graph_html.py` / `test_graph_lint.py` / `test_graph_v2_e2e.py` | **知识图谱 v2.0**：图模型→Louvain 社区→graph-data 契约→力导向 HTML→lint 校验→端到端 |
| `test_skill_evolution.py` | skill-mine 聚类（open-only）、skill-gate/stage/adopt 全链路 |
| `test_ops_metrics_cli.py`（237 行，2026-07-09 新增） | `ingest-stats` 代理指标聚合、`proposals-resolve` 精确/批量退场 + `--all-matching` 护栏 |
| `test_doctor_cli.py`（176 行，新增） | `window-done --writes-file`、`reset-source` dry-run/apply/护栏 |
| `test_staging_clean_cli.py`（154 行，新增） | staging 三分类、`--apply` 双护栏、幂等 |
| `test_page_rules.py` | 页正文规则原语，含 `extract_question_stems`/`extract_propositions` |
| `test_mineru_backend.py`（386 行） | MinerU 归一（mock 注入，不依赖真实安装） |

---

## 8. 关键实现事实与易混点

> 开发时最容易记错的几处，均以源码为准。

**preflight 验收 = 12 项确定性 check**（`preflight_eval.evaluate()` 依次调用）：
`check_artifact_schema` / `check_page_coverage` / `check_window_monotonic` / `check_window_contract` /
`check_asset_traceability` / `check_dual_audit` / `check_evidence_bundle` / `check_risk_coverage` /
`check_risk_signals`（扫描·OCR） / `check_orphan_blocks`（孤儿块） / `check_source_ref_integrity` /
`check_detection_distribution`。`--strict` 遇 high/fail → exit 2。

**lint 发布门禁规则集 = 27 个违规标识**（`wiki_gate.lint_pages`/`render_safety_violations`/
`lint_risk_traceability` + `pipeline.cmd_lint` 自身，order/safety/provenance；2026-07-12 静态提取核对）：
`L1`（裸 evidence id）/ `evidence-footnote` / `source-image-embed`（D-1/G1 正文禁嵌源图）/
`frontmatter-incomplete`（G2，非 source 内容页必带 `source_refs`）/ `title-duplicate-h1` /
`formula-table-pipe` / **`table-wikilink-pipe`**（表格行内 wikilink 别名竖线必须转义为 `[[path\|alias]]`，
2026-07-08 新增） / `L6-empty-lesson` / `content-too-short` / `broken-link` /
**渲染安全四条（2026-07-11 六阶段重构，`render_safety_violations` 唯一实现，同一扫描作为
vault preflight 复检全库 published 页、`vault-lint` CLI 可独立跑）**：`callout-unknown`（类型白名单，
消费统一 callout 解析器 `page_rules.parse_callouts` 的节点+错误头）/ `callout-nested-malformed`
（块内同级 `[!type]` 头渲染成字面量、折叠答案泄漏；嵌套须 `> > [!type]`）/
`math-delimiter-nonobsidian`（`\(…\)`/`\[…\]` Obsidian 不渲染）/ `question-stem-empty`（空题干） /
`L7-synthesis-missing` / `topics-missing`（两者与 `overview-seed` 同属 ingest 阶段 E 义务，kb-save 会话
批豁免） / `placeholder-unfilled`（本轮 proposed 与已 published 页两处判定） / **`overview-seed`**
（2026-07-08 新增，防"lint 失败回滚吃掉 overview 就地编辑、重跑无人复查"） / `concepts-uncovered` /
`duplicate-canonical` / `risk-traceability`（仅 MinerU 风险源触发） / `unattributed-proposed`（孤儿
proposed 页：既无 frontmatter 归属也不在任何 write_set） / **`unaccounted-write`**（2026-07-11：归属≠记账
——本轮 proposed 的 topic/comparison/synthesis/overview 必须入台账：ingest 的窗口 `--writes` 或 kb-save 的
会话 `candidate_write_set.json`） / **`legacy-concept-scaffold`**（2026-07-11：概念页旧模板骨架标题共现
≥3 成套复活即阻断，单个自然标题合法） / **`session-candidate-missing`** / **`session-identity-mismatch`**
（2026-07-12：kb-save 会话完整性——candidate 路径缺失或页面 `save_session` 身份不符即整体 fail-closed） /
**`source-page-missing`**（本批产出 concept 但 `sources/<src>.md` 台账页不存在 → 阻断，2026-07-08 新增；
kb-save 会话批豁免）。
**发布门分两段事务隔离（2026-07-11）**：vault preflight（published 渲染旧伤 → 阻断 promote + 按
rule+path+content_hash 去重进 `Review-Queue/vault-health-*.md`，**不回滚当前批、不写 lint 阶段状态**）→
batch lint（当前批违规才回滚快照）。kb-save 发布走会话作用域：`lint --source kb-save --session <run_id>`
（先过 saved 模式 Q1 契约，candidate 集同时决定 membership 与 accounting）。
**正文小节标题不是门禁**（D-4：`page_rules.REQUIRED_SECTIONS` 七个页型键仍在，但值已清空为 `[]`，结构
交写作 LLM + `purpose.md` 决定）。

**内容路由与写作装置是 advisory，零 CLI 校验**（2026-07-08 引入）：内容路由（`ingest/references/
content-routing.md`）按 5 分类（理论/方法/案例/参考/观点）判断每章写法取向，记进 `digest.md`「路由表」；
写作装置预算（`ingest/references/write-pages.md`「Phase D」）约束正文默认零装置、推导折叠不计预算鼓励用、
其余装置一页至多一种。**两者机器都不检查**——机器只守秩序（概念去重/图谱可导航）、安全（不嵌源图/不留
占位符）、溯源（`source_refs` 完整）三类底线，正文该长什么样完全交给 LLM + `purpose.md`。偏离路由标签
须记 `[routing-deviation]`，作为后续 `skill-evolve` 修订路由分类表本身的证据（"活文档"）。

**quiz-index / propositions 两个派生阅读层**（`wiki_gate.py`，2026-07-07/08 新增）：`rebuild-quiz` /
`rebuild-propositions` 零 LLM 从 published 页正文提取 `[!question]` 题干（不含答案）与具名命题
`**命题（名）**：…`（名字即锚点，v1 不编号，域内重名软警告不阻断）；两者与知识图谱同为 **publish-isolated**
（失败只 warn、不阻断发布），lint 收尾自动重建。

**运营层四件套是"改状态/删文件三命令默认 dry-run"的统一约定**（2026-07-09 新增，`proposals-resolve` /
`reset-source` / `staging-clean`）：不带 `--apply` 一律只打印计划、零改动；`skill-mine` 现在只统计
`review_proposals.status='open'` 行，已修复信号须经 `proposals-resolve` 退场才会从 backlog 消失。

**续跑脚本 `-MaxWindows`（默认 4）**：`resume-ingest.ps1` 的 `[int]$MaxWindows` 限定单次触发处理的 window 数
并注入续跑 prompt，避免单次长会话因模型不可用整体失败。

**运维 / 阈值 env 变量**（默认见 `thresholds.py`，改动折进阶段缓存指纹）：
`STUDY_KB_ROOT`（锚点重定向）/ `STUDY_KB_PYTHON`（续跑解释器）/ `PYTHONUTF8=1`（CJK 必设）/
`MINERU_DISABLE=1`（禁 MinerU）/ `MINERU_MODEL_SOURCE`（模型源，默认 modelscope）；
**精确 27 个** `STUDY_KB_*` 检测/门禁阈值（18 个折进缓存指纹 `_CACHE_KEYED` + 9 个门禁/观测/审计专用不折进，
如 `STUDY_KB_TOPIC_THRESHOLD`/`STUDY_KB_GRAPH_DENSE_DEGREE`）。

**命名易混点：**
- `preflight_eval`（CLI 验收模块）vs `source-preflight`（只读预处理 skill）——同根不同物。
- `windows`（命令）= processing windows（读取单位），与操作系统 Windows 无关。
- **`docs/skill-runtime/routing.md`（命令路由：把用户请求分派到哪个技能）vs `ingest/references/
  content-routing.md`（内容路由：按章判断写法取向，写进 digest）——同叫"routing"但完全是两个不同机制、
  不同文件、不同层级（前者驱动技能自动触发，后者是 ingest 内部 advisory 写作协议）。**
- `evidence.json`（逐页证据）vs `reconciliation.json`（双审记录）vs `parse_report.json`（解析报告）——三者职责不同（§5.2）。
- `apply-obsidian-style` 是纯配置层命令（写学习库 CSS 观感片段），无专门测试。
- `templates/` 目录现在只有 2 个文件（`concept.md`/`overview.md`），不是旧版本的 7 个——其余 5 个已删
  （无运行时读取者）。
- `reopen`（ingest 段已收尾来源做增量补充）vs `reset-source`（预处理段确定性回退，两者作用阶段互斥，
  不可混用）。

---

*（本文档以源码为准描述当前实现；2026-07-10 对照 `main` 分支 `8cd4db0` 全量核对，§1-§8 每处结论均已
逐条核实，不再有未核对的历史快照残留。）*
