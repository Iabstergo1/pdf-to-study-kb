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


def _mk_concept(vault, *, domain, name, aliases=(), cid=None, scope=None):
    """直接落一个概念页文件（绕协议，模拟已有 vault 内容）。"""
    cid = cid or concept_store.canonical_id(domain, name, aliases)
    slug = cid.rsplit(".", 1)[1]
    if domain == "shared":
        rel = Path("concepts") / f"{slug}.md"
    else:
        rel = Path("domains") / domain / "concepts" / f"{slug}.md"
    meta = {"type": "concept", "canonical_id": cid, "canonical_name": name,
            "aliases": list(aliases), "scope": scope or ("shared" if domain == "shared" else "domain"),
            "domain": domain, "source_refs": [], "page_path": rel.as_posix(),
            "managed_by": "pipeline", "status": "proposed"}
    mdpage.write_page(Path(vault) / rel, meta, f"# {name}\n")
    return cid, rel.as_posix()


def test_scan_finds_domain_and_shared_pages(tmp_path):
    _mk_concept(tmp_path, domain="game-theory", name="信号博弈", aliases=["Signaling Game"])
    _mk_concept(tmp_path, domain="shared", name="期望效用")
    metas = concept_store.scan_concept_pages(tmp_path)
    assert {m["canonical_id"] for m in metas} == {
        "concept.game-theory.signaling-game", "concept.shared.期望效用"}


def test_build_registry_ok_and_write_deterministic(tmp_path):
    _mk_concept(tmp_path, domain="game-theory", name="信号博弈", aliases=["Signaling Game"])
    metas = concept_store.scan_concept_pages(tmp_path)
    reg, errors, warnings = concept_store.build_registry(metas)
    assert not errors and not warnings
    sha1 = concept_store.write_registry(tmp_path, reg)
    sha2 = concept_store.write_registry(tmp_path, reg)
    assert sha1 == sha2 and len(sha1) == 64  # 重建字节级确定
    text = (tmp_path / "concepts" / "_registry.yaml").read_text(encoding="utf-8")
    assert "concept.game-theory.signaling-game" in text


def test_duplicate_canonical_id_is_error(tmp_path):
    _mk_concept(tmp_path, domain="game-theory", name="信号博弈", cid="concept.game-theory.x")
    # 第二页：不同文件名、相同 canonical_id（真实的重复场景是两个文件）
    rel = Path("domains") / "game-theory" / "concepts" / "y.md"
    meta = {"type": "concept", "canonical_id": "concept.game-theory.x", "canonical_name": "发信号",
            "aliases": [], "scope": "domain", "domain": "game-theory", "source_refs": [],
            "page_path": rel.as_posix(), "managed_by": "pipeline", "status": "proposed"}
    mdpage.write_page(tmp_path / rel, meta, "# 发信号\n")
    reg, errors, warnings = concept_store.build_registry(concept_store.scan_concept_pages(tmp_path))
    assert any("duplicate canonical_id" in e for e in errors)


def test_alias_collision_same_domain_is_warning(tmp_path):
    _mk_concept(tmp_path, domain="d", name="A", aliases=["撞名"])
    _mk_concept(tmp_path, domain="d", name="B", aliases=["撞名"])
    reg, errors, warnings = concept_store.build_registry(concept_store.scan_concept_pages(tmp_path))
    assert not errors
    assert any("alias collision" in w for w in warnings)


def test_write_aliases_derived_view(tmp_path):
    _mk_concept(tmp_path, domain="game-theory", name="信号博弈", aliases=["Signaling Game"])
    reg, _, _ = concept_store.build_registry(concept_store.scan_concept_pages(tmp_path))
    concept_store.write_registry(tmp_path, reg)
    concept_store.write_aliases(tmp_path, reg)
    text = (tmp_path / "aliases.md").read_text(encoding="utf-8")
    assert "Signaling Game" in text and "signaling-game" in text and "派生文件" in text
