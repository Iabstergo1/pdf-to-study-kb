---
name: skill-evolve
description: 把一次反复出现的 ingest/lint 失败沉淀成对某个 skill 的有界改进——读 skill-mine 产出的 backlog → 在隔离分支写 bounded SKILL.md 编辑 → 跑 skill-gate（pytest+双树对等+gate-integrity）→ skill-stage 候选，交人 skill-adopt。当用户说“把这次踩的坑沉淀进 skill / evolve skill / 让 skill 自我改进 / 处理 skill backlog 第 N 条”时使用。仅用于改进本项目 skill 自身；“总结这篇 / 解释这段 / 翻译一下 / 把这本书加进知识库（那是 ingest）”绝不触发。
---

# skill-evolve —— 让 skill 在 gate 守护下越用越稳（唯一 LLM，人触发）

把一个**反复出现**的失败，沉淀成对某个 skill 的**有界改进**；改对没改对由**确定性测试**判，发布由**人**拍板。本 skill 是六条铁律下「人触发的唯一 LLM 动作」，**绝不无人值守批跑**。

## 1. 触发 / 负样本

触发：用户要把反复出现的失败沉淀进某个 skill——“evolve skill / 处理 backlog 第 N 条 / 把这次踩的坑写进 skill / 让某 skill 自我改进”。

**负样本（绝不触发）：**
- “把这本书 / 这个 PDF 加进知识库” → 那是 **ingest**，不是改 skill。
- “总结这篇 / 解释这段 / 翻译一下 / 查知识库里的 X” → 只读或 ingest/kb-query，与改 skill 无关。
- 一次性、不复现的失败（backlog `count` = 1）→ 不值得改 skill，不进。
- 任何需要改 `tests/` 或 `pipeline.py` 的 gate 逻辑才能“通过”的诉求 → 越权，拒绝。

## 2. 输入

- `skill-mine` 产出的 `pipeline-workspace/skill-evolution/backlog.yaml`：每条带 `signature / count / sources / sample_reason`。
- 用户指定要处理的 backlog 条目（按 `signature` 或序号）。
- 目标 skill 的 `SKILL.md`（双树两份）+ 失败样例上下文（`review_proposals` 的 `reason` / Review-Queue 条目）。

## 3. 输出

- 对**单个** skill 的 **bounded 编辑**：只动该 `SKILL.md` 的一两个小节；**双树同步改、保持字节对等**。
- 隔离分支上的候选 + `skill-stage` 登记的提案 `pipeline-workspace/skill-evolution/candidates/<id>/proposal.diff`。
- **不直接发布**：候选语义等同 `proposed`，须人 `skill-adopt` 才合并进双树（stage→adopt 即两阶段发布的类比）。

## 4. 依赖

- CLI：`skill-mine`（读 backlog）、`skill-gate`（确定性门）、`skill-stage`（登记提案）、`skill-adopt`（人采纳）。
- 隔离：git 分支 / worktree（候选与线上隔离）。
- 真值：`CLAUDE.md` / `AGENTS.md`（六条铁律，尤其铁律 #1：本 skill 是**人触发**的唯一 LLM 动作）。
- **不依赖**任何 LLM-judge / 训练后端 / rollout-replay。

## 5. 持久化 artifact

全部落 gitignored 工作区 `pipeline-workspace/skill-evolution/`：
- `backlog.yaml`（skill-mine 产出，输入）。
- `candidates/<id>/proposal.diff`（skill-stage 产出，供人审）。
- `audit.jsonl`（staged / adopted / 被拒“此路不通”负样本，append-only 留痕）。

## 6. CLI 命令（业务逻辑全在这里）

```bash
python scripts/pipeline.py skill-mine                       # 失败信号 → backlog.yaml
# 人读 backlog，挑一条 count>=2（反复出现）的 signature
git switch -c skill-cand/<id>                               # 隔离分支
#   在该分支写 bounded SKILL.md 编辑（双树同步，保持字节对等）
python scripts/pipeline.py skill-gate  --candidate <id>     # pytest + 双树对等 + gate-integrity
python scripts/pipeline.py skill-stage --candidate <id>     # 绿则登记提案，线上不动
#   把 proposal.diff 汇报给人，等确认
python scripts/pipeline.py skill-adopt --candidate <id>     # 人触发：重跑 gate 兜底 + 提交双树
```

## 8. 失败停止点

- `skill-gate` 红即停，不 stage：
  - **gate-integrity**：候选动了 skill 两树以外的文件（尤其 `tests/`）→ 立即停。这是越权 / 游戏自己的门。
  - **pytest 红**（含双树对等 T2）→ 停；把失败贴回，audit 记一条“此路不通”负样本，重写或放弃。
- backlog 该条 `count` = 1（不复现）→ 不值得改，停。
- 要靠改 `tests/` 或 gate 逻辑才能过 → 绝不做，停交人。
- `skill-adopt` 一律由**人**触发；本 skill 不自动 adopt。

## 9. 验收清单

- [ ] 候选 diff 只动 `.claude/skills/` 与 `.agents/skills/`（`skill-gate` 的 gate-integrity PASS）。
- [ ] 双树字节对等保持（pytest T2 绿）。
- [ ] `pytest tests` 全绿（`skill-gate` PASS）。
- [ ] 编辑是 bounded（一两个小节），且针对 backlog 那条 `signature`。
- [ ] 提案已 `skill-stage`、`audit.jsonl` 有记录；`skill-adopt` 留给人。
