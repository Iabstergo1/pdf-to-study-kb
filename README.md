# 📚 PDF → Study KB

把多来源文档（PDF / DOCX / PPTX / Markdown）编译进一个**不断长大的、多领域的本地 Obsidian 学习知识库**——按概念/主题导航，而不是线性翻原文。采用 [llm-wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) 模式：LLM 增量维护一个持久、互联的 wiki。

> **状态**：新架构是仓库唯一管线（P0–P9 已完成并入 main）。**设计唯一真值**是 [`docs/superpowers/specs/2026-06-08-claude-code-wiki-redesign-design.md`](docs/superpowers/specs/2026-06-08-claude-code-wiki-redesign-design.md)，关键决策见 [`docs/adr/`](docs/adr/)（`0001` 舍弃 LangGraph，勿重新引入旧管线）。构建期 plans/报告已清理，需要时查 git 历史。

## 架构

确定性 Python CLI 做预处理 + 后置门禁 + 索引 + 状态跟踪（**零 LLM**）；唯一的 LLM 是**人工触发**的交互式 Claude Code `/ingest`，它读整源、写并合并 wiki、跨页归一概念。

```text
CLI 预处理（零 LLM）        add-source → profile → source-convert → windows → workorder
        ↓ 人工触发
Claude Code /ingest（唯一 LLM）  读 source.md / 难页图 → 写 status:proposed 页 + 概念归一
        ↓ 人工触发
CLI 收尾（零 LLM）          确定性 lint → 门禁 promote(proposed→published) / 失败回滚+Review-Queue → 重建索引
```

设计要点：

- **不拆分**：不让 LLM 做语义切分；长源用确定性 processing windows（TOC/标题/token 滑窗）读取。
- **概念去重**：所有概念创建/更新走单一 `resolve-concept` 入口，命中合并、绝不重复建页；`_registry.yaml`/`aliases.md` 为派生文件。
- **两阶段发布**：`/ingest` 只写 `status: proposed`；收尾 `lint` 过门禁才 promote 成 `published` 并入 index，失败回滚 + 进 `Review-Queue/`。
- **非文字内容**：PyMuPDF 文本后端 + 难页渲染 PNG 交 Claude 多模态读图（marker/docling 为可选适配器）。

## 快速开始：一个 source 的完整生命周期

```powershell
$py = "python"   # 入口统一为 python scripts/pipeline.py <command>

# 0) 一次性：建 vault 脚手架（幂等，绝不覆盖已有文件）
& $py scripts/pipeline.py init-vault

# 1) 预处理（零 LLM，可重跑，幂等跳过）
& $py scripts/pipeline.py add-source --source mybook --domain game-theory --path raw/mybook.pdf --fmt pdf
& $py scripts/pipeline.py profile        --source mybook
& $py scripts/pipeline.py source-convert --source mybook
& $py scripts/pipeline.py windows        --source mybook
& $py scripts/pipeline.py workorder      --source mybook

# 2) 唯一 LLM 步骤：在 Claude Code 里人工触发
#    /ingest mybook
#    （内部按窗循环：ingest-start → window-start → 写页 → window-done --writes → … → ingest-done）

# 3) 收尾（零 LLM）：门禁 + 发布
& $py scripts/pipeline.py lint --source mybook

# 随时查看进度与下一步
& $py scripts/pipeline.py status
& $py scripts/pipeline.py next
```

状态库默认锚定仓库根（`pipeline-workspace/state/study-kb.sqlite`）；设 `STUDY_KB_ROOT` 环境变量可整体重定向（测试/多库场景）。

## CLI 命令面（24 个子命令）

| 分组 | 命令 | 说明 |
|------|------|------|
| 状态/维护 | `status` | 每个 source 的阶段/状态 + vault 锁持有者（stale 标记） |
| | `next` | 每个 source 的下一步人工动作 + stale 锁清理建议 |
| | `unlock [--ttl 1800]` | 受控回收 stale vault 锁；heartbeat 未超时的活锁拒绝 |
| | `fail --source --stage --error` | 把崩溃残留的 running 阶段标记 failed，解卡后可重跑 |
| 预处理 | `add-source` `profile` `source-convert` `windows` `workorder` | 见上"快速开始"；逐阶段推进状态机，输入未变则 `[skip]` |
| vault | `init-vault` | 建 `wiki/` 脚手架 + overview/log/purpose 种子（幂等） |
| | `rebuild-registry` | 从概念页 frontmatter 重建 `_registry.yaml` + `aliases.md` |
| /ingest 会话支撑 | `ingest-start` / `ingest-done` | 开工（取锁 + stale registry 校验）/ 收工（释放锁） |
| | `show-window` | 打印指定 window 的源文本 |
| | `window-start` / `window-done --writes` / `window-fail` | window 级记账（写集归属、断点续跑、锁心跳） |
| | `resolve-concept` | 概念归一唯一入口：命中合并 / 未命中新建 |
| | `check-write` | 写前守卫：写入边界 + 覆盖保护三条件，DENY 则 exit 1 |
| | `snapshot-page` | 就地 merge 前快照该页（供 lint 失败回滚） |
| 收尾门禁 | `lint --source` | 只 lint/promote 归属本 source 的 proposed 页；过则发布+重建派生，败则回滚+Review-Queue |
| 跨域提升 | `promotion-candidates [--propose]` / `promote-concept --id` | 检测候选（提升一律人工确认）/ 机械提升为 shared |
| 查询会话 | `check-session --id [--saved]` | `/kb-query`、`/kb-save` 产物的目录契约检查 |

## 状态机与故障恢复

每个 source 走单向阶段流（单一业务 SQLite 记录）：

```text
registered → profiled → converted → windowed → workorder_ready
          → ingest_waiting → ingesting → ingested(proposed) → lint(published)
```

- **阶段崩溃**（卡在 running）：`pipeline.py fail --source X --stage <卡住的阶段> --error "原因"`，然后重跑该阶段。
- **lint 失败**：自动回滚本 source 的就地 merge 快照、违规清单写 `wiki/Review-Queue/`、source 进 `lint/failed`；修复后直接重跑 `lint`，或回 `/ingest`（状态机允许 `lint failed → ingest_waiting`）。
- **孤儿 proposed 页**（不归属任何 source，通常是 `/ingest` 漏了 `window-done --writes` 记账）：阻断 lint（fail-closed），按 Review-Queue 提示补归属后重跑。
- **/ingest 崩溃残留锁**：`status` 会显示 `[STALE]`，`next` 给清理建议，`unlock` 回收（默认 heartbeat 超 1800s 才允许；window 记账会自动续心跳，活跃会话不会被误判）。

## LLM 边界（Claude Code slash 命令）

全部**人工触发**，无无人值守自动化：

- `/ingest <source_id>` —— 唯一写 wiki 的 LLM 步骤（rolling digest、写入守卫、window 级续跑）。
- `/kb-query` —— 只读查询知识库，持久化 query-session。
- `/kb-save` —— 把 query-session 候选提升为 proposed 写入（有准入门槛）。
- `/kb-review` —— 处理 Review-Queue 与 review proposals。
- `/wiki-lint-semantic` —— 语义体检（L4/矛盾），只产出 proposal 不直接改页。

协议细节见 [`.claude/commands/`](.claude/commands/) 与 [`docs/skill-runtime/`](docs/skill-runtime/)。

## Vault 结构（输出）

```text
wiki/
  domains/<domain>/{lessons, concepts}   # 讲义（跟随源 TOC）+ 领域私有概念
  concepts/        # 仅 shared（跨域提升后），含 _registry.yaml（派生）
  topics/ comparisons/ synthesis/        # 综合层（一等产物）
  sources/  assets/  Review-Queue/
  overview.md      # living synthesis，入口
  index.generated.md  log.md  aliases.md # 派生（只收录 published）
```

## 在 Obsidian 中阅读

Obsidian → `Open folder as vault` → 选 `wiki/` → 从 `overview.md` 开始。所有生成笔记 frontmatter 为 Dataview 友好（`type`/`canonical_id`/`domain`/`status`/`source_refs`…）。

## 开发

- 依赖：`requirements.txt`（PyMuPDF + PyYAML + pytest；重转换后端为可选适配器）。
- 测试：`python -m pytest tests -q`（139 个，全部确定性、零 LLM）。
- `tests/test_legacy_removed.py` 守卫旧管线不被重新引入（LangGraph / 双 SQLite / plan-units）。

## 文档导航

| 文档 | 用途 |
|------|------|
| `docs/superpowers/specs/2026-06-08-…design.md` | 设计唯一真值 |
| `docs/adr/` | 架构决策记录 |
| `docs/agents/domain.md` | 领域术语 |
| `docs/skill-runtime/` | /ingest 等命令的运行时协议 |
| `CLAUDE.md` | Agent 指令 |
