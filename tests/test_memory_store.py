import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))


class FakeCompactionProvider:
    def __init__(self, summary):
        self.summary = summary
        self.calls = []

    def chat_json(self, system, user, model=None, temperature=None):
        self.calls.append({
            "system": system,
            "user": user,
            "model": model,
            "temperature": temperature,
        })
        return {"running_book_summary": self.summary}


def test_update_memory_merges_indexes_and_keeps_recent_two(tmp_path):
    from business_db import initialize_business_db
    from memory_store import DEFAULT_MEMORY, update_memory

    book_root = tmp_path / "books" / "phase7-book"
    initialize_business_db(book_root)
    memory = DEFAULT_MEMORY.copy()

    for idx in range(3):
        memory = update_memory(
            book_root,
            run_id="run-1",
            unit_id=f"U-001-0{idx + 1}",
            memory=memory,
            unit_result={
                "summary": f"summary {idx + 1}",
                "concepts": [{"term": "Nash", "definition": "first definition"}],
                "symbols": [{"symbol": "x", "meaning": "state"}],
                "evidence": [
                    {
                        "evidence_id": f"E-{idx + 1}",
                        "claim": f"claim {idx + 1}",
                        "page": idx + 1,
                        "source_heading": "h",
                        "evidence_type": "text",
                        "payload": {"preview": "p"},
                    }
                ],
            },
        )

    assert memory["running_book_summary"].count("summary") == 3
    assert memory["concept_index"]["Nash"]["definition"] == "first definition"
    assert memory["concept_index"]["Nash"]["units"] == ["U-001-01", "U-001-02", "U-001-03"]
    assert memory["symbol_index"]["x"]["units"] == ["U-001-01", "U-001-02", "U-001-03"]
    assert [item["unit_id"] for item in memory["recent_accepted"]] == ["U-001-02", "U-001-03"]

    db_path = book_root / "pipeline-workspace" / "state" / "study-kb.sqlite"
    with sqlite3.connect(db_path) as conn:
        evidence_count = conn.execute("SELECT COUNT(*) FROM evidence_ledger").fetchone()[0]
        snapshot_count = conn.execute("SELECT COUNT(*) FROM memory_snapshots").fetchone()[0]

    assert evidence_count == 3
    assert snapshot_count == 3


def test_merge_concurrent_memories_matches_sequential_for_additive_parts(tmp_path):
    """并发批后合并的可加部分（concept/symbol/evidence）应与串行处理等价，
    summary/recent_accepted 按队列顺序正确。"""
    from business_db import initialize_business_db
    from memory_store import merge_concurrent_memories, new_memory, update_memory

    book_root = tmp_path / "books" / "concurrent-book"
    initialize_business_db(book_root)

    def unit_result(idx):
        return {
            "summary": f"summary {idx}",
            "concepts": [{"term": f"C{idx}", "definition": f"def {idx}"}],
            "symbols": [{"symbol": f"s{idx}", "meaning": f"m {idx}"}],
            "evidence": [{
                "evidence_id": f"E-{idx}", "claim": f"claim {idx}", "page": idx,
                "source_heading": "h", "evidence_type": "text", "payload": {"preview": "p"},
            }],
        }

    base = new_memory()
    # 并发：两个 unit 都从同一 base 出发，各自得到 base+自身增量
    results = []
    for idx in (1, 2):
        mem = update_memory(book_root, "run-c", f"U-001-0{idx}", base, unit_result(idx))
        results.append({"memory": mem, "status": "published"})

    merged = merge_concurrent_memories(base, results)

    # 可加部分：两个 unit 的概念/符号/证据都进来了
    assert set(merged["concept_index"]) == {"C1", "C2"}
    assert set(merged["symbol_index"]) == {"s1", "s2"}
    assert [e["evidence_id"] for e in merged["evidence_ledger"]] == ["E-1", "E-2"]
    # 顺序敏感部分：summary 按队列顺序，recent_accepted 保留最近 2 个且有序
    assert "[U-001-01] summary 1" in merged["running_book_summary"]
    assert "[U-001-02] summary 2" in merged["running_book_summary"]
    assert merged["running_book_summary"].index("U-001-01") < merged["running_book_summary"].index("U-001-02")
    assert [r["unit_id"] for r in merged["recent_accepted"]] == ["U-001-01", "U-001-02"]


def test_merge_concurrent_memories_preserves_existing_base(tmp_path):
    """合并不应丢失 base 里已有的概念/证据。"""
    from memory_store import merge_concurrent_memories, new_memory

    base = new_memory()
    base["concept_index"]["Old"] = {"definition": "o", "first_unit": "U-000", "units": ["U-000"]}
    base["evidence_ledger"].append({"evidence_id": "E-old"})
    result_mem = new_memory()
    result_mem["concept_index"]["Old"] = base["concept_index"]["Old"]
    result_mem["concept_index"]["New"] = {"definition": "n", "first_unit": "U-001", "units": ["U-001"]}
    result_mem["evidence_ledger"] = [{"evidence_id": "E-old"}, {"evidence_id": "E-new"}]
    result_mem["recent_accepted"] = [{"unit_id": "U-001", "summary": "s"}]

    merged = merge_concurrent_memories(base, [{"memory": result_mem, "status": "published"}])
    assert set(merged["concept_index"]) == {"Old", "New"}
    assert [e["evidence_id"] for e in merged["evidence_ledger"]] == ["E-old", "E-new"]


def test_merge_concurrent_memories_ignores_non_published_units(tmp_path):
    """回归：进 Review-Queue 的 unit 返回 memory==base（recent_accepted 末项是上一个 unit），
    合并时必须跳过，否则会重复追加上一个 unit 的摘要、污染全书记忆。"""
    from business_db import initialize_business_db
    from memory_store import merge_concurrent_memories, new_memory, update_memory

    book_root = tmp_path / "books" / "queue-book"
    initialize_business_db(book_root)

    base = new_memory()
    base = update_memory(book_root, "run-q", "U-001-01", base, {
        "summary": "summary one", "concepts": [], "symbols": [], "evidence": [],
    })
    # U-001-02 进了 Review-Queue → 返回的 memory 是未改动的 base，status 非 published
    queued = {"memory": base, "status": "needs_human_review"}

    merged = merge_concurrent_memories(base, [queued])

    assert merged["running_book_summary"].count("[U-001-01] summary one") == 1
    assert merged["recent_accepted"] == [{"unit_id": "U-001-01", "summary": "summary one"}]


def test_compaction_rewrites_only_running_summary(tmp_path):
    from business_db import initialize_business_db
    from memory_store import update_memory

    book_root = tmp_path / "books" / "phase7-book"
    initialize_business_db(book_root)
    provider = FakeCompactionProvider("short summary")
    provider_config = SimpleNamespace(planner_model="fake-planner")
    memory = {
        "running_book_summary": "existing long summary",
        "concept_index": {"Nash": {"definition": "eq", "first_unit": "U-001-01", "units": ["U-001-01"]}},
        "symbol_index": {"x": {"meaning": "state", "first_unit": "U-001-01", "units": ["U-001-01"]}},
        "evidence_ledger": [{"evidence_id": "E-0"}],
        "recent_accepted": [{"unit_id": "U-001-01", "summary": "old"}],
    }

    updated = update_memory(
        book_root,
        run_id="run-1",
        unit_id="U-001-02",
        memory=memory,
        unit_result={"summary": "x" * 80, "concepts": [], "symbols": [], "evidence": []},
        provider=provider,
        provider_config=provider_config,
        memory_compact_char_limit=50,
    )

    assert provider.calls
    assert updated["running_book_summary"] == "short summary"
    assert updated["concept_index"]["Nash"]["definition"] == "eq"
    assert updated["symbol_index"]["x"]["meaning"] == "state"
    assert updated["evidence_ledger"][0]["evidence_id"] == "E-0"
    assert "concept_index" not in provider.calls[0]["user"]
    assert "symbol_index" not in provider.calls[0]["user"]


def test_prompt_memory_view_drops_evidence_ledger_and_flattens_indexes():
    from memory_store import prompt_memory_view

    memory = {
        "running_book_summary": "全书脉络",
        "recent_accepted": [{"unit_id": "U-9", "summary": "最近"}],
        "concept_index": {
            "Nash": {"definition": "均衡", "first_unit": "U-1", "units": ["U-1", "U-3"]},
        },
        "symbol_index": {
            "x": {"meaning": "状态", "first_unit": "U-1", "units": ["U-1", "U-2"]},
        },
        "evidence_ledger": [{"evidence_id": f"E-{i}", "payload": "x" * 500} for i in range(800)],
    }

    view = prompt_memory_view(memory)

    # evidence_ledger 完全不进 prompt 视图
    assert "evidence_ledger" not in view
    # 概念/符号索引压成 term->定义，去掉随书增长的 units / first_unit
    assert view["concept_index"] == {"Nash": "均衡"}
    assert view["symbol_index"] == {"x": "状态"}
    # 连贯性字段原样保留
    assert view["running_book_summary"] == "全书脉络"
    assert view["recent_accepted"] == [{"unit_id": "U-9", "summary": "最近"}]
    # 精简视图远小于原 memory（账本占绝大多数体积）
    import json
    assert len(json.dumps(view, ensure_ascii=False)) < len(json.dumps(memory, ensure_ascii=False)) // 10


def test_prompt_memory_view_handles_empty_and_none():
    from memory_store import prompt_memory_view

    for empty in (None, {}):
        view = prompt_memory_view(empty)
        assert view["running_book_summary"] == ""
        assert view["recent_accepted"] == []
        assert view["concept_index"] == {}
        assert view["symbol_index"] == {}


def test_reconstruct_memory_from_db_merges_snapshots_and_ledger(tmp_path):
    from business_db import initialize_business_db, record_evidence, record_memory_snapshot
    from memory_store import reconstruct_memory_from_db

    book_root = tmp_path / "books" / "recon"
    initialize_business_db(book_root)

    # 两个 unit 各自的最新快照（并集应补齐两边的概念/符号）
    record_memory_snapshot(book_root, "run-1", "U1", {
        "concept_index": {"Nash": {"definition": "均衡", "first_unit": "U1", "units": ["U1"]}},
        "symbol_index": {"x": {"meaning": "状态", "first_unit": "U1", "units": ["U1"]}},
    })
    record_memory_snapshot(book_root, "run-1", "U2", {
        "concept_index": {"Pareto": {"definition": "帕累托", "first_unit": "U2", "units": ["U2"]}},
        "symbol_index": {},
    })
    record_evidence(book_root, "E-U1-0001", "run-1", "U1", "c1", 1, None, "text", {})
    record_evidence(book_root, "E-U2-0001", "run-1", "U2", "c2", 2, None, "ocr", {"latex_preview": "y"})

    mem = reconstruct_memory_from_db(book_root)

    assert set(mem["concept_index"]) == {"Nash", "Pareto"}
    assert mem["symbol_index"]["x"]["meaning"] == "状态"
    assert {it["evidence_id"] for it in mem["evidence_ledger"]} == {"E-U1-0001", "E-U2-0001"}
