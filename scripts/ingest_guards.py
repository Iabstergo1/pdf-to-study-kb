"""/ingest 写入守卫（spec §9）：写入边界 glob、覆盖保护三条件、registry hash 守卫。纯函数 + 只读。"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path


def _glob_to_re(pattern: str) -> re.Pattern:
    out, i = [], 0
    while i < len(pattern):
        if pattern[i: i + 2] == "**":
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def in_write_scope(rel_path: str, write_scope: list[str]) -> bool:
    p = rel_path.replace("\\", "/")
    return any(_glob_to_re(g).match(p) for g in write_scope)


def _sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def can_overwrite(vault, rel_path: str, snapshot_entries: list[dict]) -> tuple[bool, str]:
    """覆盖保护（spec §9 三条件，全过才许覆盖）：①在 snapshot 中 ②managed_by != human ③磁盘 hash == snapshot hash。
    目标页不存在 = 新建，放行（写入边界另由 in_write_scope 把守）。"""
    target = Path(vault) / rel_path
    if not target.exists():
        return True, "new page"
    entry = next((e for e in snapshot_entries if e.get("path") == rel_path), None)
    if entry is None:
        return False, "not in work-order snapshot"
    if entry.get("managed_by") == "human":
        return False, "managed_by human"
    if _sha256_file(target) != entry.get("sha256"):
        return False, "disk hash changed since snapshot"
    return True, "ok"


def registry_fresh(vault, expected_hash: str) -> bool:
    """开工守卫：磁盘 _registry.yaml 的 hash 必须等于 work order 记录的 hash（spec §9）。"""
    reg = Path(vault) / "concepts" / "_registry.yaml"
    if not reg.exists():
        return expected_hash == ""
    return _sha256_file(reg) == expected_hash
