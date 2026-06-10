from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


mdpage = _load("mdpage")
concept_store = _load("concept_store")


def test_slugify_ascii_kebab():
    assert concept_store.slugify("Signaling Game") == "signaling-game"
    assert concept_store.slugify("  Nash  Equilibrium! ") == "nash-equilibrium"


def test_slugify_pure_cjk_kept():
    assert concept_store.slugify("信号博弈") == "信号博弈"


def test_canonical_id_prefers_ascii_candidate():
    # spec §6 示例：信号博弈 + alias "Signaling Game" → concept.game-theory.signaling-game
    cid = concept_store.canonical_id("game-theory", "信号博弈", aliases=["Signaling Game"])
    assert cid == "concept.game-theory.signaling-game"
    cid2 = concept_store.canonical_id("game-theory", "Nash Equilibrium")
    assert cid2 == "concept.game-theory.nash-equilibrium"


def test_canonical_id_pure_cjk_no_alias():
    assert concept_store.canonical_id("misc", "占优策略") == "concept.misc.占优策略"
