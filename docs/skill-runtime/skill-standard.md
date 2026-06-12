# Skill 工程标准（薄 skill + 厚 CLI）

> 本项目所有 `.claude/skills/*/SKILL.md`（Claude）与 `.agents/skills/*/SKILL.md`（Codex）的统一工程格式。
> 真值口径：`CLAUDE.md` / `AGENTS.md` + 本目录 `docs/skill-runtime/*`。**不引用任何已删的 spec / ADR。**

## 总原则

**薄 skill + 厚 CLI。** `scripts/pipeline.py` 是唯一业务契约：确定性、可恢复、可审计、带门禁的重活都在 CLI、状态机、lint 里。
SKILL.md 只做四件事：**编排 CLI、提示验收、标失败停点、约束中间产物**。
**业务逻辑绝不写进 prose**；prose 的职责是把 CLI 已强制的契约「显式镜像」给模型看，降低临场发挥。

## 九段契约（每个 SKILL.md 必含）

Frontmatter：`name` + `description`（描述里嵌一句话正向 trigger）。正文九段：

1. **触发 / 负样本** —— 何时触发；**显式列负样本**（不该触发的请求，如「总结这篇 / 翻译」不进写库流程）。
2. **输入** —— 需用户/对话给什么 + 读哪些文件。
3. **输出** —— 产出什么（vault 页 / staging 产物 / 报告）；**写库一律 `status: proposed`**。
4. **依赖** —— 依赖哪些 `pipeline.py` 命令 / 其他 skill / `docs/skill-runtime/*` 协议 / `references/*`。
5. **持久化 artifact** —— 必须落盘的关键中间结果 + 位置（`ingest_progress` / `staging/<src>/digest.md` / `query-sessions/` / `staging/<src>/llm-notes/` / `pipeline-workspace/reports/`）。
6. **CLI 命令** —— 编排的确切命令（**业务逻辑在这里**）。
7. **阶段拆解**（仅复杂 skill）—— 子单元清单；每个子单元带 输入 / 输出 / 验收 / 持久化 / 停止点。复杂 skill 的阶段细节拆到同目录 `references/*.md`，SKILL.md 只做总编排与索引。
8. **失败停止点** —— 何时停下交人（check-write DENY / lint 失败 / `managed_by: human` 页冲突 / 锁竞争 / 预处理报错）。
9. **验收清单** —— 可核验项，对齐 CLI 门：check-write ALLOW、lint 通过、check-session、0 个 `page_rules` 违规。

> 简单 skill（如 kb-query）第 7 段可省；其余八段必填。

## 复杂 skill 的 references/ 拆分

`ingest` 等多阶段 skill：主 `SKILL.md` 只承载九段契约的**总编排**；阶段细节落同目录 `references/`：
`references/{preflight,write-pages,synthesis,finish-lint}.md`，每个文件本身按「输入 / 输出 / 验收 / 持久化 / 停止点」组织。
主文件用相对路径指向这些 references，模型按需加载。**协议关键词（workorder.yaml、resolve-concept、check-write、window-done、status: proposed、lint 等）允许落在 references 里，但不得丢失**（测试跨 `SKILL.md + references/*` 校验）。

## 三条特定边界（v1 binding）

1. **source-xray 默认不写 vault。** 拆书式结构提取的产物默认落
   `pipeline-workspace/reports/source-xray/<src>.md` 或 `pipeline-workspace/staging/<src>/llm-notes/`；
   **只基于已发布的 source/concept/topic** 生成阅读笔记 / synthesis 候选。**不参与预处理、不决定 windows、不决定写页范围、不创建/合并概念页。**
   仅当用户明确要存进 wiki 时，才转交 `kb-save` 走两阶段发布 + lint 门禁（守住「写库必须 proposed + promote」）。
2. **kb-qa 与 wiki-lint-semantic 不抢同一触发词。** `kb-qa` = 更宽的发布后/保存前 QA 报告（覆盖率、ljg-qa 式 Q 链、概念污染、跨页矛盾、公式/证据抽查），产出报告 + Review-Queue proposal；
   `wiki-lint-semantic` 保留为「语义 lint」的专门入口（L4/矛盾/Q2）。v1 两者触发词**互斥**（语义体检类词归 wiki-lint-semantic，QA/审计/覆盖率类词归 kb-qa）；后续可把 wiki-lint-semantic 并入 kb-qa，但不在第一版双触发。
3. **预处理零 LLM。** `source-preflight` 只编排并验收 `add-source → profile → source-convert → windows → workorder`，**不做任何 LLM 语义拆书 / unit 规划**（守住 CLAUDE.md / AGENTS.md 的「不拆分」）。

## 测试口径（落地于 tests/）

- **T1 标准合规**：遍历 **`.claude/skills/*` 与 `.agents/skills/*` 两棵树**，每个 SKILL.md（含 references）覆盖九段必填项。两树都查，避免漏掉 Codex 侧。
- **T2 双 agent 对等**：`.agents/skills/*` 与 `.claude/skills/*` skill 集合一致、协议关键词一致。
- **T3 卫生**：agent 面向文件无死 `spec`/`ADR`/`docs/superpowers|adr|agents` 指针、无 `pythonProject`、无 `.Codex`。
- **T4 协议词不丢**：跨 `SKILL.md + references/*` 校验关键协议词仍在（ingest：workorder.yaml/resolve-concept/check-write/window-done/status: proposed/lint；kb-* 各自关键词）。
- **T5 source-xray 守卫**：其 SKILL.md 显式声明「不参与预处理 / 不决定窗口 / 不决定写页范围 / 不建合并概念页 / 只基于已发布内容 / 默认不写 vault」。
