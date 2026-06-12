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
- **needs_vision 合理**：`source-convert` 输出的难页数不应为 0（公式书应有若干公式页被标记）；为 0 且源含公式则可疑，复核。
- **降级告警**：若 `source-convert` 打出 pymupdf 纯文本降级告警（缺 marker），知悉公式可能失真——难页已渲 PNG 供读图兜底；必要时提示用户在 study-kb 装 marker 重跑。
- **windows 覆盖**：`windows.jsonl` 的 char 范围应覆盖 `source.md` 全文（无大段漏读）。
