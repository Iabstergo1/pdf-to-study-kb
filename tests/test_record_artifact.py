from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("state_store", ROOT / "scripts" / "state_store.py")
state_store = importlib.util.module_from_spec(spec)
spec.loader.exec_module(state_store)


def test_record_and_list_artifact(tmp_path):
    db = tmp_path / "study-kb.sqlite"
    state_store.init_db(db)
    state_store.register_source(db, "s1", domain="d", fmt="pdf")
    aid = state_store.record_artifact(db, "s1", kind="source_md", path="staging/s1/source.md", sha256="a" * 64)
    rows = state_store.list_artifacts(db, "s1")
    assert aid > 0
    assert rows[0]["kind"] == "source_md"
    assert rows[0]["sha256"] == "a" * 64


def test_record_artifact_replaces_same_kind_path(tmp_path):
    db = tmp_path / "study-kb.sqlite"
    state_store.init_db(db)
    state_store.register_source(db, "s1", domain="d", fmt="pdf")
    state_store.record_artifact(db, "s1", kind="source_md", path="staging/s1/source.md", sha256="a" * 64)
    state_store.record_artifact(db, "s1", kind="source_md", path="staging/s1/source.md", sha256="b" * 64)
    rows = [r for r in state_store.list_artifacts(db, "s1") if r["kind"] == "source_md"]
    assert len(rows) == 1 and rows[0]["sha256"] == "b" * 64  # 幂等：同 (source,kind,path) 覆盖
