"""测试沙箱边界的纯判定（conftest 消费）。

**动机（2026-07-29 事故）**：`CLAUDE.md`/`AGENTS.md` 曾建议 `--basetemp="$PWD\\tmp\\pt-$(Get-Random)"`。
`$PWD` 是**调用处**的当前目录，不是仓库根——在别的工作区（例如某个知识库根目录）跑一次测试，
pytest 的临时树就整个落进那个工作区。实测后果：一个外部工作区被写进 28 批 basetemp、
约 1.3 万文件 / 53 MB，外加一个 `__pycache__`。

对策分两层，都在这里定纯判定、由 `conftest.py` 兑现：
1. **不给 `--basetemp` 时用 pytest 默认值**（系统临时区 `tempfile.gettempdir()` 下的
   `pytest-of-<user>/pytest-<n>`，自带保留最近三次的轮换）——默认本来就是安全的。
2. **给了 `--basetemp` 就必须落在白名单内**：仓库自己的 `tmp/` 之下，或系统临时区之下。
   落到别处一律 fail-closed，因为 pytest 会**整目录清空** basetemp——指错地方不是"留下垃圾"
   那么轻，而是可能删掉别人的东西。

同理 `tmp/` 本身也不接受：它还放着 `resume-packet.txt` 等非测试产物，被整体清空会误伤。

**第二个症状（2026-07-29 同日）**：即使 basetemp 老老实实落在 `<repo>/tmp/`，跑完不清理同样有害。
技能自进化测试会生成假的候选技能包，包里带一个道具文件 `tests/test_ok.py`；这些目录没有
`__init__.py`，pytest 只能按文件名给模块命名，于是**第二个** `test_ok.py` 就报
`import file mismatch` 并让整轮 collection `Interrupted` ——裸跑 `pytest` 直接不可用，
而代码毫无问题。声明式边界写在 `pytest.ini`（`testpaths` / `norecursedirs`），
`collected_outside_tests()` 是运行期兜底：任何被收集到的用例只要不在 `tests/` 之下就 fail-closed。
"""
from __future__ import annotations

import os
from pathlib import Path


def _normalised(path) -> str:
    """不要求路径已存在（basetemp 通常还没建）：只做词法绝对化 + 大小写归一。"""
    return os.path.normcase(os.path.abspath(str(path)))


def _is_below(child: str, parent: str) -> bool:
    """严格"在 parent 之下"（相等不算——basetemp 会被整目录清空）。"""
    return child != parent and child.startswith(parent + os.sep)


def basetemp_violations(basetemp, repo_root, temp_roots) -> list[str]:
    """显式 ``--basetemp`` 的边界判定 → 违规消息列表（空 = 合规）。

    ``basetemp`` 为 None 表示走 pytest 默认值（系统临时区），恒合规。
    纯函数（不触磁盘），fail-closed 语义由调用方兑现。
    """
    if basetemp is None:
        return []
    target = _normalised(basetemp)
    allowed = [_normalised(Path(repo_root) / "tmp")]
    allowed += [_normalised(root) for root in temp_roots]
    if any(_is_below(target, root) for root in allowed):
        return []
    if any(target == root for root in allowed):
        return [
            f"--basetemp must be a fresh subdirectory, not the shared root itself: {basetemp}"
            f"（pytest 会整目录清空 basetemp；{Path(repo_root) / 'tmp'} 下还有 resume-packet.txt 等非测试产物）"
        ]
    return [
        f"--basetemp escapes the allowed sandbox: {basetemp}"
        f"（只允许 {Path(repo_root) / 'tmp'} 或系统临时区之下的子目录；pytest 会整目录清空 basetemp，"
        "指向别的工作区既会污染也可能删除他人数据。不确定就别传 --basetemp，默认值已落在系统临时区）"
    ]


def study_kb_root_violations(value, repo_root, temp_roots) -> list[str]:
    """调用方传入的 ``STUDY_KB_ROOT`` 边界判定 → 违规消息（空 = 合规或未设置）。

    **动机**：`pipeline._workspace_root()` 未设该变量时回落到**仓库根**，设了就无条件采信。
    部分 CLI 测试会自行把它指向 ``tmp_path``，但那是分散约定、不是全局保证。一旦有人把它指向
    一个真实知识库再跑 pytest，任何漏设该变量、又起子进程调 CLI 的用例都会
    **直接写那个真实 vault / SQLite / staging**。

    因此 pytest 侧改成 fail-closed：只接受临时区之下的根（系统临时目录或 `<repo>/tmp/`），
    其余一律拒绝而**不是静默覆盖**——静默覆盖会让调用者不知道自己刚才那条命令本身有数据风险。
    纯函数（不触磁盘）；fail-closed 语义由 ``conftest.py`` 兑现。
    """
    if value is None or not str(value).strip():
        return []
    target = _normalised(value)
    allowed = [_normalised(Path(repo_root) / "tmp")]
    allowed += [_normalised(root) for root in temp_roots]
    if any(_is_below(target, root) for root in allowed):
        return []
    return [
        f"unsafe STUDY_KB_ROOT for tests: {value}"
        f"（只允许 {Path(repo_root) / 'tmp'} 或系统临时区**之下**的目录。"
        "pytest 会把这个根当成知识库工作区：漏设该变量的 CLI 用例会直接写真实 wiki/SQLite/staging。"
        "跑测试时请不要设置它——conftest 会自动分配本次 session 独占的临时根；"
        "真实 STUDY_KB_ROOT 只用于 pytest 之外的人工验收命令）"
    ]


def collected_outside_tests(collected_paths, tests_dir) -> list[str]:
    """收集到的用例路径 → 违规消息列表（空 = 全部落在 ``tests/`` 之内）。

    这套件只有 ``tests/`` 一处；任何别处被收进来的 "测试" 都是残留产物（最典型的是
    ``tmp/`` 里没清理干净的 basetemp，里面躺着技能自进化用例生成的道具 ``tests/test_ok.py``）。
    声明式边界在 ``pytest.ini``；这里是运行期兜底，**报错而不是静默忽略**——静默忽略等于
    让"某个真测试没被跑到"和"某个垃圾没被收集"长得一模一样。

    纯函数（不触磁盘）；fail-closed 语义由调用方兑现。
    """
    root = _normalised(tests_dir)
    outside = sorted({str(p) for p in collected_paths if not _is_below(_normalised(p), root)})
    if not outside:
        return []
    return [
        f"collected {len(outside)} test file(s) outside {tests_dir}: {outside[0]}"
        "（本套件只有 tests/ 一处；别处的多半是没清理的 basetemp 残留。"
        "检查 pytest.ini 的 testpaths / norecursedirs，并清掉 tmp/ 下的遗留目录）"
    ]
