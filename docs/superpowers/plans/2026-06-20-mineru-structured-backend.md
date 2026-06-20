# MinerU structured backend (Spec②) Implementation Plan

> autonomous 执行：小逻辑提交，每次提交前全量 `pytest tests -q` 全绿；不 push。
> 设计：`docs/superpowers/specs/2026-06-20-mineru-structured-backend-design.md`
> 测试不依赖真实 MinerU：fake content_list + monkeypatch subprocess/`mineru_available`。

环境：`& "D:\miniconda3\envs\study-kb\python.exe" -m pytest tests -q`（pwsh，`$env:PYTHONUTF8=1`）。

## 提交序列（每个 = 失败测试→实现→全量绿→commit）

### C0（已完成）cleanup/hardening — `c44138d`

### C1 MinerU backend skeleton + unavailable fail-closed
- 新建 `scripts/source_backends/mineru_backend.py`：`mineru_available()`（`shutil.which`）、`MineruRunFailed`、`MINERU_ADAPTER_VERSION="1"`、`convert(...)` 入口在 `not mineru_available()` 时抛 `BackendUnavailable`（清晰安装提示，引 `requirements-mineru.txt`）。
- `get_backend("mineru")`（按 backend 名而非 fmt——见 C4 dispatcher 调整；C1 先加注册函数 `get_backend_by_name`）。
- 新建 `requirements-mineru.txt`（`mineru[core]` 或注释说明 + 版本）。
- tests `tests/test_mineru_backend.py`：monkeypatch `mineru_available→False` 时 `convert` 抛 `BackendUnavailable` 且消息含 "requirements-mineru"。

### C2 fake output normalization
- `mineru_backend` 内 `normalize_content_list(items, assets_src_dir, assets_out_dir)`→`list[SourceBlock]`，`render_source_md(blocks)`，`build_chapters(blocks)`，`build_mineru_report(...)`。
- 归一映射（设计 §5 表）：page=page_idx+1；block_id=`b{seq:06d}`；source_ref；header/footer/page_number/discarded 丢弃并计数；table→type=table+risk["table"]、equation→type=equation+risk["equation"]、image→type=image+risk["image"]+asset 复制。
- tests：fake content_list（text/heading/table/equation/image/header/footer）→ blocks 类型正确、page 1-based、source_ref、header/footer 不入正文、report counts（text/table/equation/image/heading/discarded）正确；`render_source_md` 含各 block 注释且 `source_md[char_start:char_end]` 与 block 对应（含 marker 注释）。

### C3 MinerU subprocess invocation
- `mineru_backend._run_mineru(src, raw_dir, *, timeout)`：subprocess `mineru -p <src> -o <raw_dir> -b pipeline`；非零/超时/输出缺失→`MineruRunFailed`。
- `convert` 串起来：mock subprocess（写 fake raw 输出）→ 归一 → 落 artifact（dispatcher 落盘或 backend 返回 BackendResult，统一 BackendResult）。
- tests：monkeypatch `subprocess.run` 断言命令含 `-b pipeline` 且**不含** `vlm`/`hybrid`；fake 写出 content_list → `convert` 产 BackendResult（report selected_backend=mineru/mineru_status=used/mineru_backend=pipeline）；subprocess 失败→`MineruRunFailed` + report mineru_failed。

### C4 source_convert dispatcher backend/policy + cache key
- `convert(src_path,*,out_dir,fmt,backend="auto",mineru_policy="conservative",profile_pages=None)`。
- `select_backend(fmt, profile_pages, *, backend, policy, available)`→后端名 + `consumed_by_auto_router` bool（设计 §4 表）。
- `converted_input_hash(raw,*,backend,policy)` 纳入 selected backend + policy + MINERU_ADAPTER_VERSION。
- `get_backend`/dispatch 按选定后端名；旧 fmt 兼容（未传 backend=auto）；返回旧键超集；`BackendUnavailable` 可访问。
- tests：select_backend 全表（md/普通pdf/扫描pdf/低文本pdf/docx/pptx × conservative/aggressive/显式）；`--backend mineru` unavailable→fail-closed；cache key 随 backend/policy 变；dense born-digital conservative→pymupdf+advice、aggressive→mineru。

### C5 pipeline CLI auto routing
- `cmd_source_convert`：argparse 加 `--backend`/`--mineru-policy`；传 dispatcher（含 profile pages）；input_hash 用新签名；记 backend。扫描件 fail-closed 重新协调：conservative auto 下扫描件走 MinerU；MinerU 不可用才 fail-closed（保留 `--force`）。
- tests（test_p2_cli）：`--backend pymupdf` 普通 md/pdf 正常；`--backend mineru` unavailable→非零退出 + 提示；扫描件 + mineru 不可用→fail-closed；普通 md 默认 auto 仍 markdown（不触 MinerU）。

### C6 windows / show-window MinerU metadata coverage
- `_attach_block_meta` 已通用——加测试：含 table/equation/image 的 MinerU blocks → window `contains` 含这些 type、`risk_flags` 含 table/equation/image、`assets` 含 image asset。
- show-window：MinerU 风险窗的 window-meta 头展示 risk_flags/assets/type（已支持 risk_flags/assets；确认 contains 也可加入头）。
- tests：构造 MinerU blocks → build_windows_from_blocks → 断言；show-window 头含 risk_flags=...table.../assets。

### C7 risk lint（mineru-only 渐进）
- `wiki_gate.lint_risk_traceability(vault, proposed, *, source_backend, risk_windows)`：mineru 源、风险窗对应 proposed 页须 source_refs 含 source/window/pages/block_ids。
- pipeline `cmd_lint` 接线：仅当本源 backend=mineru 时启用新规则（读 workorder/parse_report 的 backend）。
- tests（test_wiki_gate / test_p?_cli）：mineru 风险页缺 source_refs→fail；pymupdf 旧源不被新规则误伤。

### C8 source-preflight / workorder / ingest contract
- `source-preflight` skill：读 parse_report 展示 backend/OCR/counts/discarded/warnings/建议（**双树**）。
- workorder 已含 backend（保留，加断言）。
- ingest skill：show-window 优先、block_ids/source_refs/assets 保留（**双树**）。

### C9 docs + skill trees
- README / CLAUDE.md / AGENTS.md：移除「无重型 OCR/ML 后端」硬约束 → 「默认轻量 fast path + 可选 MinerU structured backend（3050 Ti 4GB 仅 pipeline）」。
- `.claude/skills/**` 与 `.agents/skills/**` 同步（ingest + source-preflight），保持对等守卫绿。
- tests：`test_skill_standard.py` 对等守卫绿；若 README/CLAUDE/AGENTS 有 legacy-removed 守卫断言需同步更新（`test_legacy_removed` 只查脚本/requirements，文档措辞不在其内，确认不冲突）。

### C10 final e2e / regression
- e2e：fake MinerU 全链（add-source docx → profile? → source-convert --backend mineru(mock) → windows → show-window）经 mock 跑通，产 artifact + 风险窗。
- 全量 `pytest tests -q` 绿；skill 双树 diff 复核。

## 关键不变量
- routing_advice advisory-only：`consumed_by_auto_router` 仅 auto 实际消费时 True（C0 已强制 build_parse_report 默认 False + opt-in）。
- 未装 MinerU：fast path 不受影响；`--backend mineru`/必需 MinerU 源 fail-closed，绝不静默回退。
- MinerU 命令恒 `-b pipeline`，无 vlm/hybrid 默认。
- 三后端 source view 形态一致（均从 blocks 渲染 / PyMuPDF 保留页标记 + char 兼容）。
