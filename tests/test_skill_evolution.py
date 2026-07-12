"""skill 自进化闭环（MVP-A）测试。

第一刀只覆盖 `skill-mine`（零-LLM）：把已落库的失败信号（review_proposals）
按 `kind` 聚类成 backlog——回答"哪类坑在反复出现，值得沉淀进 skill"。
隔离同其余 CLI 测试：STUDY_KB_ROOT 指向 tmp，状态库/产物绝不写真实仓库。
"""
import os
import sys
import json
import subprocess
import importlib.util
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "scripts" / "pipeline.py"

_spec = importlib.util.spec_from_file_location("state_store", ROOT / "scripts" / "state_store.py")
state_store = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(state_store)


def _run(cwd, *args):
    env = {**os.environ, "STUDY_KB_ROOT": str(cwd)}  # 隔离：状态库/产物都落 tmp
    return subprocess.run([sys.executable, str(PIPELINE), *args],
                          cwd=cwd, env=env, capture_output=True, text=True)


def _seed_db(cwd):
    db = cwd / "pipeline-workspace" / "state" / "study-kb.sqlite"
    state_store.init_db(db)
    state_store.register_source(db, "s1", domain="algorithms", fmt="md")
    return db


def test_skill_mine_backlog_clusters_counts_sources_and_sample_reason(tmp_path):
    # 一次挖掘验证 backlog 全部内容契约（合并自 按 kind 聚类计数 / 跨源 entry 两条，断言全保留）：
    # 同一规则反复失败 = 值得沉淀的信号，一次性失败 = 噪声；同一签名跨多个来源更值得沉淀，
    # entry 要带上是谁、给个样例原因供人 triage。
    db = _seed_db(tmp_path)
    state_store.register_source(db, "s2", domain="economics", fmt="pdf")
    for i in range(3):
        state_store.add_review_proposal(
            db, "s1", target_path=f"domains/algorithms/lessons/p{i}.md",
            kind="broken-link", reason="dangling wikilink")
    state_store.add_review_proposal(
        db, "s2", target_path="domains/economics/lessons/b.md",
        kind="broken-link", reason="dangling wikilink to baz.md")
    state_store.add_review_proposal(
        db, "s1", target_path="domains/algorithms/lessons/q.md",
        kind="missing-section", reason="lesson missing evidence section")

    r = _run(tmp_path, "skill-mine")
    assert r.returncode == 0, f"skill-mine 应成功退出；stderr=\n{r.stderr}"

    backlog = tmp_path / "pipeline-workspace" / "skill-evolution" / "backlog.yaml"
    assert backlog.exists(), "skill-mine 应产出 backlog.yaml"
    data = yaml.safe_load(backlog.read_text(encoding="utf-8"))
    counts = {e["signature"]: e["count"] for e in data["backlog"]}
    assert counts["broken-link"] == 4
    assert counts["missing-section"] == 1
    entry = next(e for e in data["backlog"] if e["signature"] == "broken-link")
    assert sorted(entry["sources"]) == ["s1", "s2"]
    assert entry["sample_reason"]  # 非空，给人 triage 用


# ---- skill-gate：零-LLM 确定性门（gate-integrity + pytest）----

def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def _init_repo(cwd):
    """最小 git 仓：双树各一个 skill + 一个会过的 tests/，base 提交。"""
    _git(cwd, "init", "-q")
    _git(cwd, "config", "user.email", "t@t")
    _git(cwd, "config", "user.name", "t")
    for tree in (".claude/skills/foo", ".agents/skills/foo"):
        (cwd / tree).mkdir(parents=True)
        (cwd / tree / "SKILL.md").write_text("# foo\nv1\n", encoding="utf-8")
    (cwd / "tests").mkdir()
    (cwd / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    # 同真实仓：gitignore 运行时工作区 + __pycache__，stage/adopt 的 audit/proposal 与
    # gate 重跑 pytest 生成的缓存都不被 gate 当越界文件误杀。
    (cwd / ".gitignore").write_text("pipeline-workspace/\n__pycache__/\n", encoding="utf-8")
    _git(cwd, "add", "-A")
    _git(cwd, "commit", "-qm", "base")


def test_skill_gate_rejects_candidate_touching_tests(tmp_path):
    _init_repo(tmp_path)
    # 候选偷改 tests/ = 游戏自己的门：gate-integrity 必须红，且在 pytest 之前就拦下。
    (tmp_path / "tests" / "test_ok.py").write_text(
        "def test_ok():\n    assert True  # weakened\n", encoding="utf-8")

    r = _run(tmp_path, "skill-gate", "--candidate", "c1")
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert "gate-integrity" in out and "tests/test_ok.py" in out


# ---- skill-stage / skill-adopt：登记提案 → 人采纳进双树 ----
# happy-path（gate PASS / stage 落 proposal+audit / adopt 提交双树）收拢进
# test_skill_evolution_end_to_end_loop；这里保留的是两道不同的安全边界拒绝。

def _audit_rows(cwd):
    audit = cwd / "pipeline-workspace/skill-evolution/audit.jsonl"
    if not audit.exists():
        return []
    return [json.loads(l) for l in audit.read_text(encoding="utf-8").splitlines() if l.strip()]


def test_skill_adopt_refuses_candidate_that_fails_gate(tmp_path):
    _init_repo(tmp_path)
    # 候选偷改 tests/ → adopt 重跑 gate 必须拒绝、绝不提交（最后一道兜底）。
    (tmp_path / "tests" / "test_ok.py").write_text(
        "def test_ok():\n    assert True  # weakened\n", encoding="utf-8")
    head_before = _git(tmp_path, "rev-parse", "HEAD").stdout.strip()

    r = _run(tmp_path, "skill-adopt", "--candidate", "evil")
    out = r.stdout + r.stderr
    assert r.returncode != 0
    assert "DENY" in out  # 是 adopt 的 gate 拒绝，不是命令不存在
    assert head_before == _git(tmp_path, "rev-parse", "HEAD").stdout.strip()  # 没提交


# ---- 端到端：串通确定性闭环（evolve 那步由测试模拟 LLM 输出）----

def test_skill_evolution_end_to_end_loop(tmp_path):
    _init_repo(tmp_path)
    db = tmp_path / "pipeline-workspace/state/study-kb.sqlite"
    state_store.init_db(db)
    state_store.register_source(db, "s1", domain="d", fmt="md")
    for i in range(2):  # 同一签名反复出现 = 值得沉淀
        state_store.add_review_proposal(db, "s1", target_path=f"x{i}.md",
                                        kind="broken-link", reason="dangling wikilink")

    # mine → backlog
    assert _run(tmp_path, "skill-mine").returncode == 0
    backlog = yaml.safe_load(
        (tmp_path / "pipeline-workspace/skill-evolution/backlog.yaml").read_text(encoding="utf-8"))
    assert backlog["backlog"][0]["signature"] == "broken-link"

    # evolve（模拟 LLM 的 bounded 编辑，双树同步）
    for tree in (".claude/skills/foo", ".agents/skills/foo"):
        (tmp_path / tree / "SKILL.md").write_text(
            "# foo\nv2：写 wikilink 前先确认目标页存在\n", encoding="utf-8")

    # gate：skill-only 候选放行（exit 0 + PASS）。
    rg = _run(tmp_path, "skill-gate", "--candidate", "fix-links")
    assert rg.returncode == 0, rg.stdout + rg.stderr
    assert "PASS" in (rg.stdout + rg.stderr)

    # stage：落 proposal.diff 供人审 + audit staged；线上不动（working tree 仍 dirty）。
    rs = _run(tmp_path, "skill-stage", "--candidate", "fix-links")
    assert rs.returncode == 0, rs.stdout + rs.stderr
    diff = tmp_path / "pipeline-workspace/skill-evolution/candidates/fix-links/proposal.diff"
    assert diff.exists() and diff.read_text(encoding="utf-8").strip(), "应落 proposal.diff 供人审"
    assert any(x["candidate"] == "fix-links" and x["event"] == "staged" for x in _audit_rows(tmp_path))
    assert _git(tmp_path, "status", "--porcelain").stdout.strip()

    # adopt：提交双树改动，working tree 干净（pipeline-workspace 已 gitignore）+ audit adopted。
    ra = _run(tmp_path, "skill-adopt", "--candidate", "fix-links")
    assert ra.returncode == 0, ra.stdout + ra.stderr
    events = {x["event"] for x in _audit_rows(tmp_path)}
    assert {"staged", "adopted"} <= events  # 全程留痕
    show = _git(tmp_path, "show", "--stat", "HEAD").stdout
    assert ".claude/skills/foo/SKILL.md" in show and ".agents/skills/foo/SKILL.md" in show
    assert not _git(tmp_path, "status", "--porcelain").stdout.strip()
