# P7 多领域结构 + 跨域提升流程 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: 用 superpowers:executing-plans **Inline** 执行（与 P0–P6 同）。Steps 用 checkbox（`- [ ]`）跟踪。

**Goal:** 落地 spec §6 的跨域提升流程：确定性检测"同名/同别名出现在 ≥2 个 domain"的提升候选 → 写 Review-Queue proposal（人工确认，绝不自动提升）→ 人工确认后用机械命令把单个概念页提升为 shared（移动到顶层 `concepts/`、frontmatter 改 `scope: shared`/`domain: shared`/`canonical_id: concept.shared.<slug>`、全 vault 链接重写、目标冲突时中止）。

**Architecture:** 纯确定性 Python（零 LLM）。多领域结构本身（`domains/<d>/`、命名空间 canonical_id、workorder 按域 scope）P1–P6 已就位——P7 补的是**提升的工作流**：`promotion.py`（候选检测 + 机械提升）+ 2 个 CLI（`promotion-candidates [--propose]`、`promote-concept --id`）。同名异义不合并：候选只是"语义是否复用"的人工判断入口；机械提升一次只动一个页，合并语义内容仍是人工/LLM 的事。

**Tech Stack:** Python 3.11+、stdlib、pytest。无新增依赖。

**权威链：** spec §6（跨域提升门槛：第二域语义复用才提升、须 Review-Queue 人工确认；同名异义保持各自 canonical_id）、§4（shared 概念在顶层 `concepts/`）、§13（自动只给候选，提升一律人工确认）。

**运行环境：** 测试用 `D:\miniconda3\envs\pythonProject\python.exe -m pytest`；命令用 `pwsh`。

**Git：** 从 `feat/p6-lint-gate` 开 `feat/p7-cross-domain-promotion`。验证与提交用 `&&` 链接。

---

## 真实 P0–P6 API（本期在其上构建）

- `concept_store`：`scan_concept_pages/build_registry/_norm/slugify`（P2）；registry 条目 `{canonical_name, aliases, scope, domain, page_path}`。
- `mdpage.read_page/write_page`（P2）。
- `state_store.add_review_proposal`（P6）。
- `pipeline.py`：`_vault_dir()`、`commands` dict。

## 本期范围与取舍

- **候选 ≠ 提升**：`promotion-candidates` 只检测 + （`--propose` 时）落 Review-Queue 文件 + `review_proposals` 行（kind=`promotion-candidate`）；**绝不改任何概念页**（spec §13）。
- **机械提升只动一个页**：`promote-concept --id concept.<d>.<slug>`（人工在 Review-Queue 确认后手动执行）。canonical_id 变更为 `concept.shared.<slug>`；全 vault `.md` 中旧页路径的 wikilink 文本重写为新路径；目标已存在（`concepts/<slug>.md` 或 shared 命名空间撞 id）→ 中止不动盘。
- **另一域的同名概念页不自动合并**：提升后它会被 `promotion-candidates` 继续报告为与 shared 撞名（alias collision warning 也会出现）——把内容并进 shared 页是语义工作，走 `/ingest`/人工编辑。
- **不做**：source 生命周期（spec §9.1 明确"P7 之后或单列一期，不在 P0–P8 强行实现"）；提升的反向操作（降级）——YAGNI。

## File Structure

- Create `scripts/promotion.py` — `find_candidates(registry)` + `promote_to_shared(vault, canonical_id)`。
- Modify `scripts/pipeline.py` — `promotion-candidates` / `promote-concept` 子命令。
- Tests：`tests/test_promotion.py`、`tests/test_p7_cli.py`。

---

### Task 1: 开工分支

- [ ] **Step 1:** Run `git checkout -b feat/p7-cross-domain-promotion` → Expected 切到新分支。

---

### Task 2: `promotion.py` —— 候选检测 + 机械提升

**Files:** Create `scripts/promotion.py`、Test `tests/test_promotion.py`

- [ ] **Step 1: 写失败测试**

Create `tests/test_promotion.py`:

```python
from pathlib import Path
import importlib.util

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


mdpage = _load("mdpage")
concept_store = _load("concept_store")
promotion = _load("promotion")


def _registry(vault):
    reg, errors, _ = concept_store.build_registry(concept_store.scan_concept_pages(vault))
    assert not errors
    return reg


def test_find_candidates_same_term_two_domains(tmp_path):
    concept_store.create_concept(tmp_path, domain="econ", name="Utility")
    concept_store.create_concept(tmp_path, domain="cs", name="效用函数", aliases=["Utility"])
    concept_store.create_concept(tmp_path, domain="econ", name="独占概念")          # 单域：不是候选
    concept_store.create_concept(tmp_path, domain="shared", name="期望效用")        # 已 shared：不参与
    cands = promotion.find_candidates(_registry(tmp_path))
    assert len(cands) == 1
    c = cands[0]
    assert c["term"] == "utility" and set(c["domains"]) == {"econ", "cs"}
    assert set(c["canonical_ids"]) == {"concept.econ.utility", "concept.cs.效用函数"}


def test_promote_to_shared_moves_rewrites_and_relinks(tmp_path):
    concept_store.create_concept(tmp_path, domain="econ", name="Utility",
                                 source_ref={"source": "wp", "sections": ["1"]})
    # 另一页链接到它（提升后链接必须跟着改）
    mdpage.write_page(tmp_path / "topics/t.md", {"type": "topic", "status": "published"},
                      "# T\n\n见 [[domains/econ/concepts/utility.md|效用]]。\n")
    new_cid, new_rel = promotion.promote_to_shared(tmp_path, "concept.econ.utility")
    assert new_cid == "concept.shared.utility" and new_rel == "concepts/utility.md"
    assert not (tmp_path / "domains/econ/concepts/utility.md").exists()
    meta, _ = mdpage.read_page(tmp_path / new_rel)
    assert meta["canonical_id"] == new_cid and meta["scope"] == "shared"
    assert meta["domain"] == "shared" and meta["page_path"] == new_rel
    assert meta["source_refs"] == [{"source": "wp", "sections": ["1"]}]  # 内容保留
    topic = (tmp_path / "topics/t.md").read_text(encoding="utf-8")
    assert "[[concepts/utility.md|效用]]" in topic and "domains/econ" not in topic


def test_promote_unknown_or_conflict_aborts(tmp_path):
    with pytest.raises(KeyError):
        promotion.promote_to_shared(tmp_path, "concept.d.nope")
    concept_store.create_concept(tmp_path, domain="econ", name="Utility")
    concept_store.create_concept(tmp_path, domain="shared", name="Utility")  # 目标已存在
    with pytest.raises(FileExistsError):
        promotion.promote_to_shared(tmp_path, "concept.econ.utility")
    assert (tmp_path / "domains/econ/concepts/utility.md").exists()  # 中止不动盘
```

- [ ] **Step 2:** Run `python -m pytest tests/test_promotion.py -q` → Expected FAIL（模块不存在）。

- [ ] **Step 3: 实现**

Create `scripts/promotion.py`:

```python
"""跨域提升（spec §6/§13）：候选检测（绝不自动提升）+ 人工确认后的机械提升。零 LLM。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import concept_store
import mdpage


def find_candidates(registry: dict) -> list[dict]:
    """同一规范名/别名出现在 ≥2 个不同 domain（shared 除外）→ 提升候选。只检测，不改盘。"""
    by_term: dict[str, dict[str, str]] = {}  # norm term -> {domain: cid}
    for cid in sorted(registry):
        e = registry[cid]
        if e["domain"] == "shared":
            continue
        for term in [e["canonical_name"], *e["aliases"]]:
            by_term.setdefault(concept_store._norm(term), {}).setdefault(e["domain"], cid)
    out = []
    for term in sorted(by_term):
        hits = by_term[term]
        if len(hits) >= 2:
            out.append({"term": term, "domains": sorted(hits),
                        "canonical_ids": sorted(set(hits.values()))})
    return out


def promote_to_shared(vault, canonical_id: str) -> tuple[str, str]:
    """把一个 domain 概念页机械提升为 shared：移动 + frontmatter 改写 + 全 vault 链接重写。
    目标冲突（页文件已存在 / shared 命名空间撞 id）→ 中止且不动盘。"""
    vault = Path(vault)
    registry, errors, _ = concept_store.build_registry(concept_store.scan_concept_pages(vault))
    if errors:
        raise ValueError("corrupt concept pages: " + "; ".join(errors))
    if canonical_id not in registry:
        raise KeyError(f"unknown canonical_id: {canonical_id}")
    entry = registry[canonical_id]
    if entry["domain"] == "shared":
        raise ValueError(f"{canonical_id} already shared")
    slug = canonical_id.rsplit(".", 1)[1]
    new_cid = f"concept.shared.{slug}"
    new_rel = f"concepts/{slug}.md"
    if new_cid in registry or (vault / new_rel).exists():
        raise FileExistsError(f"target exists: {new_cid} / {new_rel}")
    old_rel = entry["page_path"]
    meta, body = mdpage.read_page(vault / old_rel)
    meta.update({"canonical_id": new_cid, "scope": "shared", "domain": "shared",
                 "page_path": new_rel})
    mdpage.write_page(vault / new_rel, meta, body)
    (vault / old_rel).unlink()
    # 全 vault 链接重写：旧页路径（带/不带 .md）→ 新路径
    old_noext = old_rel[:-3]
    new_noext = new_rel[:-3]
    for f in sorted(vault.rglob("*.md")):
        text = f.read_text(encoding="utf-8")
        if old_rel in text or old_noext in text:
            f.write_text(text.replace(old_rel, new_rel).replace(old_noext, new_noext),
                         encoding="utf-8", newline="\n")
    return new_cid, new_rel
```

- [ ] **Step 4:** Run `python -m pytest tests/test_promotion.py -q` → Expected PASS（3）。
- [ ] **Step 5:** Commit

```
git add scripts/promotion.py tests/test_promotion.py docs/superpowers/plans/2026-06-10-p7-cross-domain-promotion.md && git commit -m "Add cross-domain promotion: candidate detection + mechanical promote-to-shared" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: CLI —— `promotion-candidates` / `promote-concept`

**Files:** Modify `scripts/pipeline.py`、Test `tests/test_p7_cli.py`

- [ ] **Step 1: 写失败测试**

Create `tests/test_p7_cli.py`:

```python
import os
import subprocess
import sys
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "scripts" / "pipeline.py"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


state_store = _load("state_store")
concept_store = _load("concept_store")


def _run(args, cwd):
    env = {**os.environ, "STUDY_KB_ROOT": str(cwd)}
    return subprocess.run([sys.executable, str(PIPELINE), *args], cwd=cwd,
                          capture_output=True, text=True, env=env)


def _two_domain_vault(tmp_path):
    vault = tmp_path / "wiki"
    concept_store.create_concept(vault, domain="econ", name="Utility")
    concept_store.create_concept(vault, domain="cs", name="效用", aliases=["Utility"])
    return vault


def test_promotion_candidates_lists_and_proposes(tmp_path):
    _two_domain_vault(tmp_path)
    r = _run(["promotion-candidates"], tmp_path)
    assert r.returncode == 0 and "utility" in r.stdout.lower()
    r2 = _run(["promotion-candidates", "--propose"], tmp_path)
    assert r2.returncode == 0
    queue = list((tmp_path / "wiki/Review-Queue").glob("promotion-*.md"))
    assert len(queue) == 1 and "utility" in queue[0].read_text(encoding="utf-8").lower()
    db = tmp_path / "pipeline-workspace/state/study-kb.sqlite"
    rows = state_store.list_review_proposals(db)
    assert any(p["kind"] == "promotion-candidate" for p in rows)
    # 概念页本身没被改（绝不自动提升）
    assert (tmp_path / "wiki/domains/econ/concepts/utility.md").exists()


def test_promote_concept_cli(tmp_path):
    _two_domain_vault(tmp_path)
    r = _run(["promote-concept", "--id", "concept.econ.utility"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "wiki/concepts/utility.md").exists()
    assert not (tmp_path / "wiki/domains/econ/concepts/utility.md").exists()
    # 提升后 rebuild-registry 应仍可用（shared 与 cs 同名只算 warning）
    r2 = _run(["rebuild-registry"], tmp_path)
    assert r2.returncode == 0
```

- [ ] **Step 2:** Run `python -m pytest tests/test_p7_cli.py -q` → Expected FAIL（子命令未注册）。

- [ ] **Step 3: 实现（`scripts/pipeline.py`，加在 `cmd_lint` 之后）**

```python
def cmd_promotion_candidates(args):
    """检测跨域提升候选（spec §6/§13：只给候选，提升一律人工确认）。--propose 落 Review-Queue。"""
    import state_store
    import concept_store
    import promotion
    from datetime import date
    vault = _vault_dir()
    if not vault.exists():
        print("no wiki/ vault yet")
        return
    registry, errors, _w = concept_store.build_registry(concept_store.scan_concept_pages(vault))
    if errors:
        raise SystemExit("corrupt concept pages: " + "; ".join(errors))
    cands = promotion.find_candidates(registry)
    if not cands:
        print("no promotion candidates")
        return
    for c in cands:
        print(f"[candidate] {c['term']}: domains={','.join(c['domains'])} ids={','.join(c['canonical_ids'])}")
    if getattr(args, "propose", False):
        db = _vault_state_db()
        lines = ["# 跨域提升候选（人工确认后用 promote-concept --id <canonical_id> 执行）", ""]
        for c in cands:
            lines.append(f"- `{c['term']}`：{', '.join(c['canonical_ids'])}（语义确实复用才提升；同名异义保持各自页）")
            state_store.add_review_proposal(db, "vault", target_path=c["canonical_ids"][0],
                                            kind="promotion-candidate",
                                            reason=f"term '{c['term']}' in domains {','.join(c['domains'])}")
        queue = vault / "Review-Queue" / f"promotion-{date.today().isoformat()}.md"
        queue.parent.mkdir(parents=True, exist_ok=True)
        queue.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        print(f"[OK] proposals -> {queue}")


def cmd_promote_concept(args):
    """人工确认后的机械提升：移动到顶层 concepts/ + frontmatter 改写 + 全 vault 链接重写。"""
    import promotion
    new_cid, new_rel = promotion.promote_to_shared(_vault_dir(), args.id)
    print(f"[OK] promoted -> {new_cid} ({new_rel}); 建议随后 rebuild-registry")
```

argparse 注册（lint 注册之后）：

```python
    pcp = subparsers.add_parser("promotion-candidates", help="检测跨域提升候选（--propose 落 Review-Queue）")
    pcp.add_argument("--propose", action="store_true")
    pmp = subparsers.add_parser("promote-concept", help="人工确认后机械提升一个概念为 shared")
    pmp.add_argument("--id", required=True, help="canonical_id（concept.<domain>.<slug>）")
```

`commands` dict 加：`'promotion-candidates': cmd_promotion_candidates, 'promote-concept': cmd_promote_concept,`

- [ ] **Step 4:** Run `python -m pytest tests/test_p7_cli.py -q` → Expected PASS（2）。
- [ ] **Step 5:** Commit

```
git add scripts/pipeline.py tests/test_p7_cli.py && git commit -m "Add promotion-candidates + promote-concept CLI (human-confirmed, never automatic)" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: 全量回归 + P7 验收

- [ ] **Step 1:** Run `python -m pytest -q --ignore=tmp` → Expected 全 PASS。
- [ ] **Step 2: 验收清单**：候选检测不动盘；`--propose` 落 Review-Queue + proposals（kind=promotion-candidate）；机械提升移动/改写/链接重写/内容保留；目标冲突中止不动盘；同名异义不合并（cs 的页保留原样）；提升后 registry 可重建。
- [ ] **Step 3:** Run `git status --short` → Expected 干净。

---

## Self-Review

- **Spec 覆盖**：§6 提升门槛（候选→人工→机械执行，绝不自动）✓；§13 "自动只给候选" ✓；§4 shared 落顶层 concepts/ ✓；同名异义不合并（提升只动一页，另一域页原样保留并继续被候选报告）✓；§9.1 source 生命周期明确不做 ✓。
- **占位符扫描**：无。✓
- **类型一致性**：`find_candidates(registry) -> [{term, domains, canonical_ids}]`；`promote_to_shared(vault, canonical_id) -> (new_cid, new_rel)`；CLI 与之一致；`concept_store._norm` 为 P2 已有内部函数（模块内复用，可接受）。✓
- **不越界**：零 LLM；不动状态机阶段（提升是 vault 维护操作，不挂 source 状态）。✓

## 完成后

P7 完成 = 多领域累积的提升工作流闭环。下一步 **P8：query/save-back 闭环 + review/semantic-lint 命令**（最后一期）。
