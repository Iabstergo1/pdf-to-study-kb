import os
import subprocess
import sys
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "scripts" / "pipeline.py"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


state_store = _load("state_store")
mdpage = _load("mdpage")


def _run(args, cwd):
    env = {**os.environ, "STUDY_KB_ROOT": str(cwd)}
    return subprocess.run([sys.executable, str(PIPELINE), *args], cwd=cwd,
                          capture_output=True, text=True, env=env)


def _ingest_ready(tmp_path, sid="note"):
    assert _run(["init-vault"], tmp_path).returncode == 0
    note = tmp_path / "raw" / f"{sid}.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("# A\n\naaa 内容\n", encoding="utf-8")
    for cmd in (["add-source", "--source", sid, "--domain", "misc", "--path", str(note), "--fmt", "md"],
                ["profile", "--source", sid], ["source-convert", "--source", sid],
                ["windows", "--source", sid], ["workorder", "--source", sid],
                ["ingest-start", "--source", sid]):
        r = _run(cmd, tmp_path)
        assert r.returncode == 0, f"{cmd}: {r.stderr}"
    return tmp_path / "pipeline-workspace/state/study-kb.sqlite"


GOOD_LESSON = ("# A\n\n这一节讲述 aaa 的核心思想，用足够长的干净散文正文展开：先给直觉，"
               "再说明它和相邻概念的依赖关系，最后给出第一遍阅读可以跳过什么、什么时候应该回到原文核对。"
               "这样的长度足以通过空课代理检查。[^e1]\n\n[^e1]: 证据：note §A\n")


def test_lint_pass_promotes_and_indexes(tmp_path):
    db = _ingest_ready(tmp_path)
    # 模拟 /ingest 产出一个合格 proposed lesson
    mdpage.write_page(tmp_path / "wiki/domains/misc/lessons/a.md",
                      {"type": "lesson", "status": "proposed", "managed_by": "pipeline",
                       "title": "A 课", "source": "note"}, GOOD_LESSON)
    assert _run(["ingest-done", "--source", "note"], tmp_path).returncode == 0
    r = _run(["lint", "--source", "note"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    src = state_store.get_source(db, "note")
    assert (src["current_stage"], src["current_status"]) == ("lint", "published")
    meta, _ = mdpage.read_page(tmp_path / "wiki/domains/misc/lessons/a.md")
    assert meta["status"] == "published"
    idx = (tmp_path / "wiki/index.generated.md").read_text(encoding="utf-8")
    assert "domains/misc/lessons/a.md" in idx
    log = (tmp_path / "wiki/log.md").read_text(encoding="utf-8")
    assert "lint" in log and "note" in log


BAD_NESTING = ("\n> [!question] 自测\n> 为什么？\n>\n> [!success]- 答案\n> 因为。\n")


def test_vault_preflight_blocks_without_rolling_back_current_batch(tmp_path):
    """事务隔离：旧来源 published 渲染旧伤 → 阻断 promote + Review-Queue 去重登记，
    但当前批不回滚、保持 proposed；修复旧页后直接重跑 lint 即通过。"""
    db = _ingest_ready(tmp_path, sid="old")
    old_page = tmp_path / "wiki/domains/misc/lessons/old.md"
    mdpage.write_page(old_page, {"type": "lesson", "status": "proposed", "managed_by": "pipeline",
                                 "title": "旧课", "source": "old"}, GOOD_LESSON)
    assert _run(["ingest-done", "--source", "old"], tmp_path).returncode == 0
    assert _run(["lint", "--source", "old"], tmp_path).returncode == 0
    # 模拟历史遗留：published 页含坏嵌套（当年门禁不存在，静默发布）
    meta, body = mdpage.read_page(old_page)
    mdpage.write_page(old_page, meta, body + BAD_NESTING)
    # 新来源合格批次
    _ingest_ready(tmp_path, sid="new")
    new_page = tmp_path / "wiki/domains/misc/lessons/new.md"
    mdpage.write_page(new_page, {"type": "lesson", "status": "proposed", "managed_by": "pipeline",
                                 "title": "新课", "source": "new"}, GOOD_LESSON)
    assert _run(["ingest-done", "--source", "new"], tmp_path).returncode == 0
    r = _run(["lint", "--source", "new"], tmp_path)
    assert r.returncode != 0
    out = r.stdout + r.stderr
    assert "vault-preflight" in out and "callout-nested-malformed" in out
    # 当前批未回滚：new 仍 proposed、内容原样；不出现回滚输出
    assert mdpage.read_page(new_page)[0]["status"] == "proposed"
    assert "[rollback]" not in out and "就地编辑已被回滚还原" not in out
    # 旧伤登记归属旧来源；重跑 lint 不重复造行（(rule,path,content_hash) 去重）
    con = state_store.connect(db)
    try:
        n1 = con.execute("SELECT COUNT(*) FROM review_proposals"
                         " WHERE kind='callout-nested-malformed'").fetchone()[0]
        owner = con.execute("SELECT source_id FROM review_proposals"
                            " WHERE kind='callout-nested-malformed'").fetchone()[0]
    finally:
        con.close()
    assert n1 == 1 and owner == "old"
    assert _run(["lint", "--source", "new"], tmp_path).returncode != 0
    con = state_store.connect(db)
    try:
        n2 = con.execute("SELECT COUNT(*) FROM review_proposals"
                         " WHERE kind='callout-nested-malformed'").fetchone()[0]
    finally:
        con.close()
    assert n2 == 1
    # 修复旧页（真空行拆块）→ 当前批直接通过并发布
    meta, body = mdpage.read_page(old_page)
    mdpage.write_page(old_page, meta, body.replace("？\n>\n> [!success]", "？\n\n> [!success]"))
    r3 = _run(["lint", "--source", "new"], tmp_path)
    assert r3.returncode == 0, r3.stdout + r3.stderr
    assert mdpage.read_page(new_page)[0]["status"] == "published"


_TOPIC_BODY = ("本主题把 aaa 的相关概念聚拢成一条阅读脉络，用足够长的散文说明各页之间的依赖顺序，"
               "先读什么后读什么、遇到卡点回哪一页补课。再补一段展开：主题页的价值不在罗列链接，"
               "而在给出概念之间的因果与递进关系，让读者按需进入正确的页面并知道何时返回；"
               "这一段的存在纯粹为了让正文长度稳定越过残次页兜底检查的字符下限，不承担其他语义。\n")


def test_unaccounted_write_requires_ledger_not_just_source_refs(tmp_path):
    """归属 ≠ 记账：topic/synthesis 等导航层页凭 source_refs 进本源 lint 批次，
    但本轮 proposed 必须出现在处理台账（窗口 write_set ∪ query-session 写集）。"""
    _ingest_ready(tmp_path, sid="s")
    topic = tmp_path / "wiki/topics/主题甲.md"
    mdpage.write_page(topic, {"type": "topic", "status": "proposed", "managed_by": "pipeline",
                              "title": "主题甲", "source_refs": [{"source": "s"}]}, _TOPIC_BODY)
    assert _run(["ingest-done", "--source", "s"], tmp_path).returncode == 0
    r = _run(["lint", "--source", "s"], tmp_path)
    assert r.returncode != 0 and "unaccounted-write" in (r.stdout + r.stderr)
    # 补窗口记账 → 通过（lint failed → 重新 ingest-start 合法）
    assert _run(["ingest-start", "--source", "s"], tmp_path).returncode == 0
    assert _run(["window-start", "--source", "s", "--window", "w0000", "--hash", "h1"],
                tmp_path).returncode == 0
    assert _run(["window-done", "--source", "s", "--window", "w0000",
                 "--writes", '["topics/主题甲.md"]'], tmp_path).returncode == 0
    # 回滚吃掉的就地编辑重新应用（页面是新建页，回滚即删除，需重写）
    mdpage.write_page(topic, {"type": "topic", "status": "proposed", "managed_by": "pipeline",
                              "title": "主题甲", "source_refs": [{"source": "s"}]}, _TOPIC_BODY)
    assert _run(["ingest-done", "--source", "s"], tmp_path).returncode == 0
    r2 = _run(["lint", "--source", "s"], tmp_path)
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert mdpage.read_page(topic)[0]["status"] == "published"


def _mk_saved_session(tmp_path, run_id, writes):
    """构造一个过 saved 契约的 query-session（Q1：question/answer/decision + 三个 JSON 清单）。"""
    import json as _json
    sess = tmp_path / "pipeline-workspace/query-sessions" / run_id
    sess.mkdir(parents=True)
    (sess / "question.md").write_text("问：跨源结论？\n", encoding="utf-8")
    (sess / "answer.md").write_text("答：见综合页。\n", encoding="utf-8")
    (sess / "decision.md").write_text("为何保存/写了哪些页/证据/为何不污染概念。\n", encoding="utf-8")
    (sess / "related_pages.json").write_text("[]", encoding="utf-8")
    (sess / "candidate_write_set.json").write_text(
        _json.dumps(writes, ensure_ascii=False), encoding="utf-8")
    (sess / "evidence_refs.json").write_text('["s §1"]', encoding="utf-8")
    return sess


def test_foreign_source_refs_page_not_published_by_accounting_source(tmp_path):
    """P0-2 回归（复审复现）：frontmatter 归属他源（source_refs=B）的页，即使被 A 的
    write_set 记账，也**不得被 A 发布**——归属以 frontmatter 为最高依据，write_set 只在
    页面没有任何归属字段时回退认领；记账错误由 B 侧 unaccounted-write 暴露。"""
    _ingest_ready(tmp_path, sid="b")
    assert _run(["ingest-done", "--source", "b"], tmp_path).returncode == 0
    _ingest_ready(tmp_path, sid="a")
    topic = tmp_path / "wiki/topics/他源归属主题.md"
    mdpage.write_page(topic, {"type": "topic", "status": "proposed", "managed_by": "pipeline",
                              "title": "他源归属主题", "source_refs": [{"source": "b"}]}, _TOPIC_BODY)
    # A 把它错误地记进自己的 write_set
    assert _run(["window-start", "--source", "a", "--window", "w0000", "--hash", "h1"],
                tmp_path).returncode == 0
    assert _run(["window-done", "--source", "a", "--window", "w0000",
                 "--writes", '["topics/他源归属主题.md"]'], tmp_path).returncode == 0
    assert _run(["ingest-done", "--source", "a"], tmp_path).returncode == 0
    r = _run(["lint", "--source", "a"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "他源归属主题" not in [l for l in r.stdout.splitlines() if "[lint]" in l]
    # A 的收尾不发布它：留给 b（跳过），页面保持 proposed
    assert "归属其他 source" in r.stdout
    assert mdpage.read_page(topic)[0]["status"] == "proposed"


def test_kb_save_session_scoped_membership_and_accounting(tmp_path):
    """P1b：ingest 不再读 query-session 台账（历史会话不得代记账）；kb-save 走
    `lint --source kb-save --session <run_id>`——该会话的 candidate 集同时决定
    membership 与 accounting，集外页留给其所属来源。"""
    _ingest_ready(tmp_path, sid="s")
    syn = tmp_path / "wiki/synthesis/跨源综合乙.md"
    mdpage.write_page(syn, {"type": "synthesis", "status": "proposed", "managed_by": "pipeline",
                            "title": "跨源综合乙", "save_session": "r1",
                            "source_refs": [{"source": "s"}]}, _TOPIC_BODY)
    _mk_saved_session(tmp_path, "r1", ["synthesis/跨源综合乙.md"])
    # ① ingest 路径：session 文件存在也不算数——unaccounted-write 阻断
    assert _run(["ingest-done", "--source", "s"], tmp_path).returncode == 0
    r = _run(["lint", "--source", "s"], tmp_path)
    assert r.returncode != 0 and "unaccounted-write" in (r.stdout + r.stderr)
    # ② kb-save 会话路径：membership=accounting=session 集 → 发布
    r2 = _run(["lint", "--source", "kb-save", "--session", "r1"], tmp_path)
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert mdpage.read_page(syn)[0]["status"] == "published"


def test_kb_save_lint_requires_session_and_saved_contract(tmp_path):
    """P1b 守卫：无 --session 拒绝；session 未过 saved 契约（缺 decision.md）拒绝；
    集外的他源 proposed 页不被 kb-save 顺手发布。"""
    _ingest_ready(tmp_path, sid="s")
    other = tmp_path / "wiki/topics/他源主题.md"
    mdpage.write_page(other, {"type": "topic", "status": "proposed", "managed_by": "pipeline",
                              "title": "他源主题", "source_refs": [{"source": "s"}]}, _TOPIC_BODY)
    syn = tmp_path / "wiki/synthesis/跨源综合丙.md"
    mdpage.write_page(syn, {"type": "synthesis", "status": "proposed", "managed_by": "pipeline",
                            "title": "跨源综合丙", "save_session": "r2",
                            "source_refs": [{"source": "s"}]}, _TOPIC_BODY)
    r0 = _run(["lint", "--source", "kb-save"], tmp_path)
    assert r0.returncode != 0 and "--session" in (r0.stdout + r0.stderr)
    sess = _mk_saved_session(tmp_path, "r2", ["synthesis/跨源综合丙.md"])
    (sess / "decision.md").unlink()
    r1 = _run(["lint", "--source", "kb-save", "--session", "r2"], tmp_path)
    assert r1.returncode != 0 and "decision.md" in (r1.stdout + r1.stderr)
    (sess / "decision.md").write_text("补齐决策记录。\n", encoding="utf-8")
    r2 = _run(["lint", "--source", "kb-save", "--session", "r2"], tmp_path)
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert mdpage.read_page(syn)[0]["status"] == "published"
    assert mdpage.read_page(other)[0]["status"] == "proposed"  # 集外页留给来源 s 自己收尾


def test_kb_save_session_content_identity_and_completeness(tmp_path):
    """P1b 边界：candidate 只记路径没有内容身份 → 页面 frontmatter `save_session` 定身份。
    两会话同路径互不代发；candidate 路径缺失 fail-closed（不得 promoted 0 还报成功）。"""
    _ingest_ready(tmp_path, sid="s")
    syn = tmp_path / "wiki/synthesis/同路径综合.md"
    mdpage.write_page(syn, {"type": "synthesis", "status": "proposed", "managed_by": "pipeline",
                            "title": "同路径综合", "save_session": "ra",
                            "source_refs": [{"source": "s"}]}, _TOPIC_BODY)
    _mk_saved_session(tmp_path, "ra", ["synthesis/同路径综合.md"])
    # session B 随后合规重写同一路径（换上自己的身份标记 + 自己的台账）
    _mk_saved_session(tmp_path, "rb", ["synthesis/同路径综合.md"])
    mdpage.write_page(syn, {"type": "synthesis", "status": "proposed", "managed_by": "pipeline",
                            "title": "同路径综合", "save_session": "rb",
                            "source_refs": [{"source": "s"}]},
                      _TOPIC_BODY + "\n这是 B 会话重写后的增补段落，与 A 的内容不同。\n")
    # 执行 A 的 lint → 身份不匹配 fail-closed；B 的内容不被 A 代发
    ra = _run(["lint", "--source", "kb-save", "--session", "ra"], tmp_path)
    assert ra.returncode != 0 and "session-identity-mismatch" in (ra.stdout + ra.stderr)
    assert mdpage.read_page(syn)[0]["status"] == "proposed"
    # 执行 B 的 lint → 发布的是 B 的内容
    rb = _run(["lint", "--source", "kb-save", "--session", "rb"], tmp_path)
    assert rb.returncode == 0, rb.stdout + rb.stderr
    meta, body = mdpage.read_page(syn)
    assert meta["status"] == "published" and "B 会话重写" in body
    # candidate 列了盘上不存在的路径 → fail-closed
    _mk_saved_session(tmp_path, "rc", ["synthesis/不存在的页.md"])
    rc = _run(["lint", "--source", "kb-save", "--session", "rc"], tmp_path)
    assert rc.returncode != 0 and "session-candidate-missing" in (rc.stdout + rc.stderr)


def test_kb_save_session_publishes_concept_page(tmp_path):
    """P1b 边界：kb-save 允许产出 concept 页——不得被 ingest 专属的 source-page-missing
    （sources/kb-save.md 不存在也不该存在）误拦。"""
    _ingest_ready(tmp_path, sid="s")
    concept_body = ("会话概念的散文定义正文，先给直觉再给边界条件，说明它与既有概念的关系；"
                    "这一段足够长以越过残次页兜底检查的字符下限，并补充一个可核对的最小例子："
                    "当且仅当查询会话中出现了可复用的新概念时，kb-save 才会落一个概念页。"
                    "再补一句以稳定超过一百二十字符：概念页的价值在于给出可复用的判断标准，"
                    "而不是复述查询会话里的一次性事实，后者按保存准入门禁本就不该入库。\n")
    c = tmp_path / "wiki/domains/misc/concepts/会话概念.md"
    mdpage.write_page(c, {"type": "concept", "status": "proposed", "managed_by": "pipeline",
                          "canonical_id": "concept.misc.session-concept",
                          "canonical_name": "会话概念", "domain": "misc",
                          "save_session": "rk", "source_refs": [{"source": "s"}]}, concept_body)
    syn = tmp_path / "wiki/synthesis/会话综合.md"
    mdpage.write_page(syn, {"type": "synthesis", "status": "proposed", "managed_by": "pipeline",
                            "title": "会话综合", "save_session": "rk",
                            "source_refs": [{"source": "s"}]}, _TOPIC_BODY)
    _mk_saved_session(tmp_path, "rk",
                      ["domains/misc/concepts/会话概念.md", "synthesis/会话综合.md"])
    r = _run(["lint", "--source", "kb-save", "--session", "rk"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "source-page-missing" not in (r.stdout + r.stderr)
    assert mdpage.read_page(c)[0]["status"] == "published"
    assert mdpage.read_page(syn)[0]["status"] == "published"


def test_vault_lint_cli_reports_render_safety(tmp_path):
    assert _run(["init-vault"], tmp_path).returncode == 0
    bad = tmp_path / "wiki/domains/misc/lessons/x.md"
    mdpage.write_page(bad, {"type": "lesson", "status": "published", "source": "s"},
                      GOOD_LESSON + BAD_NESTING)
    r = _run(["vault-lint"], tmp_path)
    assert r.returncode != 0 and "callout-nested-malformed" in r.stdout
    meta, body = mdpage.read_page(bad)
    mdpage.write_page(bad, meta, body.replace("？\n>\n> [!success]", "？\n\n> [!success]"))
    r2 = _run(["vault-lint"], tmp_path)
    assert r2.returncode == 0, r2.stdout + r2.stderr


def test_lint_scopes_to_own_source_pages(tmp_path):
    # P1 回归（2026-06-11 P9 code review）：lint --source B 不得 promote 其他
    # source 的 proposed 页；无 frontmatter 归属的页靠本 source 的 window write_set 归属。
    db = _ingest_ready(tmp_path, sid="a")
    mdpage.write_page(tmp_path / "wiki/domains/misc/lessons/a.md",
                      {"type": "lesson", "status": "proposed", "managed_by": "pipeline",
                       "title": "A 课", "source": "a"}, GOOD_LESSON)
    assert _run(["ingest-done", "--source", "a"], tmp_path).returncode == 0
    _ingest_ready(tmp_path, sid="b")
    mdpage.write_page(tmp_path / "wiki/domains/misc/lessons/b.md",
                      {"type": "lesson", "status": "proposed", "managed_by": "pipeline",
                       "title": "B 课", "source": "b"}, GOOD_LESSON)
    # b2：无 source 字段，只靠 window-done --writes 归属
    assert _run(["window-start", "--source", "b", "--window", "w0000", "--hash", "h1"],
                tmp_path).returncode == 0
    mdpage.write_page(tmp_path / "wiki/domains/misc/lessons/b2.md",
                      {"type": "lesson", "status": "proposed", "managed_by": "pipeline",
                       "title": "B2 课"}, GOOD_LESSON)
    assert _run(["window-done", "--source", "b", "--window", "w0000",
                 "--writes", '["domains/misc/lessons/b2.md"]'], tmp_path).returncode == 0
    assert _run(["ingest-done", "--source", "b"], tmp_path).returncode == 0
    r = _run(["lint", "--source", "b"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    # b/b2 发布；a 留在 proposed，source 状态不被 b 的收尾推进
    assert mdpage.read_page(tmp_path / "wiki/domains/misc/lessons/b.md")[0]["status"] == "published"
    assert mdpage.read_page(tmp_path / "wiki/domains/misc/lessons/b2.md")[0]["status"] == "published"
    assert mdpage.read_page(tmp_path / "wiki/domains/misc/lessons/a.md")[0]["status"] == "proposed"
    assert state_store.get_source(db, "a")["current_status"] != "published"
    idx = (tmp_path / "wiki/index.generated.md").read_text(encoding="utf-8")
    assert "lessons/b.md" in idx and "lessons/a.md" not in idx
    # 跳过的他源页要明示
    assert "lessons/a.md" in r.stdout
    # a 自己收尾后照常发布
    r2 = _run(["lint", "--source", "a"], tmp_path)
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert mdpage.read_page(tmp_path / "wiki/domains/misc/lessons/a.md")[0]["status"] == "published"


def test_lint_blocks_on_unattributed_proposed(tmp_path):
    # P1 余项回归（2026-06-11 P9 review 复验）：孤儿 proposed 页
    # （不归属任何已注册 source）必须阻断 lint——不发布 source、写 Review-Queue、lint/failed。
    # 归属其他 source 的页放行跳过（见 test_lint_scopes_to_own_source_pages），孤儿页阻断。
    db = _ingest_ready(tmp_path)
    mdpage.write_page(tmp_path / "wiki/domains/misc/lessons/a.md",
                      {"type": "lesson", "status": "proposed", "managed_by": "pipeline",
                       "title": "A 课", "source": "note"}, GOOD_LESSON)
    # 孤儿页：/ingest 忘了 window-done --writes 记账，frontmatter 也无归属
    mdpage.write_page(tmp_path / "wiki/domains/misc/lessons/orphan.md",
                      {"type": "lesson", "status": "proposed", "managed_by": "pipeline",
                       "title": "孤儿课"}, GOOD_LESSON)
    assert _run(["ingest-done", "--source", "note"], tmp_path).returncode == 0
    r = _run(["lint", "--source", "note"], tmp_path)
    assert r.returncode != 0, "孤儿 proposed 页必须阻断 lint"
    src = state_store.get_source(db, "note")
    assert (src["current_stage"], src["current_status"]) == ("lint", "failed")
    # 谁都不发布
    assert mdpage.read_page(tmp_path / "wiki/domains/misc/lessons/a.md")[0]["status"] == "proposed"
    assert mdpage.read_page(tmp_path / "wiki/domains/misc/lessons/orphan.md")[0]["status"] == "proposed"
    queue = list((tmp_path / "wiki/Review-Queue").glob("note-lint-*.md"))
    assert len(queue) == 1 and "unattributed" in queue[0].read_text(encoding="utf-8")
    assert len(state_store.list_review_proposals(db, "note")) >= 1
    # 修复归属后重试：lint 通过、全部发布、source published
    meta, body = mdpage.read_page(tmp_path / "wiki/domains/misc/lessons/orphan.md")
    meta["source"] = "note"
    mdpage.write_page(tmp_path / "wiki/domains/misc/lessons/orphan.md", meta, body)
    r2 = _run(["lint", "--source", "note"], tmp_path)
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert mdpage.read_page(tmp_path / "wiki/domains/misc/lessons/orphan.md")[0]["status"] == "published"
    assert state_store.get_source(db, "note")["current_status"] == "published"


COMPARISON_BODY = ("## 结论\n\n两种做法各有取舍，按场景选型。\n\n## 对比维度\n\n"
                   "| 维度 | A | B |\n|---|---|---|\n| 速度 | 快 | 慢 |\n\n"
                   "## 适用场景\n\nA 适合高频小数据，B 适合一次性大批量。\n\n"
                   "## 相关概念\n\n见上文两条路径。\n")


def test_reopen_enables_incremental_publish(tmp_path):
    # 通用"重开来源做增量补充"端到端：已发布源经 reopen 后再入库新综合页，
    # 旧 published 页不受影响、不被回滚；reopen 刷新 workorder 使 ingest-start registry 校验通过。
    db = _ingest_ready(tmp_path)
    mdpage.write_page(tmp_path / "wiki/domains/misc/lessons/a.md",
                      {"type": "lesson", "status": "proposed", "managed_by": "pipeline",
                       "title": "A 课", "source": "note"}, GOOD_LESSON)
    assert _run(["ingest-done", "--source", "note"], tmp_path).returncode == 0
    assert _run(["lint", "--source", "note"], tmp_path).returncode == 0
    assert state_store.get_source(db, "note")["current_status"] == "published"

    # reopen：状态机重置 + workorder 刷新
    r = _run(["reopen", "--source", "note"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    src = state_store.get_source(db, "note")
    assert (src["current_stage"], src["current_status"]) == ("workorder_ready", "done")

    # 增量开工：registry 校验通过证明 workorder 已据当前 vault 重建
    assert _run(["ingest-start", "--source", "note"], tmp_path).returncode == 0
    assert _run(["window-start", "--source", "note", "--window", "w-reopen-0", "--hash", "h"],
                tmp_path).returncode == 0
    # 新增综合页（靠 --writes 归属，避免判孤儿；G2：综合页必带 source_refs 溯源）
    mdpage.write_page(tmp_path / "wiki/comparisons/x.md",
                      {"type": "comparison", "status": "proposed", "managed_by": "pipeline",
                       "title": "X 对比", "source_refs": [{"source": "note", "sections": ["1"]}]},
                      COMPARISON_BODY)
    assert _run(["window-done", "--source", "note", "--window", "w-reopen-0",
                 "--writes", '["comparisons/x.md"]'], tmp_path).returncode == 0
    assert _run(["ingest-done", "--source", "note"], tmp_path).returncode == 0
    r2 = _run(["lint", "--source", "note"], tmp_path)
    assert r2.returncode == 0, r2.stdout + r2.stderr
    # 新页发布；旧页仍 published（增量、不回滚）
    assert mdpage.read_page(tmp_path / "wiki/comparisons/x.md")[0]["status"] == "published"
    assert mdpage.read_page(tmp_path / "wiki/domains/misc/lessons/a.md")[0]["status"] == "published"
    idx = (tmp_path / "wiki/index.generated.md").read_text(encoding="utf-8")
    assert "comparisons/x.md" in idx and "domains/misc/lessons/a.md" in idx


def test_sync_assets_copies_pngs_to_vault(tmp_path):
    # 通用：把 staging/<src>/assets 难页 PNG 复制进 wiki/assets/<src>/，公式嵌图才不断链。
    assert _run(["init-vault"], tmp_path).returncode == 0
    staging = tmp_path / "pipeline-workspace/staging/src1/assets"
    staging.mkdir(parents=True)
    (staging / "p0001.png").write_bytes(b"\x89PNG\r\n fakeimg 1")
    (staging / "p0002.png").write_bytes(b"\x89PNG\r\n fakeimg 2")
    r = _run(["sync-assets", "--source", "src1"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "wiki/assets/src1/p0001.png").read_bytes() == b"\x89PNG\r\n fakeimg 1"
    assert (tmp_path / "wiki/assets/src1/p0002.png").exists()
    # 幂等：已存在同 hash 不再复制
    r2 = _run(["sync-assets", "--source", "src1"], tmp_path)
    assert r2.returncode == 0 and "synced 0" in r2.stdout


def test_reopen_rejected_before_first_ingest_cli(tmp_path):
    # 还没第一次入库完成（停在 workorder_ready）reopen 必须报错退出
    _ingest_ready(tmp_path)  # 已推进到 ingesting
    # 直接对一个仅预处理完的新源 reopen
    note = tmp_path / "raw" / "n2.md"
    note.write_text("# B\n\nbbb\n", encoding="utf-8")
    for cmd in (["add-source", "--source", "n2", "--domain", "misc", "--path", str(note), "--fmt", "md"],
                ["profile", "--source", "n2"], ["source-convert", "--source", "n2"],
                ["windows", "--source", "n2"], ["workorder", "--source", "n2"]):
        assert _run(cmd, tmp_path).returncode == 0
    r = _run(["reopen", "--source", "n2"], tmp_path)
    assert r.returncode != 0, "未完成首轮入库的源不可 reopen"


def test_lint_fail_blocks_rolls_back_and_queues(tmp_path):
    db = _ingest_ready(tmp_path)
    vault = tmp_path / "wiki"
    # 既有页被就地 merge：先快照原版，再被改坏
    target = vault / "overview.md"
    original = target.read_text(encoding="utf-8")
    assert _run(["snapshot-page", "--source", "note", "--path", "overview.md"], tmp_path).returncode == 0
    mdpage.write_page(target, {"type": "overview", "status": "proposed"}, "被改坏的版本 [E-bad]\n")
    # 另一个坏 proposed 页（裸 E-ID + 太短）
    mdpage.write_page(vault / "domains/misc/lessons/bad.md",
                      {"type": "lesson", "status": "proposed", "source": "note"},
                      "结论 [E-p1] 而且太短\n")
    assert _run(["ingest-done", "--source", "note"], tmp_path).returncode == 0
    r = _run(["lint", "--source", "note"], tmp_path)
    assert r.returncode != 0
    src = state_store.get_source(db, "note")
    assert (src["current_stage"], src["current_status"]) == ("lint", "failed")
    # 回滚：overview 恢复原版
    assert target.read_text(encoding="utf-8") == original
    # Review-Queue 文件 + proposals 行
    queue = list((vault / "Review-Queue").glob("note-lint-*.md"))
    assert len(queue) == 1 and "L1" in queue[0].read_text(encoding="utf-8")
    assert len(state_store.list_review_proposals(db, "note")) >= 1
    # 自动 harvest：lint 失败即刷新 skill backlog（零-LLM，不必手敲 skill-mine；Claude/Codex 同生效）
    backlog = tmp_path / "pipeline-workspace/skill-evolution/backlog.yaml"
    assert backlog.exists() and "L1" in backlog.read_text(encoding="utf-8")
    # 坏页保持 proposed、index 不收录
    meta, _ = mdpage.read_page(vault / "domains/misc/lessons/bad.md")
    assert meta["status"] == "proposed"
    assert not (vault / "index.generated.md").exists() or \
        "bad.md" not in (vault / "index.generated.md").read_text(encoding="utf-8")
    # 回流：lint failed 后状态机允许回 ingest_waiting
    state_store.start_stage(db, "note", "ingest_waiting", input_hash="retry")
    assert state_store.get_source(db, "note")["current_stage"] == "ingest_waiting"


# ---------------------------------------------------------------------------
# Task 5: cmd_lint finish hook — graph 生成（graph-data + HTML）+ 发布隔离
# ---------------------------------------------------------------------------

def _setup_one_proposed(tmp_path, sid="note"):
    """ingest_ready + 一个合格 proposed lesson + ingest-done（不含 lint）。返回 vault 路径。"""
    _ingest_ready(tmp_path, sid)
    mdpage.write_page(tmp_path / f"wiki/domains/misc/lessons/{sid}.md",
                      {"type": "lesson", "status": "proposed", "managed_by": "pipeline",
                       "title": f"{sid} 课", "source": sid}, GOOD_LESSON)
    assert _run(["ingest-done", "--source", sid], tmp_path).returncode == 0
    return tmp_path / "wiki"


def test_lint_finish_builds_graph_html(tmp_path):
    vault = _setup_one_proposed(tmp_path)
    assert _run(["lint", "--source", "note"], tmp_path).returncode == 0
    assert (vault / "knowledge-graph.generated.html").exists()      # HTML 导航入口
    assert (vault / "graph-data.generated.json").exists()
    assert not (vault / "knowledge-map.generated.canvas").exists()  # canvas 已移除


def test_lint_finish_builds_graph_and_passes_graph_lint(tmp_path):
    # publish-path 集成（Knowledge Graph v2.0）：真实状态机驱动 source 到 published 后，
    # publish-isolated 钩子写出 graph-data + 力导向 HTML，且 graph-lint 在真实 vault 上 returncode 0
    # ——验证 A2 门禁（graph_model.topic_membership）+ 钩子集成不打断发布。
    import json
    vault = _setup_one_proposed(tmp_path)
    assert _run(["lint", "--source", "note"], tmp_path).returncode == 0
    gdata = vault / "graph-data.generated.json"
    assert gdata.exists()
    data = json.loads(gdata.read_text(encoding="utf-8"))
    assert data["version"] == 2 and data["scope"] == "v2.0"
    assert (vault / "knowledge-graph.generated.html").exists()
    r = _run(["graph-lint"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr           # 真实 vault graph-lint 无 fail-hard


def test_graph_build_failure_does_not_change_lint_exit_code(tmp_path):
    # 图谱构建失败（graph-data 目标预先占成目录）只 warn、保留发布；lint 仍 returncode 0。
    vault = _setup_one_proposed(tmp_path)
    gdir = vault / "graph-data.generated.json"
    gdir.mkdir(parents=True, exist_ok=True)                 # 目标是目录 → write 失败
    (gdir / "keep.txt").write_text("x", encoding="utf-8")
    r = _run(["lint", "--source", "note"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr           # 图谱失败不改 lint 退出码
    meta, _ = mdpage.read_page(vault / "domains/misc/lessons/note.md")
    assert meta["status"] == "published"                    # 发布已完成
    assert gdir.is_dir()                                    # 旧产物（此处目录）保留
    assert "[WARN]" in r.stdout


def test_lint_success_clears_stale_review_queue(tmp_path):
    # 回归（2026-07-04 手动删过一次）：首败写 Review-Queue，修复重跑通过后，
    # 本源过时的 -lint-*.md 失败报告应被自动清理（不清别源的）。
    _ingest_ready(tmp_path)
    vault = tmp_path / "wiki"
    mdpage.write_page(vault / "domains/misc/lessons/a.md",
                      {"type": "lesson", "status": "proposed", "managed_by": "pipeline",
                       "title": "A 课", "source": "note"}, "结论 [E-p1] 而且太短\n")
    assert _run(["ingest-done", "--source", "note"], tmp_path).returncode == 0
    assert _run(["lint", "--source", "note"], tmp_path).returncode != 0
    assert list((vault / "Review-Queue").glob("note-lint-*.md"))
    other = vault / "Review-Queue" / "other-lint-2026-01-01.md"
    other.write_text("别源的报告，不许动\n", encoding="utf-8")
    meta, _ = mdpage.read_page(vault / "domains/misc/lessons/a.md")
    mdpage.write_page(vault / "domains/misc/lessons/a.md", meta, GOOD_LESSON)
    r = _run(["lint", "--source", "note"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert list((vault / "Review-Queue").glob("note-lint-*.md")) == []
    assert other.exists()


QUESTION_LESSON = GOOD_LESSON + (
    "\n> [!question] 自测\n"
    "> 这一节的核心权衡是什么？\n"
    "> > [!success]- 参考答案\n"
    "> > 在直觉与依赖关系之间取舍。\n")


def test_lint_finish_builds_quiz_index(tmp_path):
    # 收尾派生阅读层：lint 通过后重建 quiz-index.generated.md（published 页题干 + 回链，无答案）
    _ingest_ready(tmp_path)
    mdpage.write_page(tmp_path / "wiki/domains/misc/lessons/a.md",
                      {"type": "lesson", "status": "proposed", "managed_by": "pipeline",
                       "title": "A 课", "source": "note"}, QUESTION_LESSON)
    assert _run(["ingest-done", "--source", "note"], tmp_path).returncode == 0
    assert _run(["lint", "--source", "note"], tmp_path).returncode == 0
    qi = (tmp_path / "wiki/quiz-index.generated.md").read_text(encoding="utf-8")
    assert "这一节的核心权衡是什么？" in qi
    assert "domains/misc/lessons/a.md" in qi
    assert "在直觉与依赖关系之间取舍" not in qi  # 索引不泄露答案


def test_quiz_build_failure_does_not_change_lint_exit_code(tmp_path):
    # publish-isolated：quiz 索引目标被占成目录 → 只 warn，发布与 lint 退出码不受影响
    vault = _setup_one_proposed(tmp_path)
    qdir = vault / "quiz-index.generated.md"
    qdir.mkdir(parents=True, exist_ok=True)
    (qdir / "keep.txt").write_text("x", encoding="utf-8")
    r = _run(["lint", "--source", "note"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    meta, _ = mdpage.read_page(vault / "domains/misc/lessons/note.md")
    assert meta["status"] == "published"


def test_rebuild_quiz_cli(tmp_path):
    # 手动重建入口（与 rebuild-graph 同型）：rebuild-quiz 独立可跑
    assert _run(["init-vault"], tmp_path).returncode == 0
    mdpage.write_page(tmp_path / "wiki/domains/misc/concepts/概念甲.md",
                      {"type": "concept", "status": "published", "managed_by": "pipeline",
                       "canonical_id": "concept.misc.jia", "canonical_name": "概念甲",
                       "domain": "misc"},
                      "正文。\n\n> [!question] 自测\n> 甲的定义要件是什么？\n"
                      "> > [!success]- 参考答案\n> > 要件略。\n")
    r = _run(["rebuild-quiz"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    qi = (tmp_path / "wiki/quiz-index.generated.md").read_text(encoding="utf-8")
    assert "甲的定义要件是什么？" in qi


def test_lint_warns_on_unanswered_question(tmp_path):
    # 有题无解 → 软警告不阻断：lint 仍通过，stdout 出现 [warn] 提示
    _ingest_ready(tmp_path)
    bare_q = GOOD_LESSON + "\n> [!question] 自测\n> 这道题没有给出任何解答？\n"
    mdpage.write_page(tmp_path / "wiki/domains/misc/lessons/a.md",
                      {"type": "lesson", "status": "proposed", "managed_by": "pipeline",
                       "title": "A 课", "source": "note"}, bare_q)
    assert _run(["ingest-done", "--source", "note"], tmp_path).returncode == 0
    r = _run(["lint", "--source", "note"], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[warn]" in r.stdout and "这道题没有给出任何解答？" in r.stdout
    meta, _ = mdpage.read_page(tmp_path / "wiki/domains/misc/lessons/a.md")
    assert meta["status"] == "published"


def test_lint_blocks_concept_batch_without_source_page_and_seed_overview(tmp_path):
    # 回归（真实书籍入库实测缺口）：整本书发布完成而 sources/<src>.md 从未写过、
    # overview 仍是 init-vault 种子——两者现均 fail-closed；补齐后放行。
    _ingest_ready(tmp_path)
    vault = tmp_path / "wiki"
    concept_body = ("这一概念解释策略互动中决策主体如何选择最优行动，并给出足够长的干净散文正文："
                    "先说直觉，再给形式化定义，最后给一个可核对的最小例子，长度超过残次页底线。"
                    "为确保稳超一百二十字符的内容底线，这里再补充一句：该概念的适用边界在于"
                    "参与者理性且规则公开，超出该边界时应改用其他建模框架来刻画互动结构。\n")
    mdpage.write_page(vault / "domains/misc/concepts/概念甲.md",
                      {"type": "concept", "status": "proposed", "managed_by": "pipeline",
                       "canonical_id": "concept.misc.jia", "canonical_name": "概念甲",
                       "domain": "misc", "source": "note"}, concept_body)
    mdpage.write_page(vault / "topics/主题一.md",
                      {"type": "topic", "status": "proposed", "managed_by": "pipeline",
                       "title": "主题一", "source": "note",
                       "source_refs": [{"source": "note", "sections": ["1"]}]},
                      "主题正文，链入 [[domains/misc/concepts/概念甲|概念甲]]，长度足够通过残次页检查，"
                      "并把该概念收编进本主题的叙述脉络之中。为确保稳超一百二十字符的内容底线，"
                      "这里再补充一句：本主题按学习顺序组织成员概念，读者可从直觉入手逐步进入形式化。\n")
    assert _run(["ingest-done", "--source", "note"], tmp_path).returncode == 0
    r = _run(["lint", "--source", "note"], tmp_path)
    assert r.returncode != 0
    queue = list((vault / "Review-Queue").glob("note-lint-*.md"))
    qtext = queue[0].read_text(encoding="utf-8")
    assert "source-page-missing" in qtext and "overview-seed" in qtext
    # 补齐：写来源台账页 + 重写 overview（归属靠 source_refs；记账靠窗口 write_set——
    # unaccounted-write 上线后 topic 页须入台账，测试同步契约）
    mdpage.write_page(vault / "sources/note.md",
                      {"type": "source", "status": "proposed", "managed_by": "pipeline",
                       "source_id": "note", "title": "Note 小册", "domain": "misc",
                       "format": "md"},
                      "一句话概括这份来源讲了什么、入库时提炼出了哪些可复用概念，以及弱化了什么。\n")
    meta, _ = mdpage.read_page(vault / "overview.md")
    meta["source_refs"] = [{"source": "note", "sections": ["1"]}]
    mdpage.write_page(vault / "overview.md", meta,
                      "## 主题导航\n\n从 [[topics/主题一|主题一]] 进入概念网络，按需深入。\n")
    assert _run(["ingest-start", "--source", "note"], tmp_path).returncode == 0
    assert _run(["window-start", "--source", "note", "--window", "w0000", "--hash", "h1"],
                tmp_path).returncode == 0
    assert _run(["window-done", "--source", "note", "--window", "w0000",
                 "--writes", '["topics/主题一.md"]'], tmp_path).returncode == 0
    assert _run(["ingest-done", "--source", "note"], tmp_path).returncode == 0
    r2 = _run(["lint", "--source", "note"], tmp_path)
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert mdpage.read_page(vault / "sources/note.md")[0]["status"] == "published"


def test_lint_failure_lists_rolled_back_files_in_queue(tmp_path):
    # 回滚可见性：lint 失败时被还原的就地编辑必须写进 Review-Queue 报告（重跑前重新应用的清单）
    _ingest_ready(tmp_path)
    vault = tmp_path / "wiki"
    target = vault / "overview.md"
    assert _run(["snapshot-page", "--source", "note", "--path", "overview.md"], tmp_path).returncode == 0
    mdpage.write_page(target, {"type": "overview", "status": "proposed"}, "被改坏的版本 [E-bad]\n")
    assert _run(["ingest-done", "--source", "note"], tmp_path).returncode == 0
    r = _run(["lint", "--source", "note"], tmp_path)
    assert r.returncode != 0
    assert "就地编辑已被回滚还原：overview.md" in r.stdout
    queue = list((vault / "Review-Queue").glob("note-lint-*.md"))
    qtext = queue[0].read_text(encoding="utf-8")
    assert "已回滚的就地编辑" in qtext and "overview.md" in qtext
