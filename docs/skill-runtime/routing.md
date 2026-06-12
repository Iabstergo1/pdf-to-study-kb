# 命令路由（决策树 + 正/负样本）

架构真值：`CLAUDE.md` / `AGENTS.md`。命令层为 `.claude/skills/<name>/SKILL.md`（Claude）/
`.agents/skills/<name>/SKILL.md`（Codex），
**全部允许模型按 `description` 自动触发**；误触发靠下方负样本压制，数据安全由确定性 CLI 守卫强制（与是否自动触发正交）。
可手动 `/name` 调用，也可在对话相关时由模型主动调用。

## 决策树

- 新外部来源（PDF/DOCX/PPTX/MD）要进知识库 → `ingest`（自身编排预处理 → 写 proposed → 收尾 lint）
- 只想先跑确定性预处理与 workorder 验收、不写语义页 → `source-preflight`
- 问已有知识 → `kb-query`（只读 + 持久化 query-session）
- query 后想留存 → `kb-save`（有准入门槛）
- 处理复核队列 → `kb-review`
- 语义体检 → `wiki-lint-semantic`
- QA/审计/覆盖率/Q 链/证据抽查 → `kb-qa`（触发词与 `wiki-lint-semantic` 互斥）
- 基于已发布内容生成拆书式阅读笔记/学习路线候选 → `source-xray`（默认只写 reports，不写 vault）

## 正例

- 「把这个 PDF / 这本书加入知识库」「ingest game-theory-whitepaper」「收录这个文档」→ `ingest`
- 「先预处理这个 PDF」「跑 source-preflight」「先看看能不能 ingest」→ `source-preflight`
- 「知识库里关于信号博弈怎么说」「查我的 wiki」→ `kb-query`
- 「把刚才的对比存进 wiki」「形成 synthesis」→ `kb-save`
- 「处理复核队列」→ `kb-review`；「给知识库做个语义体检」→ `wiki-lint-semantic`
- 「做一次知识库 QA」「审计覆盖率」「抽查证据」「跑 Q 链」→ `kb-qa`
- 「给这个已发布来源做 xray」「生成拆书阅读笔记」→ `source-xray`

## 负例（绝不触发写库 / ingest）

- 「总结这篇文章」「解释这段话」「翻译一下」→ 普通回答，不进 wiki 流程
- 「帮我配 Obsidian」「修这个代码 bug」「问个常识」→ 与知识库无关
- 「这个 PDF 讲了什么？」（仅询问，未要求入库）→ 普通回答；除非用户明说“加入知识库”
- `source-preflight` 绝不做 LLM 语义拆书 / unit 规划 / 写 proposed 页；要入库必须转 `ingest`
- `source-xray` 只基于已发布内容，默认不写 vault；要保存到 wiki 必须转 `kb-save`
- 「语义体检 / 检查矛盾 / comparison 维度 / Q2 新增价值」归 `wiki-lint-semantic`，不要触发 `kb-qa`
