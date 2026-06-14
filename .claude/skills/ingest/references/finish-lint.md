# ingest / 阶段 F — 收工 + 收尾发布（零 LLM 门禁）

**输入**：本源全部 proposed 页。**输出**：promote 到 published + 重建派生，或失败回滚 + Review-Queue。
**持久化**：`log.md` 追加 lint 行；失败项落 `review_proposals` + `wiki/Review-Queue/`。**停止点**：lint 失败时停下交人。

## 步骤

1. 全部 window 完成后：写/更新 `sources/<src>.md`（来源摘要页，模板 `templates/source.md`）。
2. `python scripts/pipeline.py ingest-done --source <src>` —— 状态进 `ingested/proposed`，释放 vault 锁。
3. **收尾门禁**：`python scripts/pipeline.py lint --source <src>`。
   - **通过** → proposed 升 `published`、并入 `index.generated.md`，重建 `_registry.yaml`/`aliases.md`。向用户汇报发布了哪些页。
   - **失败** → 就地 merge 已回滚、违规清单写 `wiki/Review-Queue/<src>-lint-*.md`；**停下**把违规与修复建议告诉用户（改页后重跑 `lint`，或用 kb-review 处理）。

## 收尾后 [warn]：综合层缺失 → reopen 增量补

lint 通过后若打印 `[warn] 本源产出 N 个 concept 但未更新综合层`，说明阶段 E 没做（综合层是一等产物）。
**别让它停在这**：`python scripts/pipeline.py reopen --source <src>` 重开来源（据当前 vault 重建 work order +
状态机回 `workorder_ready`），照常 `ingest-start` 起一轮增量，专门写 overview 知识地图 / comparison / topic /
synthesis（+ 把 needs_vision 公式页源图补嵌、给关键 concept 补 worked example、把汇总页冗余 wikilink 删到只剩强关系），
再 `ingest-done → lint`。增量 lint 只 promote 本轮新增/改写页，既有 published 页不动。

## 验收

- 通过：`pipeline status` 显示该 source `lint / published`；`index.generated.md` 收录新页（仅 published）；**无 `[warn]` 综合层缺失**。
- 失败：已回滚到 pre-ingest、违规项进 Review-Queue、source 停在 `lint/failed`（修复后状态机允许回 `ingest_waiting` 重跑）。
