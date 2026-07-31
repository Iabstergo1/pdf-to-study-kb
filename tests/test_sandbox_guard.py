"""fail-closed 沙箱守卫（tests/_sandbox.py）：纯判定矩阵 + 进程级卫生断言。

守卫动机（2026-07-29）：文档曾建议 `--basetemp="$PWD\\tmp\\..."`。`$PWD` 是调用处目录，
在别的工作区跑一次测试就把整棵 pytest 临时树写了进去（实测 28 批 / ~1.3 万文件 / 53 MB）。
pytest 会**整目录清空** basetemp，所以指错地方不只是留垃圾，还可能删掉别人的数据——
因此判定必须 fail-closed，而不是软警告。
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import _sandbox

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMP_ROOTS = [tempfile.gettempdir()]


def _v(basetemp):
    return _sandbox.basetemp_violations(basetemp, REPO_ROOT, TEMP_ROOTS)


def test_default_basetemp_is_allowed():
    # 不传 --basetemp = pytest 默认值（系统临时区 + 保留最近三次轮换），本来就是安全默认。
    assert _v(None) == []


def test_repo_tmp_subdirectory_is_allowed():
    assert _v(REPO_ROOT / "tmp" / "pt-12345") == []
    assert _v(REPO_ROOT / "tmp" / "nested" / "deeper") == []


def test_system_temp_subdirectory_is_allowed():
    assert _v(Path(tempfile.gettempdir()) / "pt-12345") == []


def test_foreign_workspace_is_flagged():
    # 事故原型：在别人的工作区里跑测试，$PWD\tmp 指向了那个工作区。
    problems = _v(Path("C:/some/other/workspace/tmp/pt-1"))
    assert problems and "escapes the allowed sandbox" in problems[0]


def test_repo_root_itself_and_source_dirs_are_flagged():
    # 仓库根、scripts/ 等都不在白名单：pytest 会把 basetemp 整个清空。
    assert _v(REPO_ROOT) and "escapes" in _v(REPO_ROOT)[0]
    assert _v(REPO_ROOT / "scripts")


def test_shared_tmp_root_itself_is_flagged():
    # tmp/ 里还有 resume-packet.txt 等非测试产物，整目录清空会误伤。
    problems = _v(REPO_ROOT / "tmp")
    assert problems and "not the shared root itself" in problems[0]


def test_relative_and_case_variants_resolve_to_the_same_verdict():
    assert _v(str(REPO_ROOT / "TMP" / "pt-1")) == []          # Windows 大小写不敏感
    assert _v(str(REPO_ROOT / "tmp" / ".." / "scripts"))      # 词法归一后逃出 tmp/


def _c(paths):
    return _sandbox.collected_outside_tests(paths, REPO_ROOT / "tests")


def test_collection_inside_tests_is_allowed():
    assert _c([]) == []
    assert _c([REPO_ROOT / "tests" / "test_sandbox_guard.py",
               REPO_ROOT / "tests" / "sub" / "test_x.py"]) == []


def test_leftover_basetemp_props_are_flagged():
    # 事故原型：tmp/ 里没清理的 basetemp 带着技能自进化用例生成的道具 tests/test_ok.py。
    problems = _c([REPO_ROOT / "tests" / "test_ok.py",
                   REPO_ROOT / "tmp" / "pt-full-1" / "case0" / "tests" / "test_ok.py"])
    assert problems and "outside" in problems[0]
    assert "pt-full-1" in problems[0]


def test_tests_dir_itself_and_siblings_are_flagged():
    assert _c([REPO_ROOT / "tests"])            # 目录本身不是"在 tests/ 之下"
    assert _c([REPO_ROOT / "scripts" / "test_x.py"])
    assert _c([Path("C:/elsewhere/tests/test_x.py")])


def test_collection_boundary_is_declared_in_pytest_ini():
    # 运行期兜底会报错，但那要先花两分半扫完 tmp/；声明式边界才是省时间的那一层。
    import configparser
    cfg = configparser.ConfigParser()
    cfg.read(REPO_ROOT / "pytest.ini", encoding="utf-8")
    assert cfg["pytest"].get("testpaths", "").split() == ["tests"], \
        "pytest.ini 缺 testpaths=tests：裸跑 pytest 会从仓库根递归扫描"
    excluded = set(cfg["pytest"].get("norecursedirs", "").split())
    # 都是 gitignore 过的本机数据目录，不含任何真测试；tmp 是本次事故的直接来源。
    for required in ("tmp", "books", "wiki", "pipeline-workspace"):
        assert required in excluded, f"pytest.ini 的 norecursedirs 缺 {required}"


def test_bytecode_writing_is_off_for_the_test_process_tree():
    # conftest.pytest_configure 已经设过；子进程（CLI 测试全部 env={**os.environ,...}）继承它。
    assert os.environ.get("PYTHONDONTWRITEBYTECODE") == "1"
    assert sys.dont_write_bytecode is True
    out = subprocess.run([sys.executable, "-c", "import sys; print(sys.dont_write_bytecode)"],
                         capture_output=True, text=True, env={**os.environ})
    assert out.stdout.strip() == "True", out.stdout + out.stderr


def test_docs_do_not_teach_pwd_relative_basetemp():
    # 成因是文档指引本身；两份项目真值都不得再出现 $PWD 相对的 basetemp 写法。
    for rel in ("CLAUDE.md", "AGENTS.md"):
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "$PWD\\tmp" not in text and "$PWD/tmp" not in text, \
            f"{rel} 仍在教 $PWD 相对的 basetemp（换目录跑就会污染别人的工作区）"


# ── STUDY_KB_ROOT 全局隔离 ────────────────────────────────────────────────────
# `pipeline._workspace_root()` 未设该变量时回落到**仓库根**，设了就无条件采信。部分 CLI 测试会
# 自行把它指向 tmp_path，但那是分散约定、不是全局保证。这里把它变成 pytest 的全局合同。

# 判定只看"是否落在临时区之下"，被测路径无需真实存在。用盘符/根锚定的虚构路径构造，
# 不写死任何人的目录名——也不能用 `REPO_ROOT.parent`：仓库若被克隆进临时区（CI 常见），
# 那个父目录反而会落进白名单，本组断言就会假绿。
_OUTSIDE_TEMP = Path(REPO_ROOT.anchor) / "not-a-temp-area" / "external-vault"


def _r(value):
    return _sandbox.study_kb_root_violations(value, REPO_ROOT, TEMP_ROOTS)


def test_unset_or_blank_study_kb_root_is_allowed():
    # 未设置 = conftest 会分配 session 临时根，判定阶段不算违规。
    assert _r(None) == [] and _r("") == [] and _r("   ") == []


def test_temp_and_repo_tmp_study_kb_roots_are_allowed():
    assert _r(Path(tempfile.gettempdir()) / "study-kb-testroot-abc") == []
    assert _r(REPO_ROOT / "tmp" / "testroot-1") == []


def test_real_vault_and_repo_root_study_kb_roots_are_flagged():
    for unsafe in (REPO_ROOT, REPO_ROOT / "wiki", REPO_ROOT / "pipeline-workspace",
                   _OUTSIDE_TEMP):
        problems = _r(unsafe)
        assert problems, f"{unsafe} 应被判为不安全的测试 STUDY_KB_ROOT"
        assert "unsafe STUDY_KB_ROOT for tests" in problems[0]


def test_temp_root_itself_is_flagged_like_basetemp():
    # 与 basetemp 同口径：只接受"之下"，不接受共享根本身。
    assert _r(tempfile.gettempdir())
    assert _r(REPO_ROOT / "tmp")


def test_session_study_kb_root_is_isolated_and_inherited_by_subprocesses():
    """场景 2：正常 session 自动拿到独占临时根，且普通子进程继承同一个根。"""
    root = os.environ.get("STUDY_KB_ROOT")
    assert root, "conftest.pytest_configure 应在 collection 前分配 session 临时根"
    norm = os.path.normcase(os.path.abspath(root))
    assert norm != os.path.normcase(str(REPO_ROOT)), "session 根不得是仓库根"
    assert _r(root) == [], f"session 根本身必须落在允许的临时区：{root}"

    out = subprocess.run(
        [sys.executable, "-c", "import os; print(os.environ.get('STUDY_KB_ROOT', ''))"],
        capture_output=True, text=True, encoding="utf-8", env={**os.environ})
    assert os.path.normcase(os.path.abspath(out.stdout.strip())) == norm, out.stdout + out.stderr


def test_workspace_root_default_no_longer_falls_back_to_the_repo():
    """场景 4：不显式传 STUDY_KB_ROOT 的子进程，`_workspace_root()` 必须落在 session 根。"""
    code = ("import sys; sys.path.insert(0, r'%s'); import pipeline; print(pipeline._workspace_root())"
            % (REPO_ROOT / "scripts"))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         encoding="utf-8", env={**os.environ})
    resolved = os.path.normcase(os.path.abspath(out.stdout.strip()))
    assert resolved != os.path.normcase(str(REPO_ROOT)), out.stdout + out.stderr
    assert resolved == os.path.normcase(os.path.abspath(os.environ["STUDY_KB_ROOT"]))


def test_per_test_env_override_is_restored_after_the_test(monkeypatch, tmp_path):
    """场景 3（纯环境部分）：单测覆盖立即生效、退出后回到 session 根。

    真正跑 CLI、断言"只写自己的 tmp_path"的那半在 `test_collection_boundary_cli.py`（cli 层）——
    本文件是 fast 层（纯函数/直接模块），不放起完整 CLI 的用例。
    """
    session_root = os.environ["STUDY_KB_ROOT"]
    monkeypatch.setenv("STUDY_KB_ROOT", str(tmp_path))
    assert os.environ["STUDY_KB_ROOT"] == str(tmp_path)
    monkeypatch.undo()
    assert os.environ["STUDY_KB_ROOT"] == session_root
