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


def test_aliases_md_no_longer_generated(tmp_path):
    # B2：aliases.md 已废弃——英文别名只保留在概念页 frontmatter，不再生成派生别名索引
    _mk_concept(tmp_path, domain="game-theory", name="信号博弈", aliases=["Signaling Game"])
    reg, _, _ = concept_store.build_registry(concept_store.scan_concept_pages(tmp_path))
    concept_store.write_registry(tmp_path, reg)
    assert not (tmp_path / "aliases.md").exists()
    assert not hasattr(concept_store, "write_aliases")
    # 别名仍在概念页 frontmatter（Obsidian 原生用于搜索/自动补全）
    meta, _ = mdpage.read_page(tmp_path / "domains/game-theory/concepts/signaling-game.md")
    assert "Signaling Game" in meta["aliases"]


def _registry_of(vault):
    reg, errors, warnings = concept_store.build_registry(concept_store.scan_concept_pages(vault))
    assert not errors
    return reg


def test_resolve_hits_alias_in_domain_then_shared(tmp_path):
    _mk_concept(tmp_path, domain="game-theory", name="信号博弈", aliases=["Signaling Game"])
    reg = _registry_of(tmp_path)
    hit = concept_store.resolve("signaling game", domain="game-theory", registry=reg)
    assert hit is not None and hit[0] == "concept.game-theory.signaling-game"
    assert concept_store.resolve("不存在的概念", domain="game-theory", registry=reg) is None


def test_is_alias_hit_flags_alias_but_not_canonical(tmp_path):
    _mk_concept(tmp_path, domain="game-theory", name="信号博弈", aliases=["Signaling Game"])
    reg = _registry_of(tmp_path)
    cid = "concept.game-theory.signaling-game"
    entry = reg[cid]
    # alias 命中 → True（囤积劫持风险提示点）
    assert concept_store.is_alias_hit("Signaling Game", cid, entry)
    assert concept_store.is_alias_hit("signaling game", cid, entry)  # 归一后仍算 alias
    # canonical_name / canonical_id 命中 → False
    assert not concept_store.is_alias_hit("信号博弈", cid, entry)
    assert not concept_store.is_alias_hit(cid, cid, entry)


def test_resolve_or_create_merges_existing_never_duplicates(tmp_path):
    _mk_concept(tmp_path, domain="game-theory", name="信号博弈", aliases=["Signaling Game"])
    reg = _registry_of(tmp_path)
    cid, path, action = concept_store.resolve_or_create_concept(
        tmp_path, mention="Signaling Game", domain="game-theory", registry=reg,
        source_ref={"source": "whitepaper", "sections": ["12.2"]})
    assert action == "merged" and cid == "concept.game-theory.signaling-game"
    pages = list((tmp_path / "domains" / "game-theory" / "concepts").glob("*.md"))
    assert len(pages) == 1  # 绝不新建重复页
    meta, _ = mdpage.read_page(path)
    assert {"source": "whitepaper", "sections": ["12.2"]} in meta["source_refs"]


def test_merge_accumulates_source_refs_and_sections(tmp_path):
    _mk_concept(tmp_path, domain="game-theory", name="信号博弈", aliases=["Signaling Game"])
    reg = _registry_of(tmp_path)
    for sec in ("5.2", "12.2", "5.2"):
        concept_store.resolve_or_create_concept(
            tmp_path, mention="信号博弈", domain="game-theory", registry=reg,
            source_ref={"source": "whitepaper", "sections": [sec]})
    meta, _ = mdpage.read_page(tmp_path / "domains/game-theory/concepts/signaling-game.md")
    assert meta["source_refs"] == [{"source": "whitepaper", "sections": ["5.2", "12.2"]}]  # 去重累积


def test_resolve_or_create_creates_when_miss(tmp_path):
    reg = {}
    cid, path, action = concept_store.resolve_or_create_concept(
        tmp_path, mention="纳什均衡", domain="game-theory", registry=reg,
        aliases=["Nash Equilibrium"], source_ref={"source": "wp", "sections": ["3.1"]})
    assert action == "created" and cid == "concept.game-theory.nash-equilibrium"
    meta, body = mdpage.read_page(path)
    assert meta["status"] == "proposed" and meta["managed_by"] == "pipeline"
    assert meta["scope"] == "domain" and meta["domain"] == "game-theory"
    # 种子脚手架 = 散文占位 + 正确嵌套的自测示例（D-4 之后无强制小节；防会话恢复后照旧骨架填空）
    assert "（待 /ingest 填写" in body            # placeholder-unfilled 门禁仍可兜住未填页
    assert "> > [!success]-" in body              # 自测嵌套折叠示例随种子进页（收割契约）
    assert "## 一句话" not in body                # 已废除的强制小节骨架不得再种进新页


def test_same_name_different_domain_stays_separate(tmp_path):
    # 同名异义不合并（spec §6：econ 的 utility vs cs 的 utility）
    _mk_concept(tmp_path, domain="econ", name="Utility")
    reg = _registry_of(tmp_path)
    cid, _, action = concept_store.resolve_or_create_concept(
        tmp_path, mention="Utility", domain="cs", registry=reg)
    assert action == "created" and cid == "concept.cs.utility"


def test_create_existing_page_raises(tmp_path):
    _mk_concept(tmp_path, domain="d", name="X")
    try:
        concept_store.create_concept(tmp_path, domain="d", name="X")
        assert False, "should raise"
    except FileExistsError:
        pass


def test_create_concept_body_follows_template(tmp_path):
    tpl_body = mdpage.read_page(ROOT / "templates" / "concept.md")[1]
    path = concept_store.create_concept(tmp_path, domain="d", name="纳什均衡")
    _, body = mdpage.read_page(path)
    assert body == tpl_body.replace("{name}", "纳什均衡")
    assert "> > [!success]-" in body  # 模板携带正确嵌套的自测示例（收割契约随种子进页）


def test_create_concept_falls_back_when_template_missing(tmp_path):
    orig = concept_store._TEMPLATES_DIR
    concept_store._TEMPLATES_DIR = tmp_path / "no-such-dir"
    try:
        path = concept_store.create_concept(tmp_path, domain="d", name="回退概念")
        _, body = mdpage.read_page(path)
        assert "（待 /ingest 填写" in body and "> > [!success]-" in body  # 回退常量与模板同构（散文占位+嵌套自测示例）
    finally:
        concept_store._TEMPLATES_DIR = orig


def test_slugify_cjk_with_ascii_fragment_kept():
    # 回归（2026-07-04 game-theory 入库踩到 3 次）：中文名里夹 ASCII 片段（"AI"/"20"/"A-F"）时，
    # 不得取出局部 ASCII 残片当 slug（曾产出 ai.md / 20.md）——含任何非 ASCII 字符即走 CJK 分支保留原字。
    assert concept_store.slugify("生成式AI的科研辅助定位") == "生成式AI的科研辅助定位"
    assert concept_store.slugify("逻辑自查清单20问") == "逻辑自查清单20问"
    assert concept_store.slugify("组合创新方法与A-F框架") == "组合创新方法与A-F框架"
    # 中文名含空白仍去空白（与纯 CJK 分支一致）
    assert concept_store.slugify("生成式 AI 助手") == "生成式AI助手"


def test_canonical_id_cjk_with_ascii_fragment_not_collapsed():
    # 有 ASCII 别名 → cid 用别名（spec §6 不变）
    cid = concept_store.canonical_id("research-method", "生成式AI的科研辅助定位",
                                     aliases=["Generative AI Research Assistant"])
    assert cid == "concept.research-method.generative-ai-research-assistant"
    # 无 ASCII 别名 → 保留全名，绝不塌缩成 "ai"/"20"
    assert concept_store.canonical_id("research-method", "逻辑自查清单20问") \
        == "concept.research-method.逻辑自查清单20问"
