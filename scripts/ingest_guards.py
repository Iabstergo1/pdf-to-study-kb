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


def _normalize_rel(rel_path: str) -> str | None:
    """归一化 vault 相对路径；绝对路径/盘符/含 .. 段一律返回 None（拒绝，防穿越逃出 vault）。"""
    p = rel_path.replace("\\", "/")
    if p.startswith("/") or re.match(r"^[A-Za-z]:", p):
        return None
    parts = [s for s in p.split("/") if s not in ("", ".")]
    if not parts or ".." in parts:
        return None
    return "/".join(parts)


def in_write_scope(rel_path: str, write_scope: list[str]) -> bool:
    p = _normalize_rel(rel_path)
    if p is None:
        return False
    return any(_glob_to_re(g).match(p) for g in write_scope)


def missing_write_paths(vault, write_set) -> list[str]:
    """write_set 里在磁盘上查无此页的路径（只读；保持入参顺序）。

    window-done 的 `--writes` 是记账台账，此前从不与磁盘对账：resolve-concept 会把 mention
    归一成 slug 文件名（`Buffer Pool` → `buffer-pool.md`），写作方却按自以为的名字记账，
    台账与产出就此漂移；（引入本对账时 concept 页尚不受 unaccounted-write 约束——2026-07-18 起
    记账义务已覆盖全部非 source 页，本对账仍是最早的 fail-fast 拦截点。）
    不安全路径（绝对/穿越）一律计入——不得因归一化拒绝而被当作"存在"放过。"""
    vault = Path(vault)
    out: list[str] = []
    for rel in write_set:
        safe = _normalize_rel(str(rel))
        if safe is None or not (vault / safe).exists():
            out.append(str(rel))
    return out


def _sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _read_managed_by(target: Path) -> str | None:
    """轻量只读：取页 frontmatter 的 managed_by；无 frontmatter / 无该键 → None。"""
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    m = re.search(r"(?m)^managed_by:\s*(\S+)\s*$", text[3:end])
    return m.group(1) if m else None


def can_overwrite(vault, rel_path: str, snapshot_entries: list[dict]) -> tuple[bool, str]:
    """覆盖保护（全过才许覆盖）。目标页不存在 = 新建，放行（边界另由 in_write_scope 把守）。
    在 snapshot 中：①managed_by != human ②磁盘 hash == snapshot hash。
    不在 snapshot 但存在于磁盘：只可能是本次 ingest 期间新建/维护的页（workorder 已快照所有既有
    概念/overview/log/source/lessons）——managed_by: pipeline 放行（如 resolve-concept 刚建的概念页、
    跨 ingest 维护的 topic/synthesis）；human / 未知 frontmatter 一律拒（护住人维护页）。"""
    safe = _normalize_rel(rel_path)
    if safe is None:
        return False, "unsafe path (absolute or traversal)"
    target = Path(vault) / safe
    if not target.exists():
        return True, "new page"
    entry = next((e for e in snapshot_entries if e.get("path") == safe), None)
    if entry is None:
        if _read_managed_by(target) == "pipeline":
            return True, "pipeline page created this run (not in snapshot)"
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
