# ingest / 阶段 A — 确定性预处理（零 LLM，可重跑，幂等跳过）

**输入**：`<src>` / `<domain>` / `<path>` / `<fmt>`。**输出**：`staging/<src>/{source.md, windows.jsonl, workorder.yaml}` + 难页 PNG。
**持久化**：以上 staging 产物 + SQLite 阶段状态。**停止点**：任一步报错则停下报告，不要跳过。

## 步骤

1. 若 `wiki/` 不存在：`python scripts/pipeline.py init-vault`（幂等，绝不覆盖已有文件）。
2. 依次跑（每步幂等，输入未变会 `[skip]`）：
   - `python scripts/pipeline.py add-source --source <src> --domain <domain> --path <path> --fmt <fmt>`
   - `python scripts/pipeline.py profile --source <src>`
   - `python scripts/pipeline.py source-convert --source <src>`
   - `python scripts/pipeline.py windows --source <src>`
   - `python scripts/pipeline.py workorder --source <src>`
3. 读 `pipeline-workspace/staging/<src>/workorder.yaml`——它定义你的全部写入边界（`write_scope`）、registry hash、页面快照。**没有 work order 不进入阶段 B。**

## 验收（进入阶段 B 前必须满足）

- `workorder.yaml` 已生成，`write_scope` 覆盖 `domains/<domain>/**` 等。
- **needs_vision 合理**：`source-convert` 输出的难页数不应为 0（含公式 / 图表的书应有若干页被标记）；为 0 且源含公式或插图则可疑，复核。
- **难页（route B）**：`source-convert` 对难页（公式 / 矢量图 / 表 / 图表标题，高召回）打 `[info]`——纯文本会拍平上/下标、且看不见矢量图与无框线表，每页渲整页 PNG 供 ingest 读图保真。确认难页 PNG 已生成、`pages.jsonl` 有 `needs_vision_reason` 即可（不依赖任何 OCR/ML 后端）。
- **windows 覆盖**：`windows.jsonl` 的 char 范围应覆盖 `source.md` 全文（无大段漏读）。
