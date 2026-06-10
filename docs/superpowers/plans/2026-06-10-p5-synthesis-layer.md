# P5 综合层一等产物 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: 用 superpowers:executing-plans **Inline** 执行（与 P0–P4 同）。Steps 用 checkbox（`- [ ]`）跟踪。

**Goal:** 让综合层（overview/topic/comparison/synthesis）成为一等产物：vault 脚手架 `init-vault`（spec §4 结构 + overview 种子页）、overview 模板与 L5 必需小节（防退化成章节清单）、`/ingest` 协议中综合层职责的显式化（每源必更新 overview、topic/comparison 增量、lessons 跟随 TOC）。

**Architecture:** 纯确定性 Python（零 LLM）。综合内容本身由 `/ingest`（LLM）维护——P5 的确定性部分是：①骨架与种子（`init-vault`、`templates/overview.md`）；②可检查性（`REQUIRED_SECTIONS["overview"]`，P6 组装 L5 门禁）；③协议义务（ingest.md 增补"综合层职责"节 + 文档测试断言）。

**Tech Stack:** Python 3.11+、stdlib、pytest。无新增依赖。

**权威链：** spec §4（vault 结构）、§7（综合层一等产物、overview 三节、收尾不改写综合内容）、§11 L5。

**运行环境：** 测试用 `D:\miniconda3\envs\pythonProject\python.exe -m pytest`；命令用 `pwsh`。

**Git：** 从 `feat/p4-ingest-command` 开 `feat/p5-synthesis-layer`。逐任务提交。

---

## 真实 P0–P4 API（本期在其上构建）

- `page_rules.REQUIRED_SECTIONS`/`required_sections_for`/`missing_sections`（P3）——本期加 `"overview"` 类型。
- `mdpage.read_page/write_page`（P2）。
- `pipeline.py`：`_workspace_root()/_vault_dir()`、`commands` dict（P1–P4）。
- `templates/`（P3 六模板）——本期加 `overview.md`。
- `.claude/commands/ingest.md`（P4）——本期增补综合层职责节；`tests/test_command_docs.py` 追加断言。

## 本期范围与取舍

- **做**：①`REQUIRED_SECTIONS["overview"]`（spec §7：核心概念地图/推荐学习路线/模型家族对比）+ `templates/overview.md`；②CLI `init-vault`：建 §4 目录骨架 + overview/log/purpose 种子（**幂等：已存在的文件绝不覆盖**）；③ingest.md 增补"§2.5 综合层职责"。
- **不做**：L5 门禁阻断（P6 组装）；Dataview/coverage/dashboards 重建（P6 的 index 范畴）；`_meta/schema.md` 派生脚本（spec 标注"如需 vault 内可读由脚本派生"——YAGNI，指针文档已在 docs/skill-runtime/schema.md）。

## File Structure

- Modify `scripts/page_rules.py` — `REQUIRED_SECTIONS` 加 `"overview"`。
- Create `templates/overview.md` — living synthesis 种子（含 L5 三节）。
- Modify `scripts/pipeline.py` — `cmd_init_vault` + 注册。
- Modify `.claude/commands/ingest.md` — 综合层职责节。
- Tests：追加 `tests/test_page_rules.py`、`tests/test_templates.py`、`tests/test_command_docs.py`；新建 `tests/test_p5_cli.py`。

---

### Task 1: 开工分支

- [ ] **Step 1:** Run `git checkout -b feat/p5-synthesis-layer` → Expected 切到新分支。

---

### Task 2: overview 类型 —— 规则 + 模板

**Files:** Modify `scripts/page_rules.py`、Create `templates/overview.md`、追加 `tests/test_page_rules.py` 与 `tests/test_templates.py`

- [ ] **Step 1: 追加失败测试**

追加到 `tests/test_page_rules.py`：

```python
def test_overview_required_sections_l5():
    secs = page_rules.required_sections_for("overview")
    assert "## 核心概念地图" in secs and "## 推荐学习路线" in secs and "## 模型家族对比" in secs
```

追加到 `tests/test_templates.py`：

```python
def test_overview_template_exists_with_l5_sections():
    meta, body = mdpage.read_page(TEMPLATES / "overview.md")
    assert meta["type"] == "overview" and meta["managed_by"] == "pipeline"
    assert page_rules.missing_sections(body, page_rules.required_sections_for("overview")) == []
```

- [ ] **Step 2:** Run `python -m pytest tests/test_page_rules.py tests/test_templates.py -q` → Expected 2 FAIL。

- [ ] **Step 3: 实现**

`scripts/page_rules.py` 的 `REQUIRED_SECTIONS` dict 中 `"synthesis"` 条目后追加：

```python
    "overview": ["## 核心概念地图", "## 推荐学习路线", "## 模型家族对比"],
```

Create `templates/overview.md`:

```markdown
---
type: overview
title: 知识库总览
status: proposed
managed_by: pipeline
---
# 知识库总览（living synthesis）

<这是 vault 入口：由 /ingest 随每次 ingest 增量维护的活综合页，禁止退化成章节清单（L5）。>

## 核心概念地图

<按领域组织的概念网络：[[概念页]] 链接 + 一句话关系（谁依赖谁、谁推广谁）>

## 推荐学习路线

<给不同目标读者的路线：先读哪些 lesson/concept、何时跳过、何时回源>

## 模型家族对比

<跨来源的模型/方法家族横向对比，链接 comparisons/ 页>
```

- [ ] **Step 4:** Run `python -m pytest tests/test_page_rules.py tests/test_templates.py -q` → Expected PASS（9+4）。
- [ ] **Step 5:** Commit

```
git add scripts/page_rules.py templates/overview.md tests/test_page_rules.py tests/test_templates.py docs/superpowers/plans/2026-06-10-p5-synthesis-layer.md
git commit -m "Add overview page type: L5 required sections + living-synthesis template" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: CLI `init-vault` —— §4 脚手架（幂等、绝不覆盖）

**Files:** Modify `scripts/pipeline.py`、Test `tests/test_p5_cli.py`

- [ ] **Step 1: 写失败测试**

Create `tests/test_p5_cli.py`:

```python
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "scripts" / "pipeline.py"

DIRS = ["_meta", "domains", "concepts", "topics", "comparisons", "synthesis",
        "sources", "assets", "Review-Queue"]


def _run(args, cwd):
    env = {**os.environ, "STUDY_KB_ROOT": str(cwd)}
    return subprocess.run([sys.executable, str(PIPELINE), *args], cwd=cwd,
                          capture_output=True, text=True, env=env)


def test_init_vault_creates_skeleton_and_seeds(tmp_path):
    r = _run(["init-vault"], tmp_path)
    assert r.returncode == 0, r.stderr
    vault = tmp_path / "wiki"
    for d in DIRS:
        assert (vault / d).is_dir(), f"missing dir: {d}"
    assert "## 核心概念地图" in (vault / "overview.md").read_text(encoding="utf-8")
    assert (vault / "log.md").exists()
    assert (vault / "_meta" / "purpose.md").exists()


def test_init_vault_idempotent_never_overwrites(tmp_path):
    _run(["init-vault"], tmp_path)
    overview = tmp_path / "wiki" / "overview.md"
    overview.write_text("HUMAN EDITED\n", encoding="utf-8")
    r = _run(["init-vault"], tmp_path)
    assert r.returncode == 0
    assert overview.read_text(encoding="utf-8") == "HUMAN EDITED\n"  # 绝不覆盖已有文件
```

- [ ] **Step 2:** Run `python -m pytest tests/test_p5_cli.py -q` → Expected FAIL（子命令未注册）。

- [ ] **Step 3: 实现（`scripts/pipeline.py`，加在 `cmd_rebuild_registry` 之前）**

```python
def cmd_init_vault(args):
    """建 wiki/ 脚手架（spec §4）+ overview/log/purpose 种子。幂等：已存在的文件/目录绝不覆盖。"""
    vault = _vault_dir()
    for d in ["_meta", "domains", "concepts", "topics", "comparisons", "synthesis",
              "sources", "assets", "Review-Queue"]:
        (vault / d).mkdir(parents=True, exist_ok=True)
    seeds = {
        "overview.md": (Path(__file__).resolve().parents[1] / "templates" / "overview.md"
                        ).read_text(encoding="utf-8"),
        "log.md": "# 操作日志（append-only：/ingest 与收尾 lint 各自追加）\n",
        "_meta/purpose.md": ("# 学习目标与偏好（用户维护）\n\n"
                             "<写下你的学习目标、当前重点、偏好的讲解风格——/ingest 会参考>\n"),
    }
    for rel, content in seeds.items():
        target = vault / rel
        if not target.exists():
            target.write_text(content, encoding="utf-8", newline="\n")
            print(f"[OK] seeded {rel}")
        else:
            print(f"[keep] {rel} exists")
    print(f"[OK] vault skeleton at {vault}")
```

argparse 注册（rebuild-registry 注册行之前）：

```python
    subparsers.add_parser("init-vault", help="建 wiki/ 脚手架 + overview/log/purpose 种子（幂等）")
```

`commands` dict 加：`'init-vault': cmd_init_vault,`

- [ ] **Step 4:** Run `python -m pytest tests/test_p5_cli.py -q` → Expected PASS（2）。
- [ ] **Step 5:** Commit

```
git add scripts/pipeline.py tests/test_p5_cli.py
git commit -m "Add init-vault CLI: spec section-4 skeleton + seeds (idempotent, never overwrites)" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: `/ingest` 综合层职责显式化

**Files:** Modify `.claude/commands/ingest.md`、追加 `tests/test_command_docs.py`

- [ ] **Step 1: 追加失败测试**

追加到 `tests/test_command_docs.py`：

```python
def test_ingest_doc_synthesis_duties():
    text = (ROOT / ".claude/commands/ingest.md").read_text(encoding="utf-8")
    for must in ["综合层职责", "overview.md", "核心概念地图", "章节清单",
                 "topics/", "comparisons/", "跟随源 TOC"]:
        assert must in text, f"ingest.md 缺综合层职责要素: {must}"
```

- [ ] **Step 2:** Run `python -m pytest tests/test_command_docs.py -q` → Expected 1 FAIL。

- [ ] **Step 3: 在 `.claude/commands/ingest.md` 的"## 2. 写页纪律"节之后插入**

```markdown
## 2.5 综合层职责（一等产物，spec §7——不是可选项）

- **overview.md 每源必更新**：把本源带来的新概念挂进"核心概念地图"、调整"推荐学习路线"、
  补充"模型家族对比"。overview 是 living synthesis，**禁止退化成章节清单**（L5 会拦）。
- **topic**：本源与已有内容形成跨章节/跨来源主题时，增量更新 `topics/<主题>.md`
  （核心综合 + 各来源贡献表 + 未解决问题；与既有结论矛盾时记入"未解决问题"，不要悄悄改写）。
- **comparison**：出现 2+ 个可横向对比的模型/方法时建/更新 `comparisons/` 页。
- **synthesis**：跨来源沉淀出单一来源给不了的洞见时写 `synthesis/` 页。
- **lessons 跟随源 TOC**：每个源章节产出 lesson 是线性辅助层；概念/主题才是主组织。
- 收尾 CLI 只重建派生（index/registry/aliases），**不改写以上综合内容**——它们由你维护。
```

- [ ] **Step 4:** Run `python -m pytest tests/test_command_docs.py -q` → Expected PASS（4）。
- [ ] **Step 5:** Commit

```
git add .claude/commands/ingest.md tests/test_command_docs.py
git commit -m "Make synthesis-layer duties explicit in /ingest protocol (overview per-source, L5)" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: 全量回归 + P5 验收

- [ ] **Step 1:** Run `python -m pytest -q --ignore=tmp` → Expected 全 PASS。
- [ ] **Step 2: 验收清单**：`init-vault` 建齐 §4 目录 + 三种子；重跑不覆盖人工修改；overview 模板含 L5 三节且 `required_sections_for("overview")` 可供 P6 组装；ingest.md 综合层职责被测试断言锁定。
- [ ] **Step 3:** Run `git status --short` → Expected 干净。

---

## Self-Review

- **Spec 覆盖**：§4 结构（T3 目录清单逐一对照，`index.generated.md`/`aliases.md` 是派生不在种子内——正确）；§7 三节 + "收尾不改写综合内容"（T2/T4）；L5 可检查性（T2，阻断在 P6）。✓
- **占位符扫描**：模板/种子内 `<尖括号>` 为运行时格式契约。✓
- **类型一致性**：`required_sections_for("overview")` 与 REQUIRED_SECTIONS key 一致；`cmd_init_vault` 只用既有 `_vault_dir()`。✓
- **不越界**：不组装 L5 门禁（P6）、不动状态机、零 LLM。✓

## 完成后

P5 完成 = 综合层有了骨架、种子、可检查规则与协议义务。下一步 **P6：学习质量 lint + 后置门禁 + promote/回滚 + Review-Queue + index 重建**（两阶段发布闭环）。
