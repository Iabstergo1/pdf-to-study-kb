# 命令路由（决策树 + 正/负样本）

架构真值：spec §3.4。所有写库命令 = 显式 slash command，用户敲了才跑；模型不得自行触发。

## 决策树

- 新外部来源（PDF/DOCX/PPTX/MD）要进知识库 → 预处理 CLI → `/ingest <source_id>`
- 问已有知识 → `/kb-query "<question>"`（只读，P8）
- query 后想留存 → `/kb-save <session_id>`（P8）
- 处理复核队列 → `/kb-review`（P8）
- 语义体检 → `/wiki-lint-semantic`（P8）

## 正例

- 「把这个 PDF / 这本书加入知识库」「ingest game-theory-whitepaper」→ `/ingest`
- 「知识库里关于信号博弈怎么说」→ `/kb-query`
- 「把刚才的对比存进 wiki」→ `/kb-save`

## 负例（绝不触发写库 / ingest）

- 「总结这篇文章」「解释这段话」「翻译一下」→ 普通回答，不进 wiki 流程
- 「帮我配 Obsidian」「修这个代码 bug」→ 与知识库无关
- 「这个 PDF 讲了什么？」（仅询问，未要求入库）→ 普通回答；除非用户明说"加入知识库"
