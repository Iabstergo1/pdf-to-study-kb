"""MinerU structured backend（Spec 2）：optional，subprocess 调 MinerU CLI（`-b pipeline`）。

把复杂源（扫描/低文本 PDF、DOCX、PPTX、复杂表格公式图片）归一成项目 artifact 契约
（source.md + blocks.jsonl + chapters.json + parse_report.json + assets/）。
不引入 RAGFlow；不 import MinerU 内部 API（只 subprocess CLI）；硬件 RTX 3050 Ti 4GB →
默认仅 pipeline 后端，禁 vlm/hybrid。
"""
from __future__ import annotations

import shutil
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import source_artifacts as sa  # noqa: F401（C2/C3 归一用）
from source_backends import BackendUnavailable

# adapter 版本：归一逻辑实质变化就 +1，折进 converted 缓存键（与 PROFILER/ARTIFACT/WINDOWING 同规）。
MINERU_ADAPTER_VERSION = "1"
DEFAULT_TIMEOUT_SECONDS = 1800


class MineruRunFailed(RuntimeError):
    """MinerU 子进程失败（非零/超时/输出缺失）。dispatcher/pipeline 据此 fail-closed，不静默回退。"""


def mineru_available() -> bool:
    """是否可调用 MinerU CLI（subprocess 探测，不 import 内部 API）。"""
    return shutil.which("mineru") is not None


def convert(src_path, *, out_dir, input_hash, timeout=DEFAULT_TIMEOUT_SECONDS):
    if not mineru_available():
        raise BackendUnavailable(
            "MinerU 未安装：--backend mineru 需要 MinerU（本项目仅用 pipeline 后端）。"
            "安装见 requirements-mineru.txt（pip install -r requirements-mineru.txt）；"
            "未安装时请用 --backend pymupdf，或 --backend auto 的轻量路径。")
    # 子进程调用 + content_list 归一在 C2/C3 落地。
    raise NotImplementedError("mineru convert 归一在 C2/C3 实现")
