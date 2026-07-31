"""收集边界的端到端验收：真的起一个子进程裸跑收集，断言收集面只有 tests/。

为什么值得一条 subprocess 测试（而不是只信 pytest.ini 的静态断言）：
2026-07-29 的故障里，`pytest.ini` 既没有 `testpaths` 也没有 `norecursedirs`，裸跑 `pytest`
从仓库根递归扫描，把 `tmp/` 下没清理的 basetemp 当成了测试树。那里面躺着技能自进化用例
生成的道具 `tests/test_ok.py`；这些目录没有 `__init__.py`，pytest 只能按文件名给模块命名，
于是第二个同名文件直接 `import file mismatch` → 整轮 `Interrupted`。

**代码毫无问题，但默认测试命令不可用**——这种故障静态断言容易漏掉（比如有人把 testpaths
写成 `tests scripts`，或 norecursedirs 少列一个数据目录），只有真跑一次收集才发现得了。
收集本身现在是秒级（norecursedirs 生效后 ~2s），所以这条放 cli 层，代价可接受。
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = REPO_ROOT / "tests"

# 两条声明式边界各管一种调用方式，必须分别验证——两者都写在 pytest.ini 里，
# 但**只有一条**会在给定调用下起作用：
#   - 不给路径参数（裸跑 `pytest`）→ 由 `testpaths` 决定收集面，`norecursedirs` 根本用不上；
#   - 显式给 `.` → 路径参数覆盖 testpaths，改由 `norecursedirs` 剪掉 tmp/ 等数据目录。
# 只测裸跑就等于只测了 testpaths：把 norecursedirs 那行删掉，行为用例照样全绿
# （删除本身另由 test_sandbox_guard 的静态断言兜住，但"声明还在、实际不起作用"只有跑一次才知道）。
_INVOCATIONS = [
    pytest.param([], "testpaths", id="bare"),
    pytest.param(["."], "norecursedirs", id="explicit-dot"),
]


def _collect(*extra_args):
    """跑一次 `pytest --collect-only`；extra_args 为空即裸跑，完全由 pytest.ini 决定收集面。"""
    return subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "--no-header",
         "-p", "no:cacheprovider", *extra_args],
        cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"})


def _bare_collect():
    return _collect()


def test_bare_collection_stays_inside_tests_and_succeeds():
    result = _bare_collect()
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]

    node_paths = {line.split("::", 1)[0].replace("\\", "/")
                  for line in result.stdout.splitlines()
                  if "::" in line and not line.startswith(("=", "-", " "))}
    assert node_paths, "collect-only 没有输出任何用例节点"
    outside = sorted(p for p in node_paths if not p.startswith("tests/"))
    assert not outside, (
        f"裸跑收集到了 tests/ 以外的路径：{outside[:5]}"
        "（多半是 tmp/ 下没清理的 basetemp 残留，或 pytest.ini 的 testpaths/norecursedirs 被改坏）")


@pytest.mark.parametrize("extra_args,mechanism", _INVOCATIONS)
def test_leftover_basetemp_props_are_excluded(extra_args, mechanism):
    """把事故现场原样重建在仓库 tmp/ 下，两种调用方式都必须干净通过。

    重建**两份**同名道具是关键——单份不会触发 `import file mismatch`，正是第二份让整轮
    collection 中止。两个 param 各自钉住一条边界：`bare` 走 testpaths，`explicit-dot` 走
    norecursedirs（后者是 `pytest .` 唯一的防线，没有它这条用例会立刻变红）。
    """
    scene = REPO_ROOT / "tmp" / "collection-boundary-probe"
    props = [scene / "case-a" / "tests" / "test_ok.py",
             scene / "case-b" / "tests" / "test_ok.py"]
    try:
        for prop in props:
            prop.parent.mkdir(parents=True, exist_ok=True)
            prop.write_text("def test_ok():\n    assert True\n", encoding="utf-8", newline="\n")

        result = _collect(*extra_args)

        assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
        assert "import file mismatch" not in result.stdout + result.stderr
        assert "test_ok" not in result.stdout, (
            f"道具文件被收集了：pytest.ini 的 {mechanism} 没把 tmp/ 挡在收集面之外"
            f"（调用方式：pytest {' '.join(extra_args) or '（无路径参数）'}）")
    finally:
        shutil.rmtree(scene, ignore_errors=True)


# ── STUDY_KB_ROOT 隔离的端到端验收（2026-07-31）─────────────────────────────────
# 纯判定矩阵在 test_sandbox_guard.py（fast 层）；这里跑真子进程，验证 conftest 在 **collection
# 之前**就兑现了拒绝与隔离——这是只有真跑一次才看得出来的性质。

def _fingerprint(root: Path):
    """(相对路径, sha256, mtime_ns) 聚合指纹；用于断言真实树一个字节都没动。"""
    import hashlib
    if not root.exists():
        return None
    h = hashlib.sha256()
    for f in sorted(root.rglob("*")):
        if f.is_file():
            st = f.stat()
            h.update(f.relative_to(root).as_posix().encode("utf-8"))
            h.update(hashlib.sha256(f.read_bytes()).digest())
            h.update(str(st.st_mtime_ns).encode("ascii"))
    return h.hexdigest()


@pytest.mark.parametrize("unsafe_rel", ["", "wiki", "pipeline-workspace"])
def test_pytest_refuses_an_unsafe_study_kb_root_before_collection(unsafe_rel):
    """场景 1：把 STUDY_KB_ROOT 指向真实库再跑 pytest，必须在收集前 fail-closed。

    仅 `--collect-only`，且断言目标树 byte/mtime 不变——这条测试自己绝不能写真实库。
    """
    unsafe = REPO_ROOT / unsafe_rel if unsafe_rel else REPO_ROOT
    if not unsafe.exists():
        pytest.skip(f"{unsafe} 不存在（本机未初始化），跳过")
    before = _fingerprint(unsafe)

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "--no-header",
         "-p", "no:cacheprovider", "tests/test_sandbox_guard.py"],
        cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "STUDY_KB_ROOT": str(unsafe), "PYTHONUTF8": "1",
             "PYTHONDONTWRITEBYTECODE": "1"})

    combined = result.stdout + result.stderr
    assert result.returncode != 0, combined[-3000:]
    assert "unsafe STUDY_KB_ROOT for tests" in combined, combined[-3000:]
    assert _fingerprint(unsafe) == before, f"{unsafe} 在被拒绝的这轮里被写过"


def test_a_clean_session_allocates_an_isolated_study_kb_root():
    """场景 2（端到端）：在**完全没有** STUDY_KB_ROOT 的环境里跑真 pytest，隔离断言自证。

    子 session 里跑的那两条用例本身就断言"根落在临时区、不等于仓库根、子进程继承同一个根"，
    所以这里只需要它们在干净环境下通过。
    """
    env = {k: v for k, v in os.environ.items() if k != "STUDY_KB_ROOT"}
    guard = "tests/test_sandbox_guard.py"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider",
         f"{guard}::test_session_study_kb_root_is_isolated_and_inherited_by_subprocesses",
         f"{guard}::test_workspace_root_default_no_longer_falls_back_to_the_repo"],
        cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8",
        env={**env, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-2000:]
    assert "2 passed" in result.stdout, result.stdout[-2000:]


def test_per_test_override_keeps_cli_writes_inside_its_own_tmp_path(monkeypatch, tmp_path):
    """场景 3（CLI 部分）：单测覆盖后，真实 CLI 只写自己的 tmp_path，session 根不被碰。"""
    session_root = Path(os.environ["STUDY_KB_ROOT"])
    session_before = sorted(p.name for p in session_root.iterdir())

    monkeypatch.setenv("STUDY_KB_ROOT", str(tmp_path))
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "pipeline.py"), "init-vault"],
        cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", env={**os.environ})

    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "wiki" / "overview.md").is_file(), "CLI 应写进本测试自己的 tmp_path"
    assert sorted(p.name for p in session_root.iterdir()) == session_before, \
        "session 全局根不得被本测试写入"
