# P2 Canonical 概念模型 + Registry + 别名归一 + 概念 Merge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: 用 superpowers:executing-plans **Inline** 执行（与 P0/P1 同，单契约链不拆 subagent）。Steps 用 checkbox（`- [ ]`）跟踪。

**Goal:** 落地 spec §6 的 canonical 概念数据模型——概念页 frontmatter 为唯一真值，`concepts/_registry.yaml` 与 `aliases.md` 确定性派生重建，所有概念创建/更新走单一协议 `resolve_or_create_concept`（命中即 merge、绝不新建重复页），为 P4 `/ingest` 提供归一查询基底与 registry hash。

**Architecture:** 纯确定性 Python（零 LLM）。新增 `mdpage.py`（frontmatter 读写）+ `concept_store.py`（slug/canonical_id、registry 扫描重建、resolve/merge/create 协议）+ CLI `rebuild-registry`。vault 根 = `_workspace_root()/wiki`（沿用 P1 的 `STUDY_KB_ROOT` 隔离机制）。**不涉及 source 状态机推进**（registry 是 vault 级派生，非 source 阶段）。

**Tech Stack:** Python 3.11+、stdlib、`pyyaml`（已装）、pytest。无新增依赖。

**权威链：** spec §6（canonical 模型）、§4（vault 结构）、§8（concept 页最小结构）、§14（验收"信号博弈合并单页、无跨域污染"）。

**运行环境：** 测试用 `D:\miniconda3\envs\pythonProject\python.exe -m pytest`；命令用 `pwsh`，不用 Git Bash 调 PowerShell。

**Git：** 从 `feat/p1-source-convert` 开 `feat/p2-canonical-concepts`。逐任务提交；合并/push 留到用户确认。

---

## 真实 P0/P1 API（本期在其上构建，勿改既有函数/测试）

- `scripts/pipeline.py`：`_workspace_root()`（`STUDY_KB_ROOT` 可覆盖）、`commands` dict 注册模式。本期新增 `_vault_dir()` 与 `rebuild-registry` 子命令。
- `scripts/state_store.py` / `locks.py` / `snapshots.py`：本期不需要（不推进 source 阶段、不写状态库；registry hash 在 P4 生成 work order 时才记入 `work_orders.registry_hash`）。
- 测试加载模式：`importlib.util.spec_from_file_location`（沿用 P0/P1 各测试文件的 `_load` 模式）。

## 本期范围与取舍（请先看）

- **做**：① frontmatter 读写（确定性 round-trip）；② `slugify`/`canonical_id`（命名空间 `concept.<domain>.<slug>`，CJK 名优先取 ASCII 别名做 slug——对应 spec §6 示例 信号博弈→`signaling-game`）；③ registry 扫描 `domains/*/concepts/*.md` + 顶层 `concepts/*.md` 并确定性重建 `_registry.yaml`（排序、带 sha256）+ `aliases.md` 派生视图；④ `resolve_or_create_concept` 单一协议（resolve 先本域后 shared；命中 merge 累积 `source_refs`/`aliases`；未命中 create 骨架页）；⑤ 重复检测：duplicate `canonical_id` = 结构损坏（**拒绝写 registry，exit 非 0**），同域别名碰撞 = 警告（阻断性 lint 属 P6 门禁）。
- **不做**：跨域提升自动化（`scope: domain → shared` 须 Review-Queue 人工确认，P7）；阻断性 duplicate-concept 门禁与 `index.generated.md`（P6）；任何 LLM 调用、任何对旧 `books/` 内容的迁移（旧 vault 按 spec §12 经 `/ingest` 重建，不搬运）。
- **概念骨架页**：create 时按 §8 concept 模板生成必需小节（一句话/直觉/形式化/各章如何处理/与其他概念的关系），正文留待 `/ingest`（P4）填写；frontmatter `status: proposed`、`managed_by: pipeline`（两阶段发布从第一页起就成立）。
- **registry 包含 proposed 页**：registry 是去重索引而非发布索引，必须覆盖全部概念页（否则 `/ingest` 会重建重复概念）；"只收录 published"是 `index.generated.md` 的规则（P6）。

## File Structure

- Create `scripts/mdpage.py` — Markdown 页 frontmatter 读写（确定性，`sort_keys=True`）。
- Create `scripts/concept_store.py` — slug/canonical_id、scan/build/write registry、aliases 派生、resolve/merge/create 协议。
- Modify `scripts/pipeline.py` — 加 `_vault_dir()` + `rebuild-registry` 子命令。
- Tests：`tests/test_mdpage.py`、`tests/test_concept_store.py`、`tests/test_p2_cli.py`。

---

### Task 1: 开工分支

- [ ] **Step 1:** Run `git checkout -b feat/p2-canonical-concepts`（基于 feat/p1-source-convert）→ Expected 切到新分支。
- [ ] **Step 2:** Run `git status --short` → Expected 干净（pipeline-workspace/ 报告目录未跟踪可忽略）。

---

### Task 2: `mdpage.py` —— frontmatter 读写

**Files:** Create `scripts/mdpage.py`、Test `tests/test_mdpage.py`

- [ ] **Step 1: 写失败测试**

Create `tests/test_mdpage.py`:

```python
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("mdpage", ROOT / "scripts" / "mdpage.py")
mdpage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mdpage)


def test_read_page_splits_frontmatter_and_body(tmp_path):
    p = tmp_path / "c.md"
    p.write_text("---\ntype: concept\ncanonical_name: 信号博弈\n---\n# 信号博弈\n\nbody\n", encoding="utf-8")
    meta, body = mdpage.read_page(p)
    assert meta["type"] == "concept" and meta["canonical_name"] == "信号博弈"
    assert body.startswith("# 信号博弈")


def test_read_page_no_frontmatter(tmp_path):
    p = tmp_path / "plain.md"
    p.write_text("just text\n", encoding="utf-8")
    meta, body = mdpage.read_page(p)
    assert meta == {} and body == "just text\n"


def test_write_then_read_roundtrip_deterministic(tmp_path):
    p = tmp_path / "x.md"
    meta = {"type": "concept", "aliases": ["Signaling Game"], "canonical_name": "信号博弈"}
    mdpage.write_page(p, meta, "BODY\n")
    m2, b2 = mdpage.read_page(p)
    assert m2 == meta and b2 == "BODY\n"
    first = p.read_text(encoding="utf-8")
    mdpage.write_page(p, m2, b2)  # 再写一遍字节不变（确定性）
    assert p.read_text(encoding="utf-8") == first
```

- [ ] **Step 2:** Run `python -m pytest tests/test_mdpage.py -q` → Expected FAIL（模块不存在）。

- [ ] **Step 3: 实现**

Create `scripts/mdpage.py`:

```python
"""Markdown 页读写：YAML frontmatter + 正文（确定性 round-trip；spec §6 真值在 frontmatter）。"""
from __future__ import annotations

from pathlib import Path

import yaml


def read_page(path) -> tuple[dict, str]:
    text = Path(path).read_text(encoding="utf-8")
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            meta = yaml.safe_load(text[4:end + 1]) or {}
            return meta, text[end + 5:]
    return {}, text


def write_page(path, meta: dict, body: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fm = yaml.safe_dump(meta, allow_unicode=True, sort_keys=True, default_flow_style=False)
    p.write_text(f"---\n{fm}---\n{body}", encoding="utf-8")
```

- [ ] **Step 4:** Run `python -m pytest tests/test_mdpage.py -q` → Expected PASS（3）。
- [ ] **Step 5:** Commit

```
git add scripts/mdpage.py tests/test_mdpage.py docs/superpowers/plans/2026-06-10-p2-canonical-concepts.md
git commit -m "Add mdpage frontmatter read/write (deterministic roundtrip)" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: `concept_store.py` 之一 —— slug + canonical_id

**Files:** Create `scripts/concept_store.py`、Test `tests/test_concept_store.py`

- [ ] **Step 1: 写失败测试**

Create `tests/test_concept_store.py`:

```python
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


mdpage = _load("mdpage")
concept_store = _load("concept_store")


def test_slugify_ascii_kebab():
    assert concept_store.slugify("Signaling Game") == "signaling-game"
    assert concept_store.slugify("  Nash  Equilibrium! ") == "nash-equilibrium"


def test_slugify_pure_cjk_kept():
    assert concept_store.slugify("信号博弈") == "信号博弈"


def test_canonical_id_prefers_ascii_candidate():
    # spec §6 示例：信号博弈 + alias "Signaling Game" → concept.game-theory.signaling-game
    cid = concept_store.canonical_id("game-theory", "信号博弈", aliases=["Signaling Game"])
    assert cid == "concept.game-theory.signaling-game"
    cid2 = concept_store.canonical_id("game-theory", "Nash Equilibrium")
    assert cid2 == "concept.game-theory.nash-equilibrium"


def test_canonical_id_pure_cjk_no_alias():
    assert concept_store.canonical_id("misc", "占优策略") == "concept.misc.占优策略"
```

- [ ] **Step 2:** Run `python -m pytest tests/test_concept_store.py -q` → Expected FAIL（模块不存在）。

- [ ] **Step 3: 实现**

Create `scripts/concept_store.py`:

```python
"""Canonical 概念模型（spec §6）：slug/canonical_id、registry 重建、resolve_or_create_concept。

真值在概念页 frontmatter；concepts/_registry.yaml 与 aliases.md 为派生（本模块重建，/ingest 不写）。
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mdpage

_ASCII_SLUG = re.compile(r"[^a-z0-9]+")
_SLUG_OK = re.compile(r"[a-z0-9][a-z0-9-]*")


def slugify(name: str) -> str:
    """确定性 slug：ASCII 名转 kebab；纯 CJK 名保留原字（去空白）。"""
    ascii_slug = _ASCII_SLUG.sub("-", name.strip().lower()).strip("-")
    if ascii_slug:
        return ascii_slug
    return re.sub(r"\s+", "", name.strip())


def canonical_id(domain: str, name: str, aliases=()) -> str:
    """concept.<domain>.<slug>；slug 依次试 name、各 alias，取第一个纯 ASCII 的（spec §6 示例规则）。"""
    for cand in (name, *aliases):
        s = slugify(cand)
        if _SLUG_OK.fullmatch(s):
            return f"concept.{domain}.{s}"
    return f"concept.{domain}.{slugify(name)}"
```

- [ ] **Step 4:** Run `python -m pytest tests/test_concept_store.py -q` → Expected PASS（4）。
- [ ] **Step 5:** Commit

```
git add scripts/concept_store.py tests/test_concept_store.py
git commit -m "Add concept slug + namespaced canonical_id (ascii-alias preference)" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: `concept_store.py` 之二 —— registry 扫描/重建/派生

**Files:** Modify `scripts/concept_store.py`、追加 `tests/test_concept_store.py`

- [ ] **Step 1: 追加失败测试**

追加到 `tests/test_concept_store.py`（含一个建测试 vault 的 helper，后续任务复用）：

```python
def _mk_concept(vault, *, domain, name, aliases=(), cid=None, scope=None):
    """直接落一个概念页文件（绕协议，模拟已有 vault 内容）。"""
    cid = cid or concept_store.canonical_id(domain, name, aliases)
    slug = cid.rsplit(".", 1)[1]
    if domain == "shared":
        rel = Path("concepts") / f"{slug}.md"
    else:
        rel = Path("domains") / domain / "concepts" / f"{slug}.md"
    meta = {"type": "concept", "canonical_id": cid, "canonical_name": name,
            "aliases": list(aliases), "scope": scope or ("shared" if domain == "shared" else "domain"),
            "domain": domain, "source_refs": [], "page_path": rel.as_posix(),
            "managed_by": "pipeline", "status": "proposed"}
    mdpage.write_page(Path(vault) / rel, meta, f"# {name}\n")
    return cid, rel.as_posix()


def test_scan_finds_domain_and_shared_pages(tmp_path):
    _mk_concept(tmp_path, domain="game-theory", name="信号博弈", aliases=["Signaling Game"])
    _mk_concept(tmp_path, domain="shared", name="期望效用")
    metas = concept_store.scan_concept_pages(tmp_path)
    assert {m["canonical_id"] for m in metas} == {
        "concept.game-theory.signaling-game", "concept.shared.期望效用"}


def test_build_registry_ok_and_write_deterministic(tmp_path):
    _mk_concept(tmp_path, domain="game-theory", name="信号博弈", aliases=["Signaling Game"])
    metas = concept_store.scan_concept_pages(tmp_path)
    reg, errors, warnings = concept_store.build_registry(metas)
    assert not errors and not warnings
    sha1 = concept_store.write_registry(tmp_path, reg)
    sha2 = concept_store.write_registry(tmp_path, reg)
    assert sha1 == sha2 and len(sha1) == 64  # 重建字节级确定
    text = (tmp_path / "concepts" / "_registry.yaml").read_text(encoding="utf-8")
    assert "concept.game-theory.signaling-game" in text


def test_duplicate_canonical_id_is_error(tmp_path):
    _mk_concept(tmp_path, domain="game-theory", name="信号博弈", cid="concept.game-theory.x")
    _mk_concept(tmp_path, domain="game-theory", name="发信号", cid="concept.game-theory.x")
    reg, errors, warnings = concept_store.build_registry(concept_store.scan_concept_pages(tmp_path))
    assert any("duplicate canonical_id" in e for e in errors)


def test_alias_collision_same_domain_is_warning(tmp_path):
    _mk_concept(tmp_path, domain="d", name="A", aliases=["撞名"])
    _mk_concept(tmp_path, domain="d", name="B", aliases=["撞名"])
    reg, errors, warnings = concept_store.build_registry(concept_store.scan_concept_pages(tmp_path))
    assert not errors
    assert any("alias collision" in w for w in warnings)


def test_write_aliases_derived_view(tmp_path):
    _mk_concept(tmp_path, domain="game-theory", name="信号博弈", aliases=["Signaling Game"])
    reg, _, _ = concept_store.build_registry(concept_store.scan_concept_pages(tmp_path))
    concept_store.write_registry(tmp_path, reg)
    concept_store.write_aliases(tmp_path, reg)
    text = (tmp_path / "aliases.md").read_text(encoding="utf-8")
    assert "Signaling Game" in text and "signaling-game" in text and "派生文件" in text
```

- [ ] **Step 2:** Run `python -m pytest tests/test_concept_store.py -q` → Expected FAIL（scan/build 未定义）。

- [ ] **Step 3: 实现（追加到 `scripts/concept_store.py`）**

```python
def _norm(term: str) -> str:
    return re.sub(r"\s+", " ", str(term).strip()).lower()


def _concept_dirs(vault: Path):
    yield vault / "concepts"
    domains = vault / "domains"
    if domains.exists():
        for d in sorted(p for p in domains.iterdir() if p.is_dir()):
            yield d / "concepts"


def scan_concept_pages(vault) -> list[dict]:
    """扫描全部概念页（顶层 shared + 各 domain），page_path 以实际位置为准。"""
    vault = Path(vault)
    metas = []
    for cdir in _concept_dirs(vault):
        if not cdir.exists():
            continue
        for f in sorted(cdir.glob("*.md")):
            meta, _ = mdpage.read_page(f)
            if meta.get("type") == "concept":
                meta["page_path"] = f.relative_to(vault).as_posix()
                metas.append(meta)
    return metas


def build_registry(metas: list[dict]) -> tuple[dict, list[str], list[str]]:
    """返回 (registry, errors, warnings)。registry: canonical_id → 条目。
    duplicate/missing canonical_id 是结构性损坏（errors，调用方拒绝写盘）；
    同域名/别名碰撞是重复概念征兆（warnings，阻断属 P6 门禁）。"""
    reg: dict = {}
    errors: list[str] = []
    warnings: list[str] = []
    seen_terms: dict = {}
    for m in metas:
        cid = m.get("canonical_id")
        if not cid:
            errors.append(f"missing canonical_id: {m.get('page_path')}")
            continue
        if cid in reg:
            errors.append(f"duplicate canonical_id: {cid} ({reg[cid]['page_path']} vs {m['page_path']})")
            continue
        reg[cid] = {"canonical_name": m.get("canonical_name", ""),
                    "aliases": list(m.get("aliases") or []),
                    "scope": m.get("scope", "domain"),
                    "domain": m.get("domain", ""),
                    "page_path": m["page_path"]}
        for term in [reg[cid]["canonical_name"], *reg[cid]["aliases"]]:
            key = (reg[cid]["domain"], _norm(term))
            if key in seen_terms and seen_terms[key] != cid:
                warnings.append(f"alias collision in {key[0]}: '{term}' -> {seen_terms[key]} and {cid}")
            seen_terms[key] = cid
    return reg, errors, warnings


def write_registry(vault, registry: dict) -> str:
    """写 concepts/_registry.yaml（key 排序，字节级确定），返回 sha256（P4 work order 用）。"""
    vault = Path(vault)
    out = vault / "concepts" / "_registry.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump({k: registry[k] for k in sorted(registry)},
                          allow_unicode=True, sort_keys=True, default_flow_style=False)
    out.write_text(text, encoding="utf-8")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_aliases(vault, registry: dict) -> None:
    """派生 aliases.md：别名 → 概念页（人读视图；/ingest 不写此文件）。"""
    rows = set()
    for cid in sorted(registry):
        e = registry[cid]
        for term in [e["canonical_name"], *e["aliases"]]:
            rows.add(f"- {term} → [[{e['page_path']}|{e['canonical_name']}]] (`{cid}`)")
    lines = ["# 别名索引（派生文件，由 rebuild-registry 重建，勿手改）", ""] + sorted(rows)
    (Path(vault) / "aliases.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
```

- [ ] **Step 4:** Run `python -m pytest tests/test_concept_store.py -q` → Expected PASS（9）。
- [ ] **Step 5:** Commit

```
git add scripts/concept_store.py tests/test_concept_store.py
git commit -m "Add registry scan/build/write + aliases derived view (deterministic, dup detection)" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: `concept_store.py` 之三 —— resolve_or_create_concept 协议（P2 核心）

**Files:** Modify `scripts/concept_store.py`、追加 `tests/test_concept_store.py`

- [ ] **Step 1: 追加失败测试**

追加到 `tests/test_concept_store.py`：

```python
def _registry_of(vault):
    reg, errors, warnings = concept_store.build_registry(concept_store.scan_concept_pages(vault))
    assert not errors
    return reg


def test_resolve_hits_alias_in_domain_then_shared(tmp_path):
    _mk_concept(tmp_path, domain="game-theory", name="信号博弈", aliases=["Signaling Game"])
    reg = _registry_of(tmp_path)
    hit = concept_store.resolve("signaling game", domain="game-theory", registry=reg)
    assert hit is not None and hit[0] == "concept.game-theory.signaling-game"
    assert concept_store.resolve("不存在的概念", domain="game-theory", registry=reg) is None


def test_resolve_or_create_merges_existing_never_duplicates(tmp_path):
    _mk_concept(tmp_path, domain="game-theory", name="信号博弈", aliases=["Signaling Game"])
    reg = _registry_of(tmp_path)
    cid, path, action = concept_store.resolve_or_create_concept(
        tmp_path, mention="Signaling Game", domain="game-theory", registry=reg,
        source_ref={"source": "whitepaper", "sections": ["12.2"]})
    assert action == "merged" and cid == "concept.game-theory.signaling-game"
    pages = list((tmp_path / "domains" / "game-theory" / "concepts").glob("*.md"))
    assert len(pages) == 1  # 绝不新建重复页
    meta, _ = mdpage.read_page(path)
    assert {"source": "whitepaper", "sections": ["12.2"]} in meta["source_refs"]


def test_merge_accumulates_source_refs_and_sections(tmp_path):
    _mk_concept(tmp_path, domain="game-theory", name="信号博弈", aliases=["Signaling Game"])
    reg = _registry_of(tmp_path)
    for sec in ("5.2", "12.2", "5.2"):
        concept_store.resolve_or_create_concept(
            tmp_path, mention="信号博弈", domain="game-theory", registry=reg,
            source_ref={"source": "whitepaper", "sections": [sec]})
    meta, _ = mdpage.read_page(tmp_path / "domains/game-theory/concepts/signaling-game.md")
    assert meta["source_refs"] == [{"source": "whitepaper", "sections": ["5.2", "12.2"]}]  # 去重累积


def test_resolve_or_create_creates_when_miss(tmp_path):
    reg = {}
    cid, path, action = concept_store.resolve_or_create_concept(
        tmp_path, mention="纳什均衡", domain="game-theory", registry=reg,
        aliases=["Nash Equilibrium"], source_ref={"source": "wp", "sections": ["3.1"]})
    assert action == "created" and cid == "concept.game-theory.nash-equilibrium"
    meta, body = mdpage.read_page(path)
    assert meta["status"] == "proposed" and meta["managed_by"] == "pipeline"
    assert meta["scope"] == "domain" and meta["domain"] == "game-theory"
    # §8 concept 最小结构小节齐全
    for sec in ("## 一句话", "## 直觉", "## 形式化", "## 各章如何处理", "## 与其他概念的关系"):
        assert sec in body


def test_same_name_different_domain_stays_separate(tmp_path):
    # 同名异义不合并（spec §6：econ 的 utility vs cs 的 utility）
    _mk_concept(tmp_path, domain="econ", name="Utility")
    reg = _registry_of(tmp_path)
    cid, _, action = concept_store.resolve_or_create_concept(
        tmp_path, mention="Utility", domain="cs", registry=reg)
    assert action == "created" and cid == "concept.cs.utility"


def test_create_existing_page_raises(tmp_path):
    _mk_concept(tmp_path, domain="d", name="X")
    try:
        concept_store.create_concept(tmp_path, domain="d", name="X")
        assert False, "should raise"
    except FileExistsError:
        pass
```

- [ ] **Step 2:** Run `python -m pytest tests/test_concept_store.py -q` → Expected FAIL（resolve 等未定义）。

- [ ] **Step 3: 实现（追加到 `scripts/concept_store.py`）**

```python
CONCEPT_BODY = """# {name}

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
"""


def resolve(mention: str, *, domain: str, registry: dict):
    """mention 命中 canonical（名/别名，先本域后 shared）→ (canonical_id, entry)；未命中 → None。"""
    if mention in registry:  # 直接给 canonical_id
        return mention, registry[mention]
    n = _norm(mention)
    for want in (domain, "shared"):
        for cid in sorted(registry):
            e = registry[cid]
            if e["domain"] != want:
                continue
            if _norm(e["canonical_name"]) == n or any(_norm(a) == n for a in e["aliases"]):
                return cid, e
    return None


def create_concept(vault, *, domain: str, name: str, aliases=(), source_ref=None) -> Path:
    """新建骨架概念页（status: proposed；§8 最小结构）。页已存在则拒绝——必须走 merge。"""
    cid = canonical_id(domain, name, aliases)
    slug = cid.rsplit(".", 1)[1]
    if domain == "shared":
        rel = Path("concepts") / f"{slug}.md"
    else:
        rel = Path("domains") / domain / "concepts" / f"{slug}.md"
    path = Path(vault) / rel
    if path.exists():
        raise FileExistsError(f"concept page already exists: {rel} (use merge, never duplicate)")
    meta = {"type": "concept", "canonical_id": cid, "canonical_name": name,
            "aliases": list(aliases),
            "scope": "shared" if domain == "shared" else "domain",
            "domain": domain,
            "source_refs": [source_ref] if source_ref else [],
            "page_path": rel.as_posix(), "managed_by": "pipeline", "status": "proposed"}
    mdpage.write_page(path, meta, CONCEPT_BODY.format(name=name))
    return path


def merge_concept(vault, page_path: str, *, source_ref=None, new_aliases=()) -> None:
    """merge 进既有页：只累积 frontmatter 的 source_refs/aliases（去重），绝不新建页、不动正文。"""
    path = Path(vault) / page_path
    meta, body = mdpage.read_page(path)
    if new_aliases:
        cur = list(meta.get("aliases") or [])
        known = {_norm(meta.get("canonical_name", ""))} | {_norm(x) for x in cur}
        for a in new_aliases:
            if _norm(a) not in known:
                cur.append(a)
                known.add(_norm(a))
        meta["aliases"] = cur
    if source_ref:
        refs = list(meta.get("source_refs") or [])
        hit = next((r for r in refs if r.get("source") == source_ref.get("source")), None)
        if hit:
            hit["sections"] = list(dict.fromkeys([*hit.get("sections", []),
                                                  *source_ref.get("sections", [])]))
        else:
            refs.append(source_ref)
        meta["source_refs"] = refs
    mdpage.write_page(path, meta, body)


def resolve_or_create_concept(vault, *, mention: str, domain: str, registry: dict,
                              aliases=(), source_ref=None):
    """唯一入口（spec §6）：命中 → merge 既有页，返回 (cid, path, "merged")；
    未命中 → create 骨架页，返回 (cid, path, "created")。新建后调用方需重建 registry。"""
    hit = resolve(mention, domain=domain, registry=registry)
    if hit:
        cid, entry = hit
        merge_concept(vault, entry["page_path"], source_ref=source_ref,
                      new_aliases=[mention, *aliases])
        return cid, Path(vault) / entry["page_path"], "merged"
    path = create_concept(vault, domain=domain, name=mention,
                          aliases=list(aliases), source_ref=source_ref)
    meta, _ = mdpage.read_page(path)
    return meta["canonical_id"], path, "created"
```

- [ ] **Step 4:** Run `python -m pytest tests/test_concept_store.py -q` → Expected PASS（15）。
- [ ] **Step 5:** Commit

```
git add scripts/concept_store.py tests/test_concept_store.py
git commit -m "Add resolve_or_create_concept protocol (merge-never-duplicate, namespaced isolation)" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: CLI `rebuild-registry`

**Files:** Modify `scripts/pipeline.py`、Test `tests/test_p2_cli.py`

- [ ] **Step 1: 写失败测试**

Create `tests/test_p2_cli.py`:

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


mdpage = _load("mdpage")
concept_store = _load("concept_store")


def _run(args, cwd):
    env = {**os.environ, "STUDY_KB_ROOT": str(cwd)}  # 隔离：vault/状态库都落 tmp
    return subprocess.run([sys.executable, str(PIPELINE), *args], cwd=cwd,
                          capture_output=True, text=True, env=env)


def _mk_concept(vault, *, domain, name, aliases=(), cid=None):
    cid = cid or concept_store.canonical_id(domain, name, aliases)
    slug = cid.rsplit(".", 1)[1]
    rel = (Path("concepts") / f"{slug}.md") if domain == "shared" \
        else Path("domains") / domain / "concepts" / f"{slug}.md"
    meta = {"type": "concept", "canonical_id": cid, "canonical_name": name,
            "aliases": list(aliases), "scope": "shared" if domain == "shared" else "domain",
            "domain": domain, "source_refs": [], "page_path": rel.as_posix(),
            "managed_by": "pipeline", "status": "proposed"}
    mdpage.write_page(Path(vault) / rel, meta, f"# {name}\n")


def test_rebuild_registry_writes_derived_files(tmp_path):
    vault = tmp_path / "wiki"
    _mk_concept(vault, domain="game-theory", name="信号博弈", aliases=["Signaling Game"])
    _mk_concept(vault, domain="shared", name="期望效用")
    r = _run(["rebuild-registry"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert (vault / "concepts" / "_registry.yaml").exists()
    assert (vault / "aliases.md").exists()
    assert "2 concepts" in r.stdout and "1 shared" in r.stdout


def test_rebuild_registry_refuses_on_duplicate_canonical_id(tmp_path):
    vault = tmp_path / "wiki"
    _mk_concept(vault, domain="d", name="A", cid="concept.d.x")
    _mk_concept(vault, domain="d", name="B", cid="concept.d.x")
    r = _run(["rebuild-registry"], tmp_path)
    assert r.returncode != 0
    assert "duplicate canonical_id" in (r.stdout + r.stderr)
    assert not (vault / "concepts" / "_registry.yaml").exists()  # 损坏时不写派生


def test_rebuild_registry_no_vault_yet(tmp_path):
    r = _run(["rebuild-registry"], tmp_path)
    assert r.returncode == 0
    assert "no wiki" in r.stdout.lower()
```

- [ ] **Step 2:** Run `python -m pytest tests/test_p2_cli.py -q` → Expected FAIL（子命令未注册）。

- [ ] **Step 3: 实现（`scripts/pipeline.py`）**

在 `_staging_dir()` 之后加：

```python
def _vault_dir() -> Path:
    """新架构输出 vault（spec §4），与状态库同锚点。"""
    return _workspace_root() / "wiki"


def cmd_rebuild_registry(args):
    """从概念页 frontmatter 确定性重建 concepts/_registry.yaml + aliases.md（派生，勿手改）。"""
    import concept_store
    vault = _vault_dir()
    if not vault.exists():
        print("no wiki/ vault yet")
        return
    metas = concept_store.scan_concept_pages(vault)
    registry, errors, warnings = concept_store.build_registry(metas)
    for w in warnings:
        print(f"[warn] {w}")
    if errors:
        for e in errors:
            print(f"[error] {e}", file=sys.stderr)
        raise SystemExit("registry not written (fix duplicate/missing canonical_id first)")
    sha = concept_store.write_registry(vault, registry)
    concept_store.write_aliases(vault, registry)
    shared = sum(1 for e in registry.values() if e["scope"] == "shared")
    print(f"[OK] registry: {len(registry)} concepts ({shared} shared), sha256={sha[:12]}")
```

argparse 注册（status/next 注册块附近）：

```python
    subparsers.add_parser("rebuild-registry", help="从概念页 frontmatter 重建 _registry.yaml + aliases.md")
```

并入 `commands` dict：`'rebuild-registry': cmd_rebuild_registry`。

- [ ] **Step 4:** Run `python -m pytest tests/test_p2_cli.py -q` → Expected PASS（3）。
- [ ] **Step 5: 手动 smoke（隔离根）**

```
$env:STUDY_KB_ROOT = "$env:TEMP\p2-smoke"
# 用 python -c 调 concept_store.create_concept 建 2 个概念页（一 domain 一 shared）
python scripts/pipeline.py rebuild-registry   # 应打印 [OK] registry: 2 concepts (1 shared), sha256=…
python scripts/pipeline.py rebuild-registry   # 再跑一遍 sha 相同（幂等）
$env:STUDY_KB_ROOT = $null
```

- [ ] **Step 6:** Commit

```
git add scripts/pipeline.py tests/test_p2_cli.py
git commit -m "Add rebuild-registry CLI (derived registry + aliases, refuses corrupt state)" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: 全量回归 + P2 验收

**Files:** 无改动，纯验证

- [ ] **Step 1:** Run `python -m pytest -q --ignore=tmp` → Expected: 全 PASS（P0/P1/旧测试零回归）。
- [ ] **Step 2: P2 验收清单（对照 spec §6/§14）**
  - 真值在 frontmatter：`_registry.yaml`/`aliases.md` 全部由 `rebuild-registry` 从概念页重建，重跑字节级一致（sha 相同）。
  - `信号博弈`/`Signaling Game` 经 `resolve_or_create_concept` 归一到同一页，`source_refs` 跨 section 去重累积，目录里只有一个页面文件。
  - 命名空间隔离：`concept.econ.utility` 与 `concept.cs.utility` 各自独立，不合并。
  - 未命中创建的骨架页含 §8 concept 最小结构小节、`status: proposed`、`managed_by: pipeline`。
  - duplicate canonical_id → `rebuild-registry` 拒绝写派生并 exit 非 0；同域别名碰撞 → `[warn]`。
  - 测试隔离：CLI 测试经 `STUDY_KB_ROOT` 写 tmp，真实仓库无 `wiki/`、无状态库。
- [ ] **Step 3:** Run `git status --short` → Expected 干净（报告目录未跟踪可忽略）。

---

## Self-Review

- **Spec 覆盖（§6 逐条）**：①真值在 frontmatter、派生重建（T4 write_registry/write_aliases + T6 CLI）✓；②单一协议 resolve_or_create_concept、命中 merge 绝不新建（T5，含 FileExistsError 防绕过）✓；③registry 完整（domain+shared 全扫）、带 hash（sha256 返回值，P4 work order 消费）、可重建（字节级确定测试）✓；④跨域提升门槛——本期不做自动提升（范围声明，P7），shared 概念页可手动落位且被扫描 ✓；⑤同名异义不合并（T5 测试）✓。重复检测：结构损坏 error 拒写 / 别名碰撞 warn（阻断属 P6）——范围声明明确。
- **占位符扫描**：各任务含完整测试 + 实现代码；骨架页内"（待 /ingest 填写）"是运行时产物设计（P4 填充），非计划占位符。✓
- **类型一致性**：`build_registry → (dict, list, list)`；`resolve → tuple|None`；`resolve_or_create_concept → (cid, Path, str)`；`_mk_concept` 在两个测试文件中签名一致；CLI 只消费已定义函数。✓
- **不越界**：不动 P0/P1 既有函数与测试；不写状态库；不做 lint 门禁/index（P6）、不做提升流程（P7）、零 LLM。✓
- **真实 API**：`_workspace_root()`/`commands` dict 均为 P1 已落地实物；pyyaml 已在 requirements。✓

## 完成后

P2 完成 = 概念归一基底就位：任何概念提及可确定性归一到唯一 canonical 页，registry/aliases 可重建、带 hash。下一步 **P3（页面模板 + 正文清理）** 或按 spec §15 推进；P4 work order 将直接消费 `write_registry` 的 hash 与 `resolve_or_create_concept` 协议。
