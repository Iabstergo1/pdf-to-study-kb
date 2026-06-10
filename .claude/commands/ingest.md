---
description: 把一个已预处理的 source 织入 wiki（唯一 LLM 步骤，人工触发；写 status: proposed）
argument-hint: <source_id>
---

# /ingest $1 — 整源织入 wiki

你是知识库的维护者。把 source `$1` 的内容**以概念/主题为主**织进 wiki（lessons 跟随源 TOC 为辅），
全程遵守 work order 事务协议。架构真值：`docs/superpowers/specs/2026-06-08-claude-code-wiki-redesign-design.md` §9。
按需读取：`docs/skill-runtime/schema.md`（页面类型/frontmatter）、`docs/skill-runtime/concept-resolution.md`（概念归一）。

## 0. 开工（守卫由 CLI 硬执行）

1. 读 `pipeline-workspace/staging/$1/workorder.yaml`——它定义你的全部写入边界（`write_scope`）、
   registry hash、页面快照。**没有 work order 不开工**（先 `python scripts/pipeline.py workorder --source $1`）。
2. 运行 `python scripts/pipeline.py ingest-start --source $1`。
   它会取 vault 锁并校验 stale registry——若中止，按提示重新生成 work order，不要绕过。

## 1. 逐窗处理（rolling digest，长源外部记忆）

对 `staging/$1/windows.jsonl` 里的每个 window（按 window_id 升序）：

1. **续跑检查**：若该 window 已在前次会话完成且输入未变，跳过（`window_states` 可经
   `python scripts/pipeline.py status` 辅助判断；重复完成无害但浪费）。
2. `python scripts/pipeline.py window-start --source $1 --window <id> --hash <windows.jsonl 行的 sha 或 char 范围串>`
3. **先读滚动摘要**：读 `staging/$1/digest.md`（首窗不存在则跳过）——它是你跨窗的连续性记忆。
4. `python scripts/pipeline.py show-window --source $1 --window <id>` 读取本窗源文本；
   该窗涉及 `needs_vision` 页时，直接读 `staging/$1/assets/pXXXX.png` 图片，公式写成 KaTeX `$$…$$`。
5. **织入 wiki**（写页规则见下 §2）：概念走 resolve-concept；lesson 跟随源 TOC；topic/comparison/synthesis/overview 增量更新。
6. **更新滚动摘要**：把"本窗要点、引入/更新的概念、未决线索（悬而未解的引用、跨窗概念）"
   追加进 `staging/$1/digest.md`（保持 ≤ 约 50 行，过长就压缩旧条目）。下一窗靠它衔接。
7. `python scripts/pipeline.py window-done --source $1 --window <id> --writes '["<写过的页>"]'`
   （失败时改用 `window-fail --error "<原因>"`，下次续跑只重做未完成窗。）

## 2. 写页纪律（每一笔写入都适用）

- **写前守卫**：`python scripts/pipeline.py check-write --source $1 --path <vault 相对路径>`。
  DENY（越界 / 不在快照 / hash 已变 / `managed_by: human`）→ **不写该页**，把拟议改动写成
  `wiki/Review-Queue/<page>-proposal.md`（说明想改什么、为什么）。
- **覆盖已存在页前先快照**：`python scripts/pipeline.py snapshot-page --source $1 --path <相对路径>`。
- **所有新建/修改页 frontmatter 一律 `status: proposed` + `managed_by: pipeline`**；模板见 `templates/`
  （source/lesson/concept/topic/comparison/synthesis），必需小节不可缺。
- **概念只走 `resolve-concept`**（命中合并、绝不新建重复页）：
  `python scripts/pipeline.py resolve-concept --mention "<提及>" --domain <domain> [--alias "<英文名>"] --ref-source $1 --ref-sections "<5.2>"`
  然后编辑它返回的页面填充正文。别名只写概念页 frontmatter `aliases:`。
- **派生文件绝不手写**：`concepts/_registry.yaml`、`aliases.md`、`index.generated.md` 由收尾 CLI 重建。
- lesson 正文：干净散文、无裸 E-ID、核心论断挂脚注 `[^e1]`、公式 KaTeX、难页内嵌源页截图
  （自检原语：`scripts/page_rules.py`）。
- 追加 `log.md`：`## [YYYY-MM-DD] ingest | $1 | <created/updated 页列表>`（append-only）。

## 2.5 综合层职责（一等产物，spec §7——不是可选项）

- **overview.md 每源必更新**：把本源带来的新概念挂进"核心概念地图"、调整"推荐学习路线"、
  补充"模型家族对比"。overview 是 living synthesis，**禁止退化成章节清单**（L5 会拦）。
- **topic**：本源与已有内容形成跨章节/跨来源主题时，增量更新 `topics/<主题>.md`
  （核心综合 + 各来源贡献表 + 未解决问题；与既有结论矛盾时记入"未解决问题"，不要悄悄改写）。
- **comparison**：出现 2+ 个可横向对比的模型/方法时建/更新 `comparisons/` 页。
- **synthesis**：跨来源沉淀出单一来源给不了的洞见时写 `synthesis/` 页。
- **lessons 跟随源 TOC**：每个源章节产出 lesson 是线性辅助层；概念/主题才是主组织。
- 收尾 CLI 只重建派生（index/registry/aliases），**不改写以上综合内容**——它们由你维护。

## 3. 收工

1. 全部 window 完成后：写/更新 `sources/$1.md`（来源摘要页，模板 `templates/source.md`）。
2. `python scripts/pipeline.py ingest-done --source $1` —— 状态进 `ingested/proposed`，锁释放。
3. 提示用户：运行收尾 lint/promote（P6）后内容才进入 published/index。
