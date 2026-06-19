---
name: ingest
description: 把一个新的外部来源（PDF/DOCX/PPTX/Markdown 文件）端到端加入学习知识库——确定性预处理 → 读整源写 status:proposed 页 + 概念归一 → 收尾 lint 发布。当用户说“把这本书/这个 PDF 加进知识库 / ingest <源> / 收录这个文档 / 把这个文件编进 wiki”时使用。仅用于新增外部来源入库；“总结这篇 / 解释这段 / 翻译一下 / 问个常识”等只读请求绝不触发本 skill。
---

# ingest — 整源端到端织入 wiki（唯一 LLM 写库步骤，总编排）

你是知识库的维护者。把用户指定来源**以概念/主题为主**织进 wiki（lessons 跟随源 TOC 为辅），全程遵守 work order 事务协议。
本文件只做**总编排**；每阶段细节按需读同目录 `references/*`。项目真值见 `CLAUDE.md`，工程格式见 `docs/skill-runtime/skill-standard.md`。

> **薄 skill + 厚 CLI**：执行层是确定性零 LLM CLI（`scripts/pipeline.py`），本 skill 不含业务代码、只编排它。
> `<src>` = 本次来源 source_id；命令在项目根用 study-kb 解释器运行（Windows 用 pwsh + `$env:PYTHONUTF8=1`）。

## 1. 触发 / 负样本

- **触发**：「把这本书/这个 PDF 加进知识库」「ingest \<源\>」「收录这个文档」「把这个文件编进 wiki」。
- **负样本（绝不触发）**：「总结这篇」「解释这段」「翻译一下」「问个常识」「这个 PDF 讲了什么」（仅询问、未要求入库）。

## 2. 输入

- 用户给：原始文件路径 `<path>`、领域 `<domain>`；格式 `<fmt>` 由扩展名推断（pdf/md/docx/pptx）；`<src>` 由文件名派生（小写、连字符化），**与用户确认一次 `<src>` 与 `<domain>`**。
- 读：`wiki/_meta/purpose.md`（用户学习目标/讲解偏好；作为贯穿写页与综合层的全局写作偏好，不存在或为空则用默认）、`docs/skill-runtime/{schema,concept-resolution}.md`、`templates/*`、阶段 references。

## 3. 输出

- vault 写页一律 `status: proposed` + `managed_by: pipeline`：lessons / concepts / topics / comparisons / synthesis / `sources/<src>.md` / `overview.md`。
- 派生文件（`_registry.yaml` / `aliases.md` / `index.generated.md`）**不由本 skill 写**，收尾 CLI 重建。

## 4. 依赖

- CLI：`scripts/pipeline.py`（见各阶段命令）。
- 协议：`docs/skill-runtime/schema.md`（页面类型/必需小节）、`concept-resolution.md`（归一）。
- 阶段 references：`references/preflight.md`、`references/write-pages.md`、`references/synthesis.md`、`references/finish-lint.md`。

## 5. 持久化 artifact

- `pipeline-workspace/staging/<src>/`：`source.md`、`windows.jsonl`、`chapters.json`（确定性章节图 / 导航脊柱）、`workorder.yaml`、难页 PNG、`digest.md`（跨窗滚动摘要）。
- `ingest_progress`（window 级记账，机器状态）。失败回滚快照在 `pipeline-workspace/snapshots/`。

## 6. CLI 命令（编排次序）

```text
预处理(零 LLM)  init-vault → add-source → profile → source-convert → windows → workorder
开工/逐窗(LLM)   ingest-start → 读 chapters.json 建全书理解 →[ 按章序 window-start → show-window → 写页(难页按类型嵌原图) → window-done --writes ]×N
综合层(LLM)      阶段 E：更新 overview + 建 topic/comparison/synthesis（进某窗 --writes）——一等产物，缺则 lint 阻断
收尾(零 LLM)    ingest-done → lint
增量重开(零 LLM) reopen → ingest-start →[ 逐窗补写 ]→ ingest-done → lint
```

> **reopen（增量补充已发布源）**：要给一个已收尾来源（`lint` 终态 / `ingested`）补综合层 / 公式源图 / worked example / 链接克制时，先 `python scripts/pipeline.py reopen --source <src>`——它据当前 vault 重建 work order（刷新 registry hash + 页快照，使覆盖保护与 registry 校验对当前 published 状态成立）并把状态机重置回 `workorder_ready`，再照常 `ingest-start` 起增量循环。lint 只 promote 本轮新增/改写的 `proposed` 页，既有 `published` 页原样保留（不回滚）。新建的综合页无 `source:` 归属，**务必进某 window 的 `--writes` 记账**否则判孤儿阻断。

## 7. 阶段拆解（按需读 references）

| 阶段 | 文件 | 职责 |
|---|---|---|
| A 预处理 | `references/preflight.md` | 确定性链 + 验收（needs_vision/降级告警/windows 覆盖） |
| B+C+D 逐窗写页 | `references/write-pages.md` | 开工守卫 + **先读 chapters.json 建全书理解** + 按章序逐窗子单元 U1–U7 + 按类型嵌入原图 + 写页纪律 + lint 硬规则 |
| E 综合层 | `references/synthesis.md` | overview/topic/comparison/synthesis 增量维护 |
| F 收尾 | `references/finish-lint.md` | ingest-done + lint promote/回滚 + 派生重建 |

## 8. 失败停止点（其余一路自动推进并简报进度）

预处理任一步报错；`check-write` DENY（越界/覆盖保护）；lint 失败；`managed_by: human` 页冲突；跨域提升候选；vault 锁被占。

## 9. 验收清单

- 预处理：workorder.yaml 生成、`ingest-start` 取锁 + registry 新鲜校验通过。
- 写页：每页 `check-write` ALLOW、page_rules 自检 0 违规、非 source 页均进 `window-done --writes`。
- 综合层（阶段 E 必做）：overview 已更新（非纯链接清单）+ 至少一个 topic/comparison/synthesis，均进 `--writes`；漏做则 `lint` 报 `L7-synthesis-missing` 阻断回滚。
- 收尾：`lint` 通过（promote 入 index），或失败项进 `Review-Queue/` 并已回滚。
