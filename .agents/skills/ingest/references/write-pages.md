# ingest / 阶段 B+C+D — 开工 + 逐窗写页（唯一 LLM 段，质量在这里赢）

## 阶段 B：开工（守卫由 CLI 硬执行）

`python scripts/pipeline.py ingest-start --source <src>`：取 vault 锁 + 校验 stale registry。
若中止，按提示重新生成 work order，**不要绕过**。

## 阶段 C 前置：先建立全书理解（`chapters.json` = 确定性章节图 / 导航脊柱）

逐窗写页前，先读 `staging/<src>/chapters.json`——它由 `source-convert` 据 PDF 书签**确定性**产出（每章 `chapter_id` + 页范围，**章节图由 CLI 划定并 sha256 冻结，不是 LLM 划的**；md 源退化为整书一章）。它是本源的**全书地图**：

- **先通读章节图**，对每章判断"哪些概念值得深写、哪些一句话带过"，把它当逐章深写的**共享上下文**。这是治"简陋"的关键——LLM 在整本书的结构里写，而非从孤立的 2000-token 碎片里硬凑。
- **按章组织写作**：windows 仍是**确定性读取与记账单元**（`window-done --writes` 不变，操作真值留在窗级），但**按 window 覆盖页落在哪一章、以章序推进**——同章的窗连续写完再进下一章。窗→章用 `source.md` 的 `<!-- page N -->` 标记对应章页范围。
- **续跑锚点**：中断后重读 `chapters.json` + digest `## ⏩ RESUME` 块，定位到下一章未完成的窗；章节图可由 CLI 确定性重放，LLM 不另行划分，只用它当共享上下文。
- overview 的「核心概念地图」与 topic 划分**跟随章节图**（见阶段 E）。

## 阶段 C：逐窗子单元（rolling digest，长源外部记忆）

对 `staging/<src>/windows.jsonl` 每个 window（window_id 升序、**按章序**），按 U1–U7 推进。**每个子单元有产出 + 验收 + 持久化**，不再是一段「读窗写页」。

| 子单元 | 输入 | 产出 | 验收 | 持久化 | 停止点 |
|---|---|---|---|---|---|
| **U1 读取窗口** | window_id | 本窗源文本 + 续跑判断 | `window-start` 已记账；已读 `digest.md` | `ingest_progress` | 窗不存在 |
| **U2 提取候选** | 本窗文本 | 候选概念/核心论断列表 | 每条带源 §节定位 | （列入 digest 草稿） | — |
| **U3 归一** | 候选 | `[merged]`/`[created]` 概念页 + canonical_id | 命中即 merge，绝不新建重复 | 概念页 frontmatter | registry corrupt |
| **U4 起草** | 归一结果 + 本窗文本 | `status: proposed` 页（模板必需小节齐） | frontmatter 合规、小节不缺 | vault（proposed） | check-write DENY |
| **U5 自检** | 起草页 | page_rules 自检结果 | **0 违规**才进 U6 | — | 自检不过→修，不记账 |
| **U6 记账** | 写过的页 | `window-done --writes '[...]'` | 非 source 页**全部**在 --writes | `ingest_progress` | 漏记→孤儿页 |
| **U7 digest** | 本窗要点 | 滚动维护 `digest.md`（保留最近 8 窗详情 + 旧窗折叠章节摘要）+ 刷新顶部 `## ⏩ RESUME` 块 | 含新概念/未决线索；RESUME 指向下一窗；digest 不无限膨胀 | `staging/<src>/digest.md` | — |

子单元命令细节：
- U1：`python scripts/pipeline.py window-start --source <src> --window <id> --hash <窗 sha 或 char 范围串>`；
  `python scripts/pipeline.py show-window --source <src> --window <id>` 读本窗；先读 `staging/<src>/digest.md`（首窗不存在则跳过）。
  涉及 `needs_vision` 页时直接读 `staging/<src>/assets/pXXXX.png`，公式写 KaTeX `$$…$$`。
- U3：`python scripts/pipeline.py resolve-concept --mention "<提及>" --domain <domain> [--alias "<英文名>"] --ref-source <src> --ref-sections "<5.2>"`，编辑它返回的页填充正文。
- U5：自检原语 `scripts/page_rules.py`（见下「lint 硬规则」）。
- U6：`python scripts/pipeline.py window-done --source <src> --window <id> --writes '["<写过的页>"]'`（失败改 `window-fail --error "<原因>"`）。
- U7：每窗收尾把 `digest.md` **顶部**的 `## ⏩ RESUME` 块刷新（断点续跑锚点；中断后说“继续”或由 `scripts/resume-ingest.ps1` 续跑时，靠 digest RESUME 块 + `pipeline.py next` 重新定位到下一窗——这对 Claude / Codex 都是机器可读锚点，不依赖任何会话级 hook）。该块以 `## ⏩ RESUME` 开头、到下一个 `## ` 结束，**保持精简**，至少含：**进度**（已完成窗 + 下一个窗 id 及其 `--hash`）、**续跑步骤**（`ingest-start` 幂等会报 resumed → 逐窗 `window-start → show-window → 写页 → window-done`）、**写页纪律一行**（概念走 resolve-concept、wikilink 全路径、解释器与 `PYTHONUTF8=1`）——**不要把完整窗口日志塞进 RESUME 块**。全源完成后把标题改成 `## ✅ 已完成` 以免误导续跑。这是让"中断后可续"对任意来源都生效的关键，别省。
  - **digest 滚动纪律（防上下文膨胀，无人值守续跑尤其依赖）**：窗口日志区**只保留最近 8 个 window 的逐窗详情**；更早的窗**折叠为章节级摘要**（每章一行，如「第 1–4 章 ✅（要素/工具/经典/进阶，详见已发布概念页）」），不再逐窗罗列。每写完一窗，把滑出最近 8 窗范围的旧窗压进章节摘要。目标：digest 体积随章节数线性、而非随窗数无界增长。

## 阶段 D：写页纪律（每一笔写入都适用）

- **写前守卫**：`python scripts/pipeline.py check-write --source <src> --path <vault 相对路径>`。DENY（越界/不在快照/hash 已变/`managed_by: human`）→ 不写该页，把拟议改动写 `wiki/Review-Queue/<page>-proposal.md`。
- **覆盖已存在页前先快照**：`python scripts/pipeline.py snapshot-page --source <src> --path <相对路径>`。
- **所有新建/修改页 frontmatter 一律 `status: proposed` + `managed_by: pipeline`**；模板见 `templates/`，必需小节不可缺。
- **概念只走 resolve-concept**（命中合并、绝不新建重复页）；别名只写概念页 frontmatter `aliases:`。
- **派生文件绝不手写**：`concepts/_registry.yaml`、`aliases.md`、`index.generated.md` 由收尾 CLI 重建。
- **非文本内容以原图为准（route B 必走，按类型分轨）**：凡内容来自 `needs_vision` 难页（`pages.jsonl` 的 `needs_vision_reason` 标明 formula / formula-borderline / vector-figure / table / caption），都必须内嵌该页源图 `![[assets/<src>/pXXXX.png]]`，并**按类型**处理：
  - **公式页**（formula / formula-borderline）：写完整 KaTeX **并**在旁内嵌源图——纯文本会拍平上/下标与分数（lesson 含 `$$` 缺图由 lint 硬拦；concept 把源图放进「形式化」节，别省）。
  - **图页**（vector-figure / 带图标题的 caption）：**不要凭文字重画图**——直接内嵌原图，正文只讲它在说什么、怎么读（LLM 重绘矢量图/流程图不可靠，原图才是保真背书）。
  - **表页**（table）：尽量转成 markdown 表保可搜索 / 可链接，**并**内嵌源图供核对；复杂或无框线表以原图为准。
  - 原则：**LLM 擅长理解、组织、讲解；非文本对象的忠实复刻交给原始像素**——把 LLM 自身不确定性带来的失真降到最低，不过度改写原文。
- **链接克制（防关系图噪声）**：wikilink 只连"真实强关系"（谁依赖/推广/对比/特例化谁），别建"什么都链"的中心化 hub——`sources/<src>.md`、`overview.md` 等汇总页只挑核心几个概念做 wikilink、其余用普通文本带过；概念页「与其他概念的关系」只列确有逻辑关联的，不为凑数互链。
- **深度（别退化成摘要）**：每个 concept 至少含一个 worked example 或关键推导步骤（不止下定义）；lesson 给可操作细节、worked example 而非章节复述。空泛摘要式页面视作未完成。
- 追加 `log.md`：`## [YYYY-MM-DD] ingest | <src> | <created/updated 页列表>`（append-only）。

## 收尾 lint 硬规则速查（违反任一即阻断发布；写每页前默念）

1. **wikilink 必须用完整 vault 相对路径**（非 Obsidian basename）：写 `[[domains/game-theory/concepts/cournot-model|古诺模型]]`，链到的页必须真实存在。
2. **每个 `[^e1]` 引用必须有 `[^e1]:` 定义行**；正文不得出现裸 `[E-...]` ID。
3. **必需小节标题逐字匹配**（concept 六节 / topic 三节 / comparison 四节 / synthesis 四节 / source 六节 / overview 三节）。
4. **含 `$$` 的 lesson 必须内嵌真实存在的源页 PNG** `![[assets/<src>/pXXXX.png]]`；非 needs_vision 页按需用 PyMuPDF 渲染。lesson 去占位后不得过短。
5. **概念去重**：只经 resolve-concept，命中即 merge，绝不手建重复概念页（重复 canonical_id 阻断）。
6. **归属（最易漏）**：无 `source:` frontmatter 的页（`topics/**`/`comparisons/**`/`synthesis/**`/`overview.md`）**必须在某 window 的 `window-done --writes` 里**，否则 fail-closed 判孤儿页阻断。
7. **表格内公式不能含裸 `|`**：单元格里的 `$...$` 用 `\lvert S \rvert` 代替 `|S|`（或转义 `\|`，或把复杂公式移出表格放表下）——裸 `|` 会被当列分隔符撕碎公式、KaTeX 渲染失败（lint `formula-table-pipe` 硬拦）。
8. **综合层（阶段 E）必做**：产出 concept 后必须更新 overview + 按需建 topic/comparison/synthesis（进 `--writes`），否则 lint `L7-synthesis-missing` 阻断。
