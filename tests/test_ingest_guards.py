import hashlib
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ingest_guards", ROOT / "scripts" / "ingest_guards.py")
ingest_guards = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ingest_guards)

SCOPE = ["domains/game-theory/**", "concepts/**", "sources/wp.md", "overview.md", "log.md"]


def test_in_write_scope_glob_and_exact():
    assert ingest_guards.in_write_scope("domains/game-theory/lessons/5.2.md", SCOPE)
    assert ingest_guards.in_write_scope("concepts/_registry.yaml", SCOPE)  # glob 命中（派生文件禁写在协议层）
    assert ingest_guards.in_write_scope("sources/wp.md", SCOPE)
    assert not ingest_guards.in_write_scope("sources/other.md", SCOPE)
    assert not ingest_guards.in_write_scope("domains/math-econ/lessons/1.md", SCOPE)
    assert not ingest_guards.in_write_scope("index.generated.md", SCOPE)


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_can_overwrite_three_conditions(tmp_path):
    page = tmp_path / "domains" / "d" / "concepts" / "x.md"
    page.parent.mkdir(parents=True)
    page.write_text("V1", encoding="utf-8")
    snap = [{"path": "domains/d/concepts/x.md", "sha256": _sha(page), "managed_by": "pipeline"}]
    ok, reason = ingest_guards.can_overwrite(tmp_path, "domains/d/concepts/x.md", snap)
    assert ok, reason
    # 条件③破坏：磁盘 hash 变了
    page.write_text("V2-human-edited", encoding="utf-8")
    ok, reason = ingest_guards.can_overwrite(tmp_path, "domains/d/concepts/x.md", snap)
    assert not ok and "hash" in reason
    # 条件②破坏：managed_by human
    snap2 = [{"path": "domains/d/concepts/x.md", "sha256": _sha(page), "managed_by": "human"}]
    ok, reason = ingest_guards.can_overwrite(tmp_path, "domains/d/concepts/x.md", snap2)
    assert not ok and "human" in reason
    # 条件①破坏：不在 snapshot
    ok, reason = ingest_guards.can_overwrite(tmp_path, "domains/d/concepts/x.md", [])
    assert not ok and "snapshot" in reason


def test_can_overwrite_new_page_allowed(tmp_path):
    ok, reason = ingest_guards.can_overwrite(tmp_path, "domains/d/lessons/new.md", [])
    assert ok and reason == "new page"


def test_can_overwrite_pipeline_page_created_this_run(tmp_path):
    # resolve-concept 在 ingest 期间新建的概念页：磁盘上存在、managed_by: pipeline、不在 workorder
    # 快照 → 放行（workorder 已快照所有既有页，故"不在快照"只能是本次新建）。
    page = tmp_path / "domains" / "d" / "concepts" / "new-concept.md"
    page.parent.mkdir(parents=True)
    page.write_text("---\nmanaged_by: pipeline\ntype: concept\n---\n# X\n", encoding="utf-8")
    ok, reason = ingest_guards.can_overwrite(tmp_path, "domains/d/concepts/new-concept.md", [])
    assert ok, reason
    # 但 human 页不在快照仍拒（护住人维护页）
    hpage = tmp_path / "domains" / "d" / "concepts" / "human.md"
    hpage.write_text("---\nmanaged_by: human\n---\n# H\n", encoding="utf-8")
    ok, reason = ingest_guards.can_overwrite(tmp_path, "domains/d/concepts/human.md", [])
    assert not ok and "snapshot" in reason
    # 无 frontmatter（未知）也拒
    npage = tmp_path / "domains" / "d" / "concepts" / "nofm.md"
    npage.write_text("no frontmatter", encoding="utf-8")
    ok, reason = ingest_guards.can_overwrite(tmp_path, "domains/d/concepts/nofm.md", [])
    assert not ok and "snapshot" in reason


def test_in_write_scope_rejects_traversal_and_absolute():
    # P0 回归（2026-06-11 P9 code review，报告已清理、见 git 历史）：路径穿越/绝对路径不得命中写入边界
    assert not ingest_guards.in_write_scope("domains/misc/../../outside.md", ["domains/misc/**"])
    assert not ingest_guards.in_write_scope("domains\\misc\\..\\..\\outside.md", ["domains/misc/**"])
    assert not ingest_guards.in_write_scope("../escape.md", ["**"])
    assert not ingest_guards.in_write_scope("/etc/passwd", ["**"])
    assert not ingest_guards.in_write_scope("C:/temp/x.md", ["**"])
    # 含 ./ 的等价路径归一化后仍正常匹配
    assert ingest_guards.in_write_scope("domains/misc/./lessons/a.md", ["domains/misc/**"])


def test_can_overwrite_rejects_unsafe_path(tmp_path):
    # P0 回归：can_overwrite 对逃出 vault 的路径必须拒绝（即使目标"不存在"）
    ok, reason = ingest_guards.can_overwrite(tmp_path, "domains/misc/../../outside.md", [])
    assert not ok
    ok, reason = ingest_guards.can_overwrite(tmp_path, "C:/temp/outside.md", [])
    assert not ok


def test_registry_fresh(tmp_path):
    reg = tmp_path / "concepts" / "_registry.yaml"
    reg.parent.mkdir(parents=True)
    reg.write_text("a: 1\n", encoding="utf-8")
    assert ingest_guards.registry_fresh(tmp_path, _sha(reg)) is True
    assert ingest_guards.registry_fresh(tmp_path, "0" * 64) is False
    # registry 不存在：期望空 hash 才算新鲜
    assert ingest_guards.registry_fresh(tmp_path / "no-vault", "") is True
    assert ingest_guards.registry_fresh(tmp_path / "no-vault", "0" * 64) is False
