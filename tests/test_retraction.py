"""retract-source 的纯函数层：撤库分类 / 证据导出与核验 / 精确删除。

设计动机（2026-07-17 mysql 事件）：下架动作曾直接清掉三张账本表，审计结论的原始证据
随处置消失。retraction 把「先导出证据包并核验完整 → 才允许删除 → 重建派生层」固化为
确定性顺序；本文件测文件系统层的纯函数，CLI 编排在 test_doctor_cli.py。
"""
from pathlib import Path
import importlib.util
import json

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


mdpage = _load("mdpage")
retraction = _load("retraction")


def _page(vault, rel, meta, body="正文。\n"):
    mdpage.write_page(Path(vault) / rel, meta, body)


def _vault_for_classify(tmp_path):
    v = tmp_path / "wiki"
    # 独占本源：规范台账页 + 域下错位重复台账页（mysql 事件原样）
    _page(v, "sources/s1.md", {"type": "source", "status": "published",
                               "managed_by": "pipeline", "source_id": "s1"})
    _page(v, "domains/d/sources/s1.md", {"type": "source", "status": "published",
                                         "managed_by": "pipeline", "source_id": "s1"})
    # 独占：source_refs 只含 s1
    _page(v, "domains/d/concepts/甲.md",
          {"type": "concept", "status": "published", "managed_by": "pipeline",
           "canonical_id": "concept.d.jia", "canonical_name": "甲", "domain": "d",
           "source_refs": [{"source": "s1", "sections": ["1"]}]})
    # 共享：s1 + s2 → 不删，报告
    _page(v, "domains/d/concepts/共享.md",
          {"type": "concept", "status": "published", "managed_by": "pipeline",
           "canonical_id": "concept.d.gongxiang", "canonical_name": "共享", "domain": "d",
           "source_refs": [{"source": "s1"}, {"source": "s2"}]})
    # 人工页：即便独占本源也永不动
    _page(v, "domains/d/concepts/人工.md",
          {"type": "concept", "status": "published", "managed_by": "human",
           "canonical_id": "concept.d.rengong", "canonical_name": "人工", "domain": "d",
           "source_refs": [{"source": "s1"}]})
    # lesson 靠 source 字段归属
    _page(v, "domains/d/lessons/课.md",
          {"type": "lesson", "status": "published", "managed_by": "pipeline", "source": "s1"})
    # 台账孤儿：无任何 frontmatter 归属，但在本源 write ledger
    _page(v, "topics/孤儿.md",
          {"type": "topic", "status": "published", "managed_by": "pipeline", "title": "孤儿"})
    # 他源页：完全不该出现在任何清单
    _page(v, "topics/他源.md",
          {"type": "topic", "status": "published", "managed_by": "pipeline", "title": "他源",
           "source_refs": [{"source": "s2"}]})
    return v


def test_classify_pages_sole_shared_human_ledger(tmp_path):
    v = _vault_for_classify(tmp_path)
    res = retraction.classify_pages(v, "s1", written_paths={"topics/孤儿.md"})
    assert {e["path"] for e in res["delete"]} == {
        "sources/s1.md", "domains/d/sources/s1.md", "domains/d/concepts/甲.md",
        "domains/d/lessons/课.md", "topics/孤儿.md"}
    assert {e["path"] for e in res["keep_shared"]} == {"domains/d/concepts/共享.md"}
    assert {e["path"] for e in res["keep_human"]} == {"domains/d/concepts/人工.md"}
    all_listed = {e["path"] for k in ("delete", "keep_shared", "keep_human") for e in res[k]}
    assert "topics/他源.md" not in all_listed  # 他源页绝不入清单


def test_classify_human_wins_over_ledger(tmp_path):
    # 人工页即使出现在 write ledger 也不删（覆盖保护同一原则：绝不动 human 页）
    v = tmp_path / "wiki"
    _page(v, "topics/人工台账.md",
          {"type": "topic", "status": "published", "managed_by": "human", "title": "人工台账",
           "source_refs": [{"source": "s1"}]})
    res = retraction.classify_pages(v, "s1", written_paths={"topics/人工台账.md"})
    assert res["delete"] == []
    assert {e["path"] for e in res["keep_human"]} == {"topics/人工台账.md"}


def test_export_evidence_manifest_covers_pages_db_and_plan(tmp_path):
    # P1-4（Codex 2026-07-18）：manifest 必须覆盖全部 payload——页面、七表 DB JSON、plan。
    # 只核页面时，篡改 db/*.json 或 plan.json 仍"核验通过"，证据包完整性承诺过强。
    v = _vault_for_classify(tmp_path)
    dest = tmp_path / "evidence"
    paths = ["sources/s1.md", "domains/d/concepts/甲.md"]
    db_rows = {"ingest_progress": [{"window_id": "w0000", "write_set_json": "[\"a.md\"]"}],
               "window_reads": []}
    summary = retraction.export_evidence(v, dest, paths, db_rows=db_rows,
                                         plan={"source_id": "s1", "delete": paths})
    for rel in paths:                       # 页面逐字节复制
        assert (dest / "pages" / rel).read_bytes() == (Path(v) / rel).read_bytes()
    manifest = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
    assert {m["path"] for m in manifest["pages"]} == set(paths)
    covered = {f["path"] for f in manifest["files"]}
    assert covered == {"db/ingest_progress.json", "db/window_reads.json", "plan.json"}
    import hashlib
    for f in manifest["files"]:             # 每个 payload 都有可复核哈希
        assert f["sha256"] == hashlib.sha256((dest / f["path"]).read_bytes()).hexdigest()
    assert summary["pages"] == len(paths)
    assert retraction.verify_evidence(dest) == []
    # 篡改页面副本 → 报该页
    (dest / "pages" / "sources/s1.md").write_text("tampered", encoding="utf-8")
    assert "sources/s1.md" in retraction.verify_evidence(dest)
    # 篡改 DB 导出（Codex 复现：改成空数组曾静默通过）→ 报该文件
    (dest / "db" / "ingest_progress.json").write_text("[]", encoding="utf-8")
    assert "db/ingest_progress.json" in retraction.verify_evidence(dest)
    # 篡改 plan → 报 plan
    (dest / "plan.json").write_text("{}", encoding="utf-8")
    assert "plan.json" in retraction.verify_evidence(dest)
    # 删除清单内文件 → 报缺失；塞入清单外文件 → 报 extra
    (dest / "db" / "window_reads.json").unlink()
    assert "db/window_reads.json" in retraction.verify_evidence(dest)
    (dest / "pages" / "sneaked.md").write_text("x", encoding="utf-8")
    assert any(x.startswith("extra:") and "sneaked.md" in x
               for x in retraction.verify_evidence(dest))


def test_verify_sources_match_detects_vault_drift(tmp_path):
    # P1-3 配套（删除前最后核对）：证据导出后、删除前，vault 源文件若被并发改动/删除，
    # 必须在第一项删除前整体中止——本函数给 cmd 做这道对账。
    v = _vault_for_classify(tmp_path)
    dest = tmp_path / "evidence"
    paths = ["sources/s1.md", "domains/d/concepts/甲.md"]
    retraction.export_evidence(v, dest, paths, db_rows={}, plan={"source_id": "s1"})
    assert retraction.verify_sources_match(v, dest) == []
    (Path(v) / "sources/s1.md").write_text("drifted", encoding="utf-8")
    assert retraction.verify_sources_match(v, dest) == ["sources/s1.md"]
    (Path(v) / "domains/d/concepts/甲.md").unlink()
    assert set(retraction.verify_sources_match(v, dest)) == {"sources/s1.md",
                                                             "domains/d/concepts/甲.md"}


def test_delete_pages_removes_and_prunes_empty_domain_dirs(tmp_path):
    v = _vault_for_classify(tmp_path)
    doomed = ["domains/d/sources/s1.md", "domains/d/lessons/课.md"]
    n = retraction.delete_pages(v, doomed)
    assert n == 2
    assert not (v / "domains/d/sources").exists()      # 空目录剪掉
    assert not (v / "domains/d/lessons").exists()
    assert (v / "domains/d/concepts").exists()          # 非空目录保留
    assert (v / "sources/s1.md").exists()               # 未列出的页不动
