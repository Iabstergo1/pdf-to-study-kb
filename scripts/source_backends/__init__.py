"""源转换后端注册（Spec 1）：按 fmt 选后端。docx/pptx/mineru 留 Spec 2。"""
from __future__ import annotations


class BackendUnavailable(RuntimeError):
    pass


def get_backend(fmt: str):
    """返回该 fmt 的后端模块（提供 convert(src_path, *, out_dir, input_hash) -> BackendResult）。"""
    if fmt == "md":
        from . import markdown_backend
        return markdown_backend
    if fmt == "pdf":
        from . import pymupdf_backend
        return pymupdf_backend
    raise BackendUnavailable(f"no Spec 1 fmt backend for fmt={fmt}（docx/pptx 经 Spec 2 mineru）")


def get_backend_by_name(name: str):
    """按后端名返回模块（Spec 2 dispatcher 用）：markdown / pymupdf / mineru。"""
    if name == "markdown":
        from . import markdown_backend
        return markdown_backend
    if name == "pymupdf":
        from . import pymupdf_backend
        return pymupdf_backend
    if name == "mineru":
        from . import mineru_backend
        return mineru_backend
    raise BackendUnavailable(f"unknown backend: {name}")
