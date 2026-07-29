"""query-session 目录契约 + Q1 确定性检查（spec §7.1/§11；零 LLM）。

session 只落文件系统 pipeline-workspace/query-sessions/<run_id>/，不进 artifacts 表（spec §3.4）。
"""
from __future__ import annotations

import json
from pathlib import Path

_REQUIRED_QUERY = ["question.md", "answer.md"]
_REQUIRED_SAVED_FILES = ["decision.md"]
_REQUIRED_SAVED_LISTS = {  # 通用 JSON 清单：文件名 -> 是否必须非空
    "related_pages.json": False,
    "evidence_refs.json": True,
}


def canonical_vault_rel(value) -> str:
    """Return one canonical vault-relative path or raise ``ValueError``.

    Query-session write ledgers are compared by path identity.  Accepting aliases such as ``./``,
    duplicate separators, or Windows backslashes would let the write guard and the on-disk vault name
    the same file differently, so the contract rejects them instead of silently normalising them.
    """
    if not isinstance(value, str):
        raise ValueError("path must be a string")
    if not value or value != value.strip():
        raise ValueError("path must be non-empty with no surrounding whitespace")
    if "\\" in value:
        raise ValueError("path must use canonical '/' separators (backslash aliases are forbidden)")
    if ":" in value or any(ord(ch) < 32 for ch in value):
        raise ValueError("path contains a drive/stream prefix or control character")
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("path must be relative and canonical (no /./, //, .., or trailing /)")
    if any(part != part.strip() or part.endswith(".") for part in parts):
        raise ValueError("path segments must not have leading/trailing whitespace or trailing dots")
    return value


def _read_json_list(path: Path) -> list:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path.name} is not valid readable UTF-8 JSON: {exc}") from exc
    if not isinstance(data, list):
        raise ValueError(f"{path.name} must be a JSON list")
    return data


def load_candidate_write_set(session_dir) -> list[str]:
    """Load the non-empty, unique, canonical candidate path list."""
    path = Path(session_dir) / "candidate_write_set.json"
    if not path.exists():
        raise ValueError("missing candidate_write_set.json")
    data = _read_json_list(path)
    if not data:
        raise ValueError("candidate_write_set.json must be non-empty after save")
    out: list[str] = []
    for index, value in enumerate(data):
        try:
            out.append(canonical_vault_rel(value))
        except ValueError as exc:
            raise ValueError(f"candidate_write_set.json[{index}] invalid: {exc}") from exc
    if len(out) != len(set(out)):
        raise ValueError("candidate_write_set.json contains duplicate paths")
    return out


def load_write_authorizations(session_dir, *, allow_missing=False, allow_empty=False) -> list[dict]:
    """Load strict ``{path, mode: new}`` entries, preserving their deterministic list order."""
    path = Path(session_dir) / "write_authorizations.json"
    if not path.exists():
        if allow_missing:
            return []
        raise ValueError("missing write_authorizations.json")
    data = _read_json_list(path)
    if not data and not allow_empty:
        raise ValueError("write_authorizations.json must be non-empty after save")
    out: list[dict] = []
    for index, entry in enumerate(data):
        if not isinstance(entry, dict) or set(entry) != {"path", "mode"}:
            raise ValueError(
                f"write_authorizations.json[{index}] must contain exactly path and mode")
        if entry["mode"] != "new":
            raise ValueError(f"write_authorizations.json[{index}].mode must be 'new'")
        try:
            rel = canonical_vault_rel(entry["path"])
        except ValueError as exc:
            raise ValueError(f"write_authorizations.json[{index}].path invalid: {exc}") from exc
        out.append({"path": rel, "mode": "new"})
    paths = [entry["path"] for entry in out]
    if len(paths) != len(set(paths)):
        raise ValueError("write_authorizations.json contains duplicate paths")
    return out


def check_session(session_dir, *, saved: bool) -> list[str]:
    """返回问题清单；空列表 = Q1 通过。saved=True 时按 /kb-save 后的完整契约检查。"""
    d = Path(session_dir)
    if not d.is_dir():
        return [f"session dir not found: {d}"]
    problems: list[str] = []
    for name in _REQUIRED_QUERY:
        if not (d / name).exists():
            problems.append(f"missing {name}")
    if not saved:
        return problems
    for name in _REQUIRED_SAVED_FILES:
        if not (d / name).exists():
            problems.append(f"missing {name} (为什么保存/写了哪些页/证据/为何不污染概念)")
    for name, must_be_nonempty in _REQUIRED_SAVED_LISTS.items():
        f = d / name
        if not f.exists():
            problems.append(f"missing {name}")
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            problems.append(f"{name} is not valid readable UTF-8 JSON")
            continue
        if not isinstance(data, list):
            problems.append(f"{name} must be a JSON list")
        elif must_be_nonempty and not data:
            problems.append(f"{name} must be non-empty after save")
    candidates = authorizations = None
    try:
        candidates = load_candidate_write_set(d)
    except ValueError as exc:
        problems.append(str(exc))
    try:
        authorizations = load_write_authorizations(d)
    except ValueError as exc:
        problems.append(str(exc))
    if candidates is not None and authorizations is not None:
        authorized = {entry["path"] for entry in authorizations}
        if set(candidates) != authorized:
            problems.append("write_authorizations.json paths must exactly match candidate_write_set.json")
    return problems
