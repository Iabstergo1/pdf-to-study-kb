"""隔离 runner（Spec 2 option a）：在**子进程**里 import MinerU 并跑 do_parse(backend="pipeline")。

主进程绝不 import MinerU；`mineru_backend._run_mineru` 用 `sys.executable` 调本文件。
MinerU 3.4.0 的 orchestrated CLI（mineru -b pipeline）在本机起本地 mineru-api 后健康检查 503，
故改走本 in-process do_parse 路径——但仍保持进程隔离（在独立子进程里 import + 跑）。
Windows multiprocessing spawn：do_parse 内部用 ProcessPoolExecutor，故必须 __main__ guard + freeze_support。
强制 pipeline-only：拒绝 vlm-engine / hybrid-engine / 本地 VLM。
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path


def run(input_path, output_dir, *, backend: str = "pipeline", lang: str = "ch", _do_parse=None):
    """子进程内：读 input → do_parse(backend='pipeline') 写 output_dir/<name>/auto/*content_list.json。
    `_do_parse` 仅供测试注入；生产路径在子进程内 import MinerU（主进程不 import）。"""
    if backend != "pipeline":
        raise SystemExit(f"mineru_runner: only 'pipeline' backend allowed, got {backend!r}")
    os.environ.setdefault("MINERU_MODEL_SOURCE", "modelscope")  # 模型源默认 modelscope（环境可覆盖）
    # 模型缓存默认落到仓库内 .mineru-models/（不污染 C 盘 ~/.cache）；环境已设则尊重外部覆盖。
    # 控制 MinerU 自动下载位置的真值是 modelscope 的 MODELSCOPE_CACHE（mineru.json 的 models-dir 在
    # 3.4 自动下载路径上不生效）；项目内存放便于双 agent / 多机复用，且 wiki/ 同级被 gitignore。
    _models_dir = Path(__file__).resolve().parents[2] / ".mineru-models"
    os.environ.setdefault("MODELSCOPE_CACHE", str(_models_dir))
    if _do_parse is None:
        from mineru.cli.common import do_parse as _do_parse   # 仅子进程内 import（进程隔离）
    pdf_bytes = Path(input_path).read_bytes()
    name = Path(input_path).stem
    _do_parse(str(output_dir), [name], [pdf_bytes], [lang], backend="pipeline", parse_method="auto")


def main(argv=None):
    p = argparse.ArgumentParser(description="Isolated MinerU pipeline runner (Spec 2)")
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--backend", default="pipeline", choices=["pipeline"])  # pipeline-only（拒绝 vlm/hybrid）
    p.add_argument("--lang", default="ch")
    args = p.parse_args(argv)
    run(args.input, args.output, backend=args.backend, lang=args.lang)
    print("mineru_runner: done")


if __name__ == "__main__":
    from multiprocessing import freeze_support
    freeze_support()
    main()
