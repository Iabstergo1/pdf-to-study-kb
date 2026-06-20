from pathlib import Path
import importlib.util

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


concept_store = _load("concept_store")
workorder = _load("workorder")


def _vault_with_concepts(tmp_path):
    vault = tmp_path / "wiki"
    concept_store.create_concept(vault, domain="game-theory", name="信号博弈",
                                 aliases=["Signaling Game"])
    concept_store.create_concept(vault, domain="shared", name="期望效用")
    concept_store.create_concept(vault, domain="other-domain", name="无关概念")
    (vault / "overview.md").write_text("# overview\n", encoding="utf-8")
    return vault


def test_build_workorder_contract(tmp_path):
    vault = _vault_with_concepts(tmp_path)
    staging = tmp_path / "staging" / "wp"
    staging.mkdir(parents=True)
    wo = workorder.build_workorder(vault, source_id="wp", domain="game-theory",
                                   staging_dir=staging)
    assert wo["source_id"] == "wp" and wo["domain"] == "game-theory"
    assert "domains/game-theory/**" in wo["write_scope"]
    assert "sources/wp.md" in wo["write_scope"]
    assert len(wo["registry"]["hash"]) == 64
    assert wo["registry"]["scope"] == ["domain:game-theory", "shared"]
    # 概念快照：本域 + shared，全量；排除其它域
    cids = {e["canonical_id"] for e in wo["concept_pages_snapshot"]}
    assert "concept.game-theory.signaling-game" in cids
    assert "concept.shared.期望效用" in cids
    assert all("other-domain" not in c for c in cids)
    assert all(len(e["sha256"]) == 64 and e["managed_by"] for e in wo["concept_pages_snapshot"])
    # 其它目标页快照：已存在的 overview.md
    other_paths = {e["path"] for e in wo["other_pages_snapshot"]}
    assert "overview.md" in other_paths
    assert wo["on_failure"] == "route_to_review_queue"
    assert wo["source"]["processing_windows"].endswith("windows.jsonl")


def test_write_workorder_yaml_roundtrip(tmp_path):
    vault = _vault_with_concepts(tmp_path)
    staging = tmp_path / "staging" / "wp"
    staging.mkdir(parents=True)
    wo = workorder.build_workorder(vault, source_id="wp", domain="game-theory",
                                   staging_dir=staging)
    path = workorder.write_workorder(staging, wo)
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert loaded["registry"]["hash"] == wo["registry"]["hash"]


def test_registry_hash_matches_disk_after_build(tmp_path):
    vault = _vault_with_concepts(tmp_path)
    staging = tmp_path / "staging" / "wp"
    staging.mkdir(parents=True)
    wo = workorder.build_workorder(vault, source_id="wp", domain="game-theory",
                                   staging_dir=staging)
    ig = _load("ingest_guards")
    assert ig.registry_fresh(vault, wo["registry"]["hash"]) is True  # build 后磁盘即新鲜


def test_workorder_source_superset(tmp_path):
    vault = _vault_with_concepts(tmp_path)
    staging = tmp_path / "staging" / "wp"
    staging.mkdir(parents=True)
    wo = workorder.build_workorder(vault, source_id="wp", domain="game-theory",
                                   staging_dir=staging)
    src = wo["source"]
    # 旧键保留（向后兼容）
    assert src["text_md"].endswith("source.md")
    assert src["page_images_dir"].endswith("assets")
    assert src["processing_windows"].endswith("windows.jsonl")
    # 新键（超集）
    assert src["source_md"].endswith("source.md")
    assert src["blocks_jsonl"].endswith("blocks.jsonl")
    assert src["parse_report_json"].endswith("parse_report.json")
    assert src["chapters_json"].endswith("chapters.json")
    assert src["assets_dir"].endswith("assets")
    assert src["backend"] == "unknown"          # 无 parse_report.json（legacy staging）→ unknown


def test_workorder_backend_read_from_parse_report(tmp_path):
    vault = _vault_with_concepts(tmp_path)
    staging = tmp_path / "staging" / "wp"
    staging.mkdir(parents=True)
    (staging / "parse_report.json").write_text('{"selected_backend": "pymupdf"}', encoding="utf-8")
    wo = workorder.build_workorder(vault, source_id="wp", domain="game-theory",
                                   staging_dir=staging)
    assert wo["source"]["backend"] == "pymupdf"
