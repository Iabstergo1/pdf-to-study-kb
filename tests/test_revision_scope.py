"""R-08 修订面推广：非采纳来源准入、射程、identity 锚、scope digest 与射程自封闭。

fast 层：直接调用 legacy_revision 内部函数，不 spawn subprocess；工作区全部在
tmp_path 内，状态库用 state_store.init_db + 直插行构造（夹具只负责摆出状态形态，
不测状态机本身）。
"""

import hashlib
import pathlib
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import legacy_revision
import state_store

INGEST = "demo-ingest-source"
REUSE = "demo-reuse-source"
ADOPT = "demo-adopt-source"


def _sha_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def _sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_page(path, *, source, rel, status="published", managed_by="pipeline"):
    slug = pathlib.Path(rel).stem
    meta = {
        "aliases": [slug],
        "canonical_id": f"concept.demo.{slug}",
        "canonical_name": slug,
        "citations": [],
        "domain": "demo",
        "managed_by": managed_by,
        "page_path": rel,
        "scope": "domain",
        "source_refs": [{"source": source, "sections": ["p1"]}],
        "status": status,
        "type": "concept",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = yaml.safe_dump(meta, allow_unicode=True, sort_keys=True,
                        default_flow_style=False)
    body = "demo page body used for R-08 admission tests." * 4
    path.write_text(f"---\n{fm}---\n{body}\n", encoding="utf-8", newline="\n")


def _write_source_page(vault, source, *, manifest_sha, domain):
    rel = f"sources/{source}.md"
    meta = {
        "adoption_manifest_sha256": manifest_sha,
        "aliases": [],
        "canonical_id": f"source.{source}",
        "canonical_name": "Demo legacy",
        "domain": domain,
        "format": "legacy-vault",
        "managed_by": "pipeline",
        "page_path": rel,
        "scope": "source",
        "source_id": source,
        "status": "published",
        "title": "Demo legacy",
        "type": "source",
    }
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = yaml.safe_dump(meta, allow_unicode=True, sort_keys=True,
                        default_flow_style=False)
    path.write_text(f"---\n{fm}---\nbody\n", encoding="utf-8", newline="\n")
    return path


def _ingest_workspace(tmp_path):
    src = INGEST
    vault = tmp_path / "wiki"
    alpha = vault / "domains/demo/concepts/alpha.md"
    _write_page(alpha, source=src, rel="domains/demo/concepts/alpha.md")
    beta = vault / "domains/demo/concepts/beta.md"
    _write_page(beta, source="other-source", rel="domains/demo/concepts/beta.md")
    staging = tmp_path / "pipeline-workspace" / "staging" / src
    staging.mkdir(parents=True, exist_ok=True)
    wo = {
        "source": src,
        "source_id": src,
        "domain": "demo",
        "concept_pages_snapshot": [{
            "canonical_id": "concept.demo.alpha",
            "managed_by": "pipeline",
            "path": "domains/demo/concepts/alpha.md",
            "sha256": _sha(alpha),
        }],
        "write_scope": ["domains/demo/concepts/**"],
    }
    wo_path = staging / "workorder.yaml"
    wo_path.write_text(yaml.safe_dump(wo, allow_unicode=True, sort_keys=True),
                       encoding="utf-8", newline="\n")
    db = tmp_path / "pipeline-workspace" / "state" / "study-kb.sqlite"
    state_store.init_db(db)
    con = sqlite3.connect(db)
    now = _now()
    con.execute(
        "INSERT INTO sources(source_id,domain,format,added_at,current_stage,current_status)"
        " VALUES (?,?,?,?,?,?)",
        (src, "demo", "pdf", now, "lint", "published"))
    con.execute(
        "INSERT INTO source_stage_runs(source_id,stage,status,started_at,input_hash,"
        "finished_at,output_hash) VALUES (?,?,?,?,?,?,?)",
        (src, "lint", "done", now, "a" * 64, now, "a" * 64))
    con.execute(
        "INSERT INTO artifacts(source_id,kind,path,sha256,created_at) VALUES (?,?,?,?,?)",
        (src, "workorder", str(wo_path), _sha(wo_path), now))
    con.execute(
        "INSERT INTO work_orders(source_id,path,registry_hash,write_scope_json,created_at,round)"
        " VALUES (?,?,?,?,?,?)",
        (src, "workorder.yaml", None, "{}", now, 1))
    con.execute(
        "INSERT INTO ingest_progress(source_id,window_id,input_hash,started_at,status,round)"
        " VALUES (?,?,?,?,?,?)",
        (src, "w0000", "b" * 64, now, "finished", 1))
    con.execute(
        "INSERT INTO window_reads(source_id,window_id,read_at,round) VALUES (?,?,?,?)",
        (src, "w0000", now, 1))
    con.commit()
    con.close()
    return tmp_path


def _reuse_workspace(tmp_path):
    src = REUSE
    vault = tmp_path / "wiki"
    alpha = vault / "domains/demo/concepts/alpha.md"
    _write_page(alpha, source=src, rel="domains/demo/concepts/alpha.md")
    reuses = tmp_path / "pipeline-workspace" / "reuses" / src
    reuses.mkdir(parents=True, exist_ok=True)
    manifest = {
        "domain": "demo",
        "format": "external-vault-reuse",
        "mapped_target_count": 1,
        "source_id": src,
        "target_pages": [{
            "path": "domains/demo/concepts/alpha.md",
            "sha256": _sha(alpha),
            "mapped_origin_count": 1,
        }],
        "version": 1,
    }
    manifest_path = reuses / "manifest.json"
    manifest_path.write_text(
        legacy_revision._json_bytes(manifest).decode("utf-8"),
        encoding="utf-8", newline="\n")
    db = tmp_path / "pipeline-workspace" / "state" / "study-kb.sqlite"
    state_store.init_db(db)
    con = sqlite3.connect(db)
    now = _now()
    con.execute(
        "INSERT INTO sources(source_id,domain,format,added_at,current_stage,current_status)"
        " VALUES (?,?,?,?,?,?)",
        (src, "demo", "external-vault-reuse", now, "reused", "published"))
    con.execute(
        "INSERT INTO source_stage_runs(source_id,stage,status,started_at,input_hash,"
        "finished_at,output_hash) VALUES (?,?,?,?,?,?,?)",
        (src, "reused", "done", now, "c" * 64, now, "c" * 64))
    con.execute(
        "INSERT INTO artifacts(source_id,kind,path,sha256,created_at) VALUES (?,?,?,?,?)",
        (src, "reuse_evidence", str(manifest_path), _sha(manifest_path), now))
    con.commit()
    con.close()
    return tmp_path


def _adoption_workspace(tmp_path):
    src = ADOPT
    vault = tmp_path / "wiki"
    alpha = vault / "domains/demo/concepts/alpha.md"
    _write_page(alpha, source=src, rel="domains/demo/concepts/alpha.md")
    evidence_dir = tmp_path / "pipeline-workspace" / "adoptions" / src
    (evidence_dir / "files" / "domains" / "demo" / "concepts").mkdir(
        parents=True, exist_ok=True)
    (evidence_dir / "files" / "domains" / "demo" / "concepts" / "alpha.md").write_bytes(
        alpha.read_bytes())
    manifest = {
        "baseline_archive": str(tmp_path / "legacy-baseline.zip"),
        "baseline_sha256": "0" * 64,
        "domain": "demo",
        "format": "legacy-vault",
        "pages": [{
            "path": "domains/demo/concepts/alpha.md",
            "sha256": _sha(alpha),
            "size": alpha.stat().st_size,
        }],
        "source_id": src,
        "title": "Demo legacy",
        "version": 1,
    }
    manifest_path = evidence_dir / "manifest.json"
    manifest_path.write_text(
        legacy_revision._json_bytes(manifest).decode("utf-8"),
        encoding="utf-8", newline="\n")
    manifest_sha = _sha(manifest_path)
    _write_source_page(vault, src, manifest_sha=manifest_sha,
                       domain="demo")
    db = tmp_path / "pipeline-workspace" / "state" / "study-kb.sqlite"
    state_store.init_db(db)
    con = sqlite3.connect(db)
    now = _now()
    con.execute(
        "INSERT INTO sources(source_id,domain,format,added_at,current_stage,current_status)"
        " VALUES (?,?,?,?,?,?)",
        (src, "demo", "legacy-vault", now, "adopted", "published"))
    con.execute(
        "INSERT INTO source_stage_runs(source_id,stage,status,started_at,input_hash,"
        "finished_at,output_hash) VALUES (?,?,?,?,?,?,?)",
        (src, "adopted", "done", now, manifest_sha, now, manifest_sha))
    con.execute(
        "INSERT INTO artifacts(source_id,kind,path,sha256,created_at) VALUES (?,?,?,?,?)",
        (src, "adoption_evidence", str(manifest_path), manifest_sha, now))
    con.commit()
    con.close()
    return tmp_path


def _request_file(tmp_path, *, pages, source):
    payload = {
        "version": 1,
        "source_id": source,
        "valid_until": (datetime.now(timezone.utc) + timedelta(days=7))
        .isoformat(timespec="seconds"),
        "mode": "edit",
        "pages": pages,
    }
    path = tmp_path / "request.yaml"
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
                    encoding="utf-8", newline="\n")
    return path


def _request_page(rel, *, fu=None):
    page = {
        "path": rel,
        "reason": "R-08 admission test",
        "evidence": [{
            "citation": {
                "source": "Example docs",
                "title": "Example",
                "url": "https://example.invalid/",
                "accessed_on": "2030-01-01",
            },
            "supports": "test",
        }],
        "citation_removals": [],
    }
    if fu is not None:
        page["frontmatter_updates"] = fu
    return page


ALPHA = "domains/demo/concepts/alpha.md"
BETA = "domains/demo/concepts/beta.md"


def test_ingest_owned_page_dry_run_passes(tmp_path):
    _ingest_workspace(tmp_path)
    request = _request_file(tmp_path, source=INGEST,
                            pages=[_request_page(ALPHA)])
    result = legacy_revision.run(workspace=tmp_path, source=INGEST,
                                 request_path=request)
    assert result["phase"] == "planned"
    assert result["pages"] == [ALPHA]


def test_ingest_unowned_page_rejected_with_scope_error(tmp_path):
    _ingest_workspace(tmp_path)
    request = _request_file(tmp_path, source=INGEST,
                            pages=[_request_page(BETA)])
    with pytest.raises(legacy_revision.LegacyRevisionError) as excinfo:
        legacy_revision.run(workspace=tmp_path, source=INGEST,
                            request_path=request)
    assert "not owned by source" in str(excinfo.value)


def test_ingest_unsettled_running_window_rejected_with_state_error(tmp_path):
    _ingest_workspace(tmp_path)
    db = tmp_path / "pipeline-workspace" / "state" / "study-kb.sqlite"
    con = sqlite3.connect(db)
    con.execute(
        "UPDATE ingest_progress SET status='running' WHERE source_id=? AND window_id=?",
        (INGEST, "w0000"))
    con.commit()
    con.close()
    request = _request_file(tmp_path, source=INGEST,
                            pages=[_request_page(ALPHA)])
    with pytest.raises(legacy_revision.LegacyRevisionError) as excinfo:
        legacy_revision.run(workspace=tmp_path, source=INGEST,
                            request_path=request)
    assert "in-progress ingest windows" in str(excinfo.value)


def test_ingest_active_vault_lock_rejected(tmp_path):
    _ingest_workspace(tmp_path)
    db = tmp_path / "pipeline-workspace" / "state" / "study-kb.sqlite"
    now = _now()
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO source_locks(scope,holder,pid,started_at,heartbeat_at)"
        " VALUES ('vault',?,?,?,?)",
        ("another-command", 1234, now, now))
    con.commit()
    con.close()
    request = _request_file(tmp_path, source=INGEST,
                            pages=[_request_page(ALPHA)])
    with pytest.raises(legacy_revision.LegacyRevisionError) as excinfo:
        legacy_revision.run(workspace=tmp_path, source=INGEST,
                            request_path=request)
    assert "active vault lock" in str(excinfo.value)


def test_scope_digest_changes_when_owned_pages_change(tmp_path):
    _ingest_workspace(tmp_path)
    digest_before = legacy_revision._scope_digest(tmp_path / "wiki", INGEST)
    gamma = tmp_path / "wiki" / "domains/demo/concepts/gamma.md"
    _write_page(gamma, source=INGEST, rel="domains/demo/concepts/gamma.md")
    digest_after_add = legacy_revision._scope_digest(tmp_path / "wiki", INGEST)
    assert digest_before != digest_after_add
    alpha = tmp_path / "wiki" / ALPHA
    alpha.write_text(alpha.read_text() + "edit", encoding="utf-8")
    digest_after_edit = legacy_revision._scope_digest(tmp_path / "wiki", INGEST)
    assert digest_after_add != digest_after_edit


def test_source_refs_cannot_be_expanded_via_revision(tmp_path):
    """评审补钉：经修订改 source_refs 必须被拒（一次修订无法给自己扩大未来射程）。"""
    _ingest_workspace(tmp_path)
    page = _request_page(
        ALPHA,
        fu={"source_refs": {"add": [{"source": "sneaky", "sections": ["p1"]}]}})
    request = _request_file(tmp_path, source=INGEST, pages=[page])
    with pytest.raises(legacy_revision.LegacyRevisionError) as excinfo:
        legacy_revision.run(workspace=tmp_path, source=INGEST,
                            request_path=request)
    assert "only supports aliases" in str(excinfo.value)


def test_reuse_owned_page_dry_run_passes(tmp_path):
    _reuse_workspace(tmp_path)
    request = _request_file(tmp_path, source=REUSE,
                            pages=[_request_page(ALPHA)])
    result = legacy_revision.run(workspace=tmp_path, source=REUSE,
                                 request_path=request)
    assert result["phase"] == "planned"
    assert result["pages"] == [ALPHA]


def test_adoption_identity_keeps_manifest_anchor_and_no_scope_digest(tmp_path):
    """硬约束钉死：采纳 identity 键集合与锚保持不变（scope_digest 缺席）。"""
    _adoption_workspace(tmp_path)
    request_file = _request_file(tmp_path, source=ADOPT,
                                 pages=[_request_page(ALPHA)])
    normal, _raw, request_sha = legacy_revision._validate_request(
        request_file, ADOPT)
    context = legacy_revision._revision_context(tmp_path, ADOPT)
    assert context["kind"] == "adoption"
    authorization, _candidates = legacy_revision._build_authorization(
        context, normal, request_sha)
    assert "adoption_manifest_sha256" in authorization
    assert "scope_digest" not in authorization
    identity_keys = {
        "adoption_manifest_sha256", "contract_version", "mode", "pages",
        "request_sha256", "revert_operation", "source_id", "valid_until",
    }
    assert set(authorization) == identity_keys | {"operation_id"}


def test_scope_recheck_rejects_a_page_that_left_the_source_after_signing(tmp_path):
    """切换前必须复验授权页仍属本源（prepush-audit-2026-08-08 F3 的真实缺口）。

    签发时查过一次 `rel in scope_paths`，但 prepare→commit 之间按设计要跨人工编辑
    候选的时间；期间该页可能不再带本来源的 source_refs。此前无人复验：`scope_digest`
    只在签发期算一次、运行期从不比对，运行期真正被复验的锚是 workorder artifact 字节，
    与"当前哪些页带 source_refs"无关。
    """
    _ingest_workspace(tmp_path)
    request_file = _request_file(tmp_path, source=INGEST,
                                 pages=[_request_page(ALPHA)])
    normal, _raw, request_sha = legacy_revision._validate_request(request_file, INGEST)
    context = legacy_revision._revision_context(tmp_path, INGEST)
    authorization, _candidates = legacy_revision._build_authorization(
        context, normal, request_sha)
    # 签发那一刻是通过的
    legacy_revision._assert_scope_still_owns(context, authorization)

    # 之后该页改归另一个来源（source_refs 不再含 INGEST）
    alpha = tmp_path / "wiki" / ALPHA
    _write_page(alpha, source="someone-else", rel=ALPHA)
    fresh = legacy_revision._revision_context(tmp_path, INGEST)

    with pytest.raises(legacy_revision.LegacyRevisionError) as excinfo:
        legacy_revision._assert_scope_still_owns(fresh, authorization)
    message = str(excinfo.value)
    assert "left the source scope since signing" in message
    assert ALPHA in message
    assert INGEST in message


def test_scope_recheck_passes_when_unrelated_owned_pages_change(tmp_path):
    """复验只看"授权页是否仍属本源"，不看内容漂移。

    刻意**不**重算 scope_digest 比对：那会让任一无关归属页的正常编辑都炸掉操作，
    而 prepare→commit 之间跨人工编辑是这条合同的设计前提。
    """
    _ingest_workspace(tmp_path)
    request_file = _request_file(tmp_path, source=INGEST,
                                 pages=[_request_page(ALPHA)])
    normal, _raw, request_sha = legacy_revision._validate_request(request_file, INGEST)
    context = legacy_revision._revision_context(tmp_path, INGEST)
    authorization, _candidates = legacy_revision._build_authorization(
        context, normal, request_sha)

    gamma = tmp_path / "wiki" / "domains/demo/concepts/gamma.md"
    _write_page(gamma, source=INGEST, rel="domains/demo/concepts/gamma.md")
    fresh = legacy_revision._revision_context(tmp_path, INGEST)
    assert fresh["scope_digest"] != context["scope_digest"], "前提：digest 确实变了"

    legacy_revision._assert_scope_still_owns(fresh, authorization)  # 不得抛


def test_ingest_authorization_uses_scope_digest_anchor(tmp_path):
    _ingest_workspace(tmp_path)
    request_file = _request_file(tmp_path, source=INGEST,
                                 pages=[_request_page(ALPHA)])
    normal, _raw, request_sha = legacy_revision._validate_request(
        request_file, INGEST)
    context = legacy_revision._revision_context(tmp_path, INGEST)
    assert context["kind"] == "ingest"
    authorization, _candidates = legacy_revision._build_authorization(
        context, normal, request_sha)
    assert "scope_digest" in authorization
    assert "adoption_manifest_sha256" not in authorization
    assert authorization["scope_digest"] == context["scope_digest"]


def test_switch_ledgers_adoption_requires_zero(tmp_path):
    """R-08 补正：采纳来源切换期三台账必须全零（今天行为不变）。"""
    _adoption_workspace(tmp_path)
    context = legacy_revision._revision_context(tmp_path, ADOPT)
    assert context["kind"] == "adoption"
    legacy_revision._verify_switch_ledgers(
        context, {"source_id": ADOPT})
    db = tmp_path / "pipeline-workspace" / "state" / "study-kb.sqlite"
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO work_orders(source_id,path,registry_hash,write_scope_json,"
        "created_at,round) VALUES (?,?,?,?,?,?)",
        (ADOPT, "x.yaml", None, "{}", "2026-08-07T00:00:00+00:00", 1))
    con.commit()
    con.close()
    with pytest.raises(legacy_revision.LegacyRevisionError) as excinfo:
        legacy_revision._verify_switch_ledgers(
            context, {"source_id": ADOPT})
    assert "ingest ledger changed during switch" in str(excinfo.value)


def test_switch_ledgers_ingest_settled_shape(tmp_path):
    """R-08 补正：摄取来源切换期要求落定形态（work_orders=1、窗口全 finished）。"""
    _ingest_workspace(tmp_path)
    context = legacy_revision._revision_context(tmp_path, INGEST)
    assert context["kind"] == "ingest"
    legacy_revision._verify_switch_ledgers(
        context, {"source_id": INGEST})
    db = tmp_path / "pipeline-workspace" / "state" / "study-kb.sqlite"
    con = sqlite3.connect(db)
    con.execute(
        "UPDATE ingest_progress SET status='running' WHERE source_id=? "
        "AND window_id=?", (INGEST, "w0000"))
    con.commit()
    con.close()
    with pytest.raises(legacy_revision.LegacyRevisionError) as excinfo:
        legacy_revision._verify_switch_ledgers(
            context, {"source_id": INGEST})
    assert "windows changed during switch" in str(excinfo.value)
