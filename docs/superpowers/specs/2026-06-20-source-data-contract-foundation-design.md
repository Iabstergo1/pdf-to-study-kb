# Spec① · 源数据契约地基（Source Data Contract Foundation）

> 状态：设计稿（待用户复核 → writing-plans）
> 日期：2026-06-20
> 范围标识：**Spec①（两段式重构的第一段）**。本段**零 MinerU、零硬件依赖**。
> 关联：Spec②（MinerU structured backend + 两级 auto 路由 + 风险 lint）在本地基稳定后单独开。

---

## 1. 背景与动机

当前预处理把 `source.md`（PyMuPDF 抽纯文本 / Markdown 直通）当作**唯一源材料**，`windowing` 在其字符流上按标题/页码切窗。这条 route B 路径对 born-digital PDF 有效，但：

- `source.md` 既是「给 LLM 顺读的视图」又是「程序切窗/定位的事实坐标」，两个职责耦合在一份纯文本里；
- 没有结构化事实层，未来接 MinerU（结构化 text/table/equation/image 块）无处落地；
- `parse_report` 缺失，付费 ingest 前无法用确定性质量信号判断「这本书是否值得交给 LLM / 是否该换后端」。

**本段目标（一句话）**：把 `source.md` 从唯一源材料**降级为 LLM 顺读视图**，新增 `blocks.jsonl`（确定性事实层）+ `parse_report.json`（质量/建议报告），并让 `windowing` 从 blocks 构窗——**全部以纯加法 + 行为保留的方式完成，不引入 MinerU、不改任何 skill 树**。

这是 Spec② 的地基：MinerU 接进来时只需新增一个 backend 产出同一套 artifact 契约，下游（windowing / show-window / workorder / preflight）无需再改形状。

---

## 2. 范围与非目标

### 2.1 本段做（In scope）

1. 新增数据契约模块 `scripts/source_artifacts.py`（`SourceBlock` / `ParseReport` / `ConvertResult` + 序列化）。
2. 把 `source_convert.py` 降级为 dispatcher，后端拆进 `scripts/source_backends/{markdown,pymupdf}_backend.py`。
3. 现有 Markdown / PyMuPDF 两后端均产出：`source.md` + `blocks.jsonl` + `chapters.json`（已有，不改）+ `parse_report.json` + `assets/`。
4. `windowing` 支持 block-aware windows；无 blocks（legacy staging）退回旧 char-window。
5. `workorder` / `show-window` / `record-artifact` 升级为**超集**以记录/展示新 artifact。
6. 全量 pytest 覆盖：旧路径兼容、新 artifact 生成、block-aware window、char fallback、show-window 块头。

### 2.2 本段不做（Non-goals，显式留给 Spec②）

- ❌ 不引入 MinerU；不新增 `requirements-mineru.txt`。
- ❌ 不调用 MinerU、**不检测 MinerU 是否安装**。
- ❌ 不新增 `--backend` / `--mineru-policy` CLI 路由开关；`auto` 不据 `routing_advice` 切后端。
- ❌ 不从 PyMuPDF 字体/坐标推断 heading 块，不引入 `get_text("dict")` 细粒度碎片化。
- ❌ 不新增基于 risk_flags 的「激进」lint 规则。
- ❌ 不改 `.claude/skills/**` 与 `.agents/skills/**`（双树字节对等天然保持）；skill/文档文字同步留给 Spec②。

---

## 3. 关键设计原则（来自需求澄清，硬约束）

1. **coarse-grained blocks**：Spec① 是「硬件无关的数据契约地基」，不是提前复刻 MinerU 的细粒度 layout 能力。PyMuPDF 用**页粒度**稳定块，**不**模拟 layout。
2. **行为保留**：PyMuPDF 不产 heading 块 → block-window 不按标题切 → 完整保留今天 PDF 的「页标记禁标题切」防碎片化（v5）。Markdown 复刻现有 `_sections` 结构 → 保留今天 md 行为。
3. **纯加法/超集**：`convert()` 返回 dict、`build_windows()` char 契约、`workorder.source` 契约都有测试钉死——一律保留旧字段，只新增。
4. **blocks.jsonl 是事实层，不是页面**：它是 windowing 的输入，不是 LLM 语义切分结果，更不是 Obsidian 页。schema 须能容纳未来 MinerU 的 `table/equation/image` 细类型，但①不要求 PyMuPDF 提供这些。
5. **parse_report advisory-only**：①写描述性路由建议，但**任何路由逻辑都不得在①消费**；`consumed_by_auto_router` 恒 `false`，`mineru_status="not_checked"`（**不写** `mineru_available`，避免被误解为已探测安装）。

---

## 4. 架构

```text
scripts/
  source_artifacts.py          # 数据契约 + 序列化（纯函数；唯一 I/O 是显式 write_*/read_*）
  source_backends/
    __init__.py                # get_backend(fmt) → ① 注册 {md: markdown, pdf: pymupdf}；预留 mineru 空位
    markdown_backend.py        # 由 _convert_markdown 迁入 + 产 section-level blocks + parse_report
    pymupdf_backend.py         # 由 _convert_pdf_text 迁入 + 产 page blocks + route-B PNG（逻辑原样）+ parse_report
  source_convert.py            # dispatcher：选后端 → 写 source.md/blocks.jsonl/chapters.json/parse_report.json/assets → 返回 ConvertResult
  windowing.py                 # 新增 build_windows_from_blocks()；保留 build_windows()（char，fallback）
  workorder.py                 # source 块加 source_md/blocks_jsonl/parse_report_json/assets_dir/backend（旧键全留）
  pipeline.py                  # cmd_source_convert 记 blocks/parse_report artifact；cmd_windows 选块窗/char 窗；cmd_show_window 加块元数据头
```

**单元边界（可独立测试）：**

| 单元 | 做什么 | 怎么用 | 依赖 |
|---|---|---|---|
| `source_artifacts` | 定义 `SourceBlock`/`ParseReport`/`ConvertResult`，blocks.jsonl 与 parse_report.json 读写 | `write_blocks(path, blocks)` / `read_blocks(path)` / `write_parse_report(path, report)` | 仅 stdlib（dataclasses/json） |
| `source_backends.markdown` | md → (source_md, blocks, chapters, parse_report) | `convert(src_path, out_dir) -> BackendResult` | `source_profile`, `chaptering`, `source_artifacts` |
| `source_backends.pymupdf` | pdf → (source_md, blocks, chapters, parse_report) + 难页 PNG | 同上 | `fitz`, `source_profile`, `chaptering`, `source_artifacts` |
| `source_convert`（dispatcher） | 按 fmt 选后端、落盘全部 artifact、拼 `ConvertResult` | `convert(src_path, *, out_dir, fmt) -> dict` | 上述后端 |
| `windowing.build_windows_from_blocks` | blocks → block-aware windows | 纯函数 | 无 I/O |

---

## 5. 数据契约

### 5.1 `SourceBlock`

```python
@dataclass
class SourceBlock:
    block_id: str            # 全源单调序号 "b{seq:06d}"（PyMuPDF 1 块/页时 seq 即页序）
    type: str                # "text" | "heading"（① PyMuPDF 仅 text；Markdown 有 heading/text）
                             # 预留 "table" | "equation" | "image"（② MinerU），① 不产出
    text: str
    page: int                # 1-based（PyMuPDF=页号；Markdown=1）
    char_start: int          # 进 source.md 的字符偏移（保 char 兼容 + 供窗口聚合/show-window 切片）
    char_end: int
    text_level: int | None = None   # Markdown heading 的 # 级数；正文/PyMuPDF 为 None
    heading_path: str = ""          # Markdown：所属标题路径；PyMuPDF：""
    asset_path: str | None = None   # needs_vision 页 PNG 的 staging 相对路径（assets/pXXXX.png）；否则 None
    risk_flags: list[str] = field(default_factory=list)  # 由 profile reasons 派生
    source_ref: str = ""            # f"p{page:04d}#{block_id}" → 如 "p0043#b000043"
```

- **risk_flags 取值**（来自 `source_profile.needs_vision_reasons`）：`formula` / `formula-borderline` / `table` / `vector-figure` / `scanned-or-image` / `caption`。PyMuPDF 页块直接把该页的 reasons 填入。Markdown 块通常为空（md 无难页概念）。
- **page/坐标统一 1-based**：所有归一在后端内完成，下游不再 `+1`。

### 5.2 `blocks.jsonl`

每行一个 `SourceBlock` 的 JSON。示例（PyMuPDF，p43 公式难页）：

```json
{"block_id":"b000043","type":"text","page":43,"char_start":18233,"char_end":18901,"text_level":null,"heading_path":"","asset_path":"assets/p0043.png","risk_flags":["formula","caption"],"source_ref":"p0043#b000043","text":"…该页拍平后的纯文本…"}
```

示例（Markdown，二级标题段）：

```json
{"block_id":"b000007","type":"heading","page":1,"char_start":540,"char_end":1804,"text_level":2,"heading_path":"3 两阶段博弈 > 3.2 子博弈完美","asset_path":null,"risk_flags":[],"source_ref":"p0001#b000007","text":"## 3.2 子博弈完美\n\n在子博弈完美均衡中……（该 section 的完整正文，直到下一切分边界）"}
```

> **Markdown 块语义（消歧）**：section 块的 `text` 是该 section 的**完整 Markdown 片段**（heading 行 + 其下正文，直到下一切分边界）；`type="heading"` 仅表示该块**首行**是 heading，**绝不**把 heading 行单拆成小块而丢正文——否则破坏与 `_sections`/char 窗的等价性。

### 5.3 `parse_report.json`（advisory-only）

**公共信封**（两后端都有）：

```json
{
  "selected_backend": "pymupdf",
  "backend_policy": "contract_only",
  "artifact_version": "1",
  "input_hash": "<sha256(raw):PROFILER_VERSION:ARTIFACT_VERSION>",
  "routing_advice": {
    "recommended_backend": "pymupdf",
    "structured_reparse_recommended": false,
    "reasons": [],
    "advisory_only": true,
    "consumed_by_auto_router": false
  },
  "mineru_status": "not_checked",
  "warnings": []
}
```

复杂 PDF（建议未来重解析）示例：

```json
{
  "selected_backend": "pymupdf",
  "backend_policy": "contract_only",
  "routing_advice": {
    "recommended_backend": "mineru",
    "structured_reparse_recommended": true,
    "reasons": ["low_text_density", "scan_suspected", "table_or_formula_dense"],
    "advisory_only": true,
    "consumed_by_auto_router": false
  },
  "mineru_status": "not_checked",
  "warnings": []
}
```

**per-backend 附加字段：**

- PyMuPDF：`page_count` / `block_count` / `needs_vision_pages`(list) / `risk_flag_counts`(dict flag→count)。
- Markdown：`section_count` / `heading_count` / `block_count`。

**`routing_advice` 的确定性算法（advisory，读 profile 已算信号，零 MinerU、零硬件探测）：**

- PyMuPDF（reasons 由 backend/report 层**聚合 profile 已有 per-page 信号**得出，**不在 `source_profile` 新增职责**）：
  - `scan_suspected` ⟸ 带 `scanned-or-image` risk_flag 的页占比 ≥ 阈值（建议 0.30，tunable）。注：≥80%/≥80% 的**整本**扫描件已在 convert 前 fail-closed（见 §9），不会到这；此项仅捕获进入了 convert 的 sub-threshold 混合源。
  - `low_text_density` ⟸ 页均 `text_len` 低于阈值（建议 100，tunable）。
  - `table_or_formula_dense` ⟸ 带 `formula`/`formula-borderline`/`table` risk_flag 的页占比 ≥ 阈值（建议 0.30，tunable）。
  - 任一 reason 命中 → `recommended_backend="mineru"`, `structured_reparse_recommended=true`；否则 `recommended_backend="pymupdf"`, `false`, `reasons=[]`。
- Markdown：恒 `recommended_backend="markdown"`, `structured_reparse_recommended=false`（md fast path 足够）。
- 阈值为 advisory，Spec② 会重新校准；①只保证**确定性 + 不被任何路由逻辑消费**。

### 5.4 `ConvertResult`（`source_convert.convert()` 返回 dict，超集）

保留现有键：`source_md` / `sha256` / `assets_dir` / `pages` / `needs_vision_pages` / `chapters` / `chapters_path` / `chapters_sha`。
新增键：`blocks_path` / `blocks_sha` / `parse_report_path` / `parse_report_sha` / `backend`。

> `ARTIFACT_VERSION`（常量，置于 `source_artifacts.py`，初值 `"1"`）折进 `converted` 阶段 input_hash；artifact 格式升级即失效缓存、强制对任意来源重产 blocks/report（与 `PROFILER_VERSION`/`WINDOWING_VERSION` 同规）。

---

## 6. blocks.jsonl 生成规则

### 6.1 PyMuPDF backend（coarse / 页粒度）

- 逐页：构造**一个** `SourceBlock(type="text", page=i+1)`，`text = page.get_text().strip()`（与 source.md 该页正文逐字同源）。
- `char_start/char_end`：覆盖该页在 `source.md` 中的**完整 page segment**（`<!-- page N -->` 标记 + 该页正文），由与 `_page_ranges_for_md` **同一套 marker 扫描**派生（抽成共享 helper），与 show-window 页范围同源、是唯一定位真值。不变量（测试钉死）：`source_md[char_start:char_end]` 同时包含该页 `<!-- page N -->` 标记与 `block.text`；多页窗聚合后不丢任何标记。
- `risk_flags`：取该页 `source_profile.needs_vision_reasons`。
- `asset_path`：若该页 `needs_vision`，渲染整页 PNG（逻辑与今天一致）并填相对路径；否则 `None`。
- **不**做字体/坐标 heading 推断；`type` 恒 `text`，`text_level=None`，`heading_path=""`。

### 6.2 Markdown backend（section-level）

- 复用与 `windowing._sections` **一致**的标题切分，得到 `(heading_path, char_start, char_end)` 段。
- 每段构造一个 `SourceBlock`：`text` = 该 section 的**完整 Markdown 片段**（heading 行 + 其下正文）；段首是标题行则 `type="heading"`（仅表示首行是 heading）且 `text_level=#` 级数，否则 `type="text"`；`heading_path` 填该段标题路径；`page=1`。**绝不**把 heading 行单拆成小块而丢正文（破坏等价性）。
- 之所以与 `_sections` 一致：保证 §7 的「md 块窗 ≈ 今天 char 窗」等价性。

> `chapters.json` 由现有 `chaptering.chapters_from_toc` 产出，**本段不改**。

---

## 7. Windowing（block-aware + char fallback）

### 7.1 新函数 `build_windows_from_blocks(blocks, *, target_tokens=2000, max_tokens=4000, overlap_tokens=200)`

- 按 `heading_path` 把连续同路径块聚成 section（PyMuPDF 全程 `""` → 单 section）。
- section 内贪心累积块直到 token 预算（`_est_tokens` 复用），出窗；section/单块超 `max_tokens` 时按字符滑窗 + overlap 子切（复用现有滑窗逻辑，落在该块 `[char_start, char_end]` 内）——**这保证 PyMuPDF 密集页与今天 char 行为一致**。
- 窗口字段（**windows.jsonl 超集**）：

```json
{
  "window_id": "w0032",
  "mode": "blocks",
  "heading_path": "3 两阶段博弈 > 3.2 子博弈完美",
  "char_start": 18233,
  "char_end": 21044,
  "overlap_before": 0,
  "block_ids": ["b000120", "b000121", "b000122"],
  "page_start": 41,
  "page_end": 47,
  "token_estimate": 2800,
  "contains": ["text"],
  "assets": ["assets/p0043.png"],
  "risk_flags": ["formula", "table"]
}
```

- `char_start/char_end`：由窗内块的 source.md 跨度聚合（min start / max end）——保旧消费者（show-window 切片、page-range 计算）。因 PyMuPDF 页块 span 连续且各含自身 `<!-- page N -->` 标记，多页窗聚合后**不丢任何页标记**。

### 7.2 char fallback（legacy）

- `build_windows(md, ...)`（现有 char 实现）**原样保留**，输出 `mode="chars"`（新增该字段，其余不变）。
- 触发：staging 无 `blocks.jsonl`（旧 staging / 预处理产物被清理）。

### 7.3 `cmd_windows` 调度与版本

- 有 `blocks.jsonl` → `read_blocks` → `build_windows_from_blocks`；input_hash = `sha256(blocks.jsonl) + ":" + WINDOWING_VERSION`。
- 无 → 读 `source.md` → `build_windows`；input_hash = `sha256(source.md) + ":" + WINDOWING_VERSION`（与今天一致）。
- `WINDOWING_VERSION` `"2" → "3"`（切分逻辑实质变化，失效缓存）。

---

## 8. CLI 改动（全加法）

| 命令 | 改动 | 兼容性 |
|---|---|---|
| `source-convert`（`cmd_source_convert`） | dispatcher 落盘 `blocks.jsonl`/`parse_report.json`；`record_artifact` 新增 `kind="blocks"`、`kind="parse_report"`；`converted` input_hash = `sha256(raw)+PROFILER_VERSION+ARTIFACT_VERSION`（保留 `PROFILER_VERSION` 以连带难页 PNG 重渲） | 返回 dict 旧键全留；难页 PNG 流程不变 |
| `windows`（`cmd_windows`） | 见 §7.3 | 无 blocks 自动退 char |
| `show-window`（`cmd_show_window`） | mode=blocks 时，在现有 route-b 资产头 + 窗文本**之前**打印「块元数据头」（heading_path / page 范围 / block_ids / risk_flags / assets）；mode=chars 时**完全照旧** | 旧 ingest 流程消费不变（块头是额外上下文）；窗文本仍按 char 切片 source.md |
| `workorder`（`build_workorder`） | `source` 块新增 `source_md`/`blocks_jsonl`/`parse_report_json`/`assets_dir`/`backend`；保留 `text_md`/`page_images_dir`/`processing_windows` | 旧测试/消费者不破 |

> **不新增** `--backend` / `--mineru-policy`（留 Spec②）。dispatcher 内部按 fmt 选后端，与今天对外行为一致。

---

## 9. 错误处理 / 兼容 / 迁移

- **未装 MinerU**：①根本不引用 MinerU → 全链照跑（验收硬标准）。
- **未知/未实现 fmt**（docx/pptx）：`source_convert` 继续抛 `BackendUnavailable`（与今天一致；结构化适配留 Spec②/MinerU）。
- **整本扫描件（fail-closed）**：`is_scanned_source` 命中时 `source-convert` 在进入 backend **之前** `SystemExit`（与今天一致，**状态机边界不变**）→ 该源**不产 source.md/blocks/parse_report**。`parse_report.json` 仅保证在 `source-convert` 成功进入 backend conversion 时生成；Spec① **不为「让扫描件也有 parse_report」改动状态机**，扫描件真实自动路由留 Spec②。（若后端内部再遇 fail-closed，可选写最小 parse_report 后退出，非①硬要求。）
- **legacy staging（无 blocks.jsonl）**：windows + show-window 自动退 char 模式。
- **版本/缓存与 forward-only 重跑陷阱**：`ARTIFACT_VERSION`/`WINDOWING_VERSION` 失效缓存使**新源**自动产出新 artifact；但状态机 forward-only，**在制源**（已过 `profiled/converted`）需 reset 对应 DB 行 + 清 staging 才能重出（记录于运行手册，非阻断）。测试一律用全新源，不受影响。

---

## 10. 测试与验收

### 10.1 新增/扩展测试

- **新 `tests/test_source_artifacts.py`**：`SourceBlock` 往返（`write_blocks`/`read_blocks` 等价）；`ParseReport` 形状（公共信封 + per-backend 字段；`advisory_only=true`、`consumed_by_auto_router=false`、`mineru_status="not_checked"`、无 `mineru_available` 键）；`ConvertResult` 键为旧键超集。
- **扩 `tests/test_source_convert.py`**：
  - markdown → 产 `source.md`+`blocks.jsonl`(section 块，含 heading 的有 `text_level`/`heading_path`)+`chapters.json`+`parse_report.json`(markdown 字段)；**section 块 `text` 含整段（heading 行 + 正文）**，与 `source_md[char_start:char_end]` 一致。
  - pymupdf → 产同套 artifact；`blocks.jsonl` 每页 1 块、`type=text`、`risk_flags` 来自 profile；needs_vision 页 → 块 `asset_path` 置位 + PNG 仍生成 + `risk_flags` 含对应 reason。**char span 不变量**：每个 page 块 `source_md[char_start:char_end]` 含该页 `<!-- page N -->` 标记与 `block.text`，全块 span 连续拼回不丢标记。**parse_report 仅在 convert 运行时生成**：整本扫描件 fail-closed 用例断言**不产** parse_report（停在 profile，状态机边界不变）。
  - `convert()` 返回 dict 含旧键 + 新键（`blocks_path`/`parse_report_path`/`backend` 等）。
  - 现有断言（markdown passthrough / 文本 PDF / chapters / 矢量图渲染 / 未知后端 raise）**全部保留通过**。
- **扩 `tests/test_windowing.py`**：
  - `build_windows_from_blocks` md：按 heading 切，且对代表性 md **与今天 `build_windows`(char) 输出等价**（heading_path 序列 + 窗数）。
  - pymupdf 页块：合并为多页 token 窗、**不碎片化**——移植 `test_page_marker_disables_heading_split` 场景到块形态，断言短 2 页源 → 1 窗、`heading_path==""`。
  - 超长 section/块 → 字符子切 + overlap；窗带 `block_ids`/`page_start`/`page_end`/`contains`/`assets`/`risk_flags`。
  - 无 blocks → 退回 char（现有 char 用例不动、`mode=="chars"`）。
- **show-window 块头测试**：mode=blocks 输出含 heading_path/page 范围/block_ids/risk_flags/assets，且仍打印窗文本与 route-b 资产头。
- **扩 `tests/test_workorder.py`**：`source` 块含新字段且旧字段（`processing_windows` 等）仍在。
- **record_artifact 新 kind**：在相关 p-cli 测试（如 `test_record_artifact`/`test_p*_cli`）补 `blocks`/`parse_report` 落库断言。

### 10.2 验收标准（全绿 = Spec① 完成）

1. **不安装 MinerU**，现有 PDF / MD ingestion 端到端跑通。
2. staging 下可见新 artifact：`blocks.jsonl` + `parse_report.json`（+ 既有 `source.md`/`chapters.json`/`assets/`）——以 `source-convert` 成功进入 backend 为前提；整本扫描件 fail-closed 仍停在 profile、**不产** parse_report（状态机边界不变）。
3. `windows.jsonl` 可从 blocks 生成（`mode=blocks`），legacy 无 blocks 时退回 char（`mode=chars`）。
4. `show-window` 能展示 block-aware window 的 page 范围 / block_ids / assets / risk_flags。
5. `workorder.yaml` 记录 source artifacts（含 `backend`）。
6. `pytest tests -q` 全绿（含 legacy 守卫 `test_legacy_removed`、双树对等守卫）。
7. `.claude/skills/**` 与 `.agents/skills/**` **零改动**（diff 为空）。

---

## 11. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 块窗边界与今天 char 窗不同导致 ingest 行为漂移 | md 走与 `_sections` 一致的切分 + 等价性测试；pdf 页块沿用「无 heading → 单 section 滑窗」，移植 v5 防碎片化用例钉死 |
| `source.md`/`blocks.jsonl` 字符偏移不一致 | 块由 source.md 切出，`char_start/char_end` 即其在 source.md 的区间，单一真值；show-window 仍按 char 切片 |
| 缓存版本变更使在制源卡住（forward-only） | 运行手册说明 reset DB 行 + staging；测试用全新源 |
| 误把 `routing_advice` 当成已接通的路由 | `advisory_only=true`/`consumed_by_auto_router=false`/`mineru_status="not_checked"` 三重显式标记 + 测试断言①无消费者 |
| 无意改动 skill 树破坏双树对等 | 验收标准 7：skill 两树 diff 必须为空；show-window 改动只在 `pipeline.py` |

---

## 12. Spec② 交接点（本段只预留，不实现）

- 新增 `scripts/source_backends/mineru_backend.py`（subprocess 调 `mineru -b pipeline`；未装/ OOM → `parse_report` 标 `mineru_failed`/`failure_reason` + fail-closed 或 Review-Queue）。
- `get_backend` 注册 mineru；新增 `source-convert --backend auto|pymupdf|mineru` 与 `--mineru-policy conservative|aggressive`。
- `auto`（conservative）接通 `routing_advice`：md→markdown；普通 born-digital PDF→pymupdf（即使密集，仅写 advice）；docx/pptx 与 扫描/低文本密度 PDF→mineru（不可用 fail-closed）。`aggressive` 才让密集 born-digital 也走 mineru。
- `mineru_status` 由 `not_checked` 变为真实探测；`consumed_by_auto_router` 在被 auto 读时置 `true`。
- 新增 risk_flags 风险 lint（仅对 `selected_backend=mineru` 的新源渐进启用，不破坏旧来源）。
- 双 skill 树 + README/CLAUDE.md/AGENTS.md 同步（移除「无重型 OCR/ML 后端」硬约束的措辞，改为「默认轻量 fast path + 可选 MinerU structured backend」）。
- 硬件约束：目标机 RTX 3050 Ti 4GB → MinerU 仅 `pipeline` 后端、显式 `-b pipeline`、禁 vlm/hybrid。
