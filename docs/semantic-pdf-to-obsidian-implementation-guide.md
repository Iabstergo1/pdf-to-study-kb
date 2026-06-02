# LangGraph + SQLite 语义化 PDF-to-Obsidian 执行指导文档

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to implement this guide task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有 `section-manifest + source-slice.md + 单节讲义` 流程改造成 `semantic-unit-plan + unit LangGraph + 双 SQLite + surya-ocr + Obsidian 完整生态` 流程。

**Architecture:** Python CLI 负责 book-level 编排，LangGraph 负责 unit-level 状态图。LangGraph checkpointer 只保存图恢复点，业务 SQLite 保存观测、成本、记忆、证据和错误。PDF 内容按 unit 按需抽取，不再持久化完整 `source-slice.md`。

**Tech Stack:** Python 3.11+, PyMuPDF, LangGraph, langgraph-checkpoint-sqlite, sqlite3, PyYAML, surya-ocr>=0.20.0, pytest, Obsidian Markdown.

---

## 当前验证状态

截至 2026-06-02，`codex/semantic-phase-1` 分支已在 `game-model-test` fixture 上跑通 LangGraph semantic unit 主流程。该结论只覆盖下列命令和产物，不表示仓库内所有辅助脚本都已逐个验证。

已验证：

- `python scripts/surya_smoke.py --book game-model-test --page 1 --keep-alive`：单页 Surya OCR smoke 连续返回 exit code 0，`status=ok`，识别块数为 16。
- `python scripts/pipeline.py run-book --book game-model-test --executor langgraph-worker`：最新完整运行产物为 `books/game-model-test/pipeline-workspace/runs/run-20260602-161212/semantic-run-summary.json`，`section-3.1`、`section-3.2`、`section-3.3` 均为 `published`，`blocked=[]`，`skipped=[]`。
- `books/game-model-test/study-kb/Review-Queue/`：当前计划对应的待审队列为空；三篇当前讲义已写入 `Section-Lessons/section-3.1.md`、`section-3.2.md`、`section-3.3.md`。
- `python -m pytest -q`：99 passed, 6 skipped。
- `git diff --check` 和 `python -m py_compile scripts/surya_smoke.py`：通过。

尚未验证或不应外推的范围：

- 未逐个运行仓库里的所有辅助脚本、一次性报告脚本和 `scripts/legacy/` 旧流程。
- 未在其他 PDF、其他 book id、纯扫描件 PDF 或大体量整书上做端到端复跑。
- 未验证 vLLM/GPU 后端、Linux/macOS 后端差异或空缓存环境的首次模型下载流程。
- 未在 Obsidian 客户端中做人工视觉检查或 Dataview 渲染检查。
- 历史 `pipeline-workspace/runs/` 与历史报告中可能保留旧的 `needs_human_review`、`evidence_missing` 记录；判断当前状态时以最新 run summary 和当前 `study-kb/Review-Queue/` 为准。

本指南仍按“先补确定性底座，再接 LLM 和 LangGraph”的顺序组织，避免在没有校验和观测的情况下直接接入模型。

## 目标文件结构

### 新增文件

- `scripts/pdf_profile.py`：生成 PDF TOC、每页摘要、文本密度、图片/表格/公式风险。
- `scripts/unit_plan.py`：语义 unit plan 数据转换、schema 校验、覆盖率校验、人工审批辅助。
- `scripts/unit_context.py`：按 unit 抽取文本块、OCR 结果、证据索引、边界校验摘要。
- `scripts/ocr_surya.py`：surya-ocr 可选依赖 adapter，处理未安装、失败重试和结构化输出。
- `scripts/business_db.py`：业务 SQLite schema、事件写入、model call、cost、memory、evidence ledger。
- `scripts/memory_store.py`：rolling memory 读写、增量更新、summary compaction。
- `scripts/evidence_verifier.py`：核心结论证据覆盖、公式/符号/变量门禁。
- `scripts/review_gate.py`：review JSON/Markdown 输出解析和强制 reject 规则。
- `scripts/obsidian_indexes.py`：run 结束后的聚合索引构建。
- `tests/test_unit_plan.py`：semantic unit plan 覆盖率、重叠、skip、schema 测试。
- `tests/test_unit_context.py`：text/hybrid/screenshot_ocr 选择、OCR fallback、边界校验测试。
- `tests/test_business_db.py`：双 SQLite 分库、events/model_calls/memory/evidence 写入测试。
- `tests/test_unit_graph.py`：unit graph accept/revise/reject/OCR 阻断/Review-Queue 测试。
- `tests/test_obsidian_indexes.py`：Dataview frontmatter 和索引刷新测试。

### 修改文件

- `scripts/pipeline.py`：新增 `profile-pdf`, `plan-units`, `validate-unit-plan`, `review-unit-plan` 命令，保留旧命令兼容。
- `scripts/run_book.py`：读取 `config/semantic-unit-plan.yaml`，按 include unit 调度 `langgraph-worker`。
- `scripts/langgraph_worker.py`：从 section graph 改为 unit graph 节点：`prepare_context -> generate_note -> verify_evidence -> review_note -> revise_note -> update_memory -> publish_note`。
- `scripts/llm_provider.py`：记录 provider、model、tokens、cost 所需字段，支持 planner/reviewer/author 分工。
- `scripts/obsidian_output.py`：只保留兼容入口，调用 `obsidian_indexes.build_obsidian_indexes()`。
- `schemas/section-lesson.schema.json`：确认 Dataview frontmatter 字段与模板一致。
- `templates/section-lesson.template.md`：确认 `managed_by: pipeline` 和 unit 字段完整。
- `templates/review-report.template.md`：确认必须包含证据对照表和公式风险清单。
- `requirements.txt`：必须包含 `langgraph-checkpoint-sqlite>=3.0.1` 和 `surya-ocr>=0.20.0`；执行时允许环境注释掉 surya 依赖并让高公式页进入人工队列。

Required dependency diff:

```diff
 langgraph>=0.2.0 # 有状态 LLM 流水线（langgraph-worker）
+langgraph-checkpoint-sqlite>=3.0.1 # LangGraph SqliteSaver checkpoint

+# 可选：高公式页 OCR。Surya 2 Python API 依赖 >=0.20.0；未安装时高公式页进入 Review-Queue 人工处理。
+surya-ocr>=0.20.0
```

Surya OCR 运行要求与注意事项：

- `surya-ocr>=0.20.0` 会声明 PyTorch 相关依赖；通常不需要在本项目再单独列 `torch`。
- Surya 2 OCR 需要推理后端。GPU 路径通常使用 vLLM/NVIDIA 环境；Windows/CPU 路径使用 llama.cpp 的 `llama-server.exe`。本项目会尝试自动发现 WinGet 安装的 `llama-server.exe`，也可以用 `LLAMA_CPP_BINARY` 显式指定。
- 模型文件不提交到仓库。首次运行可能需要下载并缓存模型；已验证环境中缓存包括 `surya-2.gguf` 和 `surya-2-mmproj.gguf`。空缓存、离线环境或代理异常时，先跑 smoke check 再跑整书。
- CPU/llama.cpp 路径很慢，单页 smoke 也可能耗时数分钟。整书运行会把成功 OCR 的页面写入 `books/<book>/pipeline-workspace/ocr-cache/page-XXXX.json`，后续复跑优先使用缓存。
- 后台 `llama-server` 异常退出时可能遗留 lock/sentinel 状态，表现为 OCR 进程返回 `status=failed` 或无法获取锁。本项目 adapter 会清理指向已退出进程的 stale 状态；如果仍失败，先执行 `python scripts/surya_smoke.py --book <book> --page 1 --keep-alive` 获取明确错误。
- OCR 结果只能作为证据候选，不应直接当作最终数学结论。高公式、高表格或混合冲突页面仍需要 evidence verifier 和 reviewer gate 兜底。
- 如果 Surya 未安装、模型不可用或推理后端不可用，行为与 OCR 不可用一致：高风险 unit 不自动发布，进入 `Review-Queue/` 等待人工处理。

## Phase 1: Book-Level CLI 与目录结构

**目标:** 新命令存在，能生成目标目录和空报告，不触发 LLM。

- [ ] **Step 1: 扩展 `_ensure_dirs()`**

Modify: `scripts/pipeline.py`

将目录列表扩展为：

```python
dirs = [
    "input",
    "config",
    "pipeline-workspace/reports",
    "pipeline-workspace/staging",
    "pipeline-workspace/reviews",
    "pipeline-workspace/runs",
    "pipeline-workspace/checkpoints",
    "pipeline-workspace/state",
    "pipeline-workspace/events",
    "study-kb/Section-Lessons",
    "study-kb/Concept-Cards",
    "study-kb/Glossary",
    "study-kb/Symbols",
    "study-kb/Formula-Ledger",
    "study-kb/Claims",
    "study-kb/Questions",
    "study-kb/Review-Queue",
    "study-kb/Learning-Maps",
    "study-kb/Source-QA",
    "study-kb/Dashboards",
]
```

Verify:

```powershell
pytest tests/test_langgraph_worker.py::test_run_book_langgraph_worker_uses_fake_provider -q
```

Expected: 旧测试仍通过，说明目录扩展未破坏旧路径。

- [ ] **Step 2: 确认语义规划 schema 文件名**

Move or verify:

- From: `schemas/section-manifest.schema.json`
- To: `schemas/semantic-unit-plan.schema.json`

Rule:

- 新语义单元规划只引用 `schemas/semantic-unit-plan.schema.json`。
- 旧 `section-manifest` 只表示 legacy section manifest，不再承载 semantic unit plan schema。
- 如果某个旧测试或旧脚本仍需要 `section-manifest.schema.json`，新增 legacy schema 或 compatibility note，不要让一个文件名表示两套契约。

Verify:

```powershell
rg -n "section-manifest\.schema|semantic-unit-plan\.schema" .
```

Expected: 主流程文档和新实现只指向 `semantic-unit-plan.schema.json`。

- [ ] **Step 3: 新增命令注册**

Modify: `scripts/pipeline.py`

新增四个 parser，并把命令映射到函数：

```python
profile_pdf_parser = subparsers.add_parser("profile-pdf", help="分析 PDF TOC、页码、风险和每页摘要")
profile_pdf_parser.add_argument("--book", required=True)
profile_pdf_parser.add_argument("--force", action="store_true")

plan_units_parser = subparsers.add_parser("plan-units", help="生成 semantic-unit-plan.candidates.yaml")
plan_units_parser.add_argument("--book", required=True)
plan_units_parser.add_argument("--force", action="store_true")

validate_unit_plan_parser = subparsers.add_parser("validate-unit-plan", help="校验 semantic unit plan 覆盖率")
validate_unit_plan_parser.add_argument("--book", required=True)

review_unit_plan_parser = subparsers.add_parser("review-unit-plan", help="人工审批 semantic unit plan")
review_unit_plan_parser.add_argument("--book", required=True)
review_unit_plan_parser.add_argument("--list", action="store_true")
```

新增函数只转发到新模块：

```python
def cmd_profile_pdf(args):
    from pdf_profile import profile_pdf_command
    profile_pdf_command(find_book_root(args.book), force=getattr(args, "force", False))

def cmd_plan_units(args):
    from unit_plan import plan_units_command
    plan_units_command(find_book_root(args.book), force=getattr(args, "force", False))

def cmd_validate_unit_plan(args):
    from unit_plan import validate_unit_plan_command
    validate_unit_plan_command(find_book_root(args.book))

def cmd_review_unit_plan(args):
    from unit_plan import review_unit_plan_command
    review_unit_plan_command(find_book_root(args.book), list_only=getattr(args, "list", False))
```

Verify:

```powershell
python scripts/pipeline.py --help
```

Expected: help 输出包含 `profile-pdf`, `plan-units`, `validate-unit-plan`, `review-unit-plan`。

## Phase 2: PDF Profile

**目标:** 用 PyMuPDF 生成 `config/pdf-profile.yaml` 和 `pipeline-workspace/reports/pdf-profile.md`。

- [ ] **Step 1: 创建 `scripts/pdf_profile.py`**

Create: `scripts/pdf_profile.py`

核心数据结构：

```python
def profile_pdf(book_root: Path) -> dict[str, Any]:
    pdf_path = find_pdf(book_root)
    doc = fitz.open(str(pdf_path))
    pages = []
    for index in range(len(doc)):
        page = doc[index]
        text_dict = page.get_text("dict")
        plain_text = page.get_text()
        pages.append(profile_page(index + 1, page, text_dict, plain_text))
    return {
        "book_id": book_root.name,
        "source_pdf": pdf_path.name,
        "total_pages": len(doc),
        "toc": [{"level": a, "title": b, "page": c} for a, b, c in doc.get_toc()],
        "pages": pages,
    }
```

`profile_page()` 逻辑：

- `text_length`: `len(plain_text.strip())`
- `summary_200`: 去掉连续空白后的前 200 字。
- `image_count`: `len(page.get_images())`
- `block_count`: `len(text_dict.get("blocks", []))`
- `formula_risk`: 命中 `\`, `∑`, `∫`, `∂`, Greek 字母、上下标样式、短变量密集行时升高。
- `table_risk`: 多行中有制表符、重复多空格、接近列对齐坐标时升高。
- `blank_variable_risk`: 文本短、图片多、公式符号少但页面存在大量空白块时升高。
- `recommended_extraction_method`: `text`, `screenshot_ocr`, `hybrid`。

Write:

- `books/<book-id>/config/pdf-profile.yaml`
- `books/<book-id>/pipeline-workspace/reports/pdf-profile.md`

Verify:

```powershell
python scripts/pipeline.py profile-pdf --book game-model-test --force
```

Expected:

- `config/pdf-profile.yaml` 存在。
- `reports/pdf-profile.md` 包含总页数、风险页统计、每页短摘要。
- 不写 `source-slice.md`。

## Phase 3: Semantic Unit Plan 与覆盖率校验

**目标:** planner 可生成候选，validator 可阻断缺页、越界、未解释重叠和非法字段。

- [ ] **Step 1: 创建 `scripts/unit_plan.py` 的 plan model**

Create: `scripts/unit_plan.py`

最小函数集合：

```python
VALID_UNIT_TYPES = {"concept", "derivation", "application", "intro", "transition", "appendix"}
VALID_EXTRACTION_METHODS = {"text", "screenshot_ocr", "hybrid"}

def expand_pages(raw_pages: list[int]) -> list[int]:
    if len(raw_pages) == 2 and raw_pages[0] <= raw_pages[1]:
        return list(range(raw_pages[0], raw_pages[1] + 1))
    return [int(page) for page in raw_pages]

def validate_unit_plan(plan: dict[str, Any], total_pages: int) -> dict[str, Any]:
    errors = []
    warnings = []
    covered: dict[int, list[str]] = {}
    for unit in plan.get("units", []):
        errors.extend(validate_unit_fields(unit))
        for page in expand_pages(unit["source_scope"]["pages"]):
            if page < 1 or page > total_pages:
                errors.append(f"{unit['unit_id']}: page {page} out of range 1..{total_pages}")
            covered.setdefault(page, []).append(unit["unit_id"])
    missing_pages = [p for p in range(1, total_pages + 1) if p not in covered]
    overlaps = [
        {"page": page, "units": ids}
        for page, ids in covered.items()
        if len(ids) > 1
    ]
    unexplained = [
        item for item in overlaps
        if any(not unit_by_id(plan, uid).get("overlap_reason") for uid in item["units"])
    ]
    if missing_pages:
        errors.append(f"missing pages: {missing_pages}")
    if unexplained:
        errors.append(f"unexplained overlaps: {unexplained}")
    return {"passed": not errors, "errors": errors, "warnings": warnings, "missing_pages": missing_pages, "overlaps": overlaps}
```

`validate_unit_fields()` 必须检查：

- `unit_id`, `title`, `unit_type`, `include`, `source_scope.pages`, `extraction_method`, `formula_risk`, `planner_confidence`, `review_status`
- `unit_type` 和 `extraction_method` 枚举合法。
- `include: false` 时 `skip_reason` 非空。
- `include: true` 时 `output_targets` 包含 `section-lesson`。
- `depends_on` 只能引用已存在或即将存在的 unit_id。

- [ ] **Step 2: 写 P0 覆盖率测试**

Create: `tests/test_unit_plan.py`

包含这些测试：

```python
def test_include_false_pages_still_count_as_covered():
    plan = {"units": [
        unit("U-001-01", [1], include=False, skip_reason="目录页"),
        unit("U-001-02", [2, 3], include=True),
    ]}
    result = validate_unit_plan(plan, total_pages=3)
    assert result["passed"]

def test_missing_page_blocks_validation():
    plan = {"units": [unit("U-001-01", [1], include=True)]}
    result = validate_unit_plan(plan, total_pages=2)
    assert not result["passed"]
    assert "missing pages" in "; ".join(result["errors"])

def test_overlap_without_reason_blocks_validation():
    plan = {"units": [
        unit("U-001-01", [1, 2], include=True),
        unit("U-001-02", [2, 3], include=True),
    ]}
    result = validate_unit_plan(plan, total_pages=3)
    assert not result["passed"]

def test_overlap_with_reason_passes():
    plan = {"units": [
        unit("U-001-01", [1, 2], include=True, overlap_reason="跨页标题"),
        unit("U-001-02", [2, 3], include=True, overlap_reason="跨页标题"),
    ]}
    result = validate_unit_plan(plan, total_pages=3)
    assert result["passed"]
```

Run:

```powershell
pytest tests/test_unit_plan.py -q
```

Expected: 先失败，再实现通过。

- [ ] **Step 3: 实现 `plan-units` prompt 和候选输出**

Modify: `scripts/unit_plan.py`

`plan_units_command()` 读取：

- `config/pdf-profile.yaml`
- `config/book-profile.yaml`
- `config/study-profile.yaml`

Prompt 固定输入：

```yaml
task: 生成 semantic-unit-plan.candidates.yaml
inputs:
  toc: <pdf toc>
  total_pages: <int>
  per_page_summary_200: <page summaries>
  per_page_risks: <text_length, formula_risk, table_risk, image_count, blank_variable_risk>
  user_goal: 生成 Obsidian 本地学习知识库
constraints:
  - 必须覆盖全部页码
  - include false 仍计入覆盖
  - 重叠必须有 overlap_reason
  - 高公式页推荐 hybrid 或 screenshot_ocr
  - 每个 include unit 必须给 depends_on, risk_flags, output_targets
```

Prompt construction:

```python
def build_planner_payload(pdf_profile: dict[str, Any], book_profile: dict[str, Any], study_profile: dict[str, Any]) -> dict[str, Any]:
    pages = [
        {
            "page": page["page"],
            "summary_200": page["summary_200"],
            "text_length": page["text_length"],
            "formula_risk": page["formula_risk"],
            "table_risk": page["table_risk"],
            "image_count": page["image_count"],
            "blank_variable_risk": page["blank_variable_risk"],
        }
        for page in pdf_profile["pages"]
    ]
    return {
        "task": "generate_semantic_unit_plan",
        "book": {
            "book_id": book_profile["book_id"],
            "title": book_profile.get("title", ""),
            "language": book_profile.get("language", "zh"),
            "study_goal": "生成 Obsidian 本地学习知识库",
        },
        "toc": pdf_profile.get("toc", []),
        "total_pages": pdf_profile["total_pages"],
        "pages": pages,
        "constraints": [
            "必须覆盖 1..total_pages 的全部页码，包括 include=false 的页",
            "缺页、越界、未解释重叠均不可接受",
            "引言、目录、过渡、重复内容可 include=false，但必须给 skip_reason",
            "高公式页推荐 hybrid 或 screenshot_ocr",
            "每个 include=true unit 必须包含 depends_on, risk_flags, output_targets",
        ],
        "output_schema": {
            "book_id": "string",
            "generated_at": "ISO datetime",
            "planner_model": "string",
            "total_pages": "integer",
            "units": "array of semantic unit objects",
        },
    }
```

LLM call:

```python
from llm_provider import create_provider, load_provider_config

provider_config = load_provider_config()
provider = create_provider(provider_config)
payload = build_planner_payload(pdf_profile, book_profile, study_profile)
response = provider.chat_json(
    system=(
        "你是 PDF-to-Obsidian 的语义规划器。"
        "只输出 JSON 对象，不输出 Markdown。"
        "输出必须能直接转换为 semantic-unit-plan.candidates.yaml。"
    ),
    user=yaml.dump(payload, allow_unicode=True, sort_keys=False),
    model=provider_config.planner_model,  # 默认 DeepSeek V4 Flash
    temperature=0.1,
)
candidate_plan = normalize_planner_response(response, book_root.name, pdf_profile["total_pages"], provider_config.planner_model)
```

Parsing rules:

- 使用 `provider.chat_json()`，依赖 OpenAI-compatible `response_format={"type": "json_object"}`。
- `normalize_planner_response()` 只接受 JSON object；如果模型返回 `{"units": [...]}` 以外结构，写入 planning report 并失败。
- 写 YAML 时使用 `yaml.dump(candidate_plan, allow_unicode=True, sort_keys=False)`。
- 写入前必须调用 `validate_unit_plan(candidate_plan, total_pages)`；失败也保留 candidates 文件，但阻断进入人工审批。

Output:

- `books/<book-id>/config/semantic-unit-plan.candidates.yaml`
- `books/<book-id>/pipeline-workspace/reports/unit-planning-report.md`

Dry-run command:

```powershell
python scripts/pipeline.py plan-units --book game-model-test --force
python scripts/pipeline.py validate-unit-plan --book game-model-test
```

Expected: validation 报告写入 `pipeline-workspace/reports/unit-plan-validation.md`。失败时 exit code 非 0。

## Phase 4: Human Review of Unit Plan

**目标:** 人工审批在图外完成，输出 `config/semantic-unit-plan.yaml`。

- [ ] **Step 1: 实现 list 和交互审批**

Modify: `scripts/unit_plan.py`

`review_unit_plan_command(book_root, list_only)` 行为：

- `--list` 打印每个 unit：`unit_id`, `pages`, `include`, `extraction_method`, `formula_risk`, `planner_confidence`, `review_status`。
- 无 `--list` 时逐个处理 `review_status: pending` 或 `planner_confidence: low` 的 unit。
- 支持选择：接受、编辑标题、编辑页码、合并到前一个 unit、拆分为两个 unit、标记 `include: false`。
- 保存候选文件原地更新。
- 只有所有 include unit 状态为 `accepted` 或 `edited`，且 `validate_unit_plan()` 通过时，才写入 `config/semantic-unit-plan.yaml`。

关键防线：

```python
if not validation["passed"]:
    write_validation_report(report_path, validation)
    raise SystemExit("unit plan validation failed; see unit-plan-validation.md")
```

Interaction implementation:

- 第一版只用标准库 `input()`，不引入 `rich` 或 `prompt_toolkit`。原因：审批命令是本地低频工具，避免为了交互 UI 增加依赖。
- 每次编辑后立即重新运行 `validate_unit_plan()`，并在屏幕打印新增错误。
- 每个操作都只修改 candidates YAML，正式 plan 只在最终校验通过时生成。

CLI skeleton:

```python
def review_unit_plan_command(book_root: Path, list_only: bool = False):
    path = book_root / "config" / "semantic-unit-plan.candidates.yaml"
    plan = load_yaml(path)
    if list_only:
        print_unit_table(plan)
        return

    units = plan["units"]
    index = 0
    while index < len(units):
        unit = units[index]
        if unit.get("review_status") in {"accepted", "edited", "skipped"} and unit.get("planner_confidence") != "low":
            index += 1
            continue
        print_unit_for_review(unit)
        choice = input("[a]接受 [t]改标题 [p]改页码 [m]并入前项 [s]拆分 [x]跳过 [q]退出 > ").strip().lower()
        if choice == "a":
            unit["review_status"] = "accepted"
            index += 1
        elif choice == "t":
            unit["title"] = input("新标题 > ").strip()
            unit["review_status"] = "edited"
        elif choice == "p":
            unit["source_scope"]["pages"] = parse_pages_input(input("页码，如 1-3,5 > "))
            unit["review_status"] = "edited"
        elif choice == "m":
            merge_unit_into_previous(units, index)
            index = max(0, index - 1)
        elif choice == "s":
            split_unit_interactively(units, index)
        elif choice == "x":
            unit["include"] = False
            unit["skip_reason"] = input("跳过原因 > ").strip()
            unit["review_status"] = "skipped"
            index += 1
        elif choice == "q":
            save_yaml(path, plan)
            return
        else:
            print("无效选择")
            continue

        save_yaml(path, plan)
        print_validation_summary(validate_unit_plan(plan, plan["total_pages"]))

    validation = validate_unit_plan(plan, plan["total_pages"])
    write_validation_report(book_root / "pipeline-workspace/reports/unit-plan-validation.md", validation)
    if not validation["passed"]:
        raise SystemExit("unit plan validation failed; see unit-plan-validation.md")
    save_yaml(book_root / "config/semantic-unit-plan.yaml", mark_plan_reviewed(plan, validation))
```

Page parsing:

```python
def parse_pages_input(raw: str) -> list[int]:
    pages: list[int] = []
    for part in raw.replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = [int(x.strip()) for x in part.split("-", 1)]
            pages.extend(range(start, end + 1))
        else:
            pages.append(int(part))
    return sorted(set(pages))
```

Merge logic:

```python
def merge_unit_into_previous(units: list[dict[str, Any]], index: int):
    if index == 0:
        raise ValueError("第一个 unit 不能并入前项")
    prev = units[index - 1]
    current = units.pop(index)
    prev["source_scope"]["pages"] = sorted(set(prev["source_scope"]["pages"] + current["source_scope"]["pages"]))
    prev.setdefault("source_scope", {}).setdefault("headings", [])
    prev["source_scope"]["headings"].extend(current.get("source_scope", {}).get("headings", []))
    prev.setdefault("merge_from", []).append(current["unit_id"])
    prev["overlap_reason"] = prev.get("overlap_reason") or "人工合并连续语义单元"
    prev["review_status"] = "edited"
```

Split logic:

```python
def split_unit_interactively(units: list[dict[str, Any]], index: int):
    current = units[index]
    left_pages = parse_pages_input(input("前半 unit 页码 > "))
    right_pages = parse_pages_input(input("后半 unit 页码 > "))
    left = copy.deepcopy(current)
    right = copy.deepcopy(current)
    left["source_scope"]["pages"] = left_pages
    right["source_scope"]["pages"] = right_pages
    left["title"] = input("前半标题 > ").strip() or current["title"]
    right["title"] = input("后半标题 > ").strip() or current["title"] + "（续）"
    left["unit_id"] = current["unit_id"]
    right["unit_id"] = next_available_unit_id(units, current["unit_id"])
    left["review_status"] = right["review_status"] = "edited"
    units[index:index + 1] = [left, right]

def next_available_unit_id(units: list[dict[str, Any]], base_unit_id: str) -> str:
    match = re.match(r"^(?P<prefix>[A-Z]+-\d{3})-\d{2}$", base_unit_id)
    if not match:
        raise ValueError(f"unit_id 不符合 schema: {base_unit_id}")
    prefix = match.group("prefix")
    used = {unit["unit_id"] for unit in units}
    for number in range(1, 100):
        candidate = f"{prefix}-{number:02d}"
        if candidate not in used:
            return candidate
    raise ValueError(f"无法为 {base_unit_id} 分配新的 unit_id")
```

Verify:

```powershell
python scripts/pipeline.py review-unit-plan --book game-model-test --list
python scripts/pipeline.py review-unit-plan --book game-model-test
```

Expected:

- 审批前不存在 `semantic-unit-plan.yaml`。
- 审批并校验通过后才生成正式 plan。

## Phase 5: Unit Context Extraction 与 surya-ocr

**目标:** unit 图按需抽取当前 unit 的上下文，只保存短预览、hash、坐标和 evidence index。

- [ ] **Step 1: 创建 OCR adapter**

Create: `scripts/ocr_surya.py`

编码逻辑：

```python
class OcrUnavailable(RuntimeError):
    pass

def is_surya_available() -> bool:
    try:
        import surya
        return True
    except ImportError:
        return False

def recognize_page_image(image_path: Path) -> dict[str, Any]:
    try:
        from PIL import Image
        from surya.inference import SuryaInferenceManager
        from surya.recognition import RecognitionPredictor
    except ImportError as exc:
        raise OcrUnavailable("surya-ocr is not installed") from exc
    image = Image.open(image_path)
    manager = SuryaInferenceManager()
    predictor = RecognitionPredictor(manager)
    result = predictor([image])
    return normalize_surya_result(result)
```

Surya 2 API requirements:

- Use `surya-ocr>=0.20.0` for the `SuryaInferenceManager` based API.
- Full-page OCR returns `blocks`; equations appear in block HTML as `<math>...</math>` with KaTeX-compatible LaTeX.
- Do not instantiate `RecognitionPredictor` without a `SuryaInferenceManager`, and do not pass string paths directly to the predictor.
- Treat `ImportError`, inference server startup failure, empty `blocks`, and malformed result shape as OCR failure.

Fallback policy:

- 未安装 surya-ocr：返回 `{"status": "unavailable", "formula_risk": "high", "block_publish": True}`。
- surya 已安装但 vllm/llama.cpp 后端不可用：按 `unavailable` 处理，进入 `Review-Queue/`。
- 已安装但失败：重试一次。
- 重试后仍失败：返回 `{"status": "failed", "risk_flags": ["screenshot_ocr_failed"], "block_publish": True}`。
- OCR 空结果：按失败处理。

- [ ] **Step 2: 创建 unit context extractor**

Create: `scripts/unit_context.py`

`prepare_unit_context(book_root, unit, pdf_profile)` 输出：

```python
{
    "unit_id": unit["unit_id"],
    "source_pages": [1, 2, 3],
    "text_blocks": [
        {"page": 1, "bbox": [x0, y0, x1, y1], "text_preview": "...", "sha256": "..."}
    ],
    "ocr_blocks": [
        {"page": 2, "text_preview": "...", "latex_preview": "...", "sha256": "..."}
    ],
    "evidence_candidates": [
        {"evidence_id": "E-U-001-01-0001", "page": 1, "bbox": [...], "preview": "...", "sha256": "..."}
    ],
    "boundary_validation": {
        "start_title_match": True,
        "next_title_leak": False,
        "tail_page_has_content": True,
    },
    "block_publish": False,
    "risk_flags": [],
}
```

Extraction routing:

- `text`: PyMuPDF `get_text("dict")` only.
- `screenshot_ocr`: render all source pages with `page.get_pixmap(matrix=fitz.Matrix(2, 2))`, run surya adapter.
- `hybrid`: text for all pages, OCR only for pages whose profile has `formula_risk=high`, `blank_variable_risk=True`, `table_risk=high`, or `text_length < 50`.

Do not write:

- full `source-slice.md`
- full OCR transcript as a stable artifact

Write:

- `pipeline-workspace/staging/<unit-id>/context-preview.json`
- `pipeline-workspace/staging/<unit-id>/evidence-index.jsonl`

Verify:

```powershell
pytest tests/test_unit_context.py -q
```

Expected:

- 高公式页选择 `hybrid` 或 `screenshot_ocr`。
- surya 未安装时 unit 进入 Review-Queue 路径。
- OCR 失败只重试一次。
- hybrid 冲突优先 OCR 并标记 `hybrid_conflict`。

## Phase 6: Business SQLite 与 Events JSONL

**目标:** 业务数据和 LangGraph checkpoint 分库保存。

- [ ] **Step 1: 创建 `scripts/business_db.py`**

Create: `scripts/business_db.py`

Schema:

```sql
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  book_id TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS unit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  unit_id TEXT NOT NULL,
  node TEXT NOT NULL,
  status TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  unit_id TEXT NOT NULL,
  node TEXT NOT NULL,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  input_tokens INTEGER,
  output_tokens INTEGER,
  cost REAL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  unit_id TEXT NOT NULL,
  memory_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence_ledger (
  evidence_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  unit_id TEXT NOT NULL,
  claim TEXT NOT NULL,
  page INTEGER NOT NULL,
  source_heading TEXT,
  evidence_type TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
```

Paths:

- Business DB: `pipeline-workspace/state/study-kb.sqlite`
- LangGraph checkpoint DB: `pipeline-workspace/checkpoints/langgraph.sqlite`
- JSONL events: `pipeline-workspace/runs/<run-id>/events.jsonl`

Verify:

```powershell
pytest tests/test_business_db.py -q
```

Expected:

- `study-kb.sqlite` has business tables only.
- `langgraph.sqlite` is created by LangGraph checkpointer only.
- `events.jsonl` contains one JSON object per line with `run_id`, `unit_id`, `node`, `status`, `created_at`.

## Phase 7: Rolling Memory

**目标:** accepted unit 后增量更新记忆，超过上限只压缩 `running_book_summary`。

- [ ] **Step 1: 创建 `scripts/memory_store.py`**

Create: `scripts/memory_store.py`

Memory shape:

```python
DEFAULT_MEMORY = {
    "running_book_summary": "",
    "concept_index": {},
    "symbol_index": {},
    "evidence_ledger": [],
    "recent_accepted": [],
}
```

Update logic:

- Append unit summary to `running_book_summary`。
- Merge concepts by term: keep first definition, append unit_id to `units`。
- Merge symbols by symbol: keep first meaning, append unit_id to `units`。
- Append evidence items to ledger and business SQLite。
- Keep only last 2 `recent_accepted` entries。
- If `len(running_book_summary) > memory_compact_char_limit`, call `compact_running_summary()` below to compact only the summary text.

Compaction details:

- Default trigger: `memory_compact_char_limit=20000`。
- Default target: `memory_compact_target_chars=min(12000, int(memory_compact_char_limit * 0.6))`。
- Model: `provider_config.planner_model`，默认 DeepSeek V4 Flash。不要用 V4 Pro，除非后续实测 Flash 不能稳定压缩。
- Structured indexes are input context only; never ask the model to rewrite `concept_index`, `symbol_index`, or `evidence_ledger`。

Compaction call:

```python
def compact_running_summary(provider, provider_config, current_summary: str, recent_accepted: list[dict[str, Any]], target_chars: int) -> str:
    response = provider.chat_json(
        system=(
            "你是学习知识库的rolling memory压缩器。"
            "只输出 JSON 对象，字段 running_book_summary。"
            "不得改写概念索引、符号索引或证据账本。"
        ),
        user=yaml.dump({
            "task": "compact_running_book_summary",
            "target_chars": target_chars,
            "current_summary": current_summary,
            "recent_accepted": recent_accepted,
            "constraints": [
                "保留章节顺序、核心概念、关键依赖和未解决风险",
                "删除重复表述和局部措辞",
                "不要引入新事实",
                "输出长度必须小于 target_chars",
            ],
            "output_schema": {"running_book_summary": "string"},
        }, allow_unicode=True, sort_keys=False),
        model=provider_config.planner_model,
        temperature=0.1,
    )
    compacted = response.get("running_book_summary", "").strip()
    if not compacted or len(compacted) > target_chars:
        raise ValueError("memory compaction failed target length")
    return compacted
```

Verify:

```powershell
pytest tests/test_business_db.py tests/test_unit_graph.py -q
```

Expected:

- 概念/符号/证据结构化索引不被压缩。
- `recent_accepted` 固定最多 2 条。

## Phase 8: Unit-Level LangGraph

**目标:** 每个 approved include unit 单独 invoke，一次只处理一个 unit。

- [ ] **Step 1: 替换 state contract**

Modify: `scripts/langgraph_worker.py`

Unit state:

```python
class UnitGraphState(TypedDict, total=False):
    run_id: str
    book_id: str
    unit_id: str
    unit: dict[str, Any]
    context: dict[str, Any]
    memory: dict[str, Any]
    draft: str
    validation: dict[str, Any]
    review_decision: dict[str, Any]
    review_report: str
    revise_count: int
    status: str
    risk_flags: list[str]
    errors: list[str]
```

Nodes:

- `prepare_context`: call `unit_context.prepare_unit_context()`。
- `generate_note`: call author model; record `model_calls`。
- `verify_evidence`: call `evidence_verifier.verify_note()`。
- `review_note`: call reviewer model; parse with `review_gate`。
- `revise_note`: call author model with required fixes; max 3 attempts。
- `update_memory`: update rolling memory and business DB。
- `publish_note`: write Obsidian files only if all gates pass。

Blocking logic:

```python
if state["context"].get("block_publish"):
    return route_to_review_queue(state, reason="context_blocked")
if "formula_loss_risk" in state["risk_flags"]:
    return route_to_review_queue(state, reason="formula_loss_risk")
if "screenshot_ocr_failed" in state["risk_flags"]:
    return route_to_review_queue(state, reason="screenshot_ocr_failed")
if "evidence_missing" in state["risk_flags"]:
    return route_to_review_queue(state, reason="evidence_missing")
if state["revise_count"] >= 3:
    return route_to_review_queue(state, reason="max_revise_attempts")
```

StateGraph compilation:

```python
from langgraph.graph import END, StateGraph
from langgraph.checkpoint.sqlite import SqliteSaver

def build_unit_graph():
    builder = StateGraph(UnitGraphState)
    builder.add_node("prepare_context", prepare_context)
    builder.add_node("generate_note", generate_note)
    builder.add_node("verify_evidence", verify_evidence)
    builder.add_node("review_note", review_note)
    builder.add_node("revise_note", revise_note)
    builder.add_node("update_memory", update_memory)
    builder.add_node("publish_note", publish_note)
    builder.add_node("route_to_review_queue", route_to_review_queue)

    builder.set_entry_point("prepare_context")
    builder.add_conditional_edges(
        "prepare_context",
        route_after_prepare_context,
        {
            "generate": "generate_note",
            "review_queue": "route_to_review_queue",
        },
    )
    builder.add_edge("generate_note", "verify_evidence")
    builder.add_conditional_edges(
        "verify_evidence",
        route_after_verify_evidence,
        {
            "review": "review_note",
            "review_queue": "route_to_review_queue",
        },
    )
    builder.add_conditional_edges(
        "review_note",
        route_after_review,
        {
            "accept": "update_memory",
            "revise": "revise_note",
            "review_queue": "route_to_review_queue",
        },
    )
    builder.add_edge("revise_note", "verify_evidence")
    builder.add_edge("update_memory", "publish_note")
    builder.add_edge("publish_note", END)
    builder.add_edge("route_to_review_queue", END)
    return builder
```

Route functions:

```python
def route_after_prepare_context(state: UnitGraphState) -> str:
    if state.get("context", {}).get("block_publish"):
        return "review_queue"
    return "generate"

def route_after_verify_evidence(state: UnitGraphState) -> str:
    blocking = {"formula_loss_risk", "screenshot_ocr_failed", "evidence_missing"}
    if blocking.intersection(set(state.get("risk_flags", []))):
        return "review_queue"
    return "review"

def route_after_review(state: UnitGraphState) -> str:
    decision = state.get("review_decision", {}).get("decision", "reject")
    confidence = state.get("review_decision", {}).get("confidence", "low")
    if decision == "accept" and confidence != "low":
        return "accept"
    if decision == "revise" and confidence != "low" and state.get("revise_count", 0) < 3:
        return "revise"
    return "review_queue"
```

SqliteSaver and single-unit invoke:

```python
def invoke_unit_graph(book_root: Path, run_id: str, book_id: str, unit: dict[str, Any], deps: RuntimeDeps) -> dict[str, Any]:
    checkpoint_path = book_root / "pipeline-workspace/checkpoints/langgraph.sqlite"
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    builder = build_unit_graph()
    thread_id = f"{run_id}:{unit['unit_id']}"

    with SqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
        graph = builder.compile(checkpointer=checkpointer)
        initial_state: UnitGraphState = {
            "run_id": run_id,
            "book_id": book_id,
            "unit_id": unit["unit_id"],
            "unit": unit,
            "revise_count": 0,
            "risk_flags": list(unit.get("risk_flags", [])),
            "errors": [],
        }
        return graph.invoke(
            initial_state,
            config={"configurable": {"thread_id": thread_id}},
        )
```

Runtime rules:

- `thread_id` 必须稳定使用 `<run_id>:<unit_id>`，避免不同 unit 混用 checkpoint。
- 业务 SQLite 不得写入 `langgraph.sqlite`；node 内通过 `business_db.py` 写 `study-kb.sqlite`。
- `SqliteSaver` 来自 `langgraph-checkpoint-sqlite` 包，必须在 `requirements.txt` 中声明。

Verify:

```powershell
pytest tests/test_unit_graph.py -q
```

Expected:

- accept writes staging + review + published note.
- missing evidence rejects.
- missing evidence table rejects.
- missing formula risk table rejects.
- OCR failed unit goes to `Review-Queue/` and does not publish to `Section-Lessons/`.
- revise > 3 goes to `Review-Queue/`。

## Phase 9: Evidence, Formula, Review Gates

**目标:** 证据和公式风险是发布门禁，不是报告装饰。

- [ ] **Step 1: 创建 evidence verifier**

Create: `scripts/evidence_verifier.py`

Core checks:

```python
def verify_note(draft: str, context: dict[str, Any]) -> dict[str, Any]:
    claims = extract_core_claims(draft)
    evidence_refs = extract_evidence_refs(draft)
    missing = [claim for claim in claims if not has_evidence(claim, evidence_refs, context)]
    formula_risks = extract_formula_risks(draft, context)
    risk_flags = []
    if missing:
        risk_flags.append("evidence_missing")
    if formula_risks:
        risk_flags.append("formula_loss_risk")
    return {
        "passed": not risk_flags,
        "risk_flags": risk_flags,
        "missing_claims": missing,
        "formula_risks": formula_risks,
    }
```

- [ ] **Step 2: 创建 review gate**

Create: `scripts/review_gate.py`

Rules:

- `decision` 必须是 `accept`, `revise`, `reject`。
- `confidence` 必须是 `high`, `medium`, `low`。
- `report` 必须包含 `证据对照表`。
- `report` 必须包含 `公式风险清单`。
- 缺任一表格时强制 `decision=reject`, `confidence=low`。
- reviewer 标记“原文空白但讲义补全公式”时追加 `formula_loss_risk`。

Verify:

```powershell
pytest tests/test_unit_graph.py::test_review_without_evidence_table_rejects -q
pytest tests/test_unit_graph.py::test_review_without_formula_table_rejects -q
```

Expected: 两个测试均通过。

## Phase 10: Obsidian 完整生态输出

**目标:** 发布后刷新完整 vault，且不覆盖人工笔记。

- [ ] **Step 1: 创建 managed-by guard**

Modify: `scripts/obsidian_indexes.py`

```python
def can_overwrite(path: Path) -> bool:
    if not path.exists():
        return True
    text = path.read_text(encoding="utf-8", errors="replace")
    return "managed_by: pipeline" in text[:1000]
```

Every writer must call `can_overwrite(path)` before replacing existing Markdown.

- [ ] **Step 2: 生成索引**

Create or modify: `scripts/obsidian_indexes.py`

Generate:

- `Home.md`
- `Section-Lessons/<unit-id>.md`
- `Concept-Cards/<term>.md`
- `Glossary/<term>.md`
- `Symbols/<symbol-safe-name>.md`
- `Formula-Ledger/<unit-id>.md`
- `Claims/<unit-id>.md`
- `Questions/<unit-id>.md`
- `Review-Queue/<unit-id>.md`
- `Learning-Maps/MOC-全书学习地图.md`
- `Source-QA/覆盖率报告.md`
- `Source-QA/高风险清单.md`
- `Dashboards/质量看板.md`

Dataview frontmatter for generated lesson:

```yaml
---
type: section-lesson
unit_id: GTW-001-01
chapter: "1"
difficulty: 3
formula_risk: high
status: published
concepts: ["概念A"]
symbols: ["x"]
depends_on: []
source_pdf: "book.pdf"
source_pages: [1, 2]
risk_flags: []
managed_by: pipeline
---
```

Verify:

```powershell
pytest tests/test_obsidian_indexes.py -q
```

Expected:

- 所有输出文件存在。
- 人工文件没有 `managed_by: pipeline` 时不会被覆盖。
- Dataview 字段完整。

## Phase 11: Legacy 归档

**目标:** 旧流程可查可跑，但不会出现在主流程中。

- [ ] **Step 1: 移动旧 extract/queue 代码**

Move:

- `scripts/extract_source_slice.py` -> `scripts/legacy/extract_source_slice.py`
- `scripts/section_planner.py` -> `scripts/legacy/section_planner.py`
- `scripts/llm_section_planner.py` -> `scripts/legacy/llm_section_planner.py`

Keep wrappers if existing tests still import old names:

```python
# scripts/extract_source_slice.py
from legacy.extract_source_slice import *  # compatibility wrapper
```

- [ ] **Step 2: 标记旧测试**

Modify:

- `scripts/tests/test_extract_source_slice.py`

Add:

```python
import pytest

pytestmark = pytest.mark.legacy
```

Add to `pytest.ini`:

```ini
[pytest]
markers =
    legacy: tests for archived source-slice flow
```

Verify:

```powershell
pytest -q
pytest -m "not legacy" -q
```

Expected: 两条命令均通过。

## Phase 12: 成本控制

**目标:** run-book 开始前估算 per-unit 和 per-book 成本，超限暂停。

- [ ] **Step 1: 创建 cost estimator**

Create: `scripts/cost_guard.py`

Logic:

```python
def estimate_unit_tokens(context: dict[str, Any], memory: dict[str, Any], output_limit: int) -> dict[str, int]:
    input_chars = len(json.dumps(context, ensure_ascii=False)) + len(json.dumps(memory, ensure_ascii=False))
    estimated_input_tokens = max(1, input_chars // 2)
    return {"input_tokens": estimated_input_tokens, "output_tokens": output_limit}

def enforce_budget(unit_estimate, run_estimate, config):
    if unit_estimate["input_tokens"] > config.max_unit_input_tokens:
        return {"allowed": False, "scope": "unit", "reason": "max_unit_input_tokens"}
    if run_estimate["tokens"] > config.max_book_tokens:
        return {"allowed": False, "scope": "book", "reason": "max_book_tokens"}
    if run_estimate["cost"] > config.max_book_cost:
        return {"allowed": False, "scope": "book", "reason": "max_book_cost"}
    return {"allowed": True}
```

Verify:

```powershell
pytest tests/test_unit_graph.py::test_unit_budget_over_limit_goes_to_review_queue -q
pytest tests/test_unit_graph.py::test_book_budget_over_limit_pauses_run -q
```

Expected:

- 超 unit 上限：该 unit 进入 Review-Queue。
- 超 book 上限：整本 run 状态变为 paused，等待人工确认。

## Full Verification Commands

每完成一个 phase 后运行对应目标测试。全部实现完成后运行：

```powershell
python scripts/pipeline.py --help
pytest -q
python scripts/pipeline.py profile-pdf --book game-model-test --force
python scripts/pipeline.py plan-units --book game-model-test --force
python scripts/pipeline.py validate-unit-plan --book game-model-test
python scripts/pipeline.py review-unit-plan --book game-model-test --list
python scripts/pipeline.py run-book --book game-model-test --executor langgraph-worker --dry-run
```

如果本地没有 `books/game-model-test`，先用一份小 PDF 初始化：

```powershell
python scripts/pipeline.py init-book --book game-model-test --pdf "D:\path\to\sample.pdf" --title "Game Model Test"
```

## Acceptance Report Template

每次阶段性交付都写入：

`books/<book-id>/pipeline-workspace/reports/acceptance-report-<YYYYMMDD-HHMMSS>.md`

模板：

```markdown
# 验收报告

- book_id:
- run_id:
- generated_at:
- git_commit:
- executor: langgraph-worker

## Scope

- 本次实现范围:
- 未实现范围:
- 兼容保留:

## Command Evidence

| Command | Exit Code | Evidence |
|---------|-----------|----------|
| `python scripts/pipeline.py --help` |  |  |
| `pytest -q` |  |  |
| `python scripts/pipeline.py profile-pdf --book <book> --force` |  |  |
| `python scripts/pipeline.py validate-unit-plan --book <book>` |  |  |
| `python scripts/pipeline.py run-book --book <book> --executor langgraph-worker --dry-run` |  |  |

## Plan Validation

| Check | Result | Notes |
|-------|--------|-------|
| 所有页码被覆盖 |  |  |
| include false 页码计入覆盖 |  |  |
| 无越界页码 |  |  |
| 重叠均有 overlap_reason |  |  |
| 高公式页 extraction_method 合理 |  |  |

## Unit Graph Validation

| Check | Result | Notes |
|-------|--------|-------|
| accept unit 发布到 Section-Lessons |  |  |
| OCR 未安装进入 Review-Queue |  |  |
| OCR 失败重试一次 |  |  |
| evidence_missing 阻断发布 |  |  |
| review 缺证据表强制 reject |  |  |
| review 缺公式风险表强制 reject |  |  |
| revise 超过 3 次进入 Review-Queue |  |  |

## SQLite Validation

| Check | Result | Notes |
|-------|--------|-------|
| LangGraph checkpoint DB 独立 |  |  |
| 业务 SQLite 表完整 |  |  |
| events.jsonl 可读 |  |  |
| model_calls 记录 token/cost |  |  |
| memory_snapshots 可恢复 |  |  |
| evidence_ledger 有 evidence_id |  |  |

## Obsidian Validation

| Check | Result | Notes |
|-------|--------|-------|
| Dataview frontmatter 完整 |  |  |
| Concept-Cards 已生成 |  |  |
| Glossary 已生成 |  |  |
| Symbols 已生成 |  |  |
| Formula-Ledger 已生成 |  |  |
| Claims 已生成 |  |  |
| Questions 已生成 |  |  |
| Review-Queue 已生成 |  |  |
| Dashboards 已生成 |  |  |
| 无 managed_by 缺失文件被覆盖 |  |  |

## Risks

- 剩余风险:
- 需要人工确认的 unit:
- 成本或 token 风险:
- OCR 质量风险:

## Decision

- decision: accept | revise | reject
- required_fixes:
- approver:
```

## Release Gate

在满足以下条件前，不合并到主流程：

- `pytest -q` 通过。
- `pytest -m "not legacy" -q` 通过。
- `profile-pdf -> plan-units -> validate-unit-plan -> review-unit-plan --list -> run-book --dry-run` 走通。
- 至少一个 low-formula unit 完成端到端发布。
- 至少一个 high-formula unit 在 surya-ocr 未安装或失败时进入 `Review-Queue/`。
- `pipeline-workspace/state/study-kb.sqlite` 与 `pipeline-workspace/checkpoints/langgraph.sqlite` 分库存在。
- `events.jsonl` 能区分模型错、工具错、上下文错和验证错。
- 生成的 Obsidian vault 不覆盖没有 `managed_by: pipeline` 的人工笔记。
