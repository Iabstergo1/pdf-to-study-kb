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
