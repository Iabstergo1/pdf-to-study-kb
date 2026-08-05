"""发布前门禁 check_publishable 的行为。

这道门禁的价值全在"报得准"上：虚报会让人整体绕过它，比漏报更糟。所以这里既测
"该报的报出来"，也测"不该报的不要判失败"。

本文件里的用户目录路径是**测试输入**，不是个人环境痕迹，因此逐行加了
``publishable-allow``——门禁会扫到自己的测试文件，这正是行内豁免存在的理由。
不带用户名的盘符路径只判 warn、不影响退出码，所以不标记。
"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_publishable  # noqa: E402


def _scan(tmp_path, name, text):
    (tmp_path / name).write_text(text, encoding="utf-8")
    return check_publishable.scan_file(tmp_path, name)


@pytest.mark.parametrize("line", [
    r'path = "C:\Users\alice\vault\notes.md"',  # publishable-allow
    r"path = 'C:/Users/bob/vault/notes.md'",  # publishable-allow
    "root = /home/carol/projects/kb",  # publishable-allow
    "root = /Users/dave/projects/kb",  # publishable-allow
])
def test_user_home_paths_are_errors(tmp_path, line):
    """带用户名的绝对路径几乎不可能是有意写的通用示例，判 error。"""
    errors, warnings = _scan(tmp_path, "sample.py", line + "\n")
    assert len(errors) == 1, (errors, warnings)
    assert not warnings


@pytest.mark.parametrize("line", [
    r"把书放到 C:\books\ 下面",
    r'tmp = "C:/temp/staging"',
])
def test_generic_drive_paths_are_warnings_not_errors(tmp_path, line):
    """不带用户名的盘符路径是合法的文档/测试占位，只提示不判失败。

    仓库的 README 与 user-guide 里就有一批这样的示例路径；把它们判成失败会得到
    一堆虚报，门禁随即失去意义。
    """
    errors, warnings = _scan(tmp_path, "sample.md", line + "\n")
    assert not errors
    assert len(warnings) == 1


def test_allow_marker_suppresses_the_line(tmp_path):
    """确需保留示例时，用行内标记逐行豁免。

    被扫描的是写进 tmp 的那份文本，不是这里的源码行——源码行自己的标记只是为了
    让门禁扫本文件时放行。
    """
    sample = r'example = "C:\Users\alice\vault"'  # publishable-allow
    line = f"{sample}  # {check_publishable.ALLOW_MARKER}\n"
    errors, warnings = _scan(tmp_path, "sample.py", line)
    assert not errors
    assert not warnings


def test_clean_file_reports_nothing(tmp_path):
    errors, warnings = _scan(
        tmp_path, "clean.py", 'root = Path(__file__).resolve().parents[1]\n')
    assert not errors
    assert not warnings


def test_repository_itself_has_no_errors():
    """本仓库当前必须 0 error。

    这条是门禁的自举：它一旦变红，说明有个人环境痕迹被提交进来了。
    warning 数不做断言——文档里的示例路径会随文档增删变化。
    """
    result = subprocess.run(
        [sys.executable, "-B", str(ROOT / "scripts" / "check_publishable.py")],
        cwd=str(ROOT), capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "0 errors" in result.stdout
