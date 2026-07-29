"""不可变证据落盘的共用底座：路径边界 + canonical 序列化（纯函数，无业务语义）。

`adopt-vault` 与 `reuse-source` 都要把"当时看到的字节"冻成可重放证据，因而共享同一组
要求：输出路径不得经 symlink/junction 重定向出锚点、内容哈希按流式计算、JSON 以 canonical
形式（UTF-8、sort_keys、indent=2、结尾换行）序列化——**canonical 形式是重放契约的一部分**，
任何格式变化都会让历史 manifest 的 sha256 对不上。

这些能力此前只存在于 `vault_adoption` 的下划线私有名里，被 `source_reuse` 跨模块绑定；
提到这里后两侧都用公开 API，且各自保留自己的异常类型（见 EvidenceBoundaryError）。
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path


class EvidenceBoundaryError(Exception):
    """证据路径/序列化契约违规的公共基类。

    `vault_adoption.AdoptionError` 与 `source_reuse.ReuseError` 都继承它，但**互不为别名**：
    两条旁路的失败必须能分开捕获、分开报告。
    """


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def json_bytes(value: dict) -> bytes:
    """Canonical evidence JSON：改这里等于改所有历史 manifest 的重放契约。"""
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def resolved_inside(path, root) -> Path | None:
    """返回 strict-resolved path；父目录 symlink/junction 逃出 root 时返回 None。"""
    try:
        resolved = Path(path).resolve(strict=True)
    except OSError:
        return None
    root = Path(root).resolve(strict=True)
    return resolved if resolved != root and root in resolved.parents else None


def reject_lock(snapshot: dict | None, ttl_seconds: int, allowed_holder: str | None = None, *,
                command: str = "this command",
                error: type[Exception] = EvidenceBoundaryError) -> None:
    """两条零 LLM 旁路共用的 vault 锁前置检查：活锁直接拒，stale 锁提示先 unlock。

    `command` 只影响提示语——之前这段写死了 "before adopt-vault"，reuse-source 复用它时
    会给出错误的命令名。
    """
    if not snapshot or not snapshot.get("lock"):
        return
    lock = snapshot["lock"]
    if allowed_holder is not None and lock["holder"] == allowed_holder:
        return
    try:
        heartbeat = datetime.fromisoformat(lock["heartbeat_at"])
        if heartbeat.tzinfo is None:
            heartbeat = heartbeat.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - heartbeat).total_seconds()
    except (TypeError, ValueError):
        age = 0
    if age <= ttl_seconds:
        raise error(f"active vault lock held by {lock['holder']} since {lock['started_at']}")
    raise error(f"stale vault lock held by {lock['holder']}; run unlock before {command}")


def assert_direct_contained(path, root, label: str, *,
                            error: type[Exception] = EvidenceBoundaryError) -> None:
    """拒绝输出路径或最近既有祖先经 symlink/junction 重定向到锚点外（或别处）。"""
    root = Path(root).resolve(strict=True)
    candidate = Path(path)
    while not candidate.exists():
        if candidate.is_symlink():
            raise error(f"{label} uses a symlink outside its direct workspace path: {candidate}")
        if candidate == root or candidate.parent == candidate:
            break
        candidate = candidate.parent
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise error(f"cannot resolve {label}: {candidate}") from exc
    if resolved != root and root not in resolved.parents:
        raise error(f"{label} escapes workspace boundary: {candidate} -> {resolved}")
    lexical = Path(os.path.abspath(str(candidate)))
    if candidate.is_symlink() or os.path.normcase(str(resolved)) != os.path.normcase(str(lexical)):
        raise error(f"{label} uses a redirected symlink/junction path: {candidate} -> {resolved}")
