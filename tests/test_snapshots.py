import json
import shutil
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("snapshots", ROOT / "scripts" / "snapshots.py")
snapshots = importlib.util.module_from_spec(spec)
spec.loader.exec_module(snapshots)


def test_rollback_restores_modified_file(tmp_path):
    vault = tmp_path / "wiki"
    vault.mkdir()
    page = vault / "concept.md"
    page.write_text("ORIGINAL", encoding="utf-8")
    mani = snapshots.take_snapshot(tmp_path / "snapshots", source_id="s1", run_id="r1",
                                   files=[page], base_dir=vault)
    page.write_text("MERGED-BUT-FAILED", encoding="utf-8")
    snapshots.rollback(mani)
    assert page.read_text(encoding="utf-8") == "ORIGINAL"


def test_manifest_has_sha256_and_relpath(tmp_path):
    vault = tmp_path / "wiki"
    vault.mkdir()
    page = vault / "a.md"
    page.write_text("X", encoding="utf-8")
    mani = snapshots.take_snapshot(tmp_path / "snapshots", source_id="s1", run_id="r1",
                                   files=[page], base_dir=vault)
    data = json.loads(mani.read_text(encoding="utf-8"))
    assert data["entries"][0]["rel_path"] == "a.md"
    assert len(data["entries"][0]["sha256"]) == 64


def test_rollback_deletes_created_file(tmp_path):
    vault = tmp_path / "wiki"
    vault.mkdir()
    newp = vault / "new.md"  # 不存在
    mani = snapshots.take_snapshot(tmp_path / "snapshots", source_id="s1", run_id="r1",
                                   files=[newp], base_dir=vault)
    newp.write_text("CREATED-BUT-FAILED", encoding="utf-8")
    snapshots.rollback(mani)
    assert not newp.exists()


def test_rollback_recreates_deleted_parent_dir(tmp_path):
    vault = tmp_path / "wiki"
    sub = vault / "domains" / "game-theory" / "concepts"
    sub.mkdir(parents=True)
    page = sub / "x.md"
    page.write_text("ORIGINAL", encoding="utf-8")
    mani = snapshots.take_snapshot(tmp_path / "snapshots", source_id="s1", run_id="r1",
                                   files=[page], base_dir=vault)
    shutil.rmtree(vault / "domains")  # 目录也被删
    snapshots.rollback(mani)  # 必须重建父目录再恢复
    assert page.read_text(encoding="utf-8") == "ORIGINAL"
