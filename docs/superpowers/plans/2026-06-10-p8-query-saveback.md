# P8 Query/Save-back 闭环 + Review/Semantic-lint 命令 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: 用 superpowers:executing-plans **Inline** 执行（与 P0–P7 同）。Steps 用 checkbox（`- [ ]`）跟踪。

**Goal:** 落地 spec §7.1 的学习反哺闭环与 §3.4 命令层收尾：`/kb-query`（只读 + 持久化 query-session）、`/kb-save <session>`（命中准入门槛才写 proposed，留 decision）、`/kb-review`（处理 Review-Queue 与 review_proposals）、`/wiki-lint-semantic`（L4/矛盾/Q2 语义体检，只出 proposal）4 个显式命令 + `save-back-policy.md` + 确定性 Q1 会话检查（`query_session.py` + CLI `check-session`）。

**Architecture:** 与 P4 同模式：确定性可验证的部分进 Python（session 目录结构契约 + Q1 检查），LLM 行为进显式 slash command 协议文档（文档要素由测试断言锁定）。query-session 只落文件系统 `pipeline-workspace/query-sessions/<run_id>/`，**不进 artifacts 表**（spec §3.4：`artifacts.source_id NOT NULL` 而 session 跨来源）。

**Tech Stack:** Python 3.11+、stdlib `json`、pytest。无新增依赖。

**权威链：** spec §7.1（两步拆分/准入门槛/默认不保存/decision 硬约束）、§3.4（命令层结构/查询持久化）、§11（Q1 确定性、Q2 语义）、§15 P8。

**运行环境：** 测试用 `D:\miniconda3\envs\pythonProject\python.exe -m pytest`；命令用 `pwsh`。

**Git：** 从 `feat/p7-cross-domain-promotion` 开 `feat/p8-query-saveback`。验证与提交用 `&&` 链接。

---

## 真实 P0–P7 API（本期在其上构建）

- `pipeline.py`：`_workspace_root()`、`commands` dict；P4 既有 `resolve-concept`/`check-write`/`snapshot-page`/`lint`（/kb-save 复用同一写入纪律，spec §9 末段）。
- `state_store.list_review_proposals`（P6，/kb-review 的机器侧清单）。
- `docs/skill-runtime/routing.md`（P4，已含 4 命令路由）；`templates/`（P3/P5）。

## Session 目录契约（本期定义，Q1 检查的依据）

`pipeline-workspace/query-sessions/<run_id>/`：

| 文件 | query 后 | save 后（Q1 检查 `--saved`） |
|---|---|---|
| `question.md` | 必须 | 必须 |
| `answer.md` | 必须 | 必须 |
| `related_pages.json`（list） | 建议 | 必须（可为空 list） |
| `candidate_write_set.json`（list） | 建议 | 必须且非空 |
| `evidence_refs.json`（list） | 建议 | 必须且非空 |
| `decision.md` | — | 必须（为什么保存/写了哪些页/引用了哪些证据/为何不污染既有概念） |

## 本期范围与取舍

- **做**：①`query_session.py::check_session(session_dir, *, saved)` 纯函数返回问题清单（Q1 确定性检查）；②CLI `check-session --id <run_id> [--saved]`（问题即 exit 1）；③4 个命令文档 + `save-back-policy.md`（§7.1 准入门槛与默认不保存逐条收录）；④`tests/test_command_docs.py` 追加 4 个文档断言。
- **不做**：`query_sessions` 表（spec §3.4：确有统计需求再评估）；语义判断本身（Q2/L4 是 `/wiki-lint-semantic` 会话内 LLM 行为，CLI 零 LLM）；session 的自动创建（`/kb-query` 会话内由 Claude 写文件）。

## File Structure

- Create `scripts/query_session.py` — `check_session`。
- Modify `scripts/pipeline.py` — `cmd_check_session` + 注册。
- Create `.claude/commands/kb-query.md` / `kb-save.md` / `kb-review.md` / `wiki-lint-semantic.md`、`docs/skill-runtime/save-back-policy.md`。
- Tests：`tests/test_query_session.py`、`tests/test_p8_cli.py`；追加 `tests/test_command_docs.py`。

---

### Task 1: 开工分支

- [ ] **Step 1:** Run `git checkout -b feat/p8-query-saveback` → Expected 切到新分支。

---

### Task 2: `query_session.py` —— Q1 确定性检查

**Files:** Create `scripts/query_session.py`、Test `tests/test_query_session.py`

- [ ] **Step 1: 写失败测试**

Create `tests/test_query_session.py`:

```python
import json
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("query_session", ROOT / "scripts" / "query_session.py")
query_session = importlib.util.module_from_spec(spec)
spec.loader.exec_module(query_session)


def _mk_session(tmp_path, *, with_save=False):
    d = tmp_path / "qs-001"
    d.mkdir()
    (d / "question.md").write_text("# 问题\n", encoding="utf-8")
    (d / "answer.md").write_text("# 回答\n", encoding="utf-8")
    if with_save:
        (d / "related_pages.json").write_text("[]", encoding="utf-8")
        (d / "candidate_write_set.json").write_text('["topics/t.md"]', encoding="utf-8")
        (d / "evidence_refs.json").write_text('[{"source": "wp", "sections": ["1"]}]',
                                              encoding="utf-8")
        (d / "decision.md").write_text("# 为什么保存\n", encoding="utf-8")
    return d


def test_query_session_ok(tmp_path):
    d = _mk_session(tmp_path)
    assert query_session.check_session(d, saved=False) == []


def test_query_session_missing_required(tmp_path):
    d = tmp_path / "qs-002"
    d.mkdir()
    problems = query_session.check_session(d, saved=False)
    assert any("question.md" in p for p in problems)
    assert any("answer.md" in p for p in problems)


def test_saved_session_ok(tmp_path):
    d = _mk_session(tmp_path, with_save=True)
    assert query_session.check_session(d, saved=True) == []


def test_saved_session_requires_decision_and_nonempty_sets(tmp_path):
    d = _mk_session(tmp_path)
    (d / "candidate_write_set.json").write_text("[]", encoding="utf-8")  # 空集不行
    (d / "evidence_refs.json").write_text("not json", encoding="utf-8")  # 坏 JSON 不行
    problems = query_session.check_session(d, saved=True)
    assert any("decision.md" in p for p in problems)
    assert any("candidate_write_set" in p for p in problems)
    assert any("evidence_refs" in p for p in problems)
    assert any("related_pages" in p for p in problems)


def test_missing_dir_is_problem(tmp_path):
    problems = query_session.check_session(tmp_path / "nope", saved=False)
    assert problems and "not found" in problems[0]
```

- [ ] **Step 2:** Run `python -m pytest tests/test_query_session.py -q` → Expected FAIL（模块不存在）。

- [ ] **Step 3: 实现**

Create `scripts/query_session.py`:

```python
"""query-session 目录契约 + Q1 确定性检查（spec §7.1/§11；零 LLM）。

session 只落文件系统 pipeline-workspace/query-sessions/<run_id>/，不进 artifacts 表（spec §3.4）。
"""
from __future__ import annotations

import json
from pathlib import Path

_REQUIRED_QUERY = ["question.md", "answer.md"]
_REQUIRED_SAVED_FILES = ["decision.md"]
_REQUIRED_SAVED_LISTS = {  # 文件名 -> 是否必须非空
    "related_pages.json": False,
    "candidate_write_set.json": True,
    "evidence_refs.json": True,
}


def check_session(session_dir, *, saved: bool) -> list[str]:
    """返回问题清单；空列表 = Q1 通过。saved=True 时按 /kb-save 后的完整契约检查。"""
    d = Path(session_dir)
    if not d.is_dir():
        return [f"session dir not found: {d}"]
    problems: list[str] = []
    for name in _REQUIRED_QUERY:
        if not (d / name).exists():
            problems.append(f"missing {name}")
    if not saved:
        return problems
    for name in _REQUIRED_SAVED_FILES:
        if not (d / name).exists():
            problems.append(f"missing {name} (为什么保存/写了哪些页/证据/为何不污染概念)")
    for name, must_be_nonempty in _REQUIRED_SAVED_LISTS.items():
        f = d / name
        if not f.exists():
            problems.append(f"missing {name}")
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            problems.append(f"{name} is not valid JSON")
            continue
        if not isinstance(data, list):
            problems.append(f"{name} must be a JSON list")
        elif must_be_nonempty and not data:
            problems.append(f"{name} must be non-empty after save")
    return problems
```

- [ ] **Step 4:** Run `python -m pytest tests/test_query_session.py -q` → Expected PASS（5）。
- [ ] **Step 5:** Commit

```
git add scripts/query_session.py tests/test_query_session.py docs/superpowers/plans/2026-06-10-p8-query-saveback.md && git commit -m "Add query-session contract + deterministic Q1 check" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: CLI `check-session`

**Files:** Modify `scripts/pipeline.py`、Test `tests/test_p8_cli.py`

- [ ] **Step 1: 写失败测试**

Create `tests/test_p8_cli.py`:

```python
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "scripts" / "pipeline.py"


def _run(args, cwd):
    env = {**os.environ, "STUDY_KB_ROOT": str(cwd)}
    return subprocess.run([sys.executable, str(PIPELINE), *args], cwd=cwd,
                          capture_output=True, text=True, env=env)


def test_check_session_pass_and_fail(tmp_path):
    d = tmp_path / "pipeline-workspace" / "query-sessions" / "qs-001"
    d.mkdir(parents=True)
    (d / "question.md").write_text("# Q\n", encoding="utf-8")
    (d / "answer.md").write_text("# A\n", encoding="utf-8")
    ok = _run(["check-session", "--id", "qs-001"], tmp_path)
    assert ok.returncode == 0 and "[OK]" in ok.stdout
    # saved 契约未满足 → exit 1 且列出问题
    fail = _run(["check-session", "--id", "qs-001", "--saved"], tmp_path)
    assert fail.returncode != 0 and "decision.md" in fail.stdout
    # 不存在的 session
    nope = _run(["check-session", "--id", "qs-404"], tmp_path)
    assert nope.returncode != 0
```

- [ ] **Step 2:** Run `python -m pytest tests/test_p8_cli.py -q` → Expected FAIL（子命令未注册）。

- [ ] **Step 3: 实现（`scripts/pipeline.py`，加在 `cmd_promote_concept` 之后）**

```python
def cmd_check_session(args):
    """Q1 确定性检查：query-session 目录契约（--saved 按 /kb-save 后完整契约）。"""
    import query_session
    d = _workspace_root() / "pipeline-workspace/query-sessions" / args.id
    problems = query_session.check_session(d, saved=getattr(args, "saved", False))
    if problems:
        for p in problems:
            print(f"[Q1] {p}")
        raise SystemExit(f"check-session failed: {len(problems)} problems")
    print(f"[OK] session {args.id} passes Q1 ({'saved' if args.saved else 'query'} contract)")
```

argparse 注册（promote-concept 之后）：

```python
    csp = subparsers.add_parser("check-session", help="Q1：query-session 目录契约检查")
    csp.add_argument("--id", required=True, help="session run_id")
    csp.add_argument("--saved", action="store_true", help="按 /kb-save 后完整契约检查")
```

`commands` dict 加：`'check-session': cmd_check_session,`

- [ ] **Step 4:** Run `python -m pytest tests/test_p8_cli.py -q` → Expected PASS（1）。
- [ ] **Step 5:** Commit

```
git add scripts/pipeline.py tests/test_p8_cli.py && git commit -m "Add check-session CLI (Q1 deterministic gate for query-sessions)" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: 4 个命令文档 + save-back-policy

**Files:** Create `.claude/commands/kb-query.md`、`.claude/commands/kb-save.md`、`.claude/commands/kb-review.md`、`.claude/commands/wiki-lint-semantic.md`、`docs/skill-runtime/save-back-policy.md`、追加 `tests/test_command_docs.py`

- [ ] **Step 1: 追加失败测试**

追加到 `tests/test_command_docs.py`：

```python
def test_kb_query_doc_readonly_and_persists():
    text = (ROOT / ".claude/commands/kb-query.md").read_text(encoding="utf-8")
    for must in ["只读", "不写 vault", "query-sessions", "question.md", "answer.md",
                 "candidate_write_set", "evidence_refs", "index.generated.md"]:
        assert must in text, f"kb-query.md 缺: {must}"


def test_kb_save_doc_gate_and_discipline():
    text = (ROOT / ".claude/commands/kb-save.md").read_text(encoding="utf-8")
    for must in ["save-back-policy", "准入门槛", "status: proposed", "decision.md",
                 "resolve-concept", "check-write", "check-session", "--saved", "lint"]:
        assert must in text, f"kb-save.md 缺: {must}"


def test_kb_review_and_semantic_lint_docs():
    rev = (ROOT / ".claude/commands/kb-review.md").read_text(encoding="utf-8")
    assert "Review-Queue" in rev and "review_proposals" in rev and "promotion-candidate" in rev
    sem = (ROOT / ".claude/commands/wiki-lint-semantic.md").read_text(encoding="utf-8")
    for must in ["L4", "矛盾", "Q2", "proposal", "不直接改写"]:
        assert must in sem, f"wiki-lint-semantic.md 缺: {must}"


def test_save_back_policy_doc():
    text = (ROOT / "docs/skill-runtime/save-back-policy.md").read_text(encoding="utf-8")
    for must in ["准入门槛", "至少满足一项", "默认不保存", "一次性事实查询",
                 "managed_by: human", "resolve_or_create_concept"]:
        assert must in text, f"save-back-policy.md 缺: {must}"
```

- [ ] **Step 2:** Run `python -m pytest tests/test_command_docs.py -q` → Expected 4 FAIL。

- [ ] **Step 3: 创建 5 个文档**

Create `.claude/commands/kb-query.md`:

```markdown
---
description: 只读查询知识库并持久化 query-session（不写 vault）
argument-hint: "<question>"
---

# /kb-query "$1" — 只读查询 + 持久化

回答用户关于知识库已有内容的问题。**只读：不写 vault 任何文件**；但必须持久化一份
query-session 供事后 /kb-save 与审计（spec §7.1）。

## 步骤

1. 读 `wiki/index.generated.md`、`wiki/concepts/_registry.yaml`、相关概念/主题/来源页，回答问题。
   答案里引用相关页（wikilink）与来源（source §节）。
2. 生成 run_id（如 `qs-YYYYMMDD-HHMMSS`），把以下文件写到
   `pipeline-workspace/query-sessions/<run_id>/`（这是工作区不是 vault，允许写）：
   - `question.md`（原问题）、`answer.md`（你的回答）
   - `related_pages.json`（涉及的 vault 页路径 list）
   - `candidate_write_set.json`（若回答里产生了值得保存的综合/对比/路线，列出拟写页；否则 `[]`）
   - `evidence_refs.json`（`[{"source": ..., "sections": [...]}]`；没有就 `[]`）
3. 告诉用户 run_id，并提示：若想把结论留进 wiki，运行 `/kb-save <run_id>`（有准入门槛，
   见 `docs/skill-runtime/save-back-policy.md`）。

## 禁止

- 写 `wiki/` 下任何文件（包括 log.md）。
- 把普通解释/翻译/一次性事实当成保存候选。
```

Create `.claude/commands/kb-save.md`:

```markdown
---
description: 把一个 query-session 的候选提升为 proposed 写入 wiki（有准入门槛）
argument-hint: <session_run_id>
---

# /kb-save $1 — 显式保存（两步闭环的第二步）

作用在已有 query-session 上：先读 `pipeline-workspace/query-sessions/$1/` 全部文件，
按 `docs/skill-runtime/save-back-policy.md` 判断**准入门槛**——不满足就明确拒绝并说明原因，
不写任何页。

## 满足门槛时的写入纪律（与 /ingest 完全相同）

1. 写入范围仅限：`topics/**`、`comparisons/**`、`synthesis/**`、相关 concept 页、
   `overview.md`、`log.md`；全部 `status: proposed` + `managed_by: pipeline`。
2. 概念只走 `python scripts/pipeline.py resolve-concept ...`（命中合并绝不新建）；
   写已存在页前 `python scripts/pipeline.py check-write --source kb-save --path <rel>`
   （没有 work order 时按 DENY 处理：改走 Review-Queue proposal）+ `snapshot-page`。
3. 更新 session 目录：补全 `candidate_write_set.json`（实际写过的页）、`evidence_refs.json`，
   写 `decision.md`（为什么保存 / 写了哪些页 / 引用了哪些证据 / 为什么没有污染已有概念）。
4. 自检：`python scripts/pipeline.py check-session --id $1 --saved` 必须通过（Q1）。
5. 提示用户运行收尾 `lint` 决定 promote（语义新增价值判断 Q2 属 /wiki-lint-semantic）。
```

Create `.claude/commands/kb-review.md`:

```markdown
---
description: 处理 Review-Queue 与 review_proposals 中的待审项（人工决策辅助）
---

# /kb-review — 复核队列处理

帮用户逐条处理待审项。**你只给分析与建议，最终采纳/拒绝由用户决定。**

## 待审项来源

1. `wiki/Review-Queue/*.md`：lint 失败清单（`<source>-lint-*.md`）、跨域提升候选
   （`promotion-*.md`）、被覆盖保护拒绝的改动提案（`*-proposal.md`）。
2. 机器侧台账：`review_proposals` 表（`python scripts/pipeline.py status` 看 source 状态；
   表内容含 kind：L1/L2/.../promotion-candidate 等）。

## 逐条处理建议

- lint 违规：给出修复方案 → 用户确认后修复 → 重新 `/ingest` 或直接改页后跑 `lint`（回流）。
- promotion-candidate：判断"语义复用 vs 同名异义"；确认提升则
  `python scripts/pipeline.py promote-concept --id <canonical_id>`，随后 `rebuild-registry`；
  同名异义则保留各自页并在两页 frontmatter `aliases` 里**不要**互相添加。
- 覆盖提案：对比提案与现页，建议合并方式；human 页永远由用户亲自改。
- 处理完一条，把对应 Review-Queue 文件中该条标记为已处理（追加 `> 已处理：<结论>`）。
```

Create `.claude/commands/wiki-lint-semantic.md`:

```markdown
---
description: 语义体检（L4/矛盾/Q2）——只产出 proposal，不直接改写任何页
---

# /wiki-lint-semantic — 语义 lint（收尾 CLI 不做的那一半）

确定性 lint（L1/L2/L3/L5/L6/断链/重复）由 `pipeline lint` 负责；本命令做需要语义判断的部分，
**只产出 proposal，不直接改写任何 wiki 页**（spec §11）。

## 检查项

- **L4**：每个 `comparisons/` 页是否真正覆盖了关键差异维度（假设/适用条件/结果/成本），
  还是只有表面罗列。
- **矛盾**：跨页结论是否互相冲突（同一概念在不同 lesson/topic 里的论断不一致）。
- **Q2**：近期 `/kb-save` 产物是否真的新增学习价值，还是复述已有页面。

## 输出

把发现写成 `wiki/Review-Queue/semantic-lint-<YYYY-MM-DD>.md`：每条含
页面路径、问题描述、建议修复方向。用户经 `/kb-review` 处理。
```

Create `docs/skill-runtime/save-back-policy.md`:

```markdown
# Save-back 准入门槛（spec §7.1）

`/kb-save` 写入前必须核对。**至少满足一项**，且不得缺证据（evidence_refs 非空）：

- 形成跨来源综合、模型对比、学习路线、常见误区或自测题；
- 解决一个会反复出现的学习困惑，并能链接到已有概念/主题；
- 发现重复概念、别名、跨域提升候选或页面矛盾；
- 用户明确要求「保存到 wiki / 形成笔记 / 加进 synthesis」。

## 默认不保存

- 一次性事实查询、普通解释、没有来源支撑的推测、只复述已有页面的答案；
- 需要覆盖 `managed_by: human` 页或越过 write scope 的答案；
- 无法链接到现有 source_refs / concept_refs 的内容。

## 硬约束

- 概念写入仍走 `resolve_or_create_concept` 协议（命中即合并、绝不新建重复）。
- 全部写出页 `status: proposed`，由收尾 `lint` 决定 promote；Q2 语义判断可阻断。
- `decision.md` 必须说明：为什么保存 / 写了哪些页 / 引用了哪些证据 / 为什么没有污染已有概念。
```

- [ ] **Step 4:** Run `python -m pytest tests/test_command_docs.py -q` → Expected PASS（8）。
- [ ] **Step 5:** Commit

```
git add .claude/commands/kb-query.md .claude/commands/kb-save.md .claude/commands/kb-review.md .claude/commands/wiki-lint-semantic.md docs/skill-runtime/save-back-policy.md tests/test_command_docs.py && git commit -m "Add kb-query/kb-save/kb-review/wiki-lint-semantic commands + save-back policy" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: 全量回归 + P8 验收

- [ ] **Step 1:** Run `python -m pytest -q --ignore=tmp` → Expected 全 PASS。
- [ ] **Step 2: 验收清单（spec §14 命令路由条目）**：副作用命令均显式 slash command；`/kb-query` 只读、持久化 session、不写 vault（文档锁定 + 无 CLI 写路径）；`/kb-save` 只有命中门槛才写 proposed 且留 decision（Q1 由 `check-session --saved` 硬检查）；session 不进 artifacts 表；`/kb-review`、`/wiki-lint-semantic` 只产出建议/proposal。
- [ ] **Step 3:** Run `git status --short` → Expected 干净。

---

## Self-Review

- **Spec 覆盖**：§7.1 两步拆分/门槛/默认不保存/decision ✓（T4 文档 + T2/T3 Q1 硬检查）；§3.4 session 文件清单与"不进 artifacts" ✓；§11 Q1 确定性（T2）、Q2/L4/矛盾归语义命令（T4）✓；§15 P8 四命令 ✓。
- **占位符扫描**：无。✓
- **类型一致性**：`check_session(session_dir, *, saved) -> list[str]`；CLI `--id/--saved` 与实现一致；文档断言字符串与文档内容逐一对应。✓
- **不越界**：零 LLM；不加表；不动既有命令。✓

## 完成后

P8 完成 = spec §15 全部 P0–P8 落地。剩余收尾：旧管线下线清理期（删除 LangGraph/unit 旧代码与依赖、同步 README/CLAUDE/domain 文档）——单列最终清理计划执行后，重构全部完成。
