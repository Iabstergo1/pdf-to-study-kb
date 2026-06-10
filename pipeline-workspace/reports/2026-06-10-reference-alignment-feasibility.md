# 参考项目对照评估：总计划能否实现最终重构

- 日期：2026-06-10
- 参考基准：karpathy llm-wiki gist、sdyckjq-lab/llm-wiki-skill、nashsu/llm_wiki、SamurAIGPT/llm-wiki-agent
- 对照对象：spec §1–§15（设计唯一真值）+ ADR-0001 + 已完成（文档链、P0）+ 待实现（P1–P8）
- 结论先行：**四个参考项目的核心思想全部有明确的 spec 条款和分期承载，已完成与待实现之间链条无断档——总计划可以实现最终重构。** 附 3 个达成条件（见 §4）和 1 个范围声明（source lifecycle 在 P0–P8 外，见 §5）。

---

## 1. 四个参考各自贡献了什么、落在计划哪里

### 1.1 karpathy gist —— llm-wiki 范式本身（理念源头）

**核心思想**：LLM 不做一次性问答（RAG 检索即弃），而是**增量维护一个持久、互联、复利增长的 wiki** 作为外部记忆；查询行为本身也沉淀回 wiki。

| 借用点 | spec 条款 | 承载分期 | 状态 |
|---|---|---|---|
| 持久复利 wiki（非 RAG、非一次性转写） | §1 目标、§2 决策 | 全局 | 理念已锁定 |
| overview = living synthesis 入口 | §4、L5 lint | P5/P6 | 待实现 |
| query 也沉淀回 wiki（save-back） | §7.1 | P8 | 待实现 |
| wiki 当外部记忆、长源分批织入 | §13 风险第 1 条 | P4 | 待实现 |

### 1.2 sdyckjq-lab/llm-wiki-skill —— Claude Code 驱动 + 页面模板

**核心思想**：用 Claude Code 的 skill/命令机制维护 wiki；概念页有固定结构小节，保证读感一致。

| 借用点 | spec 条款 | 承载分期 | 状态 |
|---|---|---|---|
| 页面模板（直觉/形式化/关系等小节） | §8（明写"套 sdyckjq-lab"） | P3 | 待实现 |
| 命令层组织（prompt/模板/协议拆文件、按命令加载） | §3.4 | P4 | 待实现 |
| 模板结构可检查 → 确定性 lint L2/L3/L5 | §11 | P6 | 待实现 |

**本项目的改进**（非照抄）：参考项目用模型自动触发的 Skill，本项目改为**显式 slash command**（副作用命令绝不自动触发；若用 SKILL.md 必加 `disable-model-invocation: true`），消除命中率稀释与误触发——spec §3.4/§13 已钉死。

### 1.3 nashsu/llm_wiki —— 长源窗口化读取 + source 生命周期

**核心思想**：长源按窗口滑动读取、窗口间传递滚动摘要（rolling digest）；source 有更新/删除/取代的生命周期管理（source-delete-decision）。

| 借用点 | spec 条款 | 承载分期 | 状态 |
|---|---|---|---|
| processing windows（确定性切窗 + overlap） | §3.1（明写"借鉴 nashsu"） | **P1**（windowing.py） | 计划已写并修订 |
| rolling digest（跨窗摘要传递） | §3.1 | P4（/ingest 会话内行为） | 待实现，**见条件 C1** |
| source lifecycle（更新/删除/取代） | §9.1（明写"借鉴 nashsu"） | P7 之后/单列一期 | stub，刻意延后 |

### 1.4 SamurAIGPT/llm-wiki-agent —— 直接驱动 Claude Code、无独立 API key

**核心思想**：agent 工具直接调用 Claude Code 完成生成，不需要单独配置 API key。

| 借用点 | spec 条款 | 承载分期 | 状态 |
|---|---|---|---|
| Claude Code = 唯一 LLM 执行体 | §3 边界、ADR-0001 | P4 | 决策已锁定 |
| 订阅 key 可用、不配独立 API | ADR-0001 背景 | — | 已采纳 |

**本项目的改进**：参考项目是 agent 自动循环驱动；本项目因 key 禁止无人值守自动化，改为**人工触发的交互式 `/ingest`**——这不是妥协而是合规约束下的正确形态，且 spec §13 把它列为显式风险项管理。

## 2. 四个参考都没有、本项目自建的部分（重构的真正增量）

参考项目全部是 **prompt/skill 级**的轻量实现，没有任何一个具备工程化保障。本项目在它们之上自建了：

1. **确定性状态底座**（P0，✅ 已完成并测试）：单库状态机、原子阶段 API、锁、非 git 快照回滚——四个参考都没有崩溃恢复和进度协调能力。
2. **两阶段发布 + 确定性门禁**（P6）：参考项目的 wiki 质量全靠 prompt 自觉；本项目 proposed→lint→promote/回滚+Review-Queue 把质量变成可执行约束。
3. **概念去重的硬保证**（P2+P4+P6）：registry hash 守卫 + 单一 `resolve_or_create_concept` 协议 + 阻断性重复检查"四件套"——直接针对旧管线"信号博弈×2、不完全信息×6"的实测痛点，参考项目无对应物。
4. **覆盖保护**（snapshot+hash+managed_by 三条件）：保护人工编辑不被 LLM 覆写。

这意味着：**重构的可行性不依赖参考项目提供工程先例**——参考项目供给"方向正确性"（llm-wiki 范式被多个独立实现验证可行），工程化部分由 spec 自洽设计 + 分期 TDD 落地，P0 已证明这条路走得通（23 个测试、6 个提交、契约缺口被审查发现后可快速修复）。

## 3. 链条完整性：已完成 → 待实现 → 最终形态

```text
P0 状态底座 ✅ ──→ P1 喂料(source.md+windows+needs_vision) ──→ P2 概念去重基底
                                                                    ↓
P8 query闭环 ←── P7 多领域 ←── P6 门禁/promote ←── P5 综合层 ←── P4 /ingest(唯一LLM)
                                                                    ↑
                                                          P3 页面模板(P4 的写入格式)
```

逐期检查输入/输出衔接：P1 消费 P0 的状态 API（plan 已逐签名核对）；P2 的 registry 是 P4 work order 的守卫输入；P3 模板是 P4 写页的格式契约；P5/P6 消费 P4 的 proposed 产物；P7 在 P2 的 canonical 命名空间（`concept.<domain>.<slug>`）上做跨域提升——**命名空间设计从 P2 起就为 P7 预留，无需返工**；P8 复用 P4 的写入纪律（§9 明确 `/kb-save` 复用同一套）。spec §14 的 11 条验收标准每条都能映射到至少一个分期，无孤儿验收项。

**结论：没有"做到中途发现缺前置"的断档。** 最薄弱的衔接点已在本轮 P1 plan 修订中处理（路径锚定、崩溃恢复出口、profile 真实产出）。

## 4. 达成条件（3 条，不满足会偏离参考项目的关键经验）

- **C1（写 P4 计划时落实）：rolling digest 不能丢。** P1 的 `windows.jsonl` 只承载切窗 + overlap（机械部分）；nashsu 经验里跨窗连续性靠"滚动摘要"维持，这是 `/ingest` 会话内行为。P4 计划必须显式写进 ingest 协议（每窗结束生成 digest、下一窗带上），否则长书各窗织入会退化成"逐窗孤立生成"——恰是 ADR-0001 要消灭的旧病在 LLM 侧复发。
- **C2（顺序纪律）：P2 必须在 P4 前完成。** 没有 registry+canonical 基底，`/ingest` 的概念归一无物可查，会重演重复概念页。spec §15 顺序已正确，执行时不可为"先看到 LLM 效果"而跳期。
- **C3（P4 是风险集中期，按最小可用切）：** work order 守卫、覆盖保护、两阶段写入全是"LLM 会话内纪律 + CLI 侧验证"的配合，参考项目无先例。建议 P4 计划拆成"先单 md 短源走通完整事务协议，再开长源多窗"，把自创部分的验证面压到最小。

## 5. 范围声明：source lifecycle 不在 P0–P8 内

"不断长大的知识库"长期必然遇到 source 更新/删除/取代（nashsu 已为此设计了 source-delete-decision）。spec §9.1 已 stub 并论证 P0 的表结构无需改动即可承载，排在 P7 之后或单列一期。**最终重构（P0–P8 完成）= 新增 source 的完整闭环可用**；lifecycle 是其后的增量，不影响"重构完成"的判定，但应保留在路线图上。

## 6. 总判定

| 问题 | 判定 |
|---|---|
| 参考项目的核心思想是否都被吸收 | 是，且每条有 spec 条款 + 分期归属（§1 四表） |
| 是否有参考思想被吸收但无分期承载 | 仅 rolling digest 处于"spec 有、分期计划暂无"的缝隙 → 条件 C1 |
| 已完成部分是否支撑待实现部分 | 是，P0 API 已被 P1 plan 逐签名消费验证；衔接缺陷（F1–F3）已修 |
| 总计划能否到达最终重构 | **能**。满足 C1–C3 时，P0–P8 完成即达到 spec §14 全部验收标准 |

---

*关联：`2026-06-10-refactor-analysis.md`（同日重构分析，F1–F7 发现清单）；本轮已按 F1/F2/F3 修订 P1 plan。*
