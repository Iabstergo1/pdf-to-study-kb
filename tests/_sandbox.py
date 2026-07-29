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
