"""按机型自动安装可选 MinerU 结构化后端（Spec 2）。

用法（先 `conda activate study-kb`，用本环境解释器跑，安装就落到本环境）：
    python scripts/install_mineru.py            # 自动判断：有 N 卡装匹配 CUDA torch，否则 CPU
    python scripts/install_mineru.py --dry-run  # 只打印将执行的命令，不安装

做什么：
  1. `pip install -U mineru[core]`（pipeline 后端足够；PyPI 默认会带 CPU torch）。
  2. `nvidia-smi` 探测 NVIDIA GPU + 驱动支持的 CUDA 版本：
     - 有 GPU → 选不超过驱动 CUDA 的最新 PyTorch CUDA wheel，按 `--no-deps` 把上一步的
       **CPU torch/torchvision 同版本号替换为 `+cuXXX`**（删掉 CPU 版本，不动其它依赖）。
     - 无 GPU → 保留 CPU 构建（CPU-only 机器照常可用）。
  3. fail-closed：探到 GPU 但找不到与已装 torch 版本号匹配的 CUDA wheel 时报错退出、保留可用的
     CPU 构建，不静默装错版本。

选版纯逻辑（candidate_cu_tags / select_wheel / index_url）与副作用（pip / nvidia-smi）分开，前者由
tests/test_install_mineru.py 覆盖。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys

# 已知的 PyTorch CUDA wheel 标签 → 对应 CUDA 版本（新→旧）。新标签出现时在此追加即可。
_CU_TAGS: list[tuple[str, str]] = [
    ("130", "13.0"),
    ("128", "12.8"),
    ("126", "12.6"),
    ("124", "12.4"),
    ("121", "12.1"),
    ("118", "11.8"),
]


def index_url(cu_tag: str) -> str:
    """该 CUDA 标签对应的 PyTorch wheel 索引。"""
    return f"https://download.pytorch.org/whl/cu{cu_tag}"


def candidate_cu_tags(driver_cuda: str | None) -> list[str]:
    """驱动支持的 CUDA 版本 → 候选 cu 标签（新→旧，均不超过驱动 CUDA）。

    driver_cuda 为 None（无 GPU）→ 空列表（走 CPU）。无法解析（有 GPU 但版本异常）→ 全部候选，
    交给 select_wheel 的可用性探测决定。
    """
    if driver_cuda is None:
        return []
    m = re.match(r"(\d+)\.(\d+)", driver_cuda.strip())
    if not m:
        return [tag for tag, _ in _CU_TAGS]
    drv = float(f"{m.group(1)}.{m.group(2)}")
    return [tag for tag, cuda in _CU_TAGS if float(cuda) <= drv]


def select_wheel(driver_cuda: str | None, torch_base: str, prober) -> tuple[str, str] | None:
    """挑第一个（最新且不超过驱动）有对应版本 wheel 的标签 → (index_url, cu_tag)；没有则 None。

    prober(cu_tag, torch_base) -> bool：探测该索引是否存在 `torch==torch_base+cu{tag}`。
    """
    for tag in candidate_cu_tags(driver_cuda):
        if prober(tag, torch_base):
            return index_url(tag), tag
    return None


# ── 以下为副作用（不在纯逻辑测试范围）─────────────────────────────────

def detect_driver_cuda() -> str | None:
    """`nvidia-smi` 表头里的 "CUDA Version: X.Y"（驱动支持的最高 CUDA）；无 GPU / 无 nvidia-smi → None。"""
    try:
        out = subprocess.run(["nvidia-smi"], capture_output=True, text=True, timeout=30)
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    m = re.search(r"CUDA Version:\s*(\d+\.\d+)", out.stdout)
    return m.group(1) if m else None


def _installed_base(python: str, module: str) -> str | None:
    """已装包的纯版本号（剥掉 +cpu / +cuXXX 本地标签）；未装 → None。"""
    code = (f"import {module} as _m, re;"
            f"print(re.match(r'[0-9][0-9.]*', _m.__version__).group())")
    r = subprocess.run([python, "-c", code], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def _probe_pip_index(python: str, cu_tag: str, base: str) -> bool:
    """该 CUDA 索引是否存在 `torch==base+cu{tag}`。"""
    r = subprocess.run(
        [python, "-m", "pip", "index", "versions", "torch", "--index-url", index_url(cu_tag)],
        capture_output=True, text=True)
    return f"{base}+cu{cu_tag}" in (r.stdout + r.stderr)


def _run(cmd: list[str], *, dry_run: bool) -> None:
    print("[run]", " ".join(cmd))
    if not dry_run:
        subprocess.run(cmd, check=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="按机型自动安装 MinerU 结构化后端 + 匹配的 torch 构建")
    ap.add_argument("--dry-run", action="store_true", help="只打印命令，不实际安装")
    args = ap.parse_args(argv)
    py = sys.executable
    dry = args.dry_run

    # 1) MinerU core（默认会带 CPU torch）
    _run([py, "-m", "pip", "install", "-U", "mineru[core]"], dry_run=dry)

    # 2) 探测 GPU / 驱动 CUDA
    driver_cuda = detect_driver_cuda()
    if driver_cuda is None:
        print("[info] 未探测到 NVIDIA GPU（无 nvidia-smi）→ 保留 CPU 版 torch，无需替换。")
        return 0
    print(f"[info] 探测到 NVIDIA GPU，驱动支持 CUDA {driver_cuda}。")

    torch_base = _installed_base(py, "torch")
    if torch_base is None:
        print("[error] mineru 安装后未找到 torch；请检查上一步安装。", file=sys.stderr)
        return 1

    prober = (lambda tag, base: True) if dry else (lambda tag, base: _probe_pip_index(py, tag, base))
    chosen = select_wheel(driver_cuda, torch_base, prober)
    if chosen is None:
        print(f"[error] 探到 GPU 但 PyTorch 无 torch=={torch_base}+cuXXX 匹配 wheel（驱动 CUDA "
              f"{driver_cuda}）。保留 CPU 构建，未替换。可手动指定其它 torch 版本。", file=sys.stderr)
        return 1
    idx, tag = chosen

    # 3) 同版本号 + --no-deps：把 CPU torch/torchvision 替换为 +cuXXX（删掉 CPU 版本，不动其它依赖）
    pkgs = [f"torch=={torch_base}+cu{tag}"]
    tv_base = _installed_base(py, "torchvision")
    if tv_base is not None:
        pkgs.append(f"torchvision=={tv_base}+cu{tag}")
    print(f"[info] 选用 CUDA wheel cu{tag}（{idx}）→ 替换 CPU torch 为 GPU 构建。")
    _run([py, "-m", "pip", "install", "--no-deps", "--index-url", idx, *pkgs], dry_run=dry)

    print(f"[ok] 完成。torch=={torch_base}+cu{tag}"
          + (f"，torchvision=={tv_base}+cu{tag}" if tv_base else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
