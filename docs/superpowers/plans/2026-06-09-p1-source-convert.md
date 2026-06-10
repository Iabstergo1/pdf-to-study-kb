# P1 source-convert + processing windows + 难页 vision 标记 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: 用 superpowers:executing-plans **Inline** 执行（与 P0 同，单契约链，不拆 subagent）。Steps 用 checkbox（`- [ ]`）跟踪。

**Goal:** 把任意来源（PDF / Markdown，docx/pptx 留接口）确定性地转成 `staging/<source>/source.md` + 抽图 + 难页 PNG 标记，并切成确定性 processing windows（`windows.jsonl`），全程走 P0 状态机记账、幂等可重跑。**目标：纯文本 PDF / Markdown 端到端可跑（registered → profiled → converted → windowed）。**

**Architecture:** 纯确定性 Python（零 LLM）。每个阶段（profile / source-convert / windows）= 一个 CLI 子命令，调 P0 `state_store` 的原子阶段 API 记账、`record_artifact` 记产物 hash、`should_run_stage` 幂等跳过。后端用**适配器**：P1 默认文本后端 = 已装的 PyMuPDF（`fitz`），`pymupdf4llm`/`marker`/`docling`/`pandoc` 作可选适配器（availability check，不可用就降级或标 needs_vision），**本期不强制安装**。

**Tech Stack:** Python 3.11+、stdlib、`pymupdf`(已装)、pytest。可选未来：`pymupdf4llm`/`marker`/`docling`。无 P1 必装新依赖。

**权威链：** 设计真值 spec §5（非文字内容分层）、§3.1（processing windows）、§3.3（状态机/artifacts）。

**运行环境：** 测试用 `D:\miniconda3\envs\pythonProject\python.exe -m pytest`；执行命令用 `pwsh`，不用 Git Bash 调 PowerShell。

**Git：** 从 `feat/p0-state-foundation` 开 `feat/p1-source-convert`（P1 依赖 P0 API）。逐任务提交；合并/push 留到用户确认。

> **2026-06-10 修订**（对应重构分析报告 F1/F2/F3，见 `pipeline-workspace/reports/2026-06-10-refactor-analysis.md`）：
> ① **路径锚定统一**：新增 `_workspace_root()`（默认 repo 根，环境变量 `STUDY_KB_ROOT` 可覆盖），状态库与 staging 都建其上，修掉早稿"库在 repo 根、staging 在 cwd"的矛盾，测试经 env 注入 tmp、绝不写真实仓库状态库。
> ② **新增 Task 7 `pipeline fail` 维护命令**：救回 CLI 崩溃残留的 running 阶段（P0 契约下 running 拒绝重复 start，崩溃后否则无自助出口）。
> ③ **`profile` 阶段真实产出**：写 `staging/<source>/pages.jsonl`（逐页 text_len/公式风险/needs_vision），不再是空转盖戳；convert 用同一批纯函数复算，结果确定性一致。

---

## 真实 P0 API（P1 在其上构建，勿改 P0 代码/测试）

`scripts/state_store.py`（已实现并测试）：
- `init_db(db)` / `connect(db)`
- `register_source(db, source_id, *, domain, fmt)` → `(registered, done)`
- `get_source(db, source_id)`
- `start_stage(db, source_id, stage, *, input_hash=None) -> run_id`
- `complete_stage(db, source_id, stage, *, output_hash=None)`
- `fail_stage(db, source_id, stage, *, error)`
- `should_run_stage(db, source_id, stage, *, input_hash) -> bool`
- `status_rows(db)` / `next_actions(db)`
- `STAGES = [registered, profiled, converted, windowed, workorder_ready, ingest_waiting, ingesting, ingested, lint]`，合法迁移见 `_allowed_next`
- **`artifacts` 表已建但无写入 helper** → 本期 **T2 新增 `record_artifact` / `list_artifacts`**（扩展 P0 模块，属允许的"在 P0 API 上构建"）。

`scripts/locks.py` / `scripts/snapshots.py`：本期不需要（锁是 `/ingest` 的事，P1 只写 `pipeline-workspace/staging/`，不写 vault）。

**路径锚定（本期统一）**：状态库与 staging 都锚定在 `_workspace_root()` 下——默认 = repo 根（保持 P0 行为），设了环境变量 `STUDY_KB_ROOT` 则以其为根。`_vault_state_db()` 改为 `_workspace_root() / "pipeline-workspace/state/study-kb.sqlite"`；`_staging_dir(id)` = `_workspace_root() / "pipeline-workspace/staging" / id`。P0 测试不受影响：`test_state_db_path_is_vault_level` 只 grep 路径字符串（仍在）；`test_status_smoke_runs` 不设该 env、真实仓库库文件依旧不存在（P1 CLI 测试全程设 `STUDY_KB_ROOT=tmp`，不会创建它）。

## 本期范围与取舍（请先看）

- **做**：`add-source`（注册）、`profile`（逐页文本+风险+needs_vision 标记，**真实产出 `staging/<source>/pages.jsonl`**）、`source-convert`（文本后端 + md 直通 + 抽图 + 难页渲 PNG）、`windows`（确定性切窗）、`fail`（维护命令：把崩溃残留的 running 阶段标 failed，恢复可重跑）；全部接 P0 状态机 + artifacts + 幂等。
- **默认文本后端 = PyMuPDF（`fitz`，已装）**，保证当前 env 端到端可跑；`pymupdf4llm`/`marker`/`docling`/`pandoc` 作适配器，不可用则跳过/降级，**本期不强制安装、不写它们的集成实现**（只留 availability-check 接口 + 降级路径）。
- **难页**：P1 只**标记** `needs_vision` 并渲染 PNG 到 `assets/`；"Claude 读图写 KaTeX" 是 **P4 `/ingest`**，不在本期。
- **不做**：work order 生成（P4）、任何写 vault/wiki、任何 LLM 调用、docx/pptx 真实转换实现（留适配器 stub）。

## File Structure

- Modify `scripts/state_store.py` — 加 `record_artifact` / `list_artifacts`（仅新增函数，不改既有）。
- Create `scripts/windowing.py` — 确定性 processing windows。
- Create `scripts/source_convert.py` — 后端适配器 + 文本后端(PyMuPDF) + md 直通 + needs_vision 渲染 + 产出 source.md/assets。
- Create `scripts/source_profile.py` — 逐页 profile（文本长度/公式风险/needs_vision）。注：旧 `pdf_profile.py` 不动（P4 前旧路径保留）。
- Modify `scripts/pipeline.py` — 加 `_workspace_root()`（`STUDY_KB_ROOT` 可覆盖；`_vault_state_db()` 改建其上）+ `add-source` / `profile` / `source-convert` / `windows` / `fail` 子命令（沿用现有 `commands` dict 模式）。
- Tests：`tests/test_record_artifact.py`、`tests/test_windowing.py`、`tests/test_source_convert.py`、`tests/test_p1_cli.py`。

---

### Task 1: 开工分支

- [ ] **Step 1:** Run `git checkout -b feat/p1-source-convert`（基于 feat/p0-state-foundation）→ Expected 切到新分支。
- [ ] **Step 2:** Run `git status --short` → Expected 干净。

---

### Task 2: `state_store.record_artifact` / `list_artifacts`

**Files:** Modify `scripts/state_store.py`、Test `tests/test_record_artifact.py`

- [ ] **Step 1: 写失败测试**

Create `tests/test_record_artifact.py`:

```python
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("state_store", ROOT / "scripts" / "state_store.py")
state_store = importlib.util.module_from_spec(spec); spec.loader.exec_module(state_store)


def test_record_and_list_artifact(tmp_path):
    db = tmp_path / "study-kb.sqlite"
    state_store.init_db(db)
    state_store.register_source(db, "s1", domain="d", fmt="pdf")
    aid = state_store.record_artifact(db, "s1", kind="source_md", path="staging/s1/source.md", sha256="a" * 64)
    rows = state_store.list_artifacts(db, "s1")
    assert aid > 0
    assert rows[0]["kind"] == "source_md"
    assert rows[0]["sha256"] == "a" * 64


def test_record_artifact_replaces_same_kind_path(tmp_path):
    db = tmp_path / "study-kb.sqlite"
    state_store.init_db(db)
    state_store.register_source(db, "s1", domain="d", fmt="pdf")
    state_store.record_artifact(db, "s1", kind="source_md", path="staging/s1/source.md", sha256="a" * 64)
    state_store.record_artifact(db, "s1", kind="source_md", path="staging/s1/source.md", sha256="b" * 64)
    rows = [r for r in state_store.list_artifacts(db, "s1") if r["kind"] == "source_md"]
    assert len(rows) == 1 and rows[0]["sha256"] == "b" * 64  # 幂等：同 (source,kind,path) 覆盖
```

- [ ] **Step 2:** Run `python -m pytest tests/test_record_artifact.py -q` → Expected FAIL（函数未定义）。

- [ ] **Step 3: 实现（追加到 `scripts/state_store.py` 末尾）**

```python
def record_artifact(db_path, source_id: str, *, kind: str, path: str, sha256: str) -> int:
    """登记/更新一个产物（同 source+kind+path 覆盖，保证幂等重跑不堆重复行）。"""
    con = connect(db_path)
    try:
        con.execute(
            "DELETE FROM artifacts WHERE source_id=? AND kind=? AND path=?",
            (source_id, kind, path))
        cur = con.execute(
            "INSERT INTO artifacts(source_id,kind,path,sha256,created_at) VALUES (?,?,?,?,?)",
            (source_id, kind, path, sha256, _now()))
        con.commit()
        return int(cur.lastrowid)
    finally:
        con.close()


def list_artifacts(db_path, source_id: str) -> list[dict]:
    con = connect(db_path)
    try:
        rows = con.execute(
            "SELECT id,source_id,kind,path,sha256,created_at FROM artifacts WHERE source_id=? ORDER BY id",
            (source_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()
```

- [ ] **Step 4:** Run `python -m pytest tests/test_record_artifact.py -q` → Expected PASS（2）。
- [ ] **Step 5:** Commit
```
git add scripts/state_store.py tests/test_record_artifact.py docs/superpowers/plans/2026-06-09-p1-source-convert.md
git commit -m "Add artifact record/list helpers on P0 state_store" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: `windowing.py` —— 确定性 processing windows（P1 核心）

**Files:** Create `scripts/windowing.py`、Test `tests/test_windowing.py`

- [ ] **Step 1: 写失败测试**

Create `tests/test_windowing.py`:

```python
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("windowing", ROOT / "scripts" / "windowing.py")
windowing = importlib.util.module_from_spec(spec); spec.loader.exec_module(windowing)


def test_splits_by_top_headings():
    md = "# A\n\naaa\n\n# B\n\nbbb\n"
    ws = windowing.build_windows(md, target_tokens=1000, max_tokens=2000, overlap_tokens=0)
    paths = [w["heading_path"] for w in ws]
    assert paths == ["A", "B"]
    assert all(w["window_id"] for w in ws)


def test_oversize_section_subsplit_with_overlap():
    body = "x " * 3000  # ~6000 chars ~1500 tokens
    md = f"# Big\n\n{body}\n"
    ws = windowing.build_windows(md, target_tokens=300, max_tokens=400, overlap_tokens=50)
    assert len(ws) >= 3
    assert all(w["heading_path"] == "Big" for w in ws)
    # overlap：后一窗起点早于前一窗终点
    assert ws[1]["char_start"] < ws[0]["char_end"]


def test_no_heading_fallback_token_slices():
    md = "y " * 2000
    ws = windowing.build_windows(md, target_tokens=300, max_tokens=400, overlap_tokens=0)
    assert len(ws) >= 2
    assert all(w["heading_path"] == "" for w in ws)


def test_deterministic_same_input_same_output():
    md = "# A\n\n" + ("z " * 1000)
    a = windowing.build_windows(md, target_tokens=300, max_tokens=400, overlap_tokens=50)
    b = windowing.build_windows(md, target_tokens=300, max_tokens=400, overlap_tokens=50)
    assert a == b


def test_window_ids_stable_and_unique():
    md = "# A\n\naaa\n\n# B\n\nbbb\n"
    ws = windowing.build_windows(md)
    ids = [w["window_id"] for w in ws]
    assert len(ids) == len(set(ids))
```

- [ ] **Step 2:** Run `python -m pytest tests/test_windowing.py -q` → Expected FAIL。

- [ ] **Step 3: 实现**

Create `scripts/windowing.py`:

```python
"""确定性 processing windows（spec §3.1）：按标题切，超长按 token 滑窗 + overlap。纯函数、无 I/O。"""
from __future__ import annotations
import re

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


def _est_tokens(text: str) -> int:
    # 粗略：~4 字符/token；确定性即可，不依赖外部分词器
    return max(1, len(text) // 4)


def _sections(md: str):
    """切成 (heading_path, char_start, char_end) 段；首个 heading 前的前言归 ""。"""
    lines = md.splitlines(keepends=True)
    secs, cur_path, cur_start, pos = [], "", 0, 0
    for ln in lines:
        m = _HEADING.match(ln.rstrip("\n"))
        if m:
            if pos > cur_start:
                secs.append((cur_path, cur_start, pos))
            cur_path, cur_start = m.group(2).strip(), pos
        pos += len(ln)
    if pos > cur_start:
        secs.append((cur_path, cur_start, pos))
    return secs


def build_windows(md: str, *, target_tokens: int = 2000, max_tokens: int = 4000,
                  overlap_tokens: int = 200) -> list[dict]:
    out: list[dict] = []
    idx = 0
    for path, s, e in _sections(md):
        seg = md[s:e]
        if _est_tokens(seg) <= max_tokens:
            out.append(_win(idx, path, s, e, 0)); idx += 1
            continue
        # 超长：按 token（≈char）滑窗 + overlap
        target_chars = target_tokens * 4
        overlap_chars = overlap_tokens * 4
        step = max(1, target_chars - overlap_chars)
        p = s
        while p < e:
            w_end = min(e, p + target_chars)
            out.append(_win(idx, path, p, w_end, overlap_chars if p > s else 0)); idx += 1
            if w_end >= e:
                break
            p += step
    return out


def _win(idx: int, path: str, c0: int, c1: int, overlap_before: int) -> dict:
    return {
        "window_id": f"w{idx:04d}",
        "heading_path": path,
        "char_start": c0,
        "char_end": c1,
        "overlap_before": overlap_before,
    }
```

- [ ] **Step 4:** Run `python -m pytest tests/test_windowing.py -q` → Expected PASS（5）。
- [ ] **Step 5:** Commit
```
git add scripts/windowing.py tests/test_windowing.py
git commit -m "Add deterministic processing windows (heading split + token sliding overlap)" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: `source_profile.py` —— 逐页 profile + needs_vision 判定

**Files:** Create `scripts/source_profile.py`、Test 并入 `tests/test_source_convert.py`（见 Task 5 Step 1 同文件先建本任务测试）

- [ ] **Step 1: 写失败测试**

Create `tests/test_source_convert.py`（本任务先放 profile 测试；Task 5 追加 convert 测试）:

```python
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


source_profile = _load("source_profile")


def test_needs_vision_high_formula():
    page = {"text_len": 800, "formula_symbols": 25, "image_count": 0}
    assert source_profile.needs_vision(page) is True


def test_needs_vision_blank_image_page():
    page = {"text_len": 10, "formula_symbols": 0, "image_count": 3}
    assert source_profile.needs_vision(page) is True


def test_plain_text_page_no_vision():
    page = {"text_len": 1500, "formula_symbols": 1, "image_count": 0}
    assert source_profile.needs_vision(page) is False


def test_formula_symbol_count_detects_latex_and_greek():
    n = source_profile.count_formula_symbols(r"设 $\alpha$ 与 $\sum_{i} x_i^2$，则 ∫ f dx ≥ 0")
    assert n >= 4


def test_profile_source_md_single_page(tmp_path):
    src = tmp_path / "n.md"; src.write_text("# T\n\nbody\n", encoding="utf-8")
    pages = source_profile.profile_source(src, fmt="md")
    assert len(pages) == 1 and pages[0]["page"] == 1 and "needs_vision" in pages[0]
```

- [ ] **Step 2:** Run `python -m pytest tests/test_source_convert.py -q` → Expected FAIL。

- [ ] **Step 3: 实现**

Create `scripts/source_profile.py`:

```python
"""逐页 profile：文本长度、公式符号密度、needs_vision 判定（确定性，零 LLM；spec §5）。"""
from __future__ import annotations
import re

_FORMULA = re.compile(r"[\\∑∫∂∇√±×÷≤≥≠≈→←↔∈∉⊂⊆∀∃αβγδεθλμπσφψωΩ]|\$[^$]+\$|\^|_\{")


def count_formula_symbols(text: str) -> int:
    return len(_FORMULA.findall(text))


def needs_vision(page: dict) -> bool:
    """难页：公式符号密集 / 文本过短且有图（疑似扫描或图密集）。"""
    text_len = page.get("text_len", 0)
    formula = page.get("formula_symbols", 0)
    images = page.get("image_count", 0)
    if formula >= 12:
        return True
    if text_len < 50 and images >= 1:
        return True
    return False


def profile_page(page_number: int, text: str, image_count: int) -> dict:
    text_len = len(text.strip())
    formula = count_formula_symbols(text)
    p = {"page": page_number, "text_len": text_len, "formula_symbols": formula,
         "image_count": image_count}
    p["needs_vision"] = needs_vision(p)
    return p


def profile_source(src_path, *, fmt: str) -> list[dict]:
    """逐页 profile 整个来源（CLI profile 阶段的真实产出；pdf 用 PyMuPDF，md 视为单页）。"""
    from pathlib import Path
    src = Path(src_path)
    if fmt == "md":
        return [profile_page(1, src.read_text(encoding="utf-8"), image_count=0)]
    if fmt == "pdf":
        import fitz  # PyMuPDF（已装）
        doc = fitz.open(str(src))
        pages = [profile_page(i + 1, doc[i].get_text(), image_count=len(doc[i].get_images()))
                 for i in range(len(doc))]
        doc.close()
        return pages
    raise ValueError(f"no P1 profile backend for fmt={fmt}")
```

- [ ] **Step 4:** Run `python -m pytest tests/test_source_convert.py -q` → Expected PASS（5）。
- [ ] **Step 5:** Commit
```
git add scripts/source_profile.py tests/test_source_convert.py
git commit -m "Add per-page profile + needs_vision decision (deterministic)" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: `source_convert.py` —— 后端适配器 + 文本后端 + md 直通

**Files:** Create `scripts/source_convert.py`、追加 `tests/test_source_convert.py`

- [ ] **Step 1: 追加失败测试**

追加到 `tests/test_source_convert.py`：

```python
source_convert = _load("source_convert")


def test_markdown_passthrough(tmp_path):
    src = tmp_path / "note.md"; src.write_text("# Title\n\nbody\n", encoding="utf-8")
    out_dir = tmp_path / "staging" / "note"
    res = source_convert.convert(src, out_dir=out_dir, fmt="md")
    md = (out_dir / "source.md").read_text(encoding="utf-8")
    assert "# Title" in md
    assert res["source_md"].endswith("source.md")
    assert res["pages"]  # 至少一段 profile


def test_unknown_backend_raises(tmp_path):
    src = tmp_path / "x.xyz"; src.write_text("z", encoding="utf-8")
    try:
        source_convert.convert(src, out_dir=tmp_path / "o", fmt="xyz")
        assert False, "should raise"
    except source_convert.BackendUnavailable:
        pass


def test_text_pdf_backend_or_skips_when_pymupdf_missing(tmp_path):
    # 有 pymupdf 时跑文本 PDF；没有则适配器报 unavailable（两种都算契约成立）
    import importlib.util as u
    if u.find_spec("fitz") is None:
        import pytest; pytest.skip("pymupdf not installed")
    # 用 fitz 造一个最小单页 PDF
    import fitz
    src = tmp_path / "tiny.pdf"
    doc = fitz.open(); page = doc.new_page(); page.insert_text((72, 72), "Hello PDF body text")
    doc.save(str(src)); doc.close()
    out_dir = tmp_path / "staging" / "tiny"
    res = source_convert.convert(src, out_dir=out_dir, fmt="pdf")
    md = (out_dir / "source.md").read_text(encoding="utf-8")
    assert "Hello PDF" in md
    assert res["pages"][0]["page"] == 1
```

- [ ] **Step 2:** Run `python -m pytest tests/test_source_convert.py -q` → Expected FAIL（convert 未定义）。

- [ ] **Step 3: 实现**

Create `scripts/source_convert.py`:

```python
"""source-convert：把来源转成 staging/<source>/source.md + 逐页 profile + 难页 PNG（spec §5）。

后端适配器：md 直通 / pdf 文本(PyMuPDF) 默认；marker/docling/pandoc/pymupdf4llm 作可选适配器
（availability check，不可用就降级或标 needs_vision）。本期不强制安装重后端。
"""
from __future__ import annotations
import hashlib
from pathlib import Path

import source_profile


class BackendUnavailable(RuntimeError):
    pass


def _sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def convert(src_path, *, out_dir, fmt: str) -> dict:
    """返回 {source_md, assets_dir, pages:[profile...], needs_vision_pages:[int...]}。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    assets_dir = out_dir / "assets"
    if fmt == "md":
        md, pages = _convert_markdown(Path(src_path))
    elif fmt == "pdf":
        md, pages = _convert_pdf_text(Path(src_path), assets_dir)
    else:
        raise BackendUnavailable(f"no P1 backend for fmt={fmt} (docx/pptx 适配器后续期实现)")
    source_md = out_dir / "source.md"
    source_md.write_text(md, encoding="utf-8")
    return {
        "source_md": str(source_md),
        "sha256": _sha256_text(md),
        "assets_dir": str(assets_dir),
        "pages": pages,
        "needs_vision_pages": [p["page"] for p in pages if p.get("needs_vision")],
    }


def _convert_markdown(src: Path):
    text = src.read_text(encoding="utf-8")
    pages = [source_profile.profile_page(1, text, image_count=0)]
    return text, pages


def _convert_pdf_text(src: Path, assets_dir: Path):
    try:
        import fitz  # PyMuPDF（已装）
    except ImportError as exc:
        raise BackendUnavailable("pymupdf (fitz) not installed") from exc
    doc = fitz.open(str(src))
    parts, pages = [], []
    for i in range(len(doc)):
        page = doc[i]
        text = page.get_text()
        img_count = len(page.get_images())
        prof = source_profile.profile_page(i + 1, text, image_count=img_count)
        pages.append(prof)
        parts.append(f"\n\n<!-- page {i + 1} -->\n\n{text.strip()}\n")
        if prof["needs_vision"]:
            assets_dir.mkdir(parents=True, exist_ok=True)
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            pix.save(str(assets_dir / f"p{i + 1:04d}.png"))
    doc.close()
    return "".join(parts).strip() + "\n", pages
```

- [ ] **Step 4:** Run `python -m pytest tests/test_source_convert.py -q` → Expected PASS（profile 5 + convert 3）。
- [ ] **Step 5:** Commit
```
git add scripts/source_convert.py tests/test_source_convert.py
git commit -m "Add source-convert: md passthrough + PyMuPDF text backend + needs_vision PNG render" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: CLI 子命令 + 接 P0 状态机（add-source / profile / source-convert / windows）

**Files:** Modify `scripts/pipeline.py`、Test `tests/test_p1_cli.py`

- [ ] **Step 1: 写 smoke 测试**

Create `tests/test_p1_cli.py`:

```python
import os, subprocess, sys, sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "scripts" / "pipeline.py"


def _run(args, cwd):
    env = {**os.environ, "STUDY_KB_ROOT": str(cwd)}  # 隔离：状态库/staging 都落 tmp，绝不写真实仓库
    return subprocess.run([sys.executable, str(PIPELINE), *args], cwd=cwd,
                          capture_output=True, text=True, env=env)


def test_add_source_then_status(tmp_path):
    note = tmp_path / "raw" / "note.md"; note.parent.mkdir(parents=True); note.write_text("# T\n\nbody\n", encoding="utf-8")
    r1 = _run(["add-source", "--source", "note", "--domain", "misc", "--path", str(note), "--fmt", "md"], tmp_path)
    assert r1.returncode == 0, r1.stderr
    r2 = _run(["status"], tmp_path)
    assert "note" in r2.stdout and "registered" in r2.stdout


def test_source_convert_and_windows_advance_state(tmp_path):
    note = tmp_path / "raw" / "note.md"; note.parent.mkdir(parents=True)
    note.write_text("# A\n\naaa\n\n# B\n\nbbb\n", encoding="utf-8")
    _run(["add-source", "--source", "note", "--domain", "misc", "--path", str(note), "--fmt", "md"], tmp_path)
    assert _run(["profile", "--source", "note"], tmp_path).returncode == 0
    assert (tmp_path / "pipeline-workspace/staging/note/pages.jsonl").exists()  # profile 真实产出
    assert _run(["source-convert", "--source", "note"], tmp_path).returncode == 0
    assert _run(["windows", "--source", "note"], tmp_path).returncode == 0
    # 产物存在
    assert (tmp_path / "pipeline-workspace/staging/note/source.md").exists()
    assert (tmp_path / "pipeline-workspace/staging/note/windows.jsonl").exists()
    # 状态推进到 windowed/done
    r = _run(["status"], tmp_path); assert "windowed" in r.stdout
```

> 说明：CLI 用 vault 级状态库，锚定 `_workspace_root()`（默认 repo 根；测试经 `STUDY_KB_ROOT` 注入 tmp，见前文"路径锚定"）。本期 add-source 同时把 raw 路径记为 artifact。`--source` 为 source_id。

- [ ] **Step 2:** Run `python -m pytest tests/test_p1_cli.py -q` → Expected FAIL（子命令未注册）。

- [ ] **Step 3: 在 `pipeline.py` 加子命令（沿用现有 `commands` dict 模式）**

加命令函数（用 `state_store`、`source_convert`、`windowing`；DB 路径用 `_vault_state_db()`；staging 在 `pipeline-workspace/staging/<source>/`）：

```python
import os


def _workspace_root() -> Path:
    """状态库/staging 锚点：默认 repo 根；STUDY_KB_ROOT 覆盖（测试隔离/多库场景）。"""
    env = os.environ.get("STUDY_KB_ROOT")
    return Path(env) if env else Path(__file__).resolve().parents[1]


# P0 的 _vault_state_db() 同步改为基于 _workspace_root()（默认行为不变）：
def _vault_state_db() -> Path:
    return _workspace_root() / "pipeline-workspace/state/study-kb.sqlite"


def _staging_dir(source_id: str) -> Path:
    return _workspace_root() / "pipeline-workspace/staging" / source_id


def cmd_add_source(args):
    import state_store
    db = _vault_state_db(); db.parent.mkdir(parents=True, exist_ok=True)
    state_store.init_db(db)
    state_store.register_source(db, args.source, domain=args.domain, fmt=args.fmt)
    # 记录 raw 路径为 artifact，供后续阶段定位
    raw = Path(args.path)
    import hashlib
    sha = hashlib.sha256(raw.read_bytes()).hexdigest() if raw.exists() else ""
    state_store.record_artifact(db, args.source, kind="raw_source", path=str(raw), sha256=sha)
    print(f"[OK] registered source '{args.source}' (domain={args.domain}, fmt={args.fmt})")


def _raw_path(db, state_store, source_id: str) -> Path:
    for a in state_store.list_artifacts(db, source_id):
        if a["kind"] == "raw_source":
            return Path(a["path"])
    raise SystemExit(f"no raw_source artifact for {source_id}; run add-source first")


def cmd_profile(args):
    import state_store, source_profile, json, hashlib
    db = _vault_state_db()
    raw = _raw_path(db, state_store, args.source)
    src_row = state_store.get_source(db, args.source)
    ihash = hashlib.sha256(raw.read_bytes()).hexdigest()
    if not state_store.should_run_stage(db, args.source, "profiled", input_hash=ihash):
        print("[skip] profiled up-to-date"); return
    state_store.start_stage(db, args.source, "profiled", input_hash=ihash)
    try:
        pages = source_profile.profile_source(raw, fmt=src_row["format"])
        out = _staging_dir(args.source); out.mkdir(parents=True, exist_ok=True)
        pages_path = out / "pages.jsonl"
        pages_path.write_text("\n".join(json.dumps(p, ensure_ascii=False) for p in pages),
                              encoding="utf-8")
        ohash = hashlib.sha256(pages_path.read_bytes()).hexdigest()
        state_store.record_artifact(db, args.source, kind="pages", path=str(pages_path), sha256=ohash)
        state_store.complete_stage(db, args.source, "profiled", output_hash=ohash)
        n_vision = sum(1 for p in pages if p.get("needs_vision"))
        print(f"[OK] profiled → {len(pages)} pages ({n_vision} needs_vision)")
    except Exception as e:
        state_store.fail_stage(db, args.source, "profiled", error=str(e)); raise


def cmd_source_convert(args):
    import state_store, source_convert, hashlib
    db = _vault_state_db()
    raw = _raw_path(db, state_store, args.source)
    src_row = state_store.get_source(db, args.source)
    ihash = hashlib.sha256(raw.read_bytes()).hexdigest()
    if not state_store.should_run_stage(db, args.source, "converted", input_hash=ihash):
        print("[skip] converted up-to-date"); return
    state_store.start_stage(db, args.source, "converted", input_hash=ihash)
    try:
        out = _staging_dir(args.source)
        res = source_convert.convert(raw, out_dir=out, fmt=src_row["format"])
        # pages.jsonl 已由 profile 阶段产出；convert 内部用同一批纯函数复算 needs_vision，结果一致
        state_store.record_artifact(db, args.source, kind="source_md", path=res["source_md"], sha256=res["sha256"])
        state_store.complete_stage(db, args.source, "converted", output_hash=res["sha256"])
        print(f"[OK] converted → {res['source_md']} (needs_vision pages: {res['needs_vision_pages']})")
    except Exception as e:
        state_store.fail_stage(db, args.source, "converted", error=str(e)); raise


def cmd_windows(args):
    import state_store, windowing, json, hashlib
    db = _vault_state_db()
    out = _staging_dir(args.source)
    source_md = out / "source.md"
    if not source_md.exists():
        raise SystemExit("run source-convert first")
    md = source_md.read_text(encoding="utf-8")
    ihash = hashlib.sha256(md.encode("utf-8")).hexdigest()
    if not state_store.should_run_stage(db, args.source, "windowed", input_hash=ihash):
        print("[skip] windowed up-to-date"); return
    state_store.start_stage(db, args.source, "windowed", input_hash=ihash)
    try:
        ws = windowing.build_windows(md)
        (out / "windows.jsonl").write_text(
            "\n".join(json.dumps(w, ensure_ascii=False) for w in ws), encoding="utf-8")
        state_store.record_artifact(db, args.source, kind="windows",
                                    path=str(out / "windows.jsonl"), sha256=ihash)
        state_store.complete_stage(db, args.source, "windowed", output_hash=ihash)
        print(f"[OK] windowed → {len(ws)} windows")
    except Exception as e:
        state_store.fail_stage(db, args.source, "windowed", error=str(e)); raise
```

在 subparsers 注册（沿用现有风格）：

```python
asp = subparsers.add_parser("add-source", help="注册一个来源到状态库")
asp.add_argument("--source", required=True); asp.add_argument("--domain", required=True)
asp.add_argument("--path", required=True); asp.add_argument("--fmt", required=True, choices=["pdf", "md", "docx", "pptx"])
asp.set_defaults  # 见下 commands dict
for name in ["profile", "source-convert", "windows"]:
    p = subparsers.add_parser(name, help=f"{name} 阶段")
    p.add_argument("--source", required=True)
```

并入 `commands` dict：

```python
        'add-source': cmd_add_source,
        'profile': cmd_profile,
        'source-convert': cmd_source_convert,
        'windows': cmd_windows,
```

> 注：`_vault_state_db()` 已在 P0 加入 `pipeline.py`；本期 `cmd_add_source` 先 `init_db` 确保库存在。`import` 沿用脚本目录在 `sys.path` 的现有模式。

- [ ] **Step 4:** Run `python -m pytest tests/test_p1_cli.py -q` → Expected PASS（2）。
- [ ] **Step 5: 手动 smoke（markdown 源端到端）**

```
python scripts/pipeline.py add-source --source demo --domain misc --path <某.md> --fmt md
python scripts/pipeline.py profile --source demo
python scripts/pipeline.py source-convert --source demo
python scripts/pipeline.py windows --source demo
python scripts/pipeline.py status   # demo 应为 windowed
```

- [ ] **Step 6:** Commit
```
git add scripts/pipeline.py tests/test_p1_cli.py
git commit -m "Wire P1 stages (add-source/profile/source-convert/windows) into CLI + P0 state machine" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: `pipeline fail` —— 崩溃恢复维护命令

**Files:** Modify `scripts/pipeline.py`、追加 `tests/test_p1_cli.py`

背景：P0 契约下 `running` 状态拒绝重复 `start_stage`（正确），但 CLI 进程在 `start_stage` 与 `complete/fail` 之间崩溃会留下 running run，source 卡死且用户无自助出口（`next` 只会显示 `resume/in-progress`，而 CLI 阶段无 resume 概念）。`fail_stage` 在库层可救（running run 仍在），本任务把它暴露为维护命令。

- [ ] **Step 1: 追加失败测试**

追加到 `tests/test_p1_cli.py`：

```python
def test_fail_command_unsticks_crashed_running_stage(tmp_path):
    note = tmp_path / "raw" / "note.md"; note.parent.mkdir(parents=True)
    note.write_text("# T\n\nbody\n", encoding="utf-8")
    _run(["add-source", "--source", "note", "--domain", "misc", "--path", str(note), "--fmt", "md"], tmp_path)
    # 模拟崩溃：库层 start_stage 后不 complete/fail
    import importlib.util
    spec = importlib.util.spec_from_file_location("state_store", ROOT / "scripts" / "state_store.py")
    state_store = importlib.util.module_from_spec(spec); spec.loader.exec_module(state_store)
    db = tmp_path / "pipeline-workspace/state/study-kb.sqlite"
    state_store.start_stage(db, "note", "profiled", input_hash="h-crashed")
    r = _run(["fail", "--source", "note", "--stage", "profiled", "--error", "crashed"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert state_store.get_source(db, "note")["current_status"] == "failed"
    # 救回后该阶段可正常重跑
    assert _run(["profile", "--source", "note"], tmp_path).returncode == 0
```

- [ ] **Step 2:** Run `python -m pytest tests/test_p1_cli.py -q` → Expected FAIL（无 `fail` 子命令）。

- [ ] **Step 3: 实现（`scripts/pipeline.py`）**

```python
def cmd_fail(args):
    """维护命令：把崩溃残留的 running 阶段标记为 failed（之后可重跑该阶段）。"""
    import state_store
    db = _vault_state_db()
    state_store.fail_stage(db, args.source, args.stage, error=args.error)
    print(f"[OK] {args.source}/{args.stage} marked failed: {args.error}")
```

argparse 注册：

```python
fp = subparsers.add_parser("fail", help="维护：把崩溃残留的 running 阶段标记为 failed")
fp.add_argument("--source", required=True); fp.add_argument("--stage", required=True)
fp.add_argument("--error", required=True)
```

并入 `commands` dict：`'fail': cmd_fail`。无 running run 时 `fail_stage` 抛 `InvalidTransition`（P0 契约），属正确拒绝，不需吞。

- [ ] **Step 4:** Run `python -m pytest tests/test_p1_cli.py -q` → Expected PASS（3）。
- [ ] **Step 5:** Commit
```
git add scripts/pipeline.py tests/test_p1_cli.py
git commit -m "Add pipeline fail maintenance command (recover crashed running stage)" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: 全量回归 + P1 验收

**Files:** 无改动，纯验证

- [ ] **Step 1:** Run `python -m pytest -q` → Expected: P0 + P1 全 PASS（旧测试不破坏）。
- [ ] **Step 2: P1 验收清单**
  - markdown 源端到端跑通 registered → profiled → converted → windowed（`status` 显示 windowed）。
  - 文本 PDF（fitz 造的最小件）能产出 `source.md`，逐页 profile 含 `needs_vision`。
  - 难页（公式密集/空白带图）被标 `needs_vision` 且渲染 `assets/pXXXX.png`。
  - `windows.jsonl` 确定性、可重跑（同输入同输出）。
  - 幂等：未改输入重跑 `source-convert`/`windows` 走 `[skip]`，不重复写 artifact 行。
  - artifacts 表记录 raw_source / pages / source_md / windows 的 hash。
  - `profile` 真实产出 `pages.jsonl`（artifact kind=pages），不是空转盖戳。
  - 崩溃恢复：人为残留 running 后 `pipeline fail` 能标 failed，且该阶段可重跑。
  - 测试隔离：P1 CLI 测试全程经 `STUDY_KB_ROOT` 写 tmp，真实仓库 `pipeline-workspace/` 不被创建/触碰；P0 `test_status_smoke_runs`、`test_state_db_path_is_vault_level` 仍通过。
- [ ] **Step 3:** Run `git status --short` → Expected 干净。

---

## Self-Review

- **范围对齐 spec §5/§3.1/§3.3**：source-convert（md 直通 + PyMuPDF 文本 + needs_vision PNG）、windows（确定性切窗）、profile（逐页风险）、全程接 P0 原子阶段 API + artifacts + 幂等。✓
- **真实 P0 API**：只用已实现并测试过的 `register_source/start_stage/complete_stage/fail_stage/should_run_stage/get_source` + 本期新增 `record_artifact/list_artifacts`；未改 P0 既有函数/测试。✓
- **占位符**：确定性单元（windowing/profile/record_artifact/CLI 接线）全有完整测试+实现；重后端（marker/docling/pandoc/pymupdf4llm）按 spec 作适配器、本期不强制实现，**非占位符**（有 availability check + 降级路径 + 明确"后续期"）。
- **不越界**：不写 vault、不调 LLM、不做 work order（P4）、不做 query/save（P8）、不动 P0 既有函数/测试（`_vault_state_db()` 改为基于 `_workspace_root()` 是 P1 对 `pipeline.py` 的扩展，默认行为不变、P0 测试不破）。✓
- **2026-06-10 修订自检**：① 路径锚定——CLI 测试 env 注入 tmp，不写真实仓库状态库，P0 smoke 测试兼容性已论证；② `fail` 维护命令——崩溃残留 running 有自助出口，含"救回后可重跑"端到端测试；③ profile 真实产出 `pages.jsonl` + artifact 记账，阶段名实一致。✓
- **可执行性**：默认后端 = 已装 PyMuPDF，当前 env 端到端可跑；windows/profile 纯函数易测。

## 完成后

P1 完成 = 任意 source 可确定性转成 `source.md` + windows + needs_vision 标记，状态机记账、幂等可重跑。下一步 **P2（canonical 概念模型 + registry + 别名归一 + 概念 merge）**，或按 spec §15 顺序推进。
