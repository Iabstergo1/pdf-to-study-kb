# Source Data Contract Foundation (Spec 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `source.md` 降级为 LLM 顺读视图，新增 `blocks.jsonl`（确定性事实层）+ `parse_report.json`（advisory-only 报告），让 windowing 从 blocks 构窗——纯加法、行为保留，零 MinerU、零 skill 树改动。

**Architecture:** 新增 `source_artifacts.py`（数据契约 + 序列化）与 `source_backends/` 包（markdown/pymupdf 后端拆出）；`source_convert.py` 降级为 dispatcher。`windowing.py` 增 `build_windows_from_blocks` 并保留 char 实现作 fallback。`workorder/show-window/record-artifact` 升级为超集。每步先写失败测试再实现，频繁提交。

**Tech Stack:** Python 3.12（study-kb conda env）、PyMuPDF（`fitz`）、PyYAML、pytest、stdlib `dataclasses`/`json`/`hashlib`。Windows + PowerShell 7（跑 Python 前设 `$env:PYTHONUTF8=1`）。

---

## 重要约定（每个任务都适用）

- **运行环境**：项目根目录、study-kb 解释器。命令前缀统一：`$env:PYTHONUTF8=1; python -m pytest ...`。
- **不碰**：`.claude/skills/**`、`.agents/skills/**`（验收要求双树 diff 为空）；不引入 MinerU、不检测 MinerU、不加 `--backend`/`--mineru-policy`。
- **加法原则**：所有现有测试必须保持通过；新字段是超集，旧字段不删。
- **每个任务结束跑全量 `python -m pytest tests -q` 必须全绿再提交。**
- 参考真值：`docs/superpowers/specs/2026-06-20-source-data-contract-foundation-design.md`。

## 文件结构（决策锁定）

| 文件 | 创建/修改 | 职责 |
|---|---|---|
| `scripts/source_artifacts.py` | 创建 | `SourceBlock`/`RoutingAdvice`/`BackendResult` 数据契约 + `write_blocks`/`read_blocks`/`build_parse_report`/`write_parse_report` + `ARTIFACT_VERSION` |
| `scripts/source_backends/__init__.py` | 创建 | `get_backend(fmt)` 后端注册（md/pdf；mineru 留空位） |
| `scripts/source_backends/markdown_backend.py` | 创建 | md → `BackendResult`（section-level blocks） |
| `scripts/source_backends/pymupdf_backend.py` | 创建 | pdf → `BackendResult`（page blocks）+ 难页 PNG |
| `scripts/source_convert.py` | 修改 | 降级为 dispatcher；`converted_input_hash` 单一真值；落盘全部 artifact；返回超集 dict |
| `scripts/windowing.py` | 修改 | 增 `page_char_ranges`/`_slice_section`/`_sections_from_blocks`/`build_windows_from_blocks`；`build_windows` 加 `mode="chars"`；`WINDOWING_VERSION`→`"3"` |
| `scripts/workorder.py` | 修改 | `source` 块加 `source_md`/`blocks_jsonl`/`parse_report_json`/`assets_dir`/`backend` |
| `scripts/pipeline.py` | 修改 | `cmd_source_convert` 记 blocks/parse_report；`cmd_windows` 选块窗/char 窗；`cmd_show_window` 块头 + 复用 `page_char_ranges` |
| `tests/test_source_artifacts.py` | 创建 | 契约往返 + 报告形状 |
| `tests/test_windowing.py` | 修改 | block-aware window + 等价性 |
| `tests/test_source_convert.py` | 修改 | 两后端新 artifact + 不变量 |
| `tests/test_workorder.py` | 修改 | source 超集字段 |
| `tests/test_p2_cli.py` | 修改 | record-artifact / windows / show-window 块模式 |

---

## Task 1: `source_artifacts` — SourceBlock + blocks.jsonl 序列化

**Files:**
- Create: `scripts/source_artifacts.py`
- Test: `tests/test_source_artifacts.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_source_artifacts.py`：

```python
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


sa = _load("source_artifacts")


def test_artifact_version_present():
    assert sa.ARTIFACT_VERSION  # 非空字符串


def test_source_block_source_ref():
    assert sa.block_source_ref(43, "b000043") == "p0043#b000043"


def test_write_read_blocks_roundtrip(tmp_path):
    blocks = [
        sa.SourceBlock(block_id="b000001", type="text", text="hello", page=1,
                       char_start=0, char_end=20, risk_flags=["formula"],
                       source_ref="p0001#b000001"),
        sa.SourceBlock(block_id="b000002", type="heading", text="## T", page=1,
                       char_start=20, char_end=24, text_level=2, heading_path="T",
                       source_ref="p0001#b000002"),
    ]
    p = tmp_path / "blocks.jsonl"
    sha = sa.write_blocks(p, blocks)
    assert len(sha) == 64
    got = sa.read_blocks(p)
    assert len(got) == 2
    assert got[0]["block_id"] == "b000001" and got[0]["risk_flags"] == ["formula"]
    assert got[1]["text_level"] == 2 and got[1]["heading_path"] == "T"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `$env:PYTHONUTF8=1; python -m pytest tests/test_source_artifacts.py -q`
Expected: FAIL（`No module named source_artifacts` / 加载失败）。

- [ ] **Step 3: 最小实现**

创建 `scripts/source_artifacts.py`：

```python
"""源数据契约（Spec 1）：确定性事实层 blocks.jsonl + 质量/建议报告 parse_report.json。

纯数据 + 序列化，无业务逻辑。下游（windowing/show-window/workorder）只认这里的形状。
blocks.jsonl 是 windowing 的输入与定位事实层，不是 Obsidian 页、不是 LLM 语义切分结果；
schema 预留 MinerU 的 table/equation/image 细类型，Spec 1 的 PyMuPDF 只产页粒度 text 块。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path

# artifact 格式版本：blocks/parse_report 形状实质改动就 +1，折进 converted 阶段 input_hash，
# 使格式升级失效缓存、强制对任意来源重产（与 PROFILER_VERSION/WINDOWING_VERSION 同规）。
ARTIFACT_VERSION = "1"


@dataclass
class SourceBlock:
    block_id: str            # 全源单调序号 "b{seq:06d}"
    type: str                # "text" | "heading"（Spec 1 PyMuPDF 仅 text；预留 table/equation/image）
    text: str
    page: int                # 1-based
    char_start: int          # 进 source.md 的字符偏移（定位事实层）
    char_end: int
    text_level: int | None = None   # Markdown heading 的 # 级数；正文/PyMuPDF 为 None
    heading_path: str = ""          # Markdown：直接标题（与 _sections 一致，不嵌套）；PyMuPDF：""
    asset_path: str | None = None   # needs_vision 页 PNG 的 staging 相对路径；否则 None
    risk_flags: list = field(default_factory=list)
    source_ref: str = ""            # f"p{page:04d}#{block_id}"


def block_source_ref(page: int, block_id: str) -> str:
    return f"p{page:04d}#{block_id}"


def write_blocks(path, blocks) -> str:
    """把 SourceBlock（或 dict）列表写成 blocks.jsonl，返回内容 sha256。"""
    lines = []
    for b in blocks:
        d = asdict(b) if isinstance(b, SourceBlock) else dict(b)
        lines.append(json.dumps(d, ensure_ascii=False))
    text = "\n".join(lines)
    Path(path).write_text(text, encoding="utf-8")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_blocks(path) -> list:
    """读 blocks.jsonl 为 dict 列表（windowing 等消费方用 dict，不依赖本模块类型）。"""
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out
```

- [ ] **Step 4: 运行测试确认通过**

Run: `$env:PYTHONUTF8=1; python -m pytest tests/test_source_artifacts.py -q`
Expected: PASS（3 passed）。

- [ ] **Step 5: 提交**

```bash
git add scripts/source_artifacts.py tests/test_source_artifacts.py
git commit -m "Add SourceBlock contract + blocks.jsonl serialization (source_artifacts)"
```

---

## Task 2: `source_artifacts` — RoutingAdvice + parse_report 构建

**Files:**
- Modify: `scripts/source_artifacts.py`
- Test: `tests/test_source_artifacts.py`

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_source_artifacts.py` 末尾）

```python
def test_routing_advice_defaults():
    ra = sa.RoutingAdvice(recommended_backend="pymupdf",
                          structured_reparse_recommended=False)
    assert ra.advisory_only is True
    assert ra.consumed_by_auto_router is False
    assert ra.reasons == []


def test_build_parse_report_envelope_constants():
    ra = sa.RoutingAdvice(recommended_backend="mineru",
                          structured_reparse_recommended=True,
                          reasons=["scan_suspected"])
    rep = sa.build_parse_report("pymupdf", input_hash="abc",
                                routing_advice=ra, warnings=["w1"],
                                page_count=10, block_count=10,
                                needs_vision_pages=[3], risk_flag_counts={"formula": 2})
    assert rep["selected_backend"] == "pymupdf"
    assert rep["backend_policy"] == "contract_only"
    assert rep["mineru_status"] == "not_checked"
    assert "mineru_available" not in rep            # 禁止写真实探测字段
    assert rep["routing_advice"]["advisory_only"] is True
    assert rep["routing_advice"]["consumed_by_auto_router"] is False
    assert rep["routing_advice"]["reasons"] == ["scan_suspected"]
    assert rep["page_count"] == 10 and rep["risk_flag_counts"] == {"formula": 2}
    assert rep["artifact_version"] == sa.ARTIFACT_VERSION


def test_write_parse_report_roundtrip(tmp_path):
    import json
    ra = sa.RoutingAdvice(recommended_backend="markdown",
                          structured_reparse_recommended=False)
    rep = sa.build_parse_report("markdown", input_hash="h",
                                routing_advice=ra, section_count=3,
                                heading_count=2, block_count=3)
    p = tmp_path / "parse_report.json"
    sha = sa.write_parse_report(p, rep)
    assert len(sha) == 64
    loaded = json.loads(p.read_text(encoding="utf-8"))
    assert loaded["selected_backend"] == "markdown" and loaded["section_count"] == 3
```

- [ ] **Step 2: 运行确认失败**

Run: `$env:PYTHONUTF8=1; python -m pytest tests/test_source_artifacts.py -q`
Expected: FAIL（`module 'source_artifacts' has no attribute 'RoutingAdvice'`）。

- [ ] **Step 3: 最小实现**（追加到 `scripts/source_artifacts.py`）

```python
@dataclass
class RoutingAdvice:
    recommended_backend: str
    structured_reparse_recommended: bool
    reasons: list = field(default_factory=list)
    advisory_only: bool = True             # Spec 1 恒 True
    consumed_by_auto_router: bool = False  # Spec 1 恒 False（Spec 2 被 auto 读时才置 True）


def build_parse_report(selected_backend: str, *, input_hash: str,
                       routing_advice: "RoutingAdvice", warnings=None, **extra) -> dict:
    """组装 parse_report.json（advisory-only）。强制信封常量，避免漏写/误写。

    Spec 1 不探测 MinerU：mineru_status 恒 "not_checked"，绝不写 mineru_available。
    extra：per-backend 附加字段（pymupdf: page_count/block_count/needs_vision_pages/
    risk_flag_counts；markdown: section_count/heading_count/block_count）。
    """
    report = {
        "selected_backend": selected_backend,
        "backend_policy": "contract_only",
        "artifact_version": ARTIFACT_VERSION,
        "input_hash": input_hash,
        "routing_advice": asdict(routing_advice),
        "mineru_status": "not_checked",
        "warnings": list(warnings or []),
    }
    report.update(extra)
    return report


def write_parse_report(path, report: dict) -> str:
    text = json.dumps(report, ensure_ascii=False, indent=2)
    Path(path).write_text(text, encoding="utf-8")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: 运行确认通过**

Run: `$env:PYTHONUTF8=1; python -m pytest tests/test_source_artifacts.py -q`
Expected: PASS（6 passed）。

- [ ] **Step 5: 提交**

```bash
git add scripts/source_artifacts.py tests/test_source_artifacts.py
git commit -m "Add RoutingAdvice + advisory-only parse_report builder"
```

---

## Task 3: `windowing` — page_char_ranges + section 切片重构（保留现有行为）

**Files:**
- Modify: `scripts/windowing.py`
- Test: `tests/test_windowing.py`

> 本任务只做**无行为变化的重构** + 新增 `page_char_ranges`，并给窗口加 `mode="chars"`、`WINDOWING_VERSION`→`"3"`。现有 char 行为必须不变。

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_windowing.py`）

```python
def test_page_char_ranges_basic():
    md = "<!-- page 1 -->\n\nAAA\n\n<!-- page 2 -->\n\nBBB\n"
    r = windowing.page_char_ranges(md)
    assert set(r.keys()) == {1, 2}
    s1, e1 = r[1]
    assert md[s1:e1].startswith("<!-- page 1 -->")
    assert "AAA" in md[s1:e1]
    s2, e2 = r[2]
    assert e2 == len(md)


def test_build_windows_has_chars_mode():
    md = "# A\n\naaa\n"
    ws = windowing.build_windows(md)
    assert all(w["mode"] == "chars" for w in ws)


def test_windowing_version_bumped():
    assert windowing.WINDOWING_VERSION == "3"
```

- [ ] **Step 2: 运行确认失败**

Run: `$env:PYTHONUTF8=1; python -m pytest tests/test_windowing.py -q`
Expected: FAIL（`page_char_ranges` 不存在 / `mode` KeyError / 版本不等）。

- [ ] **Step 3: 重构实现**

把 `scripts/windowing.py` 第 14 行的版本号改为：

```python
WINDOWING_VERSION = "3"  # v3: 增 block-aware windows（build_windows_from_blocks）；窗口加 mode 字段。
```

把 `build_windows` 与 `_win`（第 41–75 行）替换为下面这版（抽出 `_slice_section`、`_win` 加 `mode`、新增 `page_char_ranges`）：

```python
_PAGE_NUM = re.compile(r"<!-- page (\d+) -->")


def page_char_ranges(md: str) -> dict:
    """source.md 各 `<!-- page N -->` 页的 char 区间 {page: (start, end)}（含 marker，覆盖整页段）。
    与 pipeline.show-window 的页范围计算同源，是 PyMuPDF page block 的唯一定位真值。"""
    markers = [(int(m.group(1)), m.start()) for m in _PAGE_NUM.finditer(md)]
    ranges = {}
    for i, (page, start) in enumerate(markers):
        end = markers[i + 1][1] if i + 1 < len(markers) else len(md)
        ranges[page] = (start, end)
    return ranges


def _slice_section(s: int, e: int, *, target_tokens: int, max_tokens: int,
                   overlap_tokens: int):
    """把一个 section [s,e) 切成 (c0, c1, overlap_before) 子窗；char 与 block 两个构建器共用。
    与旧逻辑逐字等价：≤max 一窗；否则按 token(≈char) 滑窗 + overlap。"""
    if max(1, (e - s) // 4) <= max_tokens:
        yield (s, e, 0)
        return
    target_chars = target_tokens * 4
    overlap_chars = overlap_tokens * 4
    step = max(1, target_chars - overlap_chars)
    p = s
    while p < e:
        w_end = min(e, p + target_chars)
        yield (p, w_end, overlap_chars if p > s else 0)
        if w_end >= e:
            break
        p += step


def _win(idx: int, path: str, c0: int, c1: int, overlap_before: int, mode: str = "chars") -> dict:
    return {
        "window_id": f"w{idx:04d}",
        "mode": mode,
        "heading_path": path,
        "char_start": c0,
        "char_end": c1,
        "overlap_before": overlap_before,
    }


def build_windows(md: str, *, target_tokens: int = 2000, max_tokens: int = 4000,
                  overlap_tokens: int = 200) -> list[dict]:
    """char 窗（fallback / legacy）。行为与旧版一致，仅窗口新增 mode="chars"。"""
    out: list[dict] = []
    idx = 0
    sections = [("", 0, len(md))] if _PAGE_MARKER.search(md) else _sections(md)
    for path, s, e in sections:
        for c0, c1, ov in _slice_section(s, e, target_tokens=target_tokens,
                                         max_tokens=max_tokens, overlap_tokens=overlap_tokens):
            out.append(_win(idx, path, c0, c1, ov))
            idx += 1
    return out
```

- [ ] **Step 4: 运行确认通过（含全部旧 windowing 用例）**

Run: `$env:PYTHONUTF8=1; python -m pytest tests/test_windowing.py -q`
Expected: PASS（旧用例 + 3 个新用例全过）。

- [ ] **Step 5: 提交**

```bash
git add scripts/windowing.py tests/test_windowing.py
git commit -m "Refactor windowing: extract _slice_section, add page_char_ranges, window mode field (no behavior change)"
```

---

## Task 4: `windowing` — build_windows_from_blocks + 块元数据

**Files:**
- Modify: `scripts/windowing.py`
- Test: `tests/test_windowing.py`

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_windowing.py`）

```python
def _md_blocks():
    # 复刻 markdown backend 的 section 块（heading_path = 直接标题）
    return [
        {"block_id": "b000001", "type": "heading", "text": "# A\n\naaa\n",
         "page": 1, "char_start": 0, "char_end": 9, "text_level": 1,
         "heading_path": "A", "asset_path": None, "risk_flags": []},
        {"block_id": "b000002", "type": "heading", "text": "# B\n\nbbb\n",
         "page": 1, "char_start": 9, "char_end": 18, "text_level": 1,
         "heading_path": "B", "asset_path": None, "risk_flags": []},
    ]


def test_block_windows_md_split_by_heading():
    ws = windowing.build_windows_from_blocks(_md_blocks(), target_tokens=1000,
                                             max_tokens=2000, overlap_tokens=0)
    assert [w["heading_path"] for w in ws] == ["A", "B"]
    assert all(w["mode"] == "blocks" for w in ws)
    assert ws[0]["block_ids"] == ["b000001"] and ws[1]["block_ids"] == ["b000002"]


def _pdf_blocks():
    # 两页 PyMuPDF 页块，heading_path 全 ""，第 2 页含公式难页 asset
    md_p1 = "<!-- page 1 -->\n\nintro text\n"
    md = md_p1 + "<!-- page 2 -->\n\nformula page\n"
    return md, [
        {"block_id": "b000001", "type": "text", "text": "intro text",
         "page": 1, "char_start": 0, "char_end": len(md_p1),
         "text_level": None, "heading_path": "", "asset_path": None, "risk_flags": []},
        {"block_id": "b000002", "type": "text", "text": "formula page",
         "page": 2, "char_start": len(md_p1), "char_end": len(md),
         "text_level": None, "heading_path": "", "asset_path": "assets/p0002.png",
         "risk_flags": ["formula"]},
    ]


def test_block_windows_pdf_pages_not_fragmented():
    _md, blocks = _pdf_blocks()
    ws = windowing.build_windows_from_blocks(blocks, target_tokens=1000,
                                             max_tokens=2000, overlap_tokens=0)
    assert len(ws) == 1                       # 短 2 页合并为 1 窗，绝不按页/标题碎片化
    w = ws[0]
    assert w["heading_path"] == "" and w["mode"] == "blocks"
    assert w["page_start"] == 1 and w["page_end"] == 2
    assert w["block_ids"] == ["b000001", "b000002"]
    assert w["assets"] == ["assets/p0002.png"]
    assert w["risk_flags"] == ["formula"]
    assert w["contains"] == ["text"]


def test_block_windows_oversize_block_subsplit():
    big = "z" * 12000  # ~3000 tokens
    blocks = [{"block_id": "b000001", "type": "text", "text": big, "page": 1,
               "char_start": 0, "char_end": len(big), "text_level": None,
               "heading_path": "", "asset_path": None, "risk_flags": []}]
    ws = windowing.build_windows_from_blocks(blocks, target_tokens=300, max_tokens=400,
                                             overlap_tokens=50)
    assert len(ws) >= 2
    assert all(w["mode"] == "blocks" and w["block_ids"] == ["b000001"] for w in ws)
    assert ws[1]["char_start"] < ws[0]["char_end"]   # overlap


def test_block_windows_md_equivalent_to_char_windows():
    # 关键等价性：md 块窗与今天 char 窗在 heading_path / char 区间上一致
    md = "# A\n\n" + ("z " * 1000) + "\n# B\n\nbbb\n"
    sections = windowing._sections(md)
    blocks = []
    for i, (path, s, e) in enumerate(sections):
        first = md[s:e].splitlines()[0] if md[s:e].strip() else ""
        is_h = bool(windowing._HEADING.match(first))
        blocks.append({"block_id": f"b{i+1:06d}",
                       "type": "heading" if is_h else "text", "text": md[s:e],
                       "page": 1, "char_start": s, "char_end": e,
                       "text_level": (len(windowing._HEADING.match(first).group(1)) if is_h else None),
                       "heading_path": path, "asset_path": None, "risk_flags": []})
    char_ws = windowing.build_windows(md, target_tokens=300, max_tokens=400, overlap_tokens=50)
    block_ws = windowing.build_windows_from_blocks(md_blocks_for_equiv := blocks,
                                                   target_tokens=300, max_tokens=400,
                                                   overlap_tokens=50)
    assert [(w["heading_path"], w["char_start"], w["char_end"]) for w in char_ws] == \
           [(w["heading_path"], w["char_start"], w["char_end"]) for w in block_ws]
```

- [ ] **Step 2: 运行确认失败**

Run: `$env:PYTHONUTF8=1; python -m pytest tests/test_windowing.py -q`
Expected: FAIL（`build_windows_from_blocks` 不存在）。

- [ ] **Step 3: 实现**（追加到 `scripts/windowing.py`）

```python
def _sections_from_blocks(blocks: list) -> list:
    """把有序 blocks 按「连续同 heading_path」聚成 section (path, char_start, char_end)。
    Markdown 各块 heading_path 互异 → 各自成段（= _sections）；PyMuPDF 全空 → 合并为一段。"""
    secs: list = []
    for b in blocks:
        path = b.get("heading_path", "")
        if secs and secs[-1][0] == path:
            p, s, _e = secs[-1]
            secs[-1] = (p, s, b["char_end"])
        else:
            secs.append((path, b["char_start"], b["char_end"]))
    return secs


def _attach_block_meta(w: dict, blocks: list, c0: int, c1: int) -> None:
    inwin = [b for b in blocks if not (b["char_end"] <= c0 or b["char_start"] >= c1)]
    w["block_ids"] = [b["block_id"] for b in inwin]
    pages = [b["page"] for b in inwin]
    w["page_start"] = min(pages) if pages else 0
    w["page_end"] = max(pages) if pages else 0
    w["token_estimate"] = max(1, (c1 - c0) // 4)
    w["contains"] = sorted({b["type"] for b in inwin})
    w["assets"] = [b["asset_path"] for b in inwin if b.get("asset_path")]
    flags: set = set()
    for b in inwin:
        flags.update(b.get("risk_flags") or [])
    w["risk_flags"] = sorted(flags)


def build_windows_from_blocks(blocks: list, *, target_tokens: int = 2000,
                              max_tokens: int = 4000, overlap_tokens: int = 200) -> list[dict]:
    """block-aware windows：按 section 切（与 char 窗共用 _slice_section 保等价），
    再用窗 char 区间回挂块元数据（block_ids/page 范围/contains/assets/risk_flags）。"""
    out: list[dict] = []
    idx = 0
    for path, s, e in _sections_from_blocks(blocks):
        for c0, c1, ov in _slice_section(s, e, target_tokens=target_tokens,
                                         max_tokens=max_tokens, overlap_tokens=overlap_tokens):
            w = _win(idx, path, c0, c1, ov, mode="blocks")
            _attach_block_meta(w, blocks, c0, c1)
            out.append(w)
            idx += 1
    return out
```

- [ ] **Step 4: 运行确认通过**

Run: `$env:PYTHONUTF8=1; python -m pytest tests/test_windowing.py -q`
Expected: PASS（含等价性用例）。

- [ ] **Step 5: 提交**

```bash
git add scripts/windowing.py tests/test_windowing.py
git commit -m "Add build_windows_from_blocks with block metadata + char-window equivalence"
```

---

## Task 5: `source_backends` — 包骨架 + markdown 后端

**Files:**
- Create: `scripts/source_backends/__init__.py`, `scripts/source_backends/markdown_backend.py`
- Modify: `scripts/source_artifacts.py`（加 `BackendResult`）
- Test: `tests/test_source_convert.py`

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_source_convert.py` 末尾）

```python
import sys as _sys
_sys.path.insert(0, str(ROOT / "scripts"))


def test_markdown_backend_section_blocks(tmp_path):
    import importlib
    mb = importlib.import_module("source_backends.markdown_backend")
    src = tmp_path / "n.md"
    src.write_text("# A\n\naaa\n\n## B\n\nbbb\n", encoding="utf-8")
    res = mb.convert(src, out_dir=tmp_path / "o", input_hash="h")
    # 块为 section 级，heading 块带 text_level/heading_path，text 含整段
    headings = [b for b in res.blocks if b.type == "heading"]
    assert any(b.heading_path == "A" and b.text_level == 1 for b in headings)
    a_block = next(b for b in res.blocks if b.heading_path == "A")
    assert "aaa" in a_block.text                     # 正文未被丢
    assert res.source_md[a_block.char_start:a_block.char_end] == a_block.text  # 逐字一致
    assert res.report["selected_backend"] == "markdown"
    assert res.report["routing_advice"]["recommended_backend"] == "markdown"
    assert res.report["section_count"] >= 2
    assert res.needs_vision_pages == []
```

- [ ] **Step 2: 运行确认失败**

Run: `$env:PYTHONUTF8=1; python -m pytest tests/test_source_convert.py::test_markdown_backend_section_blocks -q`
Expected: FAIL（`No module named source_backends`）。

- [ ] **Step 3: 实现**

先在 `scripts/source_artifacts.py` 追加 `BackendResult`：

```python
@dataclass
class BackendResult:
    source_md: str            # source.md 全文
    blocks: list              # list[SourceBlock]
    chapters: list            # chaptering 输出（dict 列表）
    pages: list               # 逐页 profile（dict 列表）
    report: dict              # build_parse_report 产出
    needs_vision_pages: list  # 难页页号
```

创建 `scripts/source_backends/__init__.py`：

```python
"""源转换后端注册（Spec 1）：按 fmt 选后端。mineru 留 Spec 2。"""
from __future__ import annotations


def get_backend(fmt: str):
    if fmt == "md":
        from . import markdown_backend
        return markdown_backend
    if fmt == "pdf":
        from . import pymupdf_backend
        return pymupdf_backend
    raise BackendUnavailable(f"no Spec 1 backend for fmt={fmt}（docx/pptx/mineru 留 Spec 2）")


class BackendUnavailable(RuntimeError):
    pass
```

创建 `scripts/source_backends/markdown_backend.py`：

```python
"""Markdown 后端（Spec 1）：原文即 source.md（顺读视图）；按 _sections 出 section-level 块。

heading_path 与 windowing._sections 一致（直接标题、不嵌套），保证 block 窗 ≈ char 窗等价。
section 块的 text = 该段完整 Markdown（heading 行 + 正文），绝不拆走正文。
"""
from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import source_profile
import chaptering
import windowing
import source_artifacts as sa


def convert(src_path, *, out_dir, input_hash: str):
    md = Path(src_path).read_text(encoding="utf-8")
    pages = [source_profile.profile_page(1, md, image_count=0)]
    chapters = chaptering.chapters_from_toc([], n_pages=1)
    blocks = []
    heading_count = 0
    for i, (path, s, e) in enumerate(windowing._sections(md)):
        seg = md[s:e]
        first = seg.splitlines()[0] if seg.strip() else ""
        m = windowing._HEADING.match(first)
        is_h = bool(m)
        if is_h:
            heading_count += 1
        block_id = f"b{i + 1:06d}"
        blocks.append(sa.SourceBlock(
            block_id=block_id, type="heading" if is_h else "text", text=seg,
            page=1, char_start=s, char_end=e,
            text_level=(len(m.group(1)) if is_h else None), heading_path=path,
            risk_flags=[], source_ref=sa.block_source_ref(1, block_id)))
    advice = sa.RoutingAdvice(recommended_backend="markdown",
                              structured_reparse_recommended=False)
    report = sa.build_parse_report("markdown", input_hash=input_hash, routing_advice=advice,
                                   section_count=len(blocks), heading_count=heading_count,
                                   block_count=len(blocks))
    return sa.BackendResult(source_md=md, blocks=blocks, chapters=chapters,
                            pages=pages, report=report, needs_vision_pages=[])
```

- [ ] **Step 4: 运行确认通过**

Run: `$env:PYTHONUTF8=1; python -m pytest tests/test_source_convert.py::test_markdown_backend_section_blocks -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add scripts/source_backends/__init__.py scripts/source_backends/markdown_backend.py scripts/source_artifacts.py tests/test_source_convert.py
git commit -m "Add source_backends package + markdown backend (section-level blocks)"
```

---

## Task 6: `source_backends` — pymupdf 后端

**Files:**
- Create: `scripts/source_backends/pymupdf_backend.py`
- Test: `tests/test_source_convert.py`

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_source_convert.py`）

```python
def test_pymupdf_backend_page_blocks_and_invariant(tmp_path):
    import importlib.util as u
    if u.find_spec("fitz") is None:
        import pytest; pytest.skip("pymupdf not installed")
    import fitz, importlib
    pb = importlib.import_module("source_backends.pymupdf_backend")
    src = tmp_path / "b.pdf"
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "first page body")
    page2 = doc.new_page()
    page2.insert_text((72, 72), "second page")
    for k in range(20):                       # 让第 2 页判难页（矢量图）
        page2.draw_line(fitz.Point(72, 100 + k * 5), fitz.Point(300, 100 + k * 5))
    doc.save(str(src)); doc.close()
    res = pb.convert(src, out_dir=tmp_path / "o", input_hash="h")
    assert len(res.blocks) == 2
    assert all(b.type == "text" and b.text_level is None for b in res.blocks)
    # char span 不变量：slice 含该页 marker 与 block.text
    for b in res.blocks:
        seg = res.source_md[b.char_start:b.char_end]
        assert f"<!-- page {b.page} -->" in seg
        assert b.text in seg
    # 难页：第 2 页 asset_path 置位 + PNG 生成 + risk_flags
    p2 = next(b for b in res.blocks if b.page == 2)
    assert p2.asset_path == "assets/p0002.png"
    assert (tmp_path / "o" / "assets" / "p0002.png").exists()
    assert p2.risk_flags                       # 至少一个 reason
    assert 2 in res.needs_vision_pages
    assert res.report["selected_backend"] == "pymupdf"
    assert res.report["page_count"] == 2 and res.report["block_count"] == 2
```

- [ ] **Step 2: 运行确认失败**

Run: `$env:PYTHONUTF8=1; python -m pytest tests/test_source_convert.py::test_pymupdf_backend_page_blocks_and_invariant -q`
Expected: FAIL（`No module named source_backends.pymupdf_backend`）。

- [ ] **Step 3: 实现**

创建 `scripts/source_backends/pymupdf_backend.py`：

```python
"""PyMuPDF 后端（Spec 1）：page-granularity 块（coarse），不模拟 layout、不推断 heading。

source.md 仍是页标记 + 纯文本（顺读视图）；每页一个 text 块，char span 覆盖整页段（含 marker，
由 windowing.page_char_ranges 派生，是唯一定位真值）。难页（needs_vision）渲染整页 PNG（route B）
并写 asset_path + risk_flags。routing_advice 由 backend 聚合 profile 已有 per-page 信号得出，
不改 source_profile。
"""
from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import source_profile
import chaptering
import windowing
import source_artifacts as sa

# routing_advice 阈值（advisory，Spec 2 再校准）
_LOW_TEXT_MEAN = 100
_DENSE_RATIO = 0.30


def _routing_advice(pages: list) -> sa.RoutingAdvice:
    n = len(pages) or 1
    def ratio(flag):
        return sum(1 for p in pages if flag in (p.get("needs_vision_reason") or [])) / n
    reasons = []
    if ratio("scanned-or-image") >= _DENSE_RATIO:
        reasons.append("scan_suspected")
    if sum(p.get("text_len", 0) for p in pages) / n < _LOW_TEXT_MEAN:
        reasons.append("low_text_density")
    dense = sum(1 for p in pages
                if {"formula", "formula-borderline", "table"} & set(p.get("needs_vision_reason") or [])) / n
    if dense >= _DENSE_RATIO:
        reasons.append("table_or_formula_dense")
    rec = "mineru" if reasons else "pymupdf"
    return sa.RoutingAdvice(recommended_backend=rec,
                            structured_reparse_recommended=bool(reasons), reasons=reasons)


def convert(src_path, *, out_dir, input_hash: str):
    import fitz  # PyMuPDF
    out_dir = Path(out_dir)
    assets_dir = out_dir / "assets"
    doc = fitz.open(str(src_path))
    parts, pages = [], []
    for i in range(len(doc)):
        page = doc[i]
        text = page.get_text()
        sig = source_profile.visual_signals(page)
        prof = source_profile.profile_page(i + 1, text, image_count=sig["image_count"],
                                           n_draw=sig["n_draw"], n_tables=sig["n_tables"])
        pages.append(prof)
        parts.append(f"\n\n<!-- page {i + 1} -->\n\n{text.strip()}\n")
    source_md = "".join(parts).strip() + "\n"
    ranges = windowing.page_char_ranges(source_md)

    blocks, needs_vision_pages, risk_counts = [], [], {}
    for i in range(len(doc)):
        page_no = i + 1
        prof = pages[i]
        s, e = ranges[page_no]
        flags = list(prof.get("needs_vision_reason") or [])
        for f in flags:
            risk_counts[f] = risk_counts.get(f, 0) + 1
        asset_path = None
        if prof.get("needs_vision"):
            needs_vision_pages.append(page_no)
            assets_dir.mkdir(parents=True, exist_ok=True)
            pix = doc[i].get_pixmap(matrix=fitz.Matrix(3, 3))
            pix.save(str(assets_dir / f"p{page_no:04d}.png"))
            asset_path = f"assets/p{page_no:04d}.png"
        block_id = f"b{page_no:06d}"
        blocks.append(sa.SourceBlock(
            block_id=block_id, type="text", text=doc[i].get_text().strip(),
            page=page_no, char_start=s, char_end=e, text_level=None, heading_path="",
            asset_path=asset_path, risk_flags=flags,
            source_ref=sa.block_source_ref(page_no, block_id)))
    chapters = chaptering.chapters_from_toc(doc.get_toc(), len(doc))
    doc.close()

    report = sa.build_parse_report(
        "pymupdf", input_hash=input_hash, routing_advice=_routing_advice(pages),
        page_count=len(pages), block_count=len(blocks),
        needs_vision_pages=needs_vision_pages, risk_flag_counts=risk_counts)
    return sa.BackendResult(source_md=source_md, blocks=blocks, chapters=chapters,
                            pages=pages, report=report, needs_vision_pages=needs_vision_pages)
```

- [ ] **Step 4: 运行确认通过**

Run: `$env:PYTHONUTF8=1; python -m pytest tests/test_source_convert.py::test_pymupdf_backend_page_blocks_and_invariant -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add scripts/source_backends/pymupdf_backend.py tests/test_source_convert.py
git commit -m "Add pymupdf backend (page blocks, char-span invariant, advisory routing)"
```

---

## Task 7: `source_convert` — 降级为 dispatcher

**Files:**
- Modify: `scripts/source_convert.py`
- Test: `tests/test_source_convert.py`

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_source_convert.py`）

```python
def test_convert_emits_blocks_and_parse_report_md(tmp_path):
    src = tmp_path / "n.md"
    src.write_text("# Title\n\nbody\n", encoding="utf-8")
    out_dir = tmp_path / "staging" / "n"
    res = source_convert.convert(src, out_dir=out_dir, fmt="md")
    # 旧键保留
    assert res["source_md"].endswith("source.md") and res["pages"]
    assert res["chapters_path"].endswith("chapters.json")
    # 新键 + 新文件
    assert (out_dir / "blocks.jsonl").exists()
    assert (out_dir / "parse_report.json").exists()
    assert res["backend"] == "markdown"
    assert len(res["blocks_sha"]) == 64 and len(res["parse_report_sha"]) == 64


def test_converted_input_hash_includes_versions(tmp_path):
    src = tmp_path / "n.md"
    src.write_text("x", encoding="utf-8")
    h = source_convert.converted_input_hash(src)
    import source_profile, source_artifacts
    assert source_profile.PROFILER_VERSION in h and source_artifacts.ARTIFACT_VERSION in h
```

- [ ] **Step 2: 运行确认失败**

Run: `$env:PYTHONUTF8=1; python -m pytest tests/test_source_convert.py::test_convert_emits_blocks_and_parse_report_md tests/test_source_convert.py::test_converted_input_hash_includes_versions -q`
Expected: FAIL（`blocks.jsonl` 不存在 / `converted_input_hash` 不存在）。

- [ ] **Step 3: 重写 `scripts/source_convert.py` 为 dispatcher**

整体替换文件内容：

```python
"""source-convert dispatcher（Spec 1）：按 fmt 选后端，落盘 source.md + blocks.jsonl +
chapters.json + parse_report.json + assets/，返回 ConvertResult（旧键超集 + 新键）。

后端在 source_backends/；本文件不含解析业务，只做选后端 + 持久化 + 拼返回 dict。
source.md 是 LLM 顺读视图；blocks.jsonl 是定位事实层；两者由后端同源产出。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import source_profile
import source_artifacts
from source_backends import get_backend, BackendUnavailable  # noqa: F401（对外仍暴露异常类型）


def _sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def converted_input_hash(raw_path) -> str:
    """converted 阶段缓存键（单一真值，pipeline 与 convert 共用）：
    raw sha + PROFILER_VERSION（连带难页 PNG）+ ARTIFACT_VERSION（artifact 形状）。"""
    raw = Path(raw_path).read_bytes()
    return (hashlib.sha256(raw).hexdigest() + ":" + source_profile.PROFILER_VERSION
            + ":" + source_artifacts.ARTIFACT_VERSION)


def convert(src_path, *, out_dir, fmt: str) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    backend = get_backend(fmt)                       # 未知 fmt → BackendUnavailable
    ihash = converted_input_hash(src_path)
    res = backend.convert(src_path, out_dir=out_dir, input_hash=ihash)

    source_md = out_dir / "source.md"
    source_md.write_text(res.source_md, encoding="utf-8")
    blocks_path = out_dir / "blocks.jsonl"
    blocks_sha = source_artifacts.write_blocks(blocks_path, res.blocks)
    chapters_json = json.dumps(res.chapters, ensure_ascii=False, indent=2)
    chapters_path = out_dir / "chapters.json"
    chapters_path.write_text(chapters_json, encoding="utf-8")
    report_path = out_dir / "parse_report.json"
    report_sha = source_artifacts.write_parse_report(report_path, res.report)

    return {
        "source_md": str(source_md),
        "sha256": _sha256_text(res.source_md),
        "assets_dir": str(out_dir / "assets"),
        "pages": res.pages,
        "needs_vision_pages": res.needs_vision_pages,
        "chapters": res.chapters,
        "chapters_path": str(chapters_path),
        "chapters_sha": _sha256_text(chapters_json),
        "blocks_path": str(blocks_path),
        "blocks_sha": blocks_sha,
        "parse_report_path": str(report_path),
        "parse_report_sha": report_sha,
        "backend": res.report["selected_backend"],
    }
```

- [ ] **Step 4: 运行确认通过（含 test_source_convert.py 全部旧用例）**

Run: `$env:PYTHONUTF8=1; python -m pytest tests/test_source_convert.py -q`
Expected: PASS（旧 markdown/PDF/chapters/矢量图/未知后端 用例 + 新用例全过）。

> 注：旧用例 `test_unknown_backend_raises` 仍应通过——`get_backend("xyz")` 抛 `BackendUnavailable`，且本模块 `from source_backends import ... BackendUnavailable` 重新导出，`source_convert.BackendUnavailable` 仍可用。

- [ ] **Step 5: 提交**

```bash
git add scripts/source_convert.py tests/test_source_convert.py
git commit -m "Reduce source_convert to dispatcher; emit blocks.jsonl + parse_report.json"
```

---

## Task 8: `pipeline.cmd_source_convert` — 记录新 artifact + 版本化缓存键

**Files:**
- Modify: `scripts/pipeline.py`（`cmd_source_convert`，约第 116–155 行）
- Test: `tests/test_p2_cli.py`

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_p2_cli.py`）

先在文件顶部（`mdpage = _load("mdpage")` 附近）加载 `state_store`，并新增一个走**真实 CLI** 的预处理 helper（`_run(args, cwd)` 是本文件既有 helper：以 `STUDY_KB_ROOT=cwd` 跑 pipeline.py，返回 CompletedProcess）：

```python
state_store = _load("state_store")


def _preprocess_md(tmp_path, sid, body):
    """真实 CLI 预处理到 converted：add-source → profile → source-convert，返回 staging 目录。"""
    raw = tmp_path / f"{sid}.md"
    raw.write_text(body, encoding="utf-8")
    assert _run(["add-source", "--source", sid, "--domain", "d",
                 "--path", str(raw), "--fmt", "md"], tmp_path).returncode == 0
    assert _run(["profile", "--source", sid], tmp_path).returncode == 0
    r = _run(["source-convert", "--source", sid], tmp_path)
    assert r.returncode == 0, r.stderr
    return tmp_path / "pipeline-workspace" / "staging" / sid


def test_source_convert_records_blocks_and_parse_report(tmp_path):
    sid = "p2blk"
    staging = _preprocess_md(tmp_path, sid, "# A\n\nbody\n")
    db = tmp_path / "pipeline-workspace" / "state" / "study-kb.sqlite"
    kinds = {a["kind"] for a in state_store.list_artifacts(db, sid)}
    assert "blocks" in kinds and "parse_report" in kinds
    assert (staging / "blocks.jsonl").exists()
    assert (staging / "parse_report.json").exists()
```

- [ ] **Step 2: 运行确认失败**

Run: `$env:PYTHONUTF8=1; python -m pytest tests/test_p2_cli.py -q -k blocks_and_parse_report`
Expected: FAIL（artifact kind `blocks`/`parse_report` 未记录）。

- [ ] **Step 3: 修改 `cmd_source_convert`**

把 `scripts/pipeline.py` 中 `cmd_source_convert` 的 input_hash 计算与 record_artifact 段改为：

将
```python
    ihash = hashlib.sha256(raw.read_bytes()).hexdigest() + ":" + source_profile.PROFILER_VERSION
```
改为
```python
    ihash = source_convert.converted_input_hash(raw)   # raw sha + PROFILER_VERSION + ARTIFACT_VERSION
```

并在 `res = source_convert.convert(...)` 之后、`_sync_assets` 之前，紧接现有两行 record_artifact 增加两行：
```python
        state_store.record_artifact(db, args.source, kind="source_md", path=res["source_md"], sha256=res["sha256"])
        state_store.record_artifact(db, args.source, kind="chapters", path=res["chapters_path"], sha256=res["chapters_sha"])
        state_store.record_artifact(db, args.source, kind="blocks", path=res["blocks_path"], sha256=res["blocks_sha"])
        state_store.record_artifact(db, args.source, kind="parse_report", path=res["parse_report_path"], sha256=res["parse_report_sha"])
```

（`import source_convert` 已在该函数顶部存在；无需再加。）

- [ ] **Step 4: 运行确认通过**

Run: `$env:PYTHONUTF8=1; python -m pytest tests/test_p2_cli.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add scripts/pipeline.py tests/test_p2_cli.py
git commit -m "cmd_source_convert: record blocks/parse_report artifacts + versioned input_hash"
```

---

## Task 9: `pipeline.cmd_windows` — 有 blocks 走块窗，否则 char fallback

**Files:**
- Modify: `scripts/pipeline.py`（`cmd_windows`，约第 185–214 行）
- Test: `tests/test_p2_cli.py`

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_p2_cli.py`；复用 Task 8 的 `_preprocess_md`）

```python
def test_windows_block_mode_when_blocks_present(tmp_path):
    import json
    sid = "p2win"
    _preprocess_md(tmp_path, sid, "# A\n\naaa\n\n# B\n\nbbb\n")
    assert _run(["windows", "--source", sid], tmp_path).returncode == 0
    staging = tmp_path / "pipeline-workspace" / "staging" / sid
    ws = [json.loads(l) for l in
          (staging / "windows.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    assert ws and all(w["mode"] == "blocks" for w in ws)
    assert all("block_ids" in w for w in ws)
```

- [ ] **Step 2: 运行确认失败**

Run: `$env:PYTHONUTF8=1; python -m pytest tests/test_p2_cli.py -q -k block_mode_when_blocks_present`
Expected: FAIL（windows 仍是 char 模式，无 `block_ids`）。

- [ ] **Step 3: 修改 `cmd_windows`**

把 `cmd_windows` 主体替换为（检测 blocks.jsonl → 块窗；否则 char）：

```python
def cmd_windows(args):
    """确定性 processing windows：有 blocks.jsonl 走 block-aware，否则退回 char。"""
    import state_store
    import windowing
    import source_artifacts
    import json
    import hashlib
    db = _vault_state_db()
    out = _staging_dir(args.source)
    blocks_path = out / "blocks.jsonl"
    source_md = out / "source.md"
    if not source_md.exists():
        raise SystemExit("run source-convert first")
    if blocks_path.exists():
        basis = blocks_path.read_bytes()
        build = lambda: windowing.build_windows_from_blocks(source_artifacts.read_blocks(blocks_path))
    else:
        basis = source_md.read_text(encoding="utf-8").encode("utf-8")
        build = lambda: windowing.build_windows(source_md.read_text(encoding="utf-8"))
    ihash = hashlib.sha256(basis).hexdigest() + ":" + windowing.WINDOWING_VERSION
    if not state_store.should_run_stage(db, args.source, "windowed", input_hash=ihash):
        print("[skip] windowed up-to-date")
        return
    state_store.start_stage(db, args.source, "windowed", input_hash=ihash)
    try:
        ws = build()
        (out / "windows.jsonl").write_text(
            "\n".join(json.dumps(w, ensure_ascii=False) for w in ws), encoding="utf-8")
        ohash = hashlib.sha256((out / "windows.jsonl").read_bytes()).hexdigest()
        state_store.record_artifact(db, args.source, kind="windows",
                                    path=str(out / "windows.jsonl"), sha256=ohash)
        state_store.complete_stage(db, args.source, "windowed", output_hash=ohash)
        print(f"[OK] windowed → {len(ws)} windows ({'blocks' if blocks_path.exists() else 'chars'})")
    except Exception as e:
        state_store.fail_stage(db, args.source, "windowed", error=str(e))
        raise
```

- [ ] **Step 4: 运行确认通过**

Run: `$env:PYTHONUTF8=1; python -m pytest tests/test_p2_cli.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add scripts/pipeline.py tests/test_p2_cli.py
git commit -m "cmd_windows: block-aware when blocks.jsonl present, char fallback otherwise"
```

---

## Task 10: `pipeline.cmd_show_window` — 块元数据头 + 复用 page_char_ranges

**Files:**
- Modify: `scripts/pipeline.py`（`_page_ranges_for_md` 第 452–460 行、`cmd_show_window` 第 469–511 行）
- Test: `tests/test_p2_cli.py`

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_p2_cli.py`；复用 Task 8 的 `_preprocess_md`）

```python
def test_show_window_block_header(tmp_path):
    sid = "p2show"
    _preprocess_md(tmp_path, sid, "# A\n\naaa\n")
    assert _run(["windows", "--source", sid], tmp_path).returncode == 0
    r = _run(["show-window", "--source", sid, "--window", "w0000"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert "window-meta" in r.stdout and "heading_path=A" in r.stdout
    assert "block_ids=" in r.stdout and "aaa" in r.stdout
```

- [ ] **Step 2: 运行确认失败**

Run: `$env:PYTHONUTF8=1; python -m pytest tests/test_p2_cli.py -q -k show_window_block_header`
Expected: FAIL（无 `window-meta` 头）。

- [ ] **Step 3: 修改 pipeline.py**

(a) 删除局部 `_page_ranges_for_md`（第 452–460 行），改为复用 windowing：在文件顶部 `cmd_show_window` 之前加一个薄封装，并把所有调用点改用它。具体：删除 `_page_ranges_for_md` 函数体，新增

```python
def _page_ranges_for_md(md: str) -> dict:
    """复用 windowing.page_char_ranges（单一 marker 扫描真值）。"""
    import windowing
    return windowing.page_char_ranges(md)
```

(b) 在 `cmd_show_window` 中，定位到取出 `selected` 之后、`start, end = selected["char_start"], selected["char_end"]` 这一行之后，插入块元数据头打印（仅当 `selected.get("mode") == "blocks"`）：

```python
    start, end = selected["char_start"], selected["char_end"]
    if selected.get("mode") == "blocks" and not getattr(args, "plain", False):
        hp = selected.get("heading_path", "")
        bids = ",".join(selected.get("block_ids") or [])
        rf = ",".join(selected.get("risk_flags") or [])
        assets = ",".join(selected.get("assets") or [])
        print(f"<!-- window-meta: heading_path={hp} pages={selected.get('page_start')}-{selected.get('page_end')} "
              f"block_ids={bids} risk_flags={rf} assets={assets} -->")
```

（现有的 route-b 难页资产头与 `print(md[start:end])` 段保持不动，块头打印在其前。）

- [ ] **Step 4: 运行确认通过**

Run: `$env:PYTHONUTF8=1; python -m pytest tests/test_p2_cli.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add scripts/pipeline.py tests/test_p2_cli.py
git commit -m "cmd_show_window: block-meta header; reuse windowing.page_char_ranges"
```

---

## Task 11: `workorder` — source 块超集

**Files:**
- Modify: `scripts/workorder.py`（`build_workorder` 的返回 dict，第 54–67 行）
- Test: `tests/test_workorder.py`

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_workorder.py`）

```python
def test_workorder_source_superset(tmp_path):
    vault = _vault_with_concepts(tmp_path)
    staging = tmp_path / "staging" / "wp"
    staging.mkdir(parents=True)
    wo = workorder.build_workorder(vault, source_id="wp", domain="game-theory",
                                   staging_dir=staging)
    src = wo["source"]
    # 旧键保留
    assert src["processing_windows"].endswith("windows.jsonl")
    # 新键
    assert src["source_md"].endswith("source.md")
    assert src["blocks_jsonl"].endswith("blocks.jsonl")
    assert src["parse_report_json"].endswith("parse_report.json")
    assert src["assets_dir"].endswith("assets")
    assert "backend" in src
```

- [ ] **Step 2: 运行确认失败**

Run: `$env:PYTHONUTF8=1; python -m pytest tests/test_workorder.py::test_workorder_source_superset -q`
Expected: FAIL（`source` 无 `blocks_jsonl` 等键）。

- [ ] **Step 3: 修改 `build_workorder` 的 `source` 字段**

把返回 dict 中的
```python
        "source": {"text_md": str(staging / "source.md"),
                   "page_images_dir": str(staging / "assets"),
                   "processing_windows": str(staging / "windows.jsonl")},
```
改为
```python
        "source": {"text_md": str(staging / "source.md"),           # 旧键保留（向后兼容）
                   "source_md": str(staging / "source.md"),
                   "blocks_jsonl": str(staging / "blocks.jsonl"),
                   "parse_report_json": str(staging / "parse_report.json"),
                   "chapters_json": str(staging / "chapters.json"),
                   "assets_dir": str(staging / "assets"),
                   "page_images_dir": str(staging / "assets"),       # 旧键保留
                   "processing_windows": str(staging / "windows.jsonl"),
                   "backend": _read_backend(staging)},
```

并在 `build_workorder` 上方新增一个零依赖 helper（从 parse_report 读 backend，缺失则 `"unknown"`）：

```python
def _read_backend(staging: Path) -> str:
    import json
    p = Path(staging) / "parse_report.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8")).get("selected_backend", "unknown")
        except Exception:
            return "unknown"
    return "unknown"
```

- [ ] **Step 4: 运行确认通过（含旧 workorder 用例）**

Run: `$env:PYTHONUTF8=1; python -m pytest tests/test_workorder.py -q`
Expected: PASS（旧契约用例 `test_build_workorder_contract` 仍过——`processing_windows` 键保留）。

- [ ] **Step 5: 提交**

```bash
git add scripts/workorder.py tests/test_workorder.py
git commit -m "workorder: record source artifact superset (blocks/parse_report/backend)"
```

---

## Task 12: 端到端 + 全量回归 + 验收

**Files:**
- Test: `tests/test_source_convert.py`（追加一个 e2e）

- [ ] **Step 1: 写端到端测试**（PyMuPDF 全链：convert→windows，验证不变量）

```python
def test_e2e_pdf_convert_then_block_windows(tmp_path):
    import importlib.util as u
    if u.find_spec("fitz") is None:
        import pytest; pytest.skip("pymupdf not installed")
    import fitz, json
    import importlib
    windowing = importlib.import_module("windowing")
    src = tmp_path / "e2e.pdf"
    doc = fitz.open()
    for _ in range(3):
        doc.new_page().insert_text((72, 72), "some readable body text on this page")
    doc.save(str(src)); doc.close()
    out_dir = tmp_path / "staging" / "e2e"
    res = source_convert.convert(src, out_dir=out_dir, fmt="pdf")
    md = (out_dir / "source.md").read_text(encoding="utf-8")
    blocks = json.loads("[" + ",".join(
        (out_dir / "blocks.jsonl").read_text(encoding="utf-8").splitlines()) + "]")
    # 不变量：每块 slice 含 marker + text；窗聚合不丢页标记
    for b in blocks:
        seg = md[b["char_start"]:b["char_end"]]
        assert f"<!-- page {b['page']} -->" in seg
    ws = windowing.build_windows_from_blocks(blocks)
    covered = "".join(md[w["char_start"]:w["char_end"]] for w in ws)
    assert covered.count("<!-- page") == 3        # 3 页标记一个不少
    assert res["backend"] == "pymupdf"
```

- [ ] **Step 2: 运行确认通过**

Run: `$env:PYTHONUTF8=1; python -m pytest tests/test_source_convert.py::test_e2e_pdf_convert_then_block_windows -q`
Expected: PASS。

- [ ] **Step 3: 全量回归 + 双树对等 + legacy 守卫**

Run: `$env:PYTHONUTF8=1; python -m pytest tests -q`
Expected: 全绿（含 `test_legacy_removed.py`、skill 双树对等守卫、所有 p*-cli）。

- [ ] **Step 4: 确认 skill 双树零改动**

Run: `git diff --name-only HEAD~12 -- .claude/skills .agents/skills`
Expected: 空输出（本计划未触碰 skill 两树）。

- [ ] **Step 5: 提交 + 完成分支**

```bash
git add tests/test_source_convert.py
git commit -m "Add e2e PDF convert->block-windows invariant test; Spec 1 acceptance green"
```

完成后用 superpowers:finishing-a-development-branch 决定合并/PR。

---

## Self-Review（写计划后对照 spec 自查，已修正项内联）

**1. Spec 覆盖：**
- §4 架构（source_artifacts/source_backends/dispatcher）→ Task 1–7 ✓
- §5.1 SourceBlock → Task 1 ✓；§5.3 ParseReport(advisory-only) → Task 2 ✓；§5.4 ConvertResult 超集 → Task 7 ✓
- §6.1 PyMuPDF 页块 + char 不变量 → Task 6 ✓；§6.2 Markdown section 块（整段 text）→ Task 5 ✓
- §7 block-aware windows + char fallback + 等价性 → Task 3/4/9 ✓；`WINDOWING_VERSION→3` → Task 3 ✓
- §8 show-window 块头 / workorder 超集 / record-artifact 新 kind → Task 10/11/8 ✓
- §9 扫描件 fail-closed 不动状态机（cmd_source_convert 现有 is_scanned_source 段未改）→ Task 8 保持原样 ✓；`ARTIFACT_VERSION` 折进 input_hash → Task 7/8 ✓
- §10 验收（不装 MinerU 端到端、新 artifact、块/char 双模式、skill 零改动）→ Task 12 ✓

**2. 占位扫描：** 无 TBD/TODO；每个代码步给出完整代码。Task 8–10 的 CLI 测试已对齐 `tests/test_p2_cli.py` 真实夹具（`_run(args, cwd)` 子进程 + `STUDY_KB_ROOT` 隔离 + `pipeline-workspace/staging|state` 真实路径），无虚构 helper。

**3. 类型一致性：** `SourceBlock`/`RoutingAdvice`/`BackendResult`/`build_parse_report`/`write_blocks`/`read_blocks`/`write_parse_report`/`build_windows_from_blocks`/`page_char_ranges`/`_slice_section`/`_sections_from_blocks`/`converted_input_hash`/`ARTIFACT_VERSION`/`WINDOWING_VERSION="3"` 在各任务间签名/字段一致。`source_convert` 重新导出 `BackendUnavailable` 以保 `test_unknown_backend_raises`。

**4. 歧义：** Markdown `heading_path` 明确为 `_sections` 直接标题（不嵌套），与等价性测试一致；PyMuPDF `block.text=get_text().strip()`、char span=整页段，不变量由 Task 6/12 测试钉死。
