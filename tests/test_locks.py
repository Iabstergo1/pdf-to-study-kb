from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


state_store = _load("state_store")
locks = _load("locks")


def test_acquire_then_second_blocked(tmp_path):
    db = tmp_path / "study-kb.sqlite"
    state_store.init_db(db)
    assert locks.acquire(db, scope="vault", holder="A", pid=111) is True
    assert locks.acquire(db, scope="vault", holder="B", pid=222) is False


def test_release_allows_reacquire(tmp_path):
    db = tmp_path / "study-kb.sqlite"
    state_store.init_db(db)
    locks.acquire(db, scope="vault", holder="A", pid=111)
    locks.release(db, scope="vault", holder="A")
    assert locks.acquire(db, scope="vault", holder="B", pid=222) is True


def test_stale_detected_and_breakable(tmp_path):
    db = tmp_path / "study-kb.sqlite"
    state_store.init_db(db)
    locks.acquire(db, scope="vault", holder="A", pid=111)
    locks.force_set_heartbeat(db, scope="vault", iso="2000-01-01T00:00:00+00:00")
    assert locks.is_stale(db, scope="vault", ttl_seconds=300) is True
    assert locks.break_stale(db, scope="vault", ttl_seconds=300) is True
    assert locks.acquire(db, scope="vault", holder="B", pid=222) is True
