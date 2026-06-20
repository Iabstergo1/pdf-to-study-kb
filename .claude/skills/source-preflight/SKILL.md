---
name: source-preflight
description: 对新的外部来源先跑确定性预处理链并验收 staging 产物，但不写语义 wiki 页。当用户说“先预处理这个 PDF / 跑 source-preflight / 先生成来源画像 / 先看看能不能 ingest”时使用。只做 add-source、profile、source-convert、windows、workorder 的零 LLM 验收门，不做拆书、摘要、语义 unit 规划或写库。
---

# source-preflight — 来源预处理验收门（零 LLM 语义）

对一个候选来源运行确定性 CLI 预处理链，确认 staging 产物是否足以进入 `ingest`。本 skill 是薄包装：只编排 `scripts/pipeline.py`，不做任何 LLM 语义拆书 / unit 规划 / 写页。

## 1. 触发 / 负样本

- **触发**：「先预处理这个 PDF」「跑 source-preflight」「先生成来源画像」「先看看能不能 ingest」「只跑到 workorder」。
- **负样本**：「加入知识库/收录」且要写页发布（用 `ingest`）；「总结这本书/拆书」但不入库（用 `source-xray`，且只基于已发布内容）；「查询 wiki」用 `kb-query`；「翻译/解释」普通回答。

## 2. 输入

- 用户给：原始文件路径 `<path>`、领域 `<domain>`；格式 `<fmt>` 由扩展名推断（pdf/md/docx/pptx）；`<src>` 由文件名派生。
- 执行前确认一次 `<src>` 与 `<domain>`。
- 读：`CLAUDE.md` / `AGENTS.md` 的预处理零 LLM 约束、`docs/skill-runtime/schema.md`（理解 workorder 写入边界）。

## 3. 输出

- `pipeline-workspace/staging/<src>/{source.md, blocks.jsonl, chapters.json, parse_report.json, windows.jsonl, workorder.yaml}` 与难页 PNG / 图表 asset。
- 可选的确定性预处理报告：`pipeline-workspace/reports/source-preflight/<src>.md`，读 `parse_report.json` 展示 backend、是否 OCR、table/equation/image 数、discarded（页眉页脚）数、warnings、是否建议 ingest；以及 CLI 状态、页数、needs_vision 页、降级告警、windows 覆盖、workorder `write_scope`，不写语义摘要。
- 不写 `wiki/` 语义内容页，不创建 `status: proposed` 页面，不更新概念页。

## 4. 依赖

- CLI：`init-vault`、`add-source`、`profile`、`source-convert`、`windows`、`workorder`、`status`。
- 后续真正入库交给 `ingest`，本 skill 不内联 ingest 阶段 B/C/D/E/F。
- 协议：`docs/skill-runtime/skill-standard.md` 与 `docs/skill-runtime/schema.md`。

## 5. 持久化 artifact

- `pipeline-workspace/staging/<src>/source.md`
- `pipeline-workspace/staging/<src>/windows.jsonl`
- `pipeline-workspace/staging/<src>/workorder.yaml`
- `pipeline-workspace/staging/<src>/assets/pXXXX.png`（needs_vision 页）
- `pipeline-workspace/reports/source-preflight/<src>.md`（若写报告，只含确定性事实）

## 6. CLI 命令

```text
python scripts/pipeline.py init-vault
python scripts/pipeline.py add-source --source <src> --domain <domain> --path <path> --fmt <fmt>
python scripts/pipeline.py profile --source <src>
python scripts/pipeline.py source-convert --source <src>
python scripts/pipeline.py windows --source <src>
python scripts/pipeline.py workorder --source <src>
python scripts/pipeline.py status
```

每步幂等；任一步报错就停止，不跳过。

## 7. 阶段拆解

| 子单元 | 输入 | 输出 | 验收 | 持久化 | 停止点 |
|---|---|---|---|---|---|
| P1 确认来源 | path/domain/src/fmt | 确认后的四要素 | src/domain 明确 | — | 用户未确认 |
| P2 跑确定性链 | 四要素 | source/profile/windows/workorder | 每步成功或幂等 skip | staging + SQLite | 任一步报错 |
| P3 验收产物 | staging 产物 | 可进入 ingest 的判断 | workorder.yaml 存在，windows 覆盖 source.md | 报告草稿 | workorder 缺失 |
| P4 公式页检查 | source-convert 输出 | needs_vision/PNG 记录 | 公式风险页须有整页 PNG（route B）| 报告 | 公式页未渲图 |
| P5 交接 | workorder + 报告 | 下一步建议 | 明确“可转 ingest”或列阻断项 | report | 用户要求写页则转 ingest |

## 8. 失败停止点

路径不存在；fmt 不支持；CLI 任一步失败；`source-convert` 缺后端；`windows.jsonl` 未覆盖全文；`workorder.yaml` 未生成；公式风险页没有 PNG；用户要求 LLM 拆书或语义 unit 规划。

## 9. 验收清单

- 没有写语义 wiki 页，没有 `status: proposed` 内容页。
- `source.md`、`windows.jsonl`、`workorder.yaml` 存在。
- `workorder.yaml` 含 `write_scope` 与 registry hash。
- needs_vision 页有对应 PNG 或已记录阻断。
- 报告只含确定性事实，不含语义摘要/章节解读。
