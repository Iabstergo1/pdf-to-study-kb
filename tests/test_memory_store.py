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
