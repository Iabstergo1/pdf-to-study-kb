# P3 页面模板 + 正文清理（证据进脚注）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: 用 superpowers:executing-plans **Inline** 执行（与 P0–P2 同，单契约链不拆 subagent）。Steps 用 checkbox（`- [ ]`）跟踪。

**Goal:** 落地 spec §8 的 6 类页面模板（source/lesson/concept/topic/comparison/synthesis，frontmatter 全带 Dataview 字段）+ §10 的"干净正文"确定性规则库（裸 E-ID 检测、脚注引用/定义配对、必需小节检查），并让 P2 的 `create_concept` 改从模板取正文骨架——为 P4 `/ingest` 提供写页格式契约，为 P6 lint 门禁提供可复用的检查原语。

**Architecture:** 纯确定性 Python（零 LLM）。新增 `templates/*.md` 6 个模板文件（静态格式契约，P4 命令层引用）+ `scripts/page_rules.py`（纯函数文本规则，无 I/O）；`concept_store.create_concept` 的正文骨架改为加载 `templates/concept.md`（模板缺失时回退内置常量，P2 行为不变）。**本期无 CLI、无状态机推进**——lint 门禁的组装与阻断是 P6，本期只交付模板与规则原语。

**Tech Stack:** Python 3.11+、stdlib `re`、pytest。无新增依赖。

**权威链：** spec §8（模板小节）、§10（证据进脚注、无裸 E-ID）、§11（L1/L2/L3/L5 所需的小节与文本检查——本期实现检查原语，阻断属 P6）、§3.4（templates/ 文件清单含 comparison.md）。

**运行环境：** 测试用 `D:\miniconda3\envs\pythonProject\python.exe -m pytest`；命令用 `pwsh`，不用 Git Bash 调 PowerShell。

**Git：** 从 `feat/p2-canonical-concepts` 开 `feat/p3-page-templates`。逐任务提交；合并/push 留到用户确认。

---

## 真实 P2 API（本期在其上构建）

- `scripts/mdpage.py`：`read_page(path) -> (meta, body)` / `write_page(path, meta, body)`——模板文件本身也是带 frontmatter 的 md，测试用它解析。
- `scripts/concept_store.py`：`CONCEPT_BODY`（内置骨架常量，含 `{name}` 占位）、`create_concept(vault, *, domain, name, aliases=(), source_ref=None)`（T5 修改其正文来源，签名不变）。
- 旧 `templates/` 下已有 `review-report.template.md`、`section-lesson.template.md`（旧管线，P4 删）——新模板用 spec §3.4 的裸名（`source.md` 等），无文件名冲突，**不动旧文件**。

## 本期范围与取舍（请先看）

- **做**：① 6 个模板文件（frontmatter 可被 `mdpage` 解析、占位值用 `<尖括号>` 约定）；② `page_rules.py` 三组纯函数——裸 E-ID（L1 原语）、脚注 ref/def 配对（§10"证据进脚注"的可验证形式）、必需小节（L2/L3/L5 原语，按页面类型查表）；③ `create_concept` 正文改从 `templates/concept.md` 加载（`{name}` 用 `str.replace` 替换，避免模板内其它花括号触发 `str.format` 异常）。
- **不做**：lint 门禁组装/阻断/Review-Queue（P6）；`/ingest` 写页（P4）；overview 的 L5 整页检查（P6 组装时复用 `missing_sections`）；对旧 `books/` 内容的清理（旧 vault 按 spec §12 重建，不修复）。
- **"正文清理"在新架构的含义**：新 vault 尚无内容，清理 = 把"干净正文"变成**可检查的规则**（本期）+ 模板从源头规定格式（本期）+ P6 门禁阻断违例（后续）。不存在存量清洗任务。

## File Structure

- Create `templates/source.md` / `templates/lesson.md` / `templates/concept.md` / `templates/topic.md` / `templates/comparison.md` / `templates/synthesis.md` — 6 类页面格式契约。
- Create `scripts/page_rules.py` — 纯函数文本规则（无 I/O）。
- Modify `scripts/concept_store.py` — `_concept_body()` 加载模板 + `create_concept` 改用之（仅此一处，签名不变）。
- Tests：`tests/test_page_rules.py`、`tests/test_templates.py`；`tests/test_concept_store.py` 追加 1 个模板一致性测试。

---

### Task 1: 开工分支

- [ ] **Step 1:** Run `git checkout -b feat/p3-page-templates`（基于 feat/p2-canonical-concepts）→ Expected 切到新分支。
- [ ] **Step 2:** Run `git status --short` → Expected 干净（pipeline-workspace/ 报告目录未跟踪可忽略）。

---

### Task 2: `page_rules.py` 之一 —— 裸 E-ID + 脚注配对

**Files:** Create `scripts/page_rules.py`、Test `tests/test_page_rules.py`

- [ ] **Step 1: 写失败测试**

Create `tests/test_page_rules.py`:

```python
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("page_rules", ROOT / "scripts" / "page_rules.py")
page_rules = importlib.util.module_from_spec(spec)
spec.loader.exec_module(page_rules)


def test_find_bare_evidence_ids():
    body = "结论 A [E-p12-3]，结论 B。\n\n另见 [E-fig_4]。\n"
    assert page_rules.find_bare_evidence_ids(body) == ["[E-p12-3]", "[E-fig_4]"]


def test_clean_prose_has_no_bare_ids():
    body = "干净的散文，证据在脚注。[^e1]\n\n[^e1]: 证据：whitepaper §5.2\n"
    assert page_rules.find_bare_evidence_ids(body) == []


def test_footnote_refs_and_defs():
    body = "论断一。[^e1] 论断二。[^e2]\n\n[^e1]: 证据一\n"
    assert page_rules.footnote_refs(body) == {"e1", "e2"}
    assert page_rules.footnote_defs(body) == {"e1"}
    assert page_rules.missing_footnote_defs(body) == {"e2"}


def test_footnote_def_line_not_counted_as_ref():
    body = "[^e1]: 只有定义没有引用\n"
    assert page_rules.footnote_refs(body) == set()
    assert page_rules.missing_footnote_defs(body) == set()
```

- [ ] **Step 2:** Run `python -m pytest tests/test_page_rules.py -q` → Expected FAIL（模块不存在）。

- [ ] **Step 3: 实现**

Create `scripts/page_rules.py`:

```python
"""干净正文的确定性文本规则（spec §10/§11 原语；纯函数、无 I/O；门禁组装在 P6）。"""
from __future__ import annotations

import re

# 裸 E-ID：旧管线的内联证据标记，正文里一律不许出现（L1）
_BARE_EVIDENCE = re.compile(r"\[E-[A-Za-z0-9_.\-]+\]")
# 脚注引用 [^e1]（行内）；(?!:) 排除定义行
_FOOTNOTE_REF = re.compile(r"\[\^([A-Za-z0-9_\-]+)\](?!:)")
# 脚注定义行 [^e1]: …
_FOOTNOTE_DEF = re.compile(r"^\[\^([A-Za-z0-9_\-]+)\]:", re.MULTILINE)


def find_bare_evidence_ids(body: str) -> list[str]:
    return _BARE_EVIDENCE.findall(body)


def footnote_refs(body: str) -> set[str]:
    return set(_FOOTNOTE_REF.findall(body))


def footnote_defs(body: str) -> set[str]:
    return set(_FOOTNOTE_DEF.findall(body))


def missing_footnote_defs(body: str) -> set[str]:
    return footnote_refs(body) - footnote_defs(body)
```

- [ ] **Step 4:** Run `python -m pytest tests/test_page_rules.py -q` → Expected PASS（4）。
- [ ] **Step 5:** Commit

```
git add scripts/page_rules.py tests/test_page_rules.py docs/superpowers/plans/2026-06-10-p3-page-templates.md
git commit -m "Add page rules: bare evidence-ID detection + footnote ref/def pairing" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: `page_rules.py` 之二 —— 必需小节（按页面类型）

**Files:** Modify `scripts/page_rules.py`、追加 `tests/test_page_rules.py`

- [ ] **Step 1: 追加失败测试**

追加到 `tests/test_page_rules.py`：

```python
def test_required_sections_for_concept_matches_spec8():
    secs = page_rules.required_sections_for("concept")
    assert "## 直觉" in secs and "## 形式化" in secs and "## 各章如何处理" in secs
    assert "## 与其他概念的关系" in secs


def test_missing_sections_reports_absent_only():
    body = "# X\n\n## 直觉\n\n说明\n\n## 形式化\n\n$$x$$\n"
    missing = page_rules.missing_sections(body, ["## 直觉", "## 形式化", "## 各章如何处理"])
    assert missing == ["## 各章如何处理"]


def test_missing_sections_requires_heading_line_not_substring():
    body = "正文里提到 ## 直觉 三个字但不是标题行\n"
    assert page_rules.missing_sections(body, ["## 直觉"]) == ["## 直觉"]


def test_unknown_page_type_raises():
    try:
        page_rules.required_sections_for("nonsense")
        assert False, "should raise"
    except KeyError:
        pass
```

- [ ] **Step 2:** Run `python -m pytest tests/test_page_rules.py -q` → Expected FAIL（函数未定义）。

- [ ] **Step 3: 实现（追加到 `scripts/page_rules.py`）**

```python
# 各页面类型的必需小节（spec §8；P6 门禁选择阻断子集：L2=concept、L3=topic、L5=overview）
REQUIRED_SECTIONS: dict[str, list[str]] = {
    "source": ["## 一句话总结", "## 核心观点", "## 关键概念",
               "## 与其他来源的关联", "## 精彩摘录", "## 相关页面"],
    "lesson": [],  # 干净散文，无强制小节；约束是无裸 E-ID + 脚注配对
    "concept": ["## 一句话", "## 直觉", "## 形式化", "## 各章如何处理",
                "## 与其他概念的关系", "## 自测"],
    "topic": ["## 核心综合", "## 各来源贡献", "## 未解决问题"],
    "comparison": ["## 结论", "## 对比维度", "## 适用场景", "## 相关概念"],
    "synthesis": ["## 核心洞见", "## 关键决策", "## 涉及概念", "## 待跟进"],
}


def required_sections_for(page_type: str) -> list[str]:
    return list(REQUIRED_SECTIONS[page_type])  # 未知类型 KeyError 即报错


def missing_sections(body: str, required: list[str]) -> list[str]:
    present = {ln.strip() for ln in body.splitlines() if ln.lstrip().startswith("#")}
    return [s for s in required if s not in present]
```

- [ ] **Step 4:** Run `python -m pytest tests/test_page_rules.py -q` → Expected PASS（8）。
- [ ] **Step 5:** Commit

```
git add scripts/page_rules.py tests/test_page_rules.py
git commit -m "Add required-section rules per page type (spec section 8)" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: 6 个页面模板文件

**Files:** Create `templates/source.md`、`templates/lesson.md`、`templates/concept.md`、`templates/topic.md`、`templates/comparison.md`、`templates/synthesis.md`、Test `tests/test_templates.py`

- [ ] **Step 1: 写失败测试**

Create `tests/test_templates.py`:

```python
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


mdpage = _load("mdpage")
page_rules = _load("page_rules")

TYPES = ["source", "lesson", "concept", "topic", "comparison", "synthesis"]


def test_all_six_templates_exist_and_parse():
    for t in TYPES:
        meta, body = mdpage.read_page(TEMPLATES / f"{t}.md")
        assert meta["type"] == t, f"{t}.md frontmatter type 不符"
        assert meta["status"] == "proposed" and meta["managed_by"] == "pipeline"


def test_templates_contain_required_sections():
    for t in TYPES:
        _, body = mdpage.read_page(TEMPLATES / f"{t}.md")
        assert page_rules.missing_sections(body, page_rules.required_sections_for(t)) == [], \
            f"{t}.md 缺必需小节"


def test_lesson_template_clean_prose_contract():
    _, body = mdpage.read_page(TEMPLATES / "lesson.md")
    assert page_rules.find_bare_evidence_ids(body) == []      # 无裸 E-ID
    assert page_rules.missing_footnote_defs(body) == set()    # 示例脚注引用均有定义
    assert "$$" in body                                        # KaTeX 示例
    assert "![[" in body                                       # 源页截图内嵌示例
```

- [ ] **Step 2:** Run `python -m pytest tests/test_templates.py -q` → Expected FAIL（模板不存在）。

- [ ] **Step 3: 创建 6 个模板**

Create `templates/source.md`:

```markdown
---
type: source
source_id: <source_id>
title: <来源标题>
domain: <domain>
format: <pdf|docx|pptx|md>
status: proposed
managed_by: pipeline
---
# <来源标题>

## 一句话总结

<这份来源讲什么、对学习者的价值>

## 核心观点

- <观点 1>
- <观点 2>
- <观点 3>

## 关键概念

- [[<概念页>|<概念名>]]

## 与其他来源的关联

- 补充：<它补充了哪个来源的什么>
- 反驳：<与哪个来源的什么结论冲突>
- 扩展：<在哪个方向走得更远>

## 精彩摘录

> <值得回看的原文段落>（§<节号>）

## 相关页面

- [[<lesson/topic/comparison 页>]]
```

Create `templates/lesson.md`:

```markdown
---
type: lesson
title: <小节标题>
domain: <domain>
source: <source_id>
section: "<5.2>"
status: proposed
managed_by: pipeline
---
# <小节标题>

<干净散文正文：讲清楚这一节在说什么、为什么重要、怎么用。
不写裸证据 ID；每个核心论断挂脚注；公式用 KaTeX；难页内嵌源页截图。>

例：信号发送者的策略在分离均衡下可被唯一识别。[^e1]

$$u_i(s) = \sum_j p_j \cdot v_i(s_j)$$

难页对照：

![[assets/<source_id>/p0123.png]]

[^e1]: 证据：<source_id> §5.2；源页截图 assets/<source_id>/p0123.png
```

Create `templates/concept.md`:

```markdown
---
type: concept
canonical_id: concept.<domain>.<slug>
canonical_name: <规范名>
aliases: []
scope: domain
domain: <domain>
source_refs: []
page_path: domains/<domain>/concepts/<slug>.md
status: proposed
managed_by: pipeline
---
# {name}

## 一句话

（待 /ingest 填写）

## 直觉

（待 /ingest 填写）

## 形式化

（待 /ingest 填写）

## 各章如何处理

（待 /ingest 填写）

## 与其他概念的关系

（待 /ingest 填写）

## 自测

（待 /ingest 填写：1–3 个自测问题，链接相关 lesson）
```

Create `templates/topic.md`:

```markdown
---
type: topic
title: <主题名>
domains: [<domain>]
related_concepts: []
status: proposed
managed_by: pipeline
---
# <主题名>

## 核心综合

<跨章节/跨来源把这个主题讲透：共识是什么、分歧在哪、怎么选>

## 各来源贡献

| 来源 | 章节 | 贡献 |
|---|---|---|
| <source_id> | §<节号> | <提供了什么> |

## 未解决问题

- <矛盾或缺口，待后续来源补>
```

Create `templates/comparison.md`:

```markdown
---
type: comparison
title: <A vs B（vs C）>
related_concepts: []
status: proposed
managed_by: pipeline
---
# <A vs B（vs C）>

## 结论

<一句话：什么时候选谁>

## 对比维度

| 维度 | <A> | <B> |
|---|---|---|
| <假设/适用条件> | … | … |
| <结果/复杂度> | … | … |

## 适用场景

- <A>：<场景>
- <B>：<场景>

## 相关概念

- [[<概念页>|<概念名>]]
```

Create `templates/synthesis.md`:

```markdown
---
type: synthesis
title: <综合标题>
related_concepts: []
status: proposed
managed_by: pipeline
---
# <综合标题>

## 核心洞见

<跨来源沉淀出的、单一来源给不了的结论>

## 关键决策

<基于洞见的取舍建议：在什么条件下怎么选>

## 涉及概念

- [[<概念页>|<概念名>]]

## 待跟进

- <需要更多来源/实验验证的点>
```

- [ ] **Step 4:** Run `python -m pytest tests/test_templates.py -q` → Expected PASS（3）。
- [ ] **Step 5:** Commit

```
git add templates/source.md templates/lesson.md templates/concept.md templates/topic.md templates/comparison.md templates/synthesis.md tests/test_templates.py
git commit -m "Add six wiki page templates (spec section 8, Dataview frontmatter)" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: `create_concept` 改从模板取正文（DRY，单一真值）

**Files:** Modify `scripts/concept_store.py`、追加 `tests/test_concept_store.py`

- [ ] **Step 1: 追加失败测试**

追加到 `tests/test_concept_store.py`：

```python
def test_create_concept_body_follows_template(tmp_path):
    tpl_body = mdpage.read_page(ROOT / "templates" / "concept.md")[1]
    path = concept_store.create_concept(tmp_path, domain="d", name="纳什均衡")
    _, body = mdpage.read_page(path)
    assert body == tpl_body.replace("{name}", "纳什均衡")
    assert "## 自测" in body  # 模板新增小节随之生效


def test_create_concept_falls_back_when_template_missing(tmp_path):
    orig = concept_store._TEMPLATES_DIR
    concept_store._TEMPLATES_DIR = tmp_path / "no-such-dir"
    try:
        path = concept_store.create_concept(tmp_path, domain="d", name="回退概念")
        _, body = mdpage.read_page(path)
        assert "## 直觉" in body  # 回退到内置 CONCEPT_BODY，骨架仍完整
    finally:
        concept_store._TEMPLATES_DIR = orig
```

- [ ] **Step 2:** Run `python -m pytest tests/test_concept_store.py -q` → Expected FAIL（`_TEMPLATES_DIR` 不存在 / body 不含 ## 自测）。

- [ ] **Step 3: 实现**

修改 `scripts/concept_store.py`——在 `CONCEPT_BODY = """…"""` 定义之后加：

```python
_TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"


def _concept_body(name: str) -> str:
    """概念骨架正文：优先取 templates/concept.md（单一真值）；缺失回退内置常量。
    用 str.replace 而非 format——模板里可能出现其它花括号。"""
    tpl = _TEMPLATES_DIR / "concept.md"
    if tpl.exists():
        _, body = mdpage.read_page(tpl)
        return body.replace("{name}", name)
    return CONCEPT_BODY.format(name=name)
```

并把 `create_concept` 末尾的：

```python
    mdpage.write_page(path, meta, CONCEPT_BODY.format(name=name))
    return path
```

改为：

```python
    mdpage.write_page(path, meta, _concept_body(name))
    return path
```

- [ ] **Step 4:** Run `python -m pytest tests/test_concept_store.py tests/test_p2_cli.py -q` → Expected PASS（17 + 3，P2 回归不破——P2 的小节断言是"包含"而非全等，新增 `## 自测` 不影响）。
- [ ] **Step 5:** Commit

```
git add scripts/concept_store.py tests/test_concept_store.py
git commit -m "Load concept skeleton body from templates/concept.md (fallback to builtin)" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: 全量回归 + P3 验收

**Files:** 无改动，纯验证

- [ ] **Step 1:** Run `python -m pytest -q --ignore=tmp` → Expected: 全 PASS（P0–P2/旧测试零回归）。
- [ ] **Step 2: P3 验收清单（对照 spec §8/§10）**
  - 6 个模板存在、frontmatter 可解析、`type/status/managed_by` 正确。
  - 每个模板包含本类型全部必需小节（`page_rules.required_sections_for` 自动核对）。
  - lesson 模板自证"干净正文"契约：无裸 E-ID、示例脚注 ref/def 配对、含 KaTeX 与源页截图示例。
  - `find_bare_evidence_ids` / `missing_footnote_defs` / `missing_sections` 三组原语可独立复用（P6 门禁、P4 命令层自检均可调）。
  - `create_concept` 骨架与 `templates/concept.md` 字节一致（含 `{name}` 替换）；模板缺失可回退；P2 全部行为不变。
  - 小节匹配按标题行而非子串（正文提到 "## 直觉" 不算）。
- [ ] **Step 3:** Run `git status --short` → Expected 干净（报告目录未跟踪可忽略）。

---

## Self-Review

- **Spec 覆盖**：§8 五类模板 + §3.4 的 comparison.md = 6 文件（T4，小节逐条对照）✓；§10 无裸 E-ID/证据进脚注 → 检测原语（T2）+ lesson 模板示范（T4）✓；§11 L1/L2/L3/L5 的文本原语（T2/T3，阻断组装明确留 P6）✓；concept 模板与 P2 `create_concept` 单一真值（T5，DRY）✓。
- **占位符扫描**：模板内 `<尖括号>` 与"（待 /ingest 填写）"是运行时格式契约（P4 由 LLM 填充），非计划占位符；各任务含完整测试与实现代码。✓
- **类型一致性**：`required_sections_for(page_type) -> list[str]`、`missing_sections(body, required) -> list[str]`、`find_bare_evidence_ids -> list[str]`、`footnote_refs/defs/missing_footnote_defs -> set[str]`、`_concept_body(name) -> str`——T2/T3 定义与 T4/T5 用法一一对应；`_TEMPLATES_DIR` 在 T5 定义并在其测试中引用。✓
- **不越界**：不组装门禁（P6）、不写 CLI、不动状态机、不动旧模板文件、零 LLM；`create_concept` 签名不变，P2 测试原样通过。✓
- **真实 API**：`mdpage.read_page`/`write_page`、`CONCEPT_BODY`、`create_concept` 均为 P2 已落地实物。✓

## 完成后

P3 完成 = 写页格式契约 + 干净正文规则原语就位。P4 `/ingest` 命令层将引用 `templates/*` 作为写页规范、调 `page_rules` 自检；P6 门禁直接组装这些原语为阻断性 lint。下一步 **P4：命令层 + `/ingest` + source 级 work order 事务协议**（注意同日参考对照评估的 C1：P4 计划须落实 rolling digest）。
