# P2 canonical 概念模型执行报告

- 日期：2026-06-10
- 分支：`feat/p2-canonical-concepts`（基于 `feat/p1-source-convert`）
- 计划：`docs/superpowers/plans/2026-06-10-p2-canonical-concepts.md`（随 Task 2 提交入库）
- 验证：`python -m pytest -q --ignore=tmp` → **150 passed**（129 旧 + 21 新，零回归）

## 提交清单（逐任务 TDD）

| 提交 | 任务 | 内容 |
|---|---|---|
| 76687af | Task 2 | `mdpage.py` frontmatter 读写（确定性 round-trip）+ P2 plan 文档 |
| 24dbf6b | Task 3 | `slugify` + 命名空间 `canonical_id`（ASCII 别名优先；信号博弈+Signaling Game → `concept.game-theory.signaling-game`） |
| 4a29203 | Task 4 | registry 扫描（domain+shared）/重建 `_registry.yaml`（排序+sha256）/`aliases.md` 派生；duplicate=error、别名碰撞=warn |
| 55b12ee | Task 5 | `resolve_or_create_concept` 协议：命中 merge（source_refs/aliases 去重累积）、未命中建骨架页、绝不重复 |
| 2810640 | Task 6 | CLI `rebuild-registry`（`_vault_dir()` 同锚点隔离；损坏时拒写派生 exit 非 0） |

## 执行中发现并修正的计划缺陷

Task 4 的 duplicate canonical_id 测试原设计有缺陷：`_mk_concept` 以 cid slug 当文件名，同 cid 两次写的是同一文件（覆盖而非重复），扫描只见一页。已修正为第二页用不同文件名、相同 canonical_id（真实重复场景）；Task 6 的 CLI 测试 helper 预先加了 `filename` 参数避免同一问题。

## 验收清单（逐项实测）

- [x] 真值在 frontmatter：`_registry.yaml`/`aliases.md` 全由 `rebuild-registry` 重建，重跑字节级一致（smoke 两遍 sha256=97101bf40739 相同）
- [x] `信号博弈`/`Signaling Game` 归一同一页，`source_refs` 跨 section 去重累积（5.2+12.2+重复 5.2 → `["5.2","12.2"]`），目录仅 1 个页面文件
- [x] 命名空间隔离：`concept.econ.utility` 与 `concept.cs.utility` 各自独立不合并
- [x] 骨架页含 §8 五个必需小节、`status: proposed`、`managed_by: pipeline`
- [x] duplicate canonical_id → 拒写派生 + exit 非 0；同域别名碰撞 → `[warn]`
- [x] 测试隔离：CLI 测试与 smoke 经 `STUDY_KB_ROOT` 写 tmp，真实仓库无 `wiki/`（实测 False）
- [x] 全量 150 passed，工作树干净（仅报告目录未跟踪）

## 当前状态与下一步

P2 完成 = 概念归一基底就位：任何概念提及可确定性归一到唯一 canonical 页；registry 带 sha256（P4 work order 的 `registry_hash` 直接消费）；`resolve_or_create_concept` 是 P4 `/ingest` 概念写入的唯一协议入口。

分支链：main ← feat/p0-state-foundation ← feat/p1-source-convert ← feat/p2-canonical-concepts（均未合并未 push）。下一期 **P3：页面模板 + 正文清理（证据进脚注）**。
