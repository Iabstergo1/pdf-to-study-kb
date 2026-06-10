# P6 学习质量 Lint + 后置门禁执行报告

- 日期：2026-06-10
- 分支：`feat/p6-lint-gate`（基于 `feat/p5-synthesis-layer`，保留本地）
- 计划：`docs/superpowers/plans/2026-06-10-p6-lint-gate.md`
- 验证：`python -m pytest -q --ignore=tmp` → **202 passed**（191 旧 + 11 新，零回归）

## 提交清单

| 提交 | 内容 |
|---|---|
| f4a9e73 | state_store：add/list_review_proposals |
| 5e18d79 | wiki_gate：proposed 收集 + 确定性规则（L1/L2/L3/L5/L6-代理/断链/公式邻接/脚注证据/重复 canonical） |
| d2fe189 | index.generated.md 重建（只收 published）+ promote |
| c771539 | CLI `lint` 门禁编排（pass→promote+派生重建+log+清快照；fail→回滚+Review-Queue+proposals+回流）；**修 overview 模板占位用了真实 wikilink 语法导致新 vault 自带断链** |

## 执行中发现并修正的问题

1. **overview 种子自带断链**（集成测试抓出）：模板占位文本 `[[概念页]]` 是合法 wikilink，新 vault 第一次 lint 必失败。改为不含 `[[` 的占位描述。
2. P6 CLI 测试的 GOOD_LESSON 短于 L6 代理阈值（80 字符）——加长测试正文；阈值本身是设计取舍保留。

## 验收（spec §14 对照，实测）

- [x] 门禁通过才 promote 并纳入 `index.generated.md`；source 状态 `(lint, published)`
- [x] 失败内容保持 `proposed`、不进 index；就地 merge 的 overview 按 manifest 回滚到原版
- [x] 违规落 `review_proposals`（kind=规则名）+ `Review-Queue/<source>-lint-<date>.md`
- [x] lint failed → `ingest_waiting` 回流可行（状态机实测）
- [x] promote 后派生重建（registry/aliases/index）+ log.md 追加 lint 行 + 该 source 快照清理
- L6 为代理实现（lesson 去占位后 <80 字符），精确判定需源页映射，留待真实样本校准（计划已声明）

## 下一步

P7：多领域结构落地 + 跨域提升流程（候选检测 → Review-Queue 人工确认 → 机械提升 + 链接重写）。
