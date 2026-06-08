import argparse
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))


def test_concurrent_units_share_sqlite_without_lock_errors(tmp_path):
    """并发鲁棒性：多个 unit 同时跑真实图、并发写同一对 SQLite（checkpointer+业务库），
    不应出现 'database is locked'；全部应正常发布并落库。"""
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace

    from llm_provider import FakeChatProvider
    from langgraph_worker import RuntimeDeps, UnitWorkerConfig, invoke_unit_graph
    from run_book import _enable_checkpoint_wal

    book_root = tmp_path / "books" / "concurrent-run"

    def make_unit(idx):
        return {
            "unit_id": f"U-001-0{idx}",
            "title": f"Unit {idx}",
            "source_scope": {"pages": [idx]},
            "risk_flags": [],
            "extraction_method": "text",
        }

    def make_context(idx):
        eid = f"E-U-001-0{idx}-0001"
        return {
            "unit_id": f"U-001-0{idx}",
            "source_pages": [idx],
            "evidence_candidates": [
                {"evidence_id": eid, "page": idx, "preview": "src", "sha256": "h", "evidence_type": "text"}
            ],
            "block_publish": False,
            "risk_flags": [],
        }

    def run_unit(idx):
        eid = f"E-U-001-0{idx}-0001"
        provider = FakeChatProvider([
            {"draft": f"---\nmanaged_by: pipeline\n---\n\n# Unit {idx}\n\nClaim [{eid}]"},
            {
                "decision": {"decision": "accept", "confidence": "high", "required_fixes": []},
                "report": "## 证据对照表\n\n| a | b |\n| --- | --- |\n\n## 公式风险清单\n\n| a | b |\n| --- | --- |",
            },
        ])
        deps = RuntimeDeps(
            provider=provider,
            provider_config=SimpleNamespace(provider="fake", model="m", review_model="r", planner_model="p"),
            config=UnitWorkerConfig(max_revision_retry=2),
            pdf_profile={"total_pages": 3, "pages": []},
            memory={},
            prepare_context_func=lambda b, u, p, _i=idx: make_context(_i),
            run_estimate={"tokens": 0, "cost": 0.0},
        )
        return invoke_unit_graph(book_root, "run-c", "concurrent-run", make_unit(idx), deps)

    _enable_checkpoint_wal(book_root)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(run_unit, [1, 2, 3]))

    assert all(r["status"] == "published" for r in results)
    for idx in (1, 2, 3):
        assert (book_root / "study-kb" / "Section-Lessons" / f"U-001-0{idx}.md").exists()
    assert (book_root / "pipeline-workspace" / "state" / "study-kb.sqlite").exists()


def test_one_unit_failure_does_not_abort_whole_book(monkeypatch, tmp_path):
    """健壮性：单个 unit 抛异常（如 LLM 非法 JSON）不能拖垮整本书。
    失败 unit 应被隔离、落 Review-Queue，其余 unit 正常完成，run 正常收尾。"""
    import json
    from types import SimpleNamespace

    import langgraph_worker
    import llm_provider
    import obsidian_indexes
    import pipeline
    from run_book import cmd_run_book

    book_root = tmp_path / "books" / "isolation-book"
    (book_root / "config").mkdir(parents=True)
    (book_root / "config" / "pdf-profile.yaml").write_text("source_pdf: x.pdf\npages: []\n", encoding="utf-8")
    units = [
        {"unit_id": "U-OK-1", "title": "ok1", "include": True, "review_status": "accepted",
         "source_scope": {"pages": [1]}, "extraction_method": "text", "risk_flags": []},
        {"unit_id": "U-FAIL", "title": "boom", "include": True, "review_status": "accepted",
         "source_scope": {"pages": [2]}, "extraction_method": "text", "risk_flags": []},
        {"unit_id": "U-OK-2", "title": "ok2", "include": True, "review_status": "accepted",
         "source_scope": {"pages": [3]}, "extraction_method": "text", "risk_flags": []},
    ]
    (book_root / "config" / "semantic-unit-plan.yaml").write_text(
        yaml.dump({"book_id": "isolation-book", "total_pages": 3, "units": units},
                  allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(pipeline, "find_book_root", lambda _book: book_root)
    monkeypatch.setattr(llm_provider, "load_provider_config",
                        lambda: SimpleNamespace(model="m", review_model="r", revise_model="rv", provider="fake"))
    monkeypatch.setattr(llm_provider, "create_provider", lambda cfg: SimpleNamespace())
    monkeypatch.setattr(obsidian_indexes, "build_obsidian_indexes", lambda *a, **k: None)

    def fake_invoke(book_root, run_id, book_id, unit, deps):
        if unit["unit_id"] == "U-FAIL":
            raise ValueError("LLM 输出不是合法 JSON: {截断")
        return {"status": "published"}

    monkeypatch.setattr(langgraph_worker, "invoke_unit_graph", fake_invoke)

    args = argparse.Namespace(
        book="isolation-book", executor="langgraph-worker", publish="accepted-only",
        section=None, dry_run=False, batch_size=5, max_revision_retry=2, concurrency=3,
    )
    # 不应抛异常
    cmd_run_book(args)

    # 失败 unit 落了 Review-Queue 提示
    rq = book_root / "study-kb" / "Review-Queue" / "U-FAIL.md"
    assert rq.exists()
    assert "run_failed" in rq.read_text(encoding="utf-8")

    # run summary 写出，3 个 unit 都有记录：2 published + 1 failed
    runs_dir = book_root / "pipeline-workspace" / "runs"
    summary_files = list(runs_dir.glob("*/semantic-run-summary.json"))
    assert summary_files
    summary = json.loads(summary_files[0].read_text(encoding="utf-8"))
    by_id = {r["unit_id"]: r["status"] for r in summary["results"]}
    assert by_id == {"U-OK-1": "published", "U-FAIL": "failed", "U-OK-2": "published"}


def test_run_book_langgraph_dry_run_uses_semantic_unit_plan(monkeypatch, capsys, tmp_path):
    import pipeline
    from run_book import cmd_run_book

    book_root = tmp_path / "books" / "phase-run-book"
    (book_root / "config").mkdir(parents=True)
    (book_root / "config" / "semantic-unit-plan.yaml").write_text(
        yaml.dump({
            "book_id": "phase-run-book",
            "total_pages": 2,
            "units": [
                {
                    "unit_id": "U-001-01",
                    "title": "Semantic Unit",
                    "include": True,
                    "review_status": "accepted",
                    "planner_confidence": "high",
                    "source_scope": {"pages": [1, 2]},
                    "formula_risk": "low",
                    "risk_flags": [],
                    "extraction_method": "text",
                }
            ],
        }, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    (book_root / "config" / "section-manifest.yaml").write_text(
        yaml.dump({
            "book_id": "phase-run-book",
            "sections": [{"id": "SEC-001", "status": "registered"}],
        }, allow_unicode=True),
        encoding="utf-8",
    )
    monkeypatch.setattr(pipeline, "find_book_root", lambda _book: book_root)

    args = argparse.Namespace(
        book="phase-run-book",
        executor="langgraph-worker",
        publish="accepted-only",
        section=None,
        resume=False,
        dry_run=True,
        batch_size=5,
        max_revision_retry=3,
    )
    cmd_run_book(args)

    output = capsys.readouterr().out
    assert "总 semantic units：1" in output
    assert "U-001-01" in output
    assert "SEC-001" not in output
