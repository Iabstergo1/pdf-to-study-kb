# ingest / content routing — 内容路由（开工先分类，按类推荐写法；advisory，绝非强制）

**Position in flow:** consulted **once** at phase C prelude (right after reading `chapters.json`), then
looked up per window. **Authority order:** `wiki/_meta/purpose.md` > this manual's recommendation > default
academic prose. **Zero CLI involvement** — routing is an LLM judgement recorded on paper (digest), the
deterministic layer neither computes nor enforces it.

> **Living document（防固化条款）：** this manual is a legitimate evolution target of the
> **skill-evolve** loop (skill-gate only permits candidates that touch the two skill trees — this file
> qualifies by construction). Deviation markers (§3) accumulate in digests as revision evidence: when the
> same deviation reason clusters, revise the taxonomy/recommendations via mine → evolve → gate → human
> skill-adopt. **Never silently edit the taxonomy outside that loop.**

## 1. Route once at start (per chapter, into the digest)

After building whole-book understanding from `chapters.json`, write a `## 路由表` section into
`staging/<src>/digest.md` before the first window:

```markdown
## 路由表
| 章 | 类型 | 写法取向 |
|---|---|---|
| ch3 博弈的基本要素 | 理论型 | concept+comparison 为主；长推导折叠 |
| ch7 寻找研究问题 | 方法型 | 任务型 topic；写步骤背后的判断逻辑 |
| ch16 行动计划表 | 弱化 | 按 purpose 取舍原则弱化，只提炼可复用概念 |
```

- **Granularity = chapter, never the whole book.** One book may mix types — e.g. a run of theory
  chapters, a stretch of methodology chapters, and a downgraded schedule/appendix section, all in the
  same source.
- **Judgement signals:** chapter title + opening text, plus the deterministic signals preprocessing already
  computed (formula density, table density, needs_vision distribution in `parse_report.json`).
- Unsure? Mark the chapter `待定` and decide when its first window is actually read — never force a label.
- **裸"跳过"禁止入表：**"弱化"不是 §2 taxonomy 中的类型，只是 `purpose.md` 的取舍原则；写法取向列
  **不得只写"跳过"二字**——弱化章节必须写明跳过理由或最小提炼目标（如"纯目录，无可复用概念"、
  "只提炼 X 一个概念"）。路由表授权的是**写作取向**，从不授权免读跳过任何窗口。

## 2. Taxonomy: type → recommended writing approach (5 types max; 取向 not 骨架)

| 类型 | 识别特征 | 推荐写法取向 |
|---|---|---|
| 理论型 | 定义/定理/模型/推导为主 | concept+comparison 为主；长推导用推导折叠；承重结论标具名命题 |
| 方法型 | 步骤/流程/how-to 为主 | 任务型 topic 为主，写**步骤背后的判断逻辑**而非罗列步骤；操作要点可用 checklist |
| 案例型 | 实例/故事/判例/复盘为主 | 案例解剖体（现实片段 + 逐要素高亮标注回概念页）；载体可为 theme-named lesson |
| 参考型 | 表格/规范/公式汇编/API 为主 | 紧凑速查取向的 concept 卡；表格为主、散文为辅 |
| 观点型 | 主张/论证/评论/论文为主 | 具名命题 + 立场 comparison；明确区分"作者主张"与"公认结论" |

纯目录 / 日程 / 励志过渡内容**不入路由**——直接按 `purpose.md` 的取舍原则弱化，只提炼真正可复用的
概念（先例：第一章心态篇只提炼出「最小可行模型」一个概念）。

## 3. Deviation rule（防固化的第一道闸）

Writing a window and the chapter's label does not fit? **Write what the content actually needs**, and add
one fixed-format line to that window's digest entry:

```text
[routing-deviation] chapter=<ch> 推荐=<原类型> 实际=<实际写法> 原因=<一句话>
```

Deviation is **not** a failure — it is evidence the taxonomy is aging. Recurring deviations with the same
reason are exactly what the skill-evolve loop consumes to revise this manual (add a type / adjust a
recommendation), adopted only by a human.

**空写集窗口（`window-done --writes '[]'`）的审计规则：** 跳过一个窗口是合法操作，但前提永远是
**先经 `show-window` 读过该窗口的真实内容**再判断——路由表是章节级建议，跳过决策只能在窗口级、
读过内容后做出；按章节标注批量跳过而不读窗口，属于违反本手册的执行错误。决定空写集时，在 digest
该窗口条目记一行固定格式：

```text
[window-skip] window=<id> 章=<ch> 依据=<一句话：为什么此窗口无可提炼内容>
```

连续多个窗口空写集而无 `[window-skip]` 记录，是复盘（kb-postmortem）应捕获的静默遗漏信号。

## 4. Relationship to other protocols (no conflicts by design)

- **Device budget** (`write-pages.md`) constrains *how many devices one page uses*; routing constrains
  *which writing approach a chapter leans toward* — orthogonal, both advisory.
- Routing changes writing **orientation only**; every hard rule is untouched (two-phase publish,
  resolve-concept dedup, window accounting, lint gates all apply verbatim regardless of type).
- `purpose.md` remains the supreme authority on style/depth/取舍 — routing recommendations yield to it.
