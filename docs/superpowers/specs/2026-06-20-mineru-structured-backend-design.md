# Spec② · MinerU structured backend + 两级 auto 路由 + 风险 lint

> 状态：设计稿（autonomous 执行，不等审核）
> 日期：2026-06-20
> 基础：Spec①（已合并 main）的 artifact 契约 `source.md + blocks.jsonl + chapters.json + parse_report.json + assets/`。
> 关联实现计划：`docs/superpowers/plans/2026-06-20-mineru-structured-backend.md`

## 1. 目标与边界

把 Spec① 的 artifact 契约接上 **MinerU** 作为 **optional structured backend**：MinerU 只负责把复杂源（扫描 PDF / 低文本密度 PDF / DOCX / PPTX / 复杂表格公式图片）归一成项目自己的同一套 artifact。**不引入 RAGFlow，不改 Obsidian-first 知识编译器定位。**

**三层不混同**：`blocks.jsonl`=源文档事实层；`windows.jsonl`=LLM 阅读单位；Obsidian page=学习语义结构。MinerU block 绝不直接变 Obsidian 页；window 绝不等同最终知识页；LLM 不做 OCR / 语义切分审批。

**硬件**：RTX 3050 Ti 4GB → MinerU 默认仅 `pipeline` 后端，CLI 显式 `-b pipeline`，禁 vlm/hybrid/本地 VLM。

## 2. 依赖与安装边界

- MinerU 是 optional：**不进默认 `requirements.txt`**；新增 `requirements-mineru.txt`。
- 未装 MinerU：`--backend pymupdf` 与 `auto` 的轻量路径照常；`--backend mineru` fail-closed + 清晰安装提示；auto 下需要 MinerU 的源（docx/pptx/扫描/低文本）若不可用 → fail-closed 或进 Review-Queue，绝不伪装成功。
- 测试**不依赖真实 MinerU**：fake content_list/输出 + monkeypatch subprocess。

## 3. MinerU 可用性检测

`mineru_backend.mineru_available()`：`shutil.which("mineru") is not None`（subprocess CLI 探测，不 import MinerU 内部 API）。测试 monkeypatch 它。

## 4. CLI 路由（`source-convert --backend auto|pymupdf|mineru --mineru-policy conservative|aggressive`）

默认 `--backend auto --mineru-policy conservative`。决策纯确定性，落在 dispatcher `select_backend(fmt, profile_pages, *, backend, policy, available)`：

| backend | policy | 输入 | 选择 | MinerU 不可用 |
|---|---|---|---|---|
| pymupdf | — | 任意 | pymupdf（不检测/调用 MinerU） | n/a |
| mineru | — | 任意 | mineru | **fail-closed** |
| auto | conservative | md | markdown | n/a |
| auto | conservative | 普通 born-digital pdf | pymupdf（密集仍 pymupdf，仅写 advice） | n/a |
| auto | conservative | 扫描/纯图像/低文本密度 pdf | mineru | fail-closed/Review-Queue |
| auto | conservative | docx/pptx | mineru | fail-closed |
| auto | aggressive | 公式/表格/图片密集 born-digital pdf | mineru | fail-closed |

- `routing_advice.consumed_by_auto_router`：仅当 `backend=auto` 且实际据 advice/信号选择时置 True；显式 `--backend pymupdf/mineru` 不算 auto 消费（False）。
- 「扫描/低文本密度」判定复用 Spec① profile 信号（`is_scanned_source` / 页均 text_len 低 / `scanned-or-image` 占比）。

## 5. MinerU backend（`scripts/source_backends/mineru_backend.py`）

`convert(src_path, *, out_dir, input_hash, timeout=...)`：
1. 未装 → `BackendUnavailable`。
2. subprocess 调 `mineru -p <src> -o <raw_dir> -b pipeline`（显式 pipeline；timeout；捕获 returncode/stderr）。
3. 失败（非零/超时/输出缺失）→ 写最小 `parse_report`（`selected_backend="mineru"`, `mineru_status="failed"`, `mineru_failed=true`, `failure_reason=...`）并抛 `MineruRunFailed`（dispatcher/pipeline 据此 fail-closed 或 Review-Queue，不静默回退）。
4. 成功 → 读 `*_content_list.json`，**按阅读顺序**归一为 `SourceBlock`：

| content_list type | SourceBlock.type | text | risk_flags | asset |
|---|---|---|---|---|
| text + text_level≥1 | heading | 该文本 | [] | — |
| text（无 level）/ list | text | 该文本 | [] | — |
| table | table | table_body(HTML/md) | ["table"] | img_path→assets |
| equation/formula | equation | latex | ["equation"] | — |
| image/figure | image | caption | ["image"] | img_path→assets |
| header/footer/page_number/discarded | （丢弃，不入正文块） | — | — | — |

归一规则：`page = page_idx + 1`（1-based）；`block_id = b{seq:06d}`（全源序）；`source_ref = p{page:04d}#{block_id}`；图片 asset 复制进 staging `assets/`，`asset_path` 为 staging 相对路径。

5. `source.md` **从归一 blocks 渲染**（非盲用 MinerU 原始 md），每块前置 `<!-- block:bId page:N type:T -->` 注释 + 可读正文/表/公式/图引用，使三后端 source view 形态一致；block 的 `char_start/char_end` 指向该渲染 source.md 的区间（与 PyMuPDF/Markdown 一致，供窗口聚合/show-window 切片）。
6. `chapters.json`：有 heading 层级则据 heading blocks 切章；否则最小「整书一章」，不阻塞。
7. `parse_report.json`：`selected_backend="mineru"`, `mineru_status="used"`, `mineru_backend="pipeline"`, `mineru_version`（探测失败 "unknown"），counts（`page_count/block_count/text_block_count/table_count/equation_count/image_count/heading_count/discarded_count`），`ocr_used`/`scan_suspected`（不确定写 false，不编造），`routing_advice`（advisory）。

## 6. dispatcher / cache key

`source_convert.convert(src_path, *, out_dir, fmt, backend="auto", mineru_policy="conservative", profile_pages=None)`：
- 旧调用兼容：未传 backend 等价 auto。
- 返回旧键超集不变；`BackendUnavailable` 仍可从 `source_convert` 访问。
- `converted_input_hash(raw, *, backend, policy)` 纳入：raw sha + PROFILER_VERSION + ARTIFACT_VERSION + **selected backend + policy + MINERU_ADAPTER_VERSION**。防止同源 PyMuPDF 产物被 MinerU 复用（state_store 误判 converted up-to-date）。
- backend 选择发生在转换前；pipeline 把 profile 的 pages.jsonl 传给 dispatcher 供 auto 判定。

## 7. pipeline

- `cmd_source_convert`：加 `--backend`/`--mineru-policy`，传给 dispatcher；input_hash 用新 `converted_input_hash(raw, backend=, policy=)`；记 selected backend + blocks/parse_report artifacts。扫描件 fail-closed **重新协调**：conservative auto 下扫描件**应走 MinerU**而非 profile 永久拦截；仅当 MinerU 不可用才 fail-closed（保留 `--force` 走 pymupdf）。普通 fast path 不被 MinerU 可用性拖慢（只有真要 MinerU 才探测）。
- `cmd_windows`：已有 block 逻辑保留；MinerU 的 table/equation/image block 的 `contains/assets/risk_flags` 自然进窗（`_attach_block_meta` 已通用）。不读 routing_advice。
- `cmd_show_window`：block header 已支持；MinerU 风险块的 type/assets/risk_flags 经 `contains/risk_flags/assets` 展示。原正文不破坏。

## 8. risk lint（渐进，仅 `selected_backend=mineru` 新源）

`wiki_gate` 新增 `lint_risk_traceability`：对归属某 mineru 源、且其 window 含 `table/equation/image/ocr_low_confidence` 风险的 proposed 页，要求页 `source_refs` 能追溯到 `source/window/pages/block_ids`（assets 若存在亦列）。最小规则：缺 source_refs 或缺 block_ids 即失败进 Review-Queue。PyMuPDF/旧源保留旧 needs_vision 规则，不扩大失败面（按 backend 门控）。

## 9. source-preflight / workorder / ingest contract

- `source-preflight` 读 `parse_report.json`，展示 backend / OCR / table·equation·image 数 / discarded 数 / warnings / 是否建议 ingest（短结论，详报写 reports/）。
- `workorder.source` 超集已含 `backend`，保留。
- ingest skill 文档：优先用 `show-window` 读窗，不凭 `source.md` char offset 猜范围；block mode 保留 block_ids/source_refs/assets。

## 10. 文档与双 skill 树

更新 README / CLAUDE.md / AGENTS.md：去掉「无重型 OCR/ML 后端」硬约束，改为「默认轻量 fast path（Markdown/PyMuPDF）+ 可选 MinerU structured backend（扫描/低文本/docx/pptx/复杂表格公式图片；3050 Ti 4GB 默认仅 pipeline）」。同步 `.claude/skills/**` 与 `.agents/skills/**`（ingest + source-preflight），保持双树字节对等（除 ingest 既有 per-agent 真值行）。`tests/test_skill_standard.py` 的对等守卫必须仍绿。

## 11. 测试（fake + mock，不依赖真实 MinerU）

见计划文档 §测试；覆盖：unavailable fail-closed、subprocess 含 `-b pipeline` 无 vlm/hybrid、fake content_list 归一（text/table/equation/image/header/footer）、auto conservative/aggressive 路由、backend/policy cache-key、windows 风险元数据、show-window 风险头、risk lint（mineru 失败 / pymupdf 不误伤）、docs/skill 同步、全量绿。
