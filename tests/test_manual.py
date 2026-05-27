#!/usr/bin/env python3
"""手动测试 pipeline 命令

原则：
- 真实示例书籍（博弈论白皮书）只做只读 smoke 检查
- 所有写操作使用 tmp fixture + monkey-patch
"""

import sys
import os
import shutil
import tempfile
import argparse
import json
from pathlib import Path

# 添加 scripts 目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from pipeline import find_book_root, load_manifest, get_chapter_from_section_id
import yaml

EXAMPLE_BOOK_ID = "博弈论白皮书"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tmp_book(tmp: Path, book_id="test-book", sections=None,
                   pdf_name="test.pdf", pdf_bytes=b'%PDF-1.4 fake',
                   skip_pdf=False) -> Path:
    """在 tmp 下创建一个最小 book fixture，返回 book_root。
    skip_pdf=True 时不创建假 PDF（调用方会自行复制真实 PDF）。"""
    book_root = tmp / "books" / book_id
    for d in ["input", "config", "pipeline-workspace/reports",
              "pipeline-workspace/staging", "pipeline-workspace/reviews",
              "pipeline-workspace/tasks", "study-kb/Section-Lessons",
              "study-kb/Learning-Maps", "study-kb/Source-QA"]:
        (book_root / d).mkdir(parents=True, exist_ok=True)

    if not skip_pdf:
        (book_root / "input" / pdf_name).write_bytes(pdf_bytes)

    if sections is None:
        sections = []

    manifest_lines = [
        "book_id: test\n",
        "total_sections: {}\n".format(len(sections)),
        "source_pages: 10\n",
        "\n",
        "sections:\n",
    ]
    for s in sections:
        manifest_lines.append(f"  - id: {s['id']}\n")
        manifest_lines.append(f'    source_order: "{s.get("source_order", "1")}"\n')
        manifest_lines.append(f'    title: "{s.get("title", s["id"])}"\n')
        if 'part' in s:
            manifest_lines.append(f'    part: "{s["part"]}"\n')
        if 'pages' in s:
            manifest_lines.append(f"    source_locator:\n")
            manifest_lines.append(f"      pages: {s['pages']}\n")
        manifest_lines.append(f"    status: {s.get('status', 'registered')}\n")
        manifest_lines.append(f"    extraction_risk: {s.get('extraction_risk', 'low')}\n")
        manifest_lines.append(f"    formula_risk: {s.get('formula_risk', 'low')}\n")
        manifest_lines.append(f"    publish_status: {s.get('publish_status', 'not-published')}\n")
        manifest_lines.append("\n")

    (book_root / "config" / "section-manifest.yaml").write_text(
        "".join(manifest_lines), encoding='utf-8')

    # minimal book-profile
    (book_root / "config" / "book-profile.yaml").write_text(
        yaml.dump({'book_id': book_id, 'title': 'test'}, allow_unicode=True),
        encoding='utf-8')

    return book_root


def _monkeypatch_book(pipeline_mod, book_root):
    """monkey-patch find_book_root 和 load_manifest 以指向临时 book。"""
    orig_find = pipeline_mod.find_book_root
    orig_load = pipeline_mod.load_manifest
    pipeline_mod.find_book_root = lambda bid: book_root
    pipeline_mod.load_manifest = lambda br: orig_load(book_root)
    return orig_find, orig_load


def _restore_monkeypatch(pipeline_mod, orig_find, orig_load):
    pipeline_mod.find_book_root = orig_find
    pipeline_mod.load_manifest = orig_load


def _skip_real_book_if_missing() -> bool:
    """公开仓库不提交示例书籍；本地存在时才运行真实书籍 smoke。"""
    manifest_path = Path("books") / EXAMPLE_BOOK_ID / "config" / "section-manifest.yaml"
    if manifest_path.exists():
        return False
    message = f"示例书籍不存在，跳过真实书籍 smoke: {EXAMPLE_BOOK_ID}"
    if "PYTEST_CURRENT_TEST" in os.environ:
        import pytest
        pytest.skip(message)
    print(f"[SKIP] {message}")
    return True


# ---------------------------------------------------------------------------
# Read-only smoke tests against real example book
# ---------------------------------------------------------------------------

def test_status():
    """只读 smoke: status 返回正确的 section 数量"""
    if _skip_real_book_if_missing():
        return
    book_root = find_book_root(EXAMPLE_BOOK_ID)
    manifest = load_manifest(book_root)

    total = len(manifest['sections'])
    published = [s for s in manifest['sections']
                 if s.get('publish_status') == 'published']

    print(f"Status 测试:")
    print(f"  总小节数: {total} (期望: 82)")
    lesson_dir = book_root / "study-kb" / "Section-Lessons"
    published_files = list(lesson_dir.glob("*.md"))

    print(f"  已发布: {len(published)} (期望: 与 Section-Lessons 文件数一致)")

    assert total == 82, f"总小节数错误: {total}"
    assert len(published) == len(published_files), \
        f"manifest published 数与讲义文件数不一致: {len(published)} != {len(published_files)}"
    assert len(published) >= 8, f"已发布数不应低于 Stage 3E 基线: {len(published)}"
    print("  [PASS]\n")


def test_coverage():
    """只读 smoke: coverage 按章节分组正确"""
    if _skip_real_book_if_missing():
        return
    book_root = find_book_root(EXAMPLE_BOOK_ID)
    manifest = load_manifest(book_root)

    sections = manifest['sections']

    chapter_stats = {}
    for section in sections:
        chapter = get_chapter_from_section_id(section['id'], book_root)
        if chapter not in chapter_stats:
            chapter_stats[chapter] = {'total': 0, 'published': 0}
        chapter_stats[chapter]['total'] += 1
        if section.get('publish_status') == 'published':
            chapter_stats[chapter]['published'] += 1

    total_sections = sum(s['total'] for s in chapter_stats.values())
    total_published = sum(s['published'] for s in chapter_stats.values())

    print(f"Coverage 测试:")
    print(f"  章节总数合计: {total_sections} (期望: 82)")
    manifest_published = sum(1 for s in sections if s.get('publish_status') == 'published')

    print(f"  已发布合计: {total_published} (期望: 与 manifest published 一致)")

    assert total_sections == 82, f"章节总数错误: {total_sections}"
    assert total_published == manifest_published, \
        f"章节 published 合计与 manifest 不一致: {total_published} != {manifest_published}"
    assert total_published >= 8, f"已发布数不应低于 Stage 3E 基线: {total_published}"

    expected_chapters = {
        '第一部分：认知与入门篇': {'total': 11, 'published': 6},
        '第二部分：核心利器篇': {'total': 24, 'published': 1},
        '第三部分：定向寻路篇': {'total': 12, 'published': 1},
        '第四部分：组合创新篇': {'total': 11, 'published': 0},
        '第五部分：成果落地篇': {'total': 17, 'published': 0},
        '第六部分：行动与规划篇': {'total': 7, 'published': 0},
    }

    print(f"  按章节验证:")
    for chapter, expected in expected_chapters.items():
        actual = chapter_stats.get(chapter, {'total': 0, 'published': 0})
        assert actual['total'] == expected['total'], \
            f"{chapter} 总数错误: {actual['total']} != {expected['total']}"
        assert actual['published'] >= expected['published'], \
            f"{chapter} 已发布数低于 Stage 3E 基线: {actual['published']} < {expected['published']}"
        print(f"    {chapter}: {actual['published']}/{actual['total']}")

    print("  [PASS] 通过\n")


def test_validate():
    """只读 smoke: 已发布讲义通过校验"""
    from validate_section_lesson import validate_section_lesson

    if _skip_real_book_if_missing():
        return
    book_root = find_book_root(EXAMPLE_BOOK_ID)
    study_kb = book_root / "study-kb" / "Section-Lessons"

    published_files = list(study_kb.glob("*.md"))

    print(f"Validate 测试:")
    manifest = load_manifest(book_root)
    manifest_published = [s for s in manifest['sections']
                          if s.get('publish_status') == 'published']

    print(f"  已发布讲义数: {len(published_files)} (期望: 与 manifest published 一致)")

    assert len(published_files) == len(manifest_published), \
        f"已发布讲义数与 manifest 不一致: {len(published_files)} != {len(manifest_published)}"
    assert len(published_files) >= 8, f"已发布讲义数不应低于 Stage 3E 基线: {len(published_files)}"

    all_passed = True
    for file_path in published_files:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        result = validate_section_lesson(content)
        status = "[PASS]" if result['passed'] else "[FAIL]"
        print(f"  {status} {file_path.name}")
        if not result['passed']:
            all_passed = False
            for error in result['errors']:
                print(f"    - {error}")
        assert result['passed'], f"{file_path.name} 校验失败: {result['errors']}"

    assert all_passed, "存在校验失败的文件"
    print("  [PASS] 通过\n")


def test_publish():
    """只读 smoke + tmp fixture: publish 单元测试和端到端"""
    from pipeline import cmd_publish, _update_manifest_block

    print(f"Publish 测试:")

    # --- 子测试 1: _update_manifest_block 单元测试 ---
    test_lines = [
        "  - id: SEC-001\n",
        "    status: reviewed\n",
        "    publish_status: not-published\n",
        "\n",
        "  - id: SEC-002\n",
        "    status: reviewed\n",
        "    publish_status: not-published\n",
    ]
    result_lines, n_s, n_p = _update_manifest_block(test_lines, "SEC-001")
    assert n_s == 1, f"SEC-001 status 替换次数: {n_s}"
    assert n_p == 1, f"SEC-001 publish_status 替换次数: {n_p}"
    assert "    status: published\n" in result_lines[1]
    assert "    publish_status: published\n" in result_lines[2]
    assert result_lines[5] == "    status: reviewed\n"
    assert result_lines[6] == "    publish_status: not-published\n"
    print(f"  [PASS] _update_manifest_block 单元测试通过")

    # --- 子测试 2: 端到端 publish 在临时目录中测试 ---
    import pipeline
    tmp = Path(tempfile.mkdtemp())
    try:
        book_root = _make_tmp_book(tmp, sections=[
            {'id': 'SEC-A', 'pages': [1, 2], 'status': 'reviewed'},
            {'id': 'SEC-B', 'pages': [3, 4], 'status': 'reviewed'},
        ])
        staging_dir = book_root / "pipeline-workspace" / "staging"
        reviews_dir = book_root / "pipeline-workspace" / "reviews"
        output_dir = book_root / "study-kb" / "Section-Lessons"

        # SEC-A: draft + review-decision accept
        sec_a_staging = staging_dir / "SEC-A"
        sec_a_staging.mkdir(parents=True, exist_ok=True)
        (sec_a_staging / "section-lesson-draft.md").write_text(
            "---\nid: SEC-A\ntype: section-lesson\nreview_status: draft\ngeneration_stage: draft\n---\n\n"
            "## 学习定位\n## 先记住的结论\n## 必须掌握\n## 首遍可略读\n## 核心概念\n"
            "## 模型结构、论证骨架或推导骨架\n## 直觉解释\n## 容易误解的点\n"
            "## 与个人知识体系的连接候选\n## 自测问题\n## 何时回原文\n## 原文定位\n",
            encoding='utf-8'
        )
        sec_a_review = reviews_dir / "SEC-A"
        sec_a_review.mkdir(parents=True, exist_ok=True)
        (sec_a_review / "review-decision.yaml").write_text("decision: accept\n", encoding='utf-8')

        # SEC-B: 有 draft 但没有 review-decision
        sec_b_staging = staging_dir / "SEC-B"
        sec_b_staging.mkdir(parents=True, exist_ok=True)
        (sec_b_staging / "section-lesson-draft.md").write_text(
            "---\nid: SEC-B\ntype: section-lesson\nreview_status: draft\ngeneration_stage: draft\n---\n\n内容\n",
            encoding='utf-8'
        )

        orig_find, orig_load = _monkeypatch_book(pipeline, book_root)
        try:
            args = argparse.Namespace(book="test-book", section="SEC-A", all_reviewed=False)
            cmd_publish(args)

            published_a = output_dir / "SEC-A.md"
            assert published_a.exists(), "SEC-A 未被发布到 study-kb"

            updated_manifest = (book_root / "config" / "section-manifest.yaml").read_text(encoding='utf-8')
            updated_data = yaml.safe_load(updated_manifest)
            sec_a = next(s for s in updated_data['sections'] if s['id'] == 'SEC-A')
            sec_b = next(s for s in updated_data['sections'] if s['id'] == 'SEC-B')

            assert sec_a['status'] == 'published', f"SEC-A status: {sec_a['status']}"
            assert sec_a['publish_status'] == 'published', f"SEC-A publish_status: {sec_a['publish_status']}"
            assert sec_b['status'] == 'reviewed', f"SEC-B status 不应被修改: {sec_b['status']}"
            assert sec_b['publish_status'] == 'not-published', f"SEC-B publish_status 不应被修改: {sec_b['publish_status']}"
            print(f"  [PASS] 端到端 publish: SEC-A 正确变为 published，SEC-B 未被修改")

            published_content = published_a.read_text(encoding='utf-8')
            pub_fm = yaml.safe_load(published_content.split('---\n')[1].split('\n---')[0])
            assert pub_fm['review_status'] == 'reviewed'
            assert pub_fm['generation_stage'] == 'published'
            print(f"  [PASS] 发布文件 frontmatter 正确更新")
        finally:
            _restore_monkeypatch(pipeline, orig_find, orig_load)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # --- 子测试 3: 只读 smoke — 验证真实项目已发布小节门禁 ---
    if _skip_real_book_if_missing():
        print("  [PASS] 通过\n")
        return
    book_root = find_book_root(EXAMPLE_BOOK_ID)
    manifest = load_manifest(book_root)
    published = [s for s in manifest['sections']
                 if s.get('publish_status') == 'published']
    assert len(published) >= 1, f"已发布数为 0"

    for section in published:
        section_id = section['id']
        review_path = book_root / "pipeline-workspace" / "reviews" / section_id / "review-decision.yaml"
        assert review_path.exists(), f"{section_id} 的 review-decision.yaml 不存在"
        with open(review_path, 'r', encoding='utf-8') as f:
            review_decision = yaml.safe_load(f)
        assert review_decision.get('decision') == 'accept', \
            f"{section_id} 的 decision 不是 accept: {review_decision.get('decision')}"
        lesson_path = book_root / "study-kb" / "Section-Lessons" / f"{section_id}.md"
        assert lesson_path.exists(), f"{section_id} 的讲义文件不存在"
        assert section.get('status') == 'published'
        assert section.get('publish_status') == 'published'
        print(f"  [PASS] {section_id}: 门禁检查通过")

    print("  [PASS] 通过\n")


# ---------------------------------------------------------------------------
# Tests using tmp fixtures only
# ---------------------------------------------------------------------------

def test_init_book():
    """tmp fixture: init-book 创建完整目录结构和配置"""
    from pipeline import cmd_init_book

    tmp = Path(tempfile.mkdtemp())
    try:
        fake_pdf = tmp / "test.pdf"
        fake_pdf.write_bytes(b'%PDF-1.4 fake')

        orig_cwd = Path.cwd()
        os.chdir(str(tmp))
        try:
            args = argparse.Namespace(book="test-book", pdf=str(fake_pdf), title="测试书籍", force=False)
            cmd_init_book(args)
        finally:
            os.chdir(str(orig_cwd))

        book_root = tmp / "books" / "test-book"

        expected_dirs = [
            "input", "config",
            "pipeline-workspace/reports", "pipeline-workspace/staging",
            "pipeline-workspace/reviews", "pipeline-workspace/tasks",
            "study-kb/Section-Lessons", "study-kb/Learning-Maps", "study-kb/Source-QA",
        ]
        for d in expected_dirs:
            assert (book_root / d).is_dir(), f"目录缺失: {d}"

        assert (book_root / "input" / "test.pdf").exists(), "PDF 未复制"

        bp = yaml.safe_load((book_root / "config" / "book-profile.yaml").read_text(encoding='utf-8'))
        assert bp['book_id'] == 'test-book'
        assert bp['title'] == '测试书籍'

        sp = yaml.safe_load((book_root / "config" / "study-profile.yaml").read_text(encoding='utf-8'))
        assert sp['lesson_style']['density'] == 'medium'

        pc = yaml.safe_load((book_root / "config" / "personal-context.yaml").read_text(encoding='utf-8'))
        assert pc['bridge_policy']['generate_bridge_notes'] is True

        print("  [PASS] init-book 创建完整结构")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_init_book_no_overwrite():
    """tmp fixture: init-book 拒绝覆盖已有 book"""
    from pipeline import cmd_init_book

    tmp = Path(tempfile.mkdtemp())
    try:
        fake_pdf = tmp / "test.pdf"
        fake_pdf.write_bytes(b'%PDF-1.4 fake')

        orig_cwd = Path.cwd()
        os.chdir(str(tmp))
        try:
            args = argparse.Namespace(book="test-book", pdf=str(fake_pdf), title="测试书籍", force=False)
            cmd_init_book(args)

            try:
                cmd_init_book(args)
                assert False, "应返回 exit 1 但没有"
            except SystemExit as e:
                assert e.code == 1, f"应返回 exit 1，实际: {e.code}"
        finally:
            os.chdir(str(orig_cwd))

        print("  [PASS] init-book 拒绝覆盖已有 book")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_inventory():
    """tmp fixture: inventory 生成报告 + manifest 含 part 字段 + coverage 可分组"""
    import pipeline
    from pipeline import cmd_inventory, cmd_coverage

    tmp = Path(tempfile.mkdtemp())
    try:
        # 创建一个有内置 TOC 的假 PDF 需要 pymupdf，这里用真实 PDF 的副本
        real_pdf = Path("books/博弈论白皮书/input/博弈论研究完全自学入门-自救白皮书.pdf")
        if not real_pdf.exists():
            print("  [SKIP] 真实 PDF 不存在，跳过 inventory 测试")
            return

        book_root = _make_tmp_book(tmp, book_id="inv-test", sections=[], skip_pdf=True)
        # 复制真实 PDF 到 tmp book
        import shutil as sh
        sh.copy2(str(real_pdf), str(book_root / "input" / real_pdf.name))

        orig_find, orig_load = _monkeypatch_book(pipeline, book_root)
        try:
            # 用 --write --force 生成 manifest（覆盖 _make_tmp_book 的空 manifest）
            args = argparse.Namespace(book="inv-test", write=True, force=True)
            cmd_inventory(args)

            # 验证报告文件
            assert (book_root / "pipeline-workspace" / "reports" / "pdf-structure-raw.json").exists()
            assert (book_root / "pipeline-workspace" / "reports" / "pdf-structure-report.md").exists()

            # 验证 manifest 生成且含 part 字段
            manifest_path = book_root / "config" / "section-manifest.yaml"
            assert manifest_path.exists(), "manifest 未生成"

            manifest_data = yaml.safe_load(manifest_path.read_text(encoding='utf-8'))
            sections = manifest_data.get('sections', [])
            assert len(sections) > 0, "manifest 无 sections"

            # 检查 part 字段存在
            has_part = any('part' in s for s in sections)
            assert has_part, "manifest sections 缺少 part 字段"

            # 验证 coverage 能正确分组（不全落到"未知章节"）
            # monkey-patch 回来后 coverage 用的是真实 book，这里直接调 parse_chapter_groups
            from pipeline import parse_chapter_groups
            section_to_part = parse_chapter_groups(book_root)
            unknown_count = sum(1 for v in section_to_part.values() if v == '未知章节')
            total = len(section_to_part)
            # 大部分应该能分到具体章节
            assert unknown_count < total * 0.5, \
                f"coverage 分组失败：{unknown_count}/{total} 个 section 落入'未知章节'"

            print(f"  [PASS] inventory 生成报告 + manifest 含 part + coverage 分组正确 ({total - unknown_count}/{total} 有章节)")

        finally:
            _restore_monkeypatch(pipeline, orig_find, orig_load)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_inventory_dryrun_real_book():
    """只读 smoke: 真实书籍 manifest 和 reports 完好，无写入"""
    import time

    if _skip_real_book_if_missing():
        return
    book_root = find_book_root(EXAMPLE_BOOK_ID)
    reports_dir = book_root / "pipeline-workspace" / "reports"
    manifest_path = book_root / "config" / "section-manifest.yaml"

    # 记录所有 reports 文件的时间戳
    report_files = list(reports_dir.glob("*"))
    timestamps_before = {}
    for f in report_files:
        timestamps_before[f.name] = f.stat().st_mtime_ns

    # 记录 manifest 时间戳
    manifest_mtime_before = manifest_path.stat().st_mtime_ns

    # 只读验证：manifest 可加载且 section 数正确
    manifest = load_manifest(book_root)
    assert len(manifest.get('sections', [])) == 82

    # 等 10ms 确保时间戳分辨率足够
    time.sleep(0.01)

    # 验证没有文件被修改
    manifest_mtime_after = manifest_path.stat().st_mtime_ns
    assert manifest_mtime_before == manifest_mtime_after, \
        "manifest 时间戳变化 — 被意外写入"

    for f in report_files:
        mtime_after = f.stat().st_mtime_ns
        assert timestamps_before[f.name] == mtime_after, \
            f"{f.name} 时间戳变化 — 被意外写入"

    # 验证没有新增文件
    report_files_after = list(reports_dir.glob("*"))
    assert len(report_files_after) == len(report_files), \
        f"reports 目录新增了文件: {len(report_files_after)} != {len(report_files)}"

    print("  [PASS] 真实书籍 reports + manifest 完好，零写入\n")


def test_make_tasks():
    """tmp fixture: make-tasks 生成/跳过/清理"""
    import pipeline
    from pipeline import cmd_make_tasks

    print("Make-tasks 测试:")
    tmp = Path(tempfile.mkdtemp())
    try:
        book_root = _make_tmp_book(tmp, sections=[
            {'id': 'SEC-A', 'pages': [1, 2]},
            {'id': 'SEC-B'},  # 无 pages，也没有 source-slice
        ])
        output_dir = book_root / "pipeline-workspace" / "tasks"

        # 为 SEC-A 创建 source-slice
        slice_dir = book_root / "pipeline-workspace" / "staging" / "SEC-A"
        slice_dir.mkdir(parents=True, exist_ok=True)
        (slice_dir / "source-slice.md").write_text("---\nsection_id: SEC-A\n---\n", encoding='utf-8')

        orig_find, orig_load = _monkeypatch_book(pipeline, book_root)
        try:
            # Case 1: SEC-A 有 source-slice，应生成任务包
            args_ok = argparse.Namespace(book="test-book", section="SEC-A", all_registered=False)
            cmd_make_tasks(args_ok)

            author_path = output_dir / "SEC-A_author.json"
            review_path = output_dir / "SEC-A_review.json"
            assert author_path.exists(), "author 任务包未生成"
            assert review_path.exists(), "review 任务包未生成"

            author_task = json.loads(author_path.read_text(encoding='utf-8'))
            assert author_task['task_type'] == 'author'
            assert author_task['section_id'] == 'SEC-A'
            assert author_task['skill'] == 'section-lesson-authoring'

            review_task = json.loads(review_path.read_text(encoding='utf-8'))
            assert review_task['task_type'] == 'review'
            assert review_task['section_id'] == 'SEC-A'
            assert review_task['skill'] == 'section-lesson-review'
            print(f"  [PASS] SEC-A 任务包格式正确")

            # 清理
            author_path.unlink()
            review_path.unlink()

            # Case 2: SEC-B 无 source-slice，应跳过并 exit 1
            # 先放假旧任务包验证清理
            stale_author = output_dir / "SEC-B_author.json"
            stale_review = output_dir / "SEC-B_review.json"
            stale_author.write_text('{"stale": true}', encoding='utf-8')
            stale_review.write_text('{"stale": true}', encoding='utf-8')

            args_skip = argparse.Namespace(book="test-book", section="SEC-B", all_registered=False)
            try:
                cmd_make_tasks(args_skip)
                assert False, "应返回 exit 1 但没有"
            except SystemExit as e:
                assert e.code == 1, f"应返回 exit 1，实际: {e.code}"

            assert not stale_author.exists(), "stale author 任务包未被清理"
            assert not stale_review.exists(), "stale review 任务包未被清理"
            print(f"  [PASS] SEC-B 缺少 source-slice 时正确跳过并清理旧任务包")

        finally:
            _restore_monkeypatch(pipeline, orig_find, orig_load)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("  [PASS] 通过\n")


def test_extract():
    """tmp fixture: extract 生成 source-slice 格式正确"""
    import pipeline
    from pipeline import cmd_extract

    tmp = Path(tempfile.mkdtemp())
    try:
        # 用真实 PDF 副本
        real_pdf = Path("books/博弈论白皮书/input/博弈论研究完全自学入门-自救白皮书.pdf")
        if not real_pdf.exists():
            print("  [SKIP] 真实 PDF 不存在，跳过 extract 测试")
            return

        book_root = _make_tmp_book(tmp, sections=[
            {'id': 'SEC-001', 'pages': [15, 16], 'title': '测试小节'},
        ], skip_pdf=True)
        import shutil as sh
        sh.copy2(str(real_pdf), str(book_root / "input" / real_pdf.name))

        orig_find, orig_load = _monkeypatch_book(pipeline, book_root)
        try:
            args = argparse.Namespace(book="test-book", section="SEC-001", all=False, force=False)
            cmd_extract(args)

            slice_path = book_root / "pipeline-workspace" / "staging" / "SEC-001" / "source-slice.md"
            assert slice_path.exists(), "source-slice.md 未生成"

            content = slice_path.read_text(encoding='utf-8')
            fm_text = content.split('---\n')[1]
            fm = yaml.safe_load(fm_text)
            assert fm['section_id'] == 'SEC-001'
            assert fm['extraction_mode'] == 'page-range'
            assert fm['extraction_confidence'] in ('low', 'medium', 'high')
            assert 'needs_boundary_review' in fm

            body = content.split('## 原文内容\n\n')[1]
            assert len(body.strip()) > 100, "原文内容过短"

            print("  [PASS] extract 生成 source-slice 格式正确")
        finally:
            _restore_monkeypatch(pipeline, orig_find, orig_load)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_extract_missing_pages():
    """tmp fixture: extract 缺 pages 时记录失败"""
    import pipeline
    from pipeline import cmd_extract

    tmp = Path(tempfile.mkdtemp())
    try:
        book_root = _make_tmp_book(tmp, sections=[
            {'id': 'SEC-001', 'title': '无页码小节'},  # 无 pages
        ])

        orig_find, orig_load = _monkeypatch_book(pipeline, book_root)
        try:
            args = argparse.Namespace(book="test-book", section="SEC-001", all=False, force=False)
            cmd_extract(args)

            slice_path = book_root / "pipeline-workspace" / "staging" / "SEC-001" / "source-slice.md"
            assert not slice_path.exists(), "缺 pages 时不应生成 source-slice"

            failure_path = book_root / "pipeline-workspace" / "reports" / "extraction-failures.md"
            assert failure_path.exists(), "extraction-failures.md 未生成"
            failure_content = failure_path.read_text(encoding='utf-8')
            assert 'SEC-001' in failure_content

            print("  [PASS] extract 缺 pages 时正确记录失败")
        finally:
            _restore_monkeypatch(pipeline, orig_find, orig_load)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_extract_no_overwrite():
    """tmp fixture: extract 不覆盖已有 source-slice"""
    import pipeline
    from pipeline import cmd_extract

    tmp = Path(tempfile.mkdtemp())
    try:
        real_pdf = Path("books/博弈论白皮书/input/博弈论研究完全自学入门-自救白皮书.pdf")
        if not real_pdf.exists():
            print("  [SKIP] 真实 PDF 不存在，跳过")
            return

        book_root = _make_tmp_book(tmp, sections=[
            {'id': 'SEC-001', 'pages': [15, 16], 'title': '测试小节'},
        ], skip_pdf=True)
        import shutil as sh
        sh.copy2(str(real_pdf), str(book_root / "input" / real_pdf.name))

        orig_find, orig_load = _monkeypatch_book(pipeline, book_root)
        try:
            # 第一次生成
            args = argparse.Namespace(book="test-book", section="SEC-001", all=False, force=False)
            cmd_extract(args)

            slice_path = book_root / "pipeline-workspace" / "staging" / "SEC-001" / "source-slice.md"
            assert slice_path.exists(), "source-slice 未生成"
            original_content = slice_path.read_text(encoding='utf-8')

            # 第二次不带 --force，应跳过
            cmd_extract(args)
            after_content = slice_path.read_text(encoding='utf-8')
            assert original_content == after_content, "source-slice 被意外覆盖"

            print("  [PASS] extract 不覆盖已有 source-slice")
        finally:
            _restore_monkeypatch(pipeline, orig_find, orig_load)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_extract_force():
    """tmp fixture: extract --force 覆盖已有 source-slice"""
    import pipeline
    from pipeline import cmd_extract
    from io import StringIO

    tmp = Path(tempfile.mkdtemp())
    try:
        real_pdf = Path("books/博弈论白皮书/input/博弈论研究完全自学入门-自救白皮书.pdf")
        if not real_pdf.exists():
            print("  [SKIP] 真实 PDF 不存在，跳过")
            return

        book_root = _make_tmp_book(tmp, sections=[
            {'id': 'SEC-001', 'pages': [15, 16], 'title': '测试小节'},
        ], skip_pdf=True)
        import shutil as sh
        sh.copy2(str(real_pdf), str(book_root / "input" / real_pdf.name))

        orig_find, orig_load = _monkeypatch_book(pipeline, book_root)
        try:
            slice_path = book_root / "pipeline-workspace" / "staging" / "SEC-001" / "source-slice.md"
            slice_path.parent.mkdir(parents=True, exist_ok=True)

            # 写入 sentinel 内容
            sentinel = "---\nsection_id: SEC-001\n---\n\nSENTINEL_OLD_CONTENT\n"
            slice_path.write_text(sentinel, encoding='utf-8')

            # 捕获 stdout
            old_stdout = sys.stdout
            sys.stdout = captured = StringIO()
            try:
                args_force = argparse.Namespace(book="test-book", section="SEC-001", all=False, force=True)
                cmd_extract(args_force)
            finally:
                sys.stdout = old_stdout
            stdout_text = captured.getvalue()

            # 断言不是 SKIP
            assert "[SKIP]" not in stdout_text, f"stdout 包含 [SKIP]：{stdout_text}"

            # 断言 sentinel 消失，被真实内容替换
            content = slice_path.read_text(encoding='utf-8')
            assert "SENTINEL_OLD_CONTENT" not in content, "sentinel 未被替换"
            assert "## 原文内容" in content, "缺少原文内容标题"
            body = content.split('## 原文内容\n\n')[1]
            assert len(body.strip()) > 100, "替换后原文内容过短"

            print("  [PASS] extract --force 覆盖 sentinel，非 SKIP")
        finally:
            _restore_monkeypatch(pipeline, orig_find, orig_load)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_extract_all_summary():
    """tmp fixture: extract --all 汇总统计正确（created + skipped + failed）"""
    import pipeline
    from pipeline import cmd_extract
    from io import StringIO

    tmp = Path(tempfile.mkdtemp())
    try:
        real_pdf = Path("books/博弈论白皮书/input/博弈论研究完全自学入门-自救白皮书.pdf")
        if not real_pdf.exists():
            print("  [SKIP] 真实 PDF 不存在，跳过")
            return

        book_root = _make_tmp_book(tmp, sections=[
            {'id': 'SEC-001', 'pages': [15, 16], 'title': '新建小节'},
            {'id': 'SEC-002', 'pages': [21, 22], 'title': '已有小节'},
            {'id': 'SEC-003', 'title': '缺页码小节'},  # 无 pages
        ], skip_pdf=True)
        import shutil as sh
        sh.copy2(str(real_pdf), str(book_root / "input" / real_pdf.name))

        # 为 SEC-002 预先创建 source-slice
        slice_dir = book_root / "pipeline-workspace" / "staging" / "SEC-002"
        slice_dir.mkdir(parents=True, exist_ok=True)
        (slice_dir / "source-slice.md").write_text("---\nsection_id: SEC-002\n---\nold content\n", encoding='utf-8')

        orig_find, orig_load = _monkeypatch_book(pipeline, book_root)
        try:
            # 捕获 stdout
            old_stdout = sys.stdout
            sys.stdout = captured = StringIO()
            try:
                args = argparse.Namespace(book="test-book", section=None, all=True, force=False)
                cmd_extract(args)
            finally:
                sys.stdout = old_stdout
            stdout_text = captured.getvalue()

            # 断言精确汇总行
            assert "提取完成: 1 新生成, 1 跳过(已存在), 1 失败" in stdout_text, \
                f"汇总行不匹配，实际 stdout:\n{stdout_text}"

            # SEC-001 应被创建
            assert (book_root / "pipeline-workspace" / "staging" / "SEC-001" / "source-slice.md").exists()
            # SEC-002 应被跳过（内容不变）
            assert (slice_dir / "source-slice.md").read_text(encoding='utf-8') == "---\nsection_id: SEC-002\n---\nold content\n"
            # SEC-003 应记录失败
            failure_path = book_root / "pipeline-workspace" / "reports" / "extraction-failures.md"
            assert failure_path.exists()
            assert 'SEC-003' in failure_path.read_text(encoding='utf-8')

            print("  [PASS] extract --all 汇总: 1 创建, 1 跳过, 1 失败")
        finally:
            _restore_monkeypatch(pipeline, orig_find, orig_load)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# mark-reviewed tests (tmp fixture only)
# ---------------------------------------------------------------------------

def _make_draft_with_validate(book_root, section_id):
    """为指定小节创建能通过 validate 的 draft 文件"""
    staging_dir = book_root / "pipeline-workspace" / "staging" / section_id
    staging_dir.mkdir(parents=True, exist_ok=True)
    (staging_dir / "section-lesson-draft.md").write_text(
        f"---\nid: {section_id}\ntype: section-lesson\n"
        f'source_title: "test"\nsource_locator:\n  pages: [1, 2]\n'
        f'book_order: "1"\nimportance: A\ndifficulty: 1\n'
        f"formula_risk: low\nreview_status: draft\ngeneration_stage: draft\n---\n\n"
        "# 测试\n\n"
        "## 学习定位\n## 先记住的结论\n## 必须掌握\n## 首遍可略读\n## 核心概念\n"
        "## 模型结构、论证骨架或推导骨架\n## 直觉解释\n## 容易误解的点\n"
        "## 与个人知识体系的连接候选\n## 自测问题\n## 何时回原文\n## 原文定位\n",
        encoding='utf-8'
    )


def _make_review_decision(book_root, section_id, decision='accept', required_fixes=None):
    """为指定小节创建 review-decision.yaml"""
    review_dir = book_root / "pipeline-workspace" / "reviews" / section_id
    review_dir.mkdir(parents=True, exist_ok=True)
    data = {
        'section_id': section_id,
        'reviewer': 'test',
        'review_date': '2026-05-27',
        'decision': decision,
        'scores': {
            'faithfulness': 'PASS',
            'learnability': 'PASS',
            'importance': 'PASS',
            'source_return': 'PASS',
            'structure': 'PASS',
        },
        'required_fixes': required_fixes or [],
        'warnings': [],
        'notes': '',
    }
    (review_dir / "review-decision.yaml").write_text(
        yaml.dump(data, allow_unicode=True), encoding='utf-8'
    )


def test_mark_reviewed_accept():
    """tmp fixture: accept 后 status → reviewed，publish_status 保持 not-published"""
    import pipeline
    from pipeline import cmd_mark_reviewed

    print("Mark-reviewed 测试:")
    tmp = Path(tempfile.mkdtemp())
    try:
        book_root = _make_tmp_book(tmp, sections=[
            {'id': 'SEC-A', 'pages': [1, 2], 'status': 'registered'},
        ])
        _make_draft_with_validate(book_root, 'SEC-A')
        _make_review_decision(book_root, 'SEC-A', decision='accept')

        orig_find, orig_load = _monkeypatch_book(pipeline, book_root)
        try:
            args = argparse.Namespace(book="test-book", section="SEC-A", all_accepted=False)
            cmd_mark_reviewed(args)

            manifest = yaml.safe_load(
                (book_root / "config" / "section-manifest.yaml").read_text(encoding='utf-8')
            )
            sec_a = next(s for s in manifest['sections'] if s['id'] == 'SEC-A')
            assert sec_a['status'] == 'reviewed', f"SEC-A status: {sec_a['status']}"
            assert sec_a['publish_status'] == 'not-published', \
                f"SEC-A publish_status 不应被修改: {sec_a['publish_status']}"
            print("  [PASS] accept → reviewed, publish_status 不变")
        finally:
            _restore_monkeypatch(pipeline, orig_find, orig_load)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_mark_reviewed_revise_rejected():
    """tmp fixture: decision=revise/reject 不更新"""
    import pipeline
    from pipeline import cmd_mark_reviewed

    tmp = Path(tempfile.mkdtemp())
    try:
        book_root = _make_tmp_book(tmp, sections=[
            {'id': 'SEC-A', 'pages': [1, 2], 'status': 'registered'},
            {'id': 'SEC-B', 'pages': [3, 4], 'status': 'registered'},
        ])
        _make_draft_with_validate(book_root, 'SEC-A')
        _make_review_decision(book_root, 'SEC-A', decision='revise')
        _make_draft_with_validate(book_root, 'SEC-B')
        _make_review_decision(book_root, 'SEC-B', decision='reject')

        orig_find, orig_load = _monkeypatch_book(pipeline, book_root)
        try:
            args = argparse.Namespace(book="test-book", section=None, all_accepted=True)
            cmd_mark_reviewed(args)

            manifest = yaml.safe_load(
                (book_root / "config" / "section-manifest.yaml").read_text(encoding='utf-8')
            )
            for s in manifest['sections']:
                assert s['status'] == 'registered', \
                    f"{s['id']} status 不应被修改: {s['status']}"
            print("  [PASS] decision=revise/reject 不更新 status")
        finally:
            _restore_monkeypatch(pipeline, orig_find, orig_load)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_mark_reviewed_fixes_nonempty():
    """tmp fixture: required_fixes 非空不更新"""
    import pipeline
    from pipeline import cmd_mark_reviewed

    tmp = Path(tempfile.mkdtemp())
    try:
        book_root = _make_tmp_book(tmp, sections=[
            {'id': 'SEC-A', 'pages': [1, 2], 'status': 'registered'},
        ])
        _make_draft_with_validate(book_root, 'SEC-A')
        _make_review_decision(book_root, 'SEC-A', decision='accept',
                              required_fixes=['fix-1'])

        orig_find, orig_load = _monkeypatch_book(pipeline, book_root)
        try:
            args = argparse.Namespace(book="test-book", section="SEC-A", all_accepted=False)
            try:
                cmd_mark_reviewed(args)
                assert False, "应返回 exit 1 但没有"
            except SystemExit as e:
                assert e.code == 1, f"应返回 exit 1，实际: {e.code}"

            manifest = yaml.safe_load(
                (book_root / "config" / "section-manifest.yaml").read_text(encoding='utf-8')
            )
            sec_a = next(s for s in manifest['sections'] if s['id'] == 'SEC-A')
            assert sec_a['status'] == 'registered', \
                f"SEC-A status 不应被修改: {sec_a['status']}"
            print("  [PASS] required_fixes 非空不更新")
        finally:
            _restore_monkeypatch(pipeline, orig_find, orig_load)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_mark_reviewed_no_draft():
    """tmp fixture: draft 缺失不更新"""
    import pipeline
    from pipeline import cmd_mark_reviewed

    tmp = Path(tempfile.mkdtemp())
    try:
        book_root = _make_tmp_book(tmp, sections=[
            {'id': 'SEC-A', 'pages': [1, 2], 'status': 'registered'},
        ])
        # 只创建 review-decision，不创建 draft
        _make_review_decision(book_root, 'SEC-A', decision='accept')

        orig_find, orig_load = _monkeypatch_book(pipeline, book_root)
        try:
            args = argparse.Namespace(book="test-book", section="SEC-A", all_accepted=False)
            try:
                cmd_mark_reviewed(args)
                assert False, "应返回 exit 1 但没有"
            except SystemExit as e:
                assert e.code == 1, f"应返回 exit 1，实际: {e.code}"

            manifest = yaml.safe_load(
                (book_root / "config" / "section-manifest.yaml").read_text(encoding='utf-8')
            )
            sec_a = next(s for s in manifest['sections'] if s['id'] == 'SEC-A')
            assert sec_a['status'] == 'registered', \
                f"SEC-A status 不应被修改: {sec_a['status']}"
            print("  [PASS] draft 缺失不更新")
        finally:
            _restore_monkeypatch(pipeline, orig_find, orig_load)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_mark_reviewed_validate_fail():
    """tmp fixture: validate 失败不更新"""
    import pipeline
    from pipeline import cmd_mark_reviewed

    tmp = Path(tempfile.mkdtemp())
    try:
        book_root = _make_tmp_book(tmp, sections=[
            {'id': 'SEC-A', 'pages': [1, 2], 'status': 'registered'},
        ])
        # 创建一个不会通过 validate 的 draft（缺必备章节）
        staging_dir = book_root / "pipeline-workspace" / "staging" / "SEC-A"
        staging_dir.mkdir(parents=True, exist_ok=True)
        (staging_dir / "section-lesson-draft.md").write_text(
            "---\nid: SEC-A\n---\n\n缺必备章节\n", encoding='utf-8'
        )
        _make_review_decision(book_root, 'SEC-A', decision='accept')

        orig_find, orig_load = _monkeypatch_book(pipeline, book_root)
        try:
            args = argparse.Namespace(book="test-book", section="SEC-A", all_accepted=False)
            try:
                cmd_mark_reviewed(args)
                assert False, "应返回 exit 1 但没有"
            except SystemExit as e:
                assert e.code == 1, f"应返回 exit 1，实际: {e.code}"

            manifest = yaml.safe_load(
                (book_root / "config" / "section-manifest.yaml").read_text(encoding='utf-8')
            )
            sec_a = next(s for s in manifest['sections'] if s['id'] == 'SEC-A')
            assert sec_a['status'] == 'registered', \
                f"SEC-A status 不应被修改: {sec_a['status']}"
            print("  [PASS] validate 失败不更新")
        finally:
            _restore_monkeypatch(pipeline, orig_find, orig_load)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_mark_reviewed_real_book_smoke():
    """只读 smoke: 验证 Stage 3C 的 5 个小节 review 产物存在，manifest 不被修改"""
    import time

    if _skip_real_book_if_missing():
        return
    book_root = find_book_root(EXAMPLE_BOOK_ID)
    manifest_path = book_root / "config" / "section-manifest.yaml"
    manifest_mtime_before = manifest_path.stat().st_mtime_ns

    manifest = load_manifest(book_root)

    time.sleep(0.01)

    manifest_mtime_after = manifest_path.stat().st_mtime_ns
    assert manifest_mtime_before == manifest_mtime_after, \
        "manifest 时间戳变化 — 被意外写入"

    # 确认 5 个小节：review 产物存在、decision=accept、required_fixes 为空
    # 这些小节已在 Stage 3E 发布为 published/published
    for sid in ['GTW-001-01', 'GTW-001-02', 'GTW-001-03', 'GTW-001-04', 'GTW-002-02']:
        review_path = book_root / "pipeline-workspace" / "reviews" / sid / "review-decision.yaml"
        assert review_path.exists(), f"{sid} review-decision.yaml 不存在"
        with open(review_path, 'r', encoding='utf-8') as f:
            rd = yaml.safe_load(f)
        assert rd.get('decision') == 'accept', f"{sid} decision 不是 accept: {rd.get('decision')}"
        required_fixes = rd.get('required_fixes') or []
        assert required_fixes == [], f"{sid} required_fixes 非空: {required_fixes}"
        section = next(s for s in manifest['sections'] if s['id'] == sid)
        assert section.get('status') == 'published', \
            f"{sid} status 应为 published，实际: {section.get('status')}"
        assert section.get('publish_status') == 'published', \
            f"{sid} publish_status 应为 published，实际: {section.get('publish_status')}"

    print("  [PASS] 真实书籍 mark-reviewed 只读 smoke: 5 个小节 review 产物完好，manifest 未被修改\n")


# ---------------------------------------------------------------------------
# run-book deterministic automation tests
# ---------------------------------------------------------------------------

def test_run_state_create_load_progress():
    """tmp fixture: run-state 可创建、更新、加载并计算进度"""
    from run_state import RunStateManager

    tmp = Path(tempfile.mkdtemp())
    try:
        book_root = _make_tmp_book(tmp, sections=[
            {'id': 'SEC-A', 'pages': [1, 2], 'status': 'published', 'publish_status': 'published'},
            {'id': 'SEC-B', 'pages': [3, 4], 'status': 'registered'},
            {'id': 'SEC-C', 'pages': [5, 6], 'status': 'registered'},
        ])
        manifest = yaml.safe_load(
            (book_root / "config" / "section-manifest.yaml").read_text(encoding='utf-8')
        )

        manager = RunStateManager(book_root)
        run_state = manager.create_run(
            book_id="test-book",
            config={'executor': 'claude-code-queue', 'batch_size': 2, 'max_revision_retry': 2},
            sections=manifest['sections'],
        )
        manager.update_stage(run_state, 'inventory', 'completed', total_sections=3)
        manager.update_section(run_state, 'SEC-B', 'author', 'failed', error='boom')

        loaded = manager.load_latest_run(book_root)
        assert loaded is not None, "未加载到最新 run"
        assert loaded.run_id == run_state.run_id
        assert loaded.stages['inventory']['status'] == 'completed'
        assert loaded.section_states['SEC-B'].status == 'failed'

        progress = manager.calculate_progress(loaded)
        assert progress['total'] == 3
        assert progress['published'] == 1
        assert progress['failed'] == 1
        assert progress['not_started'] == 1
        print("  [PASS] run-state 创建、更新、加载和 progress 正确")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_obsidian_output_generation():
    """tmp fixture: Obsidian Home/MOC/覆盖报告/风险清单生成正确"""
    from obsidian_output import ObsidianOutputGenerator

    tmp = Path(tempfile.mkdtemp())
    try:
        book_root = _make_tmp_book(tmp, sections=[
            {'id': 'SEC-A', 'title': '已发布重点', 'pages': [1, 2], 'part': '第一部分：测试',
             'source_order': '1.1', 'status': 'published', 'publish_status': 'published',
             'formula_risk': 'high'},
            {'id': 'SEC-B', 'title': '未发布中风险', 'pages': [3, 4], 'part': '第一部分：测试',
             'source_order': '1.2', 'status': 'registered', 'formula_risk': 'medium'},
            {'id': 'SEC-C', 'title': '低风险', 'pages': [5, 6], 'part': '第二部分：测试',
             'source_order': '2.1', 'status': 'registered', 'formula_risk': 'low'},
        ])
        (book_root / "study-kb" / "Section-Lessons" / "SEC-A.md").write_text(
            "---\nid: SEC-A\n---\n# 已发布重点\n", encoding='utf-8')
        manifest = yaml.safe_load(
            (book_root / "config" / "section-manifest.yaml").read_text(encoding='utf-8')
        )

        ObsidianOutputGenerator(book_root, manifest).generate_all()

        home = (book_root / "study-kb" / "Home.md").read_text(encoding='utf-8')
        full_map = (book_root / "study-kb" / "Learning-Maps" / "MOC-全书学习地图.md").read_text(encoding='utf-8')
        difficult = (book_root / "study-kb" / "Learning-Maps" / "MOC-难点与推导重点路线.md").read_text(encoding='utf-8')
        coverage = (book_root / "study-kb" / "Source-QA" / "小节覆盖报告.md").read_text(encoding='utf-8')
        risk = (book_root / "study-kb" / "Source-QA" / "高风险内容清单.md").read_text(encoding='utf-8')

        assert "1/3" in home
        assert "[[Section-Lessons/SEC-A|SEC-A]]" in home
        assert "[[Section-Lessons/SEC-A|SEC-A]] - 已发布重点" in full_map
        assert "SEC-B - 未发布中风险（待发布）" in full_map
        assert "SEC-A" in difficult and "SEC-B" in difficult
        assert "33.3%" in coverage
        assert "formula_risk" in risk and "high" in risk and "medium" in risk
        print("  [PASS] Obsidian 输出 6 个文件生成正确")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_run_book_dry_run_no_write():
    """tmp fixture: run-book --dry-run 只读，不创建 runs 或 Obsidian 输出"""
    import pipeline
    from run_book import cmd_run_book
    from io import StringIO

    tmp = Path(tempfile.mkdtemp())
    try:
        book_root = _make_tmp_book(tmp, sections=[
            {'id': 'SEC-A', 'pages': [1, 2], 'status': 'published', 'publish_status': 'published'},
            {'id': 'SEC-B', 'pages': [3, 4], 'status': 'registered'},
        ])
        orig_find, orig_load = _monkeypatch_book(pipeline, book_root)
        try:
            old_stdout = sys.stdout
            sys.stdout = captured = StringIO()
            try:
                args = argparse.Namespace(
                    book='test-book', pdf=None, title=None, executor='claude-code-queue',
                    publish='accepted-only', section=None, resume=False,
                    dry_run=True, batch_size=5, max_revision_retry=2)
                cmd_run_book(args)
            finally:
                sys.stdout = old_stdout
            output = captured.getvalue()

            assert "[DRY-RUN]" in output
            assert "总小节：2" in output
            assert "已 published：1" in output
            assert "待处理：1" in output
            assert not (book_root / "pipeline-workspace" / "runs").exists()
            assert not (book_root / "study-kb" / "Home.md").exists()
            print("  [PASS] run-book --dry-run 零写入")
        finally:
            _restore_monkeypatch(pipeline, orig_find, orig_load)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_run_book_claude_code_queue_generates_tasks_and_obsidian():
    """tmp fixture: run-book 生成 Claude Code 任务队列并更新 Obsidian 文件"""
    import pipeline
    from run_book import cmd_run_book
    from io import StringIO

    tmp = Path(tempfile.mkdtemp())
    try:
        book_root = _make_tmp_book(tmp, sections=[
            {'id': 'SEC-A', 'title': '已发布', 'pages': [1, 2], 'status': 'published',
             'publish_status': 'published', 'part': '第一部分：测试'},
            {'id': 'SEC-B', 'title': '待处理', 'pages': [3, 4], 'status': 'registered',
             'part': '第一部分：测试'},
        ])
        (book_root / "study-kb" / "Section-Lessons" / "SEC-A.md").write_text(
            "---\nid: SEC-A\n---\n# 已发布\n", encoding='utf-8')
        slice_dir = book_root / "pipeline-workspace" / "staging" / "SEC-B"
        slice_dir.mkdir(parents=True, exist_ok=True)
        (slice_dir / "source-slice.md").write_text(
            "---\nsection_id: SEC-B\npages: \"3-4\"\n---\n\n## 原文内容\n测试内容\n",
            encoding='utf-8')

        orig_find, orig_load = _monkeypatch_book(pipeline, book_root)
        try:
            old_stdout = sys.stdout
            sys.stdout = captured = StringIO()
            try:
                args = argparse.Namespace(
                    book='test-book', pdf=None, title=None, executor='claude-code-queue',
                    publish='accepted-only', section=None, resume=False,
                    dry_run=False, batch_size=5, max_revision_retry=2)
                cmd_run_book(args)
            finally:
                sys.stdout = old_stdout
            output = captured.getvalue()

            assert "[CLAUDE-CODE]" in output
            assert "SEC-B" in output
            assert (book_root / "pipeline-workspace" / "tasks" / "SEC-B_author.json").exists()
            assert (book_root / "pipeline-workspace" / "tasks" / "SEC-B_review.json").exists()
            assert (book_root / "pipeline-workspace" / "runs").exists()
            assert (book_root / "study-kb" / "Home.md").exists()
            assert (book_root / "study-kb" / "Learning-Maps" / "MOC-全书学习地图.md").exists()
            assert (book_root / "study-kb" / "Source-QA" / "小节覆盖报告.md").exists()
            print("  [PASS] run-book Claude Code 队列生成任务包、run-state 与 Obsidian 输出")
        finally:
            _restore_monkeypatch(pipeline, orig_find, orig_load)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_run_book_rejects_non_claude_code_executor():
    """tmp fixture: run-book 拒绝非 Claude Code executor，不写产物"""
    import pipeline
    from run_book import cmd_run_book

    tmp = Path(tempfile.mkdtemp())
    try:
        book_root = _make_tmp_book(tmp, sections=[
            {'id': 'SEC-A', 'pages': [1, 2], 'status': 'registered'},
        ])
        orig_find, orig_load = _monkeypatch_book(pipeline, book_root)
        try:
            args = argparse.Namespace(
                book='test-book', pdf=None, title=None, executor='unsupported',
                publish='accepted-only', section=None, resume=False,
                dry_run=False, batch_size=5, max_revision_retry=2)
            try:
                cmd_run_book(args)
                assert False, "非 Claude Code executor 应被拒绝"
            except SystemExit as e:
                assert "仅支持 --executor claude-code-queue" in str(e)
            assert not (book_root / "study-kb" / "Home.md").exists()
            print("  [PASS] run-book 拒绝非 Claude Code executor 且不写 Obsidian 输出")
        finally:
            _restore_monkeypatch(pipeline, orig_find, orig_load)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    print("=" * 50)
    print("Pipeline 手动测试")
    print("=" * 50 + "\n")

    # 只读 smoke tests（真实示例书籍）
    test_status()
    test_coverage()
    test_validate()
    test_publish()
    test_inventory_dryrun_real_book()

    # tmp fixture tests（不修改真实书籍）
    test_init_book()
    test_init_book_no_overwrite()
    test_inventory()
    test_make_tasks()
    test_extract()
    test_extract_missing_pages()
    test_extract_no_overwrite()
    test_extract_force()
    test_extract_all_summary()

    # mark-reviewed tests
    test_mark_reviewed_accept()
    test_mark_reviewed_revise_rejected()
    test_mark_reviewed_fixes_nonempty()
    test_mark_reviewed_no_draft()
    test_mark_reviewed_validate_fail()
    test_mark_reviewed_real_book_smoke()

    # run-book deterministic automation tests
    test_run_state_create_load_progress()
    test_obsidian_output_generation()
    test_run_book_dry_run_no_write()
    test_run_book_claude_code_queue_generates_tasks_and_obsidian()
    test_run_book_rejects_non_claude_code_executor()

    print("=" * 50)
    print("所有测试通过!")
    print("=" * 50)
