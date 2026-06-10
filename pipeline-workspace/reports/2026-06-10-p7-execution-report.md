# P7 多领域结构 + 跨域提升执行报告

- 日期：2026-06-10
- 分支：`feat/p7-cross-domain-promotion`（基于 `feat/p6-lint-gate`，保留本地）
- 计划：`docs/superpowers/plans/2026-06-10-p7-cross-domain-promotion.md`
- 验证：`python -m pytest -q --ignore=tmp` → **207 passed**（202 旧 + 5 新，零回归）

## 提交清单

| 提交 | 内容 |
|---|---|
| 2a320aa | `promotion.py`：候选检测（同名/同别名 ≥2 域，shared 除外；只检测不动盘）+ 机械提升（移动到顶层 concepts/、frontmatter 改 shared、canonical_id 重写、全 vault 链接重写、目标冲突中止不动盘） |
| 5b7bfde | CLI `promotion-candidates [--propose]`（落 Review-Queue + review_proposals，kind=promotion-candidate）+ `promote-concept --id`（人工确认后执行） |

## 执行偏差记录

计划 Task 2 测试预期写错 cid：`create_concept(name="效用函数", aliases=["Utility"])` 按 P2 规则优先取 ASCII 别名做 slug，实际 cid 是 `concept.cs.utility`。修正测试预期（实现无误）。

## 验收（逐项实测）

- [x] 候选检测：双域同名报告 1 条；单域/已 shared 不报；不改任何概念页
- [x] `--propose`：Review-Queue 文件 + proposals 行（kind=promotion-candidate）
- [x] 机械提升：页移动、`scope/domain/canonical_id/page_path` 改写、source_refs 内容保留、引用页 wikilink 重写为新路径
- [x] 冲突守卫：unknown id → KeyError；shared 目标已存在 → FileExistsError 且原页不动
- [x] 同名异义不合并：cs 域的页原样保留；提升后 rebuild-registry 可用（同名仅 warning）

## 下一步

P8（最后一期）：query/save-back 闭环 + `/kb-review` + `/wiki-lint-semantic` 命令层 + Q1 会话检查。其后做收尾清理期（删除旧管线代码/依赖、同步 README/CLAUDE/domain 文档）即完成全部重构。
