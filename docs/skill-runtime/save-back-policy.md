# Save-back 准入门槛（spec §7.1）

`/kb-save` 写入前必须核对。**至少满足一项**，且不得缺证据（evidence_refs 非空）：

- 形成跨来源综合、模型对比、学习路线、常见误区或自测题；
- 解决一个会反复出现的学习困惑，并能链接到已有概念/主题；
- 发现重复概念、别名、跨域提升候选或页面矛盾；
- 用户明确要求「保存到 wiki / 形成笔记 / 加进 synthesis」。

## 默认不保存

- 一次性事实查询、普通解释、没有来源支撑的推测、只复述已有页面的答案；
- 需要覆盖 `managed_by: human` 页或越过 write scope 的答案；
- 无法链接到现有 source_refs / concept_refs 的内容。

## 硬约束

- 概念写入仍走 `resolve_or_create_concept` 协议（命中即合并、绝不新建重复）。
- 全部写出页 `status: proposed`，由收尾 `lint` 决定 promote；Q2 语义判断可阻断。
- `decision.md` 必须说明：为什么保存 / 写了哪些页 / 引用了哪些证据 / 为什么没有污染已有概念。
