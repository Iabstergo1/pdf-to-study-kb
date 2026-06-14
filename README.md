# 📚 PDF → Study KB

> 把 **PDF / DOCX / PPTX / Markdown** 多来源文档，用**对话**增量编译进一个**不断长大、跨领域、按概念导航的本地 Obsidian 学习知识库**。

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white">
  <img alt="Tests" src="https://img.shields.io/badge/tests-passing-success">
  <img alt="Pipeline" src="https://img.shields.io/badge/pipeline-zero--LLM-blueviolet">
  <img alt="Interface" src="https://img.shields.io/badge/interface-Claude%20Code%20skills-orange">
  <img alt="Output" src="https://img.shields.io/badge/output-Obsidian%20vault-7C3AED">
</p>

这是一个 **Claude Code skills 驱动的项目**：你在 Claude Code 里用自然语言说“把这本书加进知识库”，背后的 LLM 就会自己跑完**预处理 → 写笔记 → 概念归一 → 收尾发布**全流程。不是“按章节翻译原文”，而是 [llm-wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) 模式：相同概念**合并更新**，新内容**新增页面**，库越长越互联。

> [!NOTE]
> **项目真值**：Claude Code 看 [`CLAUDE.md`](CLAUDE.md)，Codex 看 [`AGENTS.md`](AGENTS.md)（两者对等、调同一套 CLI）。skill 运行时协议在 [`docs/skill-runtime/`](docs/skill-runtime/)。

---

## 目录

- [✨ 它解决什么](#-它解决什么)
- [🏗️ 架构](#️-架构)
- [🚀 上手（克隆后三步）](#-上手克隆后三步)
- [💬 主接口：对话式 skills](#-主接口对话式-skills)
- [🛠️ 底层：确定性 CLI（手动逃生通道）](#️-底层确定性-cli手动逃生通道)
- [🔄 状态机与故障恢复](#-状态机与故障恢复)
- [⏸️ 中断续跑（上下文上限 / 订阅限额）](#-中断续跑上下文上限--订阅限额)
- [📂 Vault 结构](#-vault-结构输出)
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

## 🏗️ 架构

**对话编排层**（Claude Code skills，唯一 LLM）+ **确定性执行层**（Python CLI，零 LLM）。
skill 只是自然语言指令，通过 shell 调用 CLI；**所有业务逻辑、安全守卫都在 CLI 里**。

```text
你在 Claude Code 里说：“把这个 PDF 加进知识库，领域 game-theory”
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

## 🚀 上手（克隆后三步）

**前置：** [Python](https://www.python.org/) 3.12+、[Claude Code](https://claude.com/claude-code)（主接口）、[Obsidian](https://obsidian.md/)（可选，用来阅读成品）。

```bash
# ① 安装依赖（建议用虚拟环境 / Conda 环境，避免污染全局）
python -m pip install -r requirements.txt

# ② 自检：核心依赖就位（应打印 PyMuPDF 与 PyYAML 版本）
python -c "import fitz, yaml; print('PyMuPDF', fitz.VersionBind, '| PyYAML', yaml.__version__)"
```

```text
③ 用 Claude Code 打开本项目根目录，然后直接对话（见下一节）。
```

> [!NOTE]
> 必需依赖只有 **PyMuPDF + PyYAML**（见 [`requirements.txt`](requirements.txt)）。
> 公式保真走 route B：`source-convert` 用 PyMuPDF 抽文本，公式风险页渲染整页 PNG，由 ingest **读图写 KaTeX** 保真。不依赖任何重型 OCR/ML 后端。

---

## 💬 主接口：对话式 skills

在 Claude Code 里，**直接说人话即可**——模型会按意图自动调用对应 skill（也可手敲 `/<skill>`）。
所有写库 skill 全程受确定性 CLI 守卫保护，只写 `status: proposed`。

| skill | 一句话说什么就触发 | 它做什么 |
|------|------|------|
| **`ingest`** | “把这本书 / 这个 PDF 加进知识库，领域 X” | ⭐端到端：预处理 → 写 proposed → 收尾 lint，只在需决策时停 |
| **`kb-query`** | “知识库里关于 X 怎么说” | 只读查询 + 持久化 query-session（**不写库**） |
| **`kb-save`** | “把刚才那个对比 / 结论存进 wiki” | 把 query-session 候选存为 proposed（有准入门槛） |
| **`kb-review`** | “处理一下复核队列” | 逐条过 Review-Queue，给建议、人工定夺 |
| **`wiki-lint-semantic`** | “给知识库做个语义体检” | 查对比维度 / 跨页矛盾，只出 proposal |

> [!IMPORTANT]
> “总结这篇 / 解释这段 / 翻译一下 / 问个常识”这类只读请求**不会**触发写库——skill 的描述里写了负样本，模型会当普通问题回答。

**端到端示例（仓库内置了一本测试源可直接试）：**

```text
你：把 books/game-theory-whitepaper/input/ 里那本博弈论白皮书加进知识库，领域 game-theory

Claude（ingest skill）：
  → 确认 source_id = game-theory-whitepaper、domain = game-theory
  → 跑预处理：add-source → profile → source-convert → windows → workorder
  → 逐窗读源、写 lessons/concepts/topics（status: proposed）、归一“信号博弈/Signaling Game”
  → 跑收尾 lint：通过则 promote 进 index；失败则回滚 + 写 Review-Queue 并告诉你怎么修
  → 汇报：发布了哪些页 / 哪些进了复核队列
```

你**不需要**手动敲任何命令，也**不需要**自己写笔记内容——内容由模型在对话中生成。

---

## 🛠️ 底层：确定性 CLI（手动逃生通道）

skills 背后调用的是 `python scripts/pipeline.py <command>`（零 LLM、可独立运行）。
平时不用手敲；**排查问题、手动重跑某一步**时才需要。共 24 个子命令，按阶段分组：

<details>
<summary><b>展开：完整 CLI 命令参考</b></summary>

### 状态与维护

| 命令 | 作用 | 关键参数 |
|------|------|------|
| `status` | 列出每个 source 的阶段/状态 + vault 锁持有者（`[STALE]` 标记崩溃残留锁） | — |
| `next` | 列出每个 source 的**下一步人工动作** + stale 锁清理建议 | — |
| `init-vault` | 建 `wiki/` 脚手架 + 种子文件（幂等，不覆盖） | — |
| `unlock` | 受控回收 stale vault 锁；活锁拒绝 | `--ttl 1800` |
| `fail` | 把崩溃残留的 `running` 阶段标记 `failed` | `--source --stage --error` |
| `rebuild-registry` | 从概念页 frontmatter 重建 `_registry.yaml` + `aliases.md` | — |

### 预处理（零 LLM，顺序固定，幂等跳过）

| 命令 | 作用 | 输入 → 产出 | 关键参数 |
|------|------|------|------|
| `add-source` | 注册来源到状态库 | 原始文件 → `sources` 记录 | `--source --domain --path --fmt {pdf,md,docx,pptx}` |
| `profile` | 逐页 profile + `needs_vision` 判定 | raw → `staging/<src>/pages.jsonl` | `--source` |
| `source-convert` | 转干净 Markdown，难页渲染 PNG | raw → `staging/<src>/source.md` + `assets/` | `--source` |
| `windows` | 生成确定性 processing windows | source.md → `windows.jsonl` | `--source` |
| `workorder` | 生成 ingest 事务契约 | → `staging/<src>/workorder.yaml` | `--source` |

### `ingest` 会话支撑（通常由 skill 内部调用）

| 命令 | 作用 | 关键参数 |
|------|------|------|
| `ingest-start` / `ingest-done` | 开工（取锁 + stale registry 校验）/ 收工（释放锁） | `--source` |
| `show-window` | 打印指定 window 的源文本 | `--source --window` |
| `window-start` / `window-done` / `window-fail` | window 级记账（断点续跑 + 锁心跳） | `--source --window [--hash/--writes/--error]` |
| `resolve-concept` | 概念归一唯一入口：命中合并 / 未命中新建 | `--mention --domain [--alias --ref-source --ref-sections]` |
| `check-write` | 写前守卫：边界 + 覆盖保护（DENY 则 `exit 1`） | `--source --path` |
| `snapshot-page` | 就地 merge 前快照该页 | `--source --path` |

### 收尾、提升与查询

| 命令 | 作用 | 关键参数 |
|------|------|------|
| `lint` | 收尾门禁：proposed 过则 promote、败则回滚 + Review-Queue | `--source` |
| `promotion-candidates` | 检测跨域提升候选（人工确认） | `--propose` |
| `promote-concept` | 机械提升一个概念为 shared | `--id concept.<domain>.<slug>` |
| `check-session` | query-session 目录契约检查（Q1） | `--id <run_id> [--saved]` |

> 状态库默认锚定仓库根：`pipeline-workspace/state/study-kb.sqlite`。设环境变量 `STUDY_KB_ROOT` 可整体重定向（测试隔离 / 多库场景）。

</details>

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

长源 ingest 是一次会话里的长任务，可能撞两种"墙"。两者都**不丢进度**——确定性层是耐用底座：`ingest_progress`（窗级记账 SQLite）+ 落盘的 `status: proposed` 页 + `digest.md` 外部记忆 + **幂等 `ingest-start`**（重入报 `resumed`），任意时刻重启都能从下一个未完成 window 接着跑。

### 上下文上限（会话被压缩 / 重开）— 自动续

- 谐架 auto-compact + **SessionStart hook**：`.claude/settings.json` 在 `compact|resume` 时调用 [`scripts/resume_hint.py`](scripts/resume_hint.py)，把 `pipeline.py next`（机器派生的下一步）+ 各 staging digest 顶部的 `## ⏩ RESUME` 块重新注入上下文。
- `ingest` skill **每窗硬性维护**那个 `## ⏩ RESUME` 块（写页协议 U7），所以续跑锚点对**任意来源**自带，不是某本书的专属。

### 订阅限额（官方 5h 窗口）— 需要一点工程

诚实边界：**限额冻结期间 agent 根本跑不了，本地没有任何东西能在冻结期驱动它**；复位后必须有东西"重新点火"。三种方式按自动化程度递增：

| 方式 | 自动化 | 怎么做 |
|------|------|------|
| 手动续 | 人工 | 复位后在会话里发一句"继续"，hook 注入的 RESUME 带它接着跑 |
| **OS 调度（推荐无人值守）** | 全自动 | Windows 任务计划程序 / cron 按 **> 5h** 间隔调 [`scripts/resume-ingest.ps1`](scripts/resume-ingest.ps1)：仅在有进行中 ingest 时唤起所选 agent 的 headless 续跑（`-Agent claude` → `claude -p … --dangerously-skip-permissions`；`-Agent codex` → `codex exec --full-auto …`），冻结时空转、复位后自然成功。脚本头部含注册命令 |
| 第三方 API key | 不适用 | 按 token 计费、**没有 5h 窗口**，额度够就一路跑完，无需上面任何东西 |

> 别指望 `ScheduleWakeup` / 会话级 cron 扛 5h：前者上限 1h，后者要 REPL 常开、且冻结期自身也被限流而续不上。只有 `scripts/resume-ingest.ps1` 这种 **OS 级、独立进程**的调度才真正跨得过复位窗口。

它给的**不是"一次跑完"的硬保证，而是收敛重试**：每次 `claude -p` 都是无记忆新会话，但进度落在磁盘（`ingest_progress` + proposed 页 + digest），新会话靠 `pipeline.py next` + RESUME 块重新定位到下一个未完成 window。**没有任何一次 fire 会丢进度**；`6h > 5h` 保证不会连续两次都落在同一冻结里，落在冻结期的那次空转退出、下一次成功——单调收敛到 `ingest + lint` 全完成。前提（缺一则"自动"会断，脚本头部有详述）：① 所选 agent（`claude` 或 `codex`）已登录且在 PATH；② **非交互权限**——Claude headless 的 Bash 不会自动放行，脚本默认用 `--dangerously-skip-permissions`（仅触及本仓库 + gitignored 的 `wiki/` 运行时），或改 `acceptEdits` 但须在 `permissions.allow` 放行 `Bash(python scripts/pipeline.py:*)`；Codex 默认用 `codex exec --full-auto`（沙箱 + 不弹批准）；③ fire 时机器醒着（睡眠需唤醒定时器、笔记本需允许电池下运行——注册命令已带这些设置）。**同一 vault 同刻只许一个 ingest，别同时给两个 agent 各注册指向同库的任务。** 每次 fire 的结果会追加到 `tmp/resume.log` 供你核对。

### 克隆后能直接套用吗

能——以上对**任意领域 / 任意文档**生效，不是示例 PDF 专属：状态机、digest、`## ⏩ RESUME` 块、两个续跑脚本都随仓库分发且文档无关。个人偏好（如自动接受编辑的 `defaultMode`）放在 gitignored 的 `.claude/settings.local.json`，不强加给克隆者；想要无人值守，自己注册一次 `scripts/resume-ingest.ps1` 即可。

---

## 📂 Vault 结构（输出）

```text
wiki/
├── domains/<domain>/
│   ├── lessons/        # 讲义：跟随源 TOC 的线性辅助层
│   └── concepts/       # 领域私有概念（默认归属）
├── concepts/           # 仅 shared（跨域提升后），含 _registry.yaml（派生）
├── topics/             # 跨章节/跨来源主题综合
├── comparisons/        # 横向对比页（如 古诺 vs 伯特兰 vs 斯塔克尔伯格）
├── synthesis/          # 深度综合/结晶化
├── sources/            # 所有来源摘要（统一台账）
├── assets/             # 本地图片、源页截图
├── Review-Queue/       # 未过门禁 / 需人工决策的 proposal
├── overview.md         # living synthesis，vault 入口（LLM 维护）
├── index.generated.md  # 内容目录（派生，只收录 published）
├── aliases.md          # 别名视图（派生）
└── log.md              # append-only（ingest / lint 追加）
```

> **概念/主题为主，lessons 跟随源 TOC 为辅。** 派生文件（`index.generated.md` / `aliases.md` / `_registry.yaml`）一律由收尾 CLI 从 frontmatter 重建，写库 skill 绝不手写。

---

## 👓 在 Obsidian 中阅读

1. Obsidian → **Open folder as vault** → 选项目里的 `wiki/` 目录
2. 从 `overview.md` 开始

所有生成笔记的 frontmatter 都是 **Dataview 友好**的（`type` / `canonical_id` / `domain` / `status` / `source_refs` …），可用 Dataview 自定义检索视图。

> [!TIP]
> **frontmatter 是承重的**（Dataview 字段 + lint 全靠它），不能删。若觉得它显示在正文开头影响阅读：Obsidian → **Settings → Editor → "Properties in document" 选 "Hidden"**——文件照旧、阅读时不显示。
> **关系图（Graph）太密**多半是"汇总页把每个概念都 wikilink"造成的中心化 hub；写页纪律已要求只连真实强关系（见 ingest skill 阶段 D），汇总页只挑核心几个链、其余用普通文本。

---

## 🧪 开发与测试

```bash
# 全量测试（确定性、零 LLM）
python -m pytest tests -q

# 快速冒烟：只检查能否收集
python -m pytest tests --collect-only -q
```

- 依赖见 [`requirements.txt`](requirements.txt)。
- [`tests/test_legacy_removed.py`](tests/test_legacy_removed.py) 守卫旧管线不被重新引入（**LangGraph / 双 SQLite / plan-units / surya** 一旦回归即测试失败）。
- [`tests/test_command_docs.py`](tests/test_command_docs.py) 锁定 5 个 skill 的协议要素与 `ingest` 的端到端编排。

---

## 📚 文档导航

| 文档 | 用途 |
|------|------|
| [`CLAUDE.md`](CLAUDE.md) | **Claude Code 项目真值**（架构 / 约束 / 协作约定） |
| [`AGENTS.md`](AGENTS.md) | **Codex 项目真值**（与 CLAUDE.md 对等） |
| [`docs/skill-runtime/`](docs/skill-runtime/) | skills 的运行时协议（routing / schema / 概念归一 / save-back 准入），skill 按需加载 |
| [`.claude/skills/`](.claude/skills/) | 5 个对话式 skill 的指令文件 |
