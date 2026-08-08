"""发布前门禁：git 已跟踪的文件里不得出现个人环境痕迹。

这个仓库是拿来公开的，而用它的知识库是使用者的私有数据。两者混在一起最容易发生的
事故，是开发机上的绝对路径、用户名或某个具体部署的目录布局被顺手写进代码、注释、
文档或测试 fixture，然后随仓库一起发布出去。

lint 抓不到这类问题——它们语法完全合法。本脚本只做一件事：对全部已跟踪文件做纯文本
匹配，命中即非零退出并列出文件与行号。

用法::

    python scripts/check_publishable.py            # 检查全部已跟踪文件
    python scripts/check_publishable.py --list     # 只列出会被检查的文件

豁免有两级，都要求显式声明，都不提供目录级或模式级的整体豁免——那会让门禁悄悄失效：

1. **行内标记** ``publishable-allow``：适合源码与散文行。
2. **基线文件** ``scripts/publishable-baseline.txt``：适合**加不了行内标记**的位置。
   本仓库的 20 条 warning 全部落在 PowerShell 示例代码块里，而且多数行以反引号续行结尾——
   行内注释会破坏读者复制粘贴的命令。基线按 ``路径 + 命中文本`` 记账（**不按行号**，
   文档行号天天漂），因此示例移动位置不会让基线失效，而同一文件里出现**新的**路径形态
   （不同文本）仍会被报出来。

为什么需要第 2 级：这批 warning 是稳定不变的一组通用占位（``C:\\books``、``C:\\tmp`` 等）。
一个常年亮着 20 盏黄灯的门禁，新增的第 21 条不会有人看见——那才是真正的失效。
基线让稳态回到"0 条新增"，同时**显式记录**已接受的是哪 20 条，而不是把它们藏起来。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

#: 行内豁免标记。放在需要保留示例路径的那一行上。
ALLOW_MARKER = "publishable-allow"

#: 已接受的 warning 基线（相对仓库根）。每行 ``<路径><TAB><命中文本>``；``#`` 开头为注释。
BASELINE_REL = "scripts/publishable-baseline.txt"


def load_baseline(repo_root: Path) -> set:
    """读基线，返回 {(path, text)}。缺失即空集——基线是可选的，不存在不报错。"""
    path = repo_root / BASELINE_REL
    out = set()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, _, matched = line.partition("\t")
        if matched:
            out.add((name.strip(), matched))
    return out


def write_baseline(repo_root: Path, warnings) -> Path:
    """把当前 warning 全量写成基线（``--update-baseline``）。"""
    path = repo_root / BASELINE_REL
    rows = sorted({(f.path, f.text) for f in warnings})
    lines = [
        "# check_publishable 已接受的 warning 基线（由 --update-baseline 生成）。",
        "# 格式：<git 已跟踪路径><TAB><命中文本>。按路径+文本记账，不按行号——文档行号会漂。",
        "# 这些是文档/测试里的通用占位路径；新增条目请连同理由一起 review，不要顺手 --update-baseline。",
        "",
    ]
    lines += [f"{name}\t{matched}" for name, matched in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path

#: 不做文本扫描的后缀（二进制或体积大且无意义）。
_BINARY_SUFFIXES = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz", ".xz",
    ".woff", ".woff2", ".ttf", ".otf", ".so", ".dll", ".dylib", ".exe",
    ".sqlite", ".db", ".parquet", ".npy", ".npz",
})

# 规则分两级，因为两类命中的置信度差得很远：
#
#   error —— 路径里带用户名，几乎不可能是有意写的通用示例。命中即失败。
#   warn  —— 只是带盘符的绝对路径。文档里的 `C:\books`、测试里的 `C:/temp` 都是合法的
#            通用占位，不该判失败；但它也可能是作者自己的目录布局（比如某个具体仓库
#            的落盘位置），所以列出来供发布前人工过一眼。
#
# 把 warn 也做成失败会得到一堆虚报——这个项目在机械扫描上已经栽过三次（536→406→0），
# 虚报会让门禁被整体绕过，比漏报更糟。需要在 CI 上收紧时用 --strict。
ERROR_PATTERNS = (
    (
        "windows-user-home",
        re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s\"'<>|]+"),
        "Windows 用户目录绝对路径",
    ),
    (
        "posix-user-home",
        re.compile(r"/(?:home|Users)/[A-Za-z0-9._-]+/"),
        "Linux/macOS 用户目录绝对路径",
    ),
)

WARN_PATTERNS = (
    (
        "windows-drive-path",
        # 盘符 + 分隔符 + 至少一个路径段。排除 Users（ERROR 规则已覆盖，避免重复报告）。
        re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]+(?!Users)[A-Za-z0-9._-]+[\\/]"),
        "带盘符的绝对路径（可能是通用示例，也可能是作者的目录布局）",
    ),
)


class Finding:
    __slots__ = ("path", "lineno", "rule", "text")

    def __init__(self, path: str, lineno: int, rule: str, text: str) -> None:
        self.path = path
        self.lineno = lineno
        self.rule = rule
        self.text = text

    def __str__(self) -> str:
        return f"{self.path}:{self.lineno}: [{self.rule}] {self.text}"


def tracked_files(repo_root: Path) -> list[str]:
    """git 已跟踪的文件。未跟踪的文件不会被发布，不在射程内。"""
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=str(repo_root),
        capture_output=True, text=True, check=True)
    return [name for name in result.stdout.split("\0") if name]


def _should_scan(repo_root: Path, name: str) -> bool:
    path = repo_root / name
    if path.suffix.lower() in _BINARY_SUFFIXES:
        return False
    try:
        return b"\0" not in path.read_bytes()[:8192]
    except OSError:
        return False


def scan_file(repo_root: Path, name: str) -> tuple[list[Finding], list[Finding]]:
    """返回 (errors, warnings)。"""
    path = repo_root / name
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [], []
    errors: list[Finding] = []
    warnings: list[Finding] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if ALLOW_MARKER in line:
            continue
        for rule, pattern, _label in ERROR_PATTERNS:
            match = pattern.search(line)
            if match:
                errors.append(Finding(name, lineno, rule, match.group(0)))
                break
        else:
            for rule, pattern, _label in WARN_PATTERNS:
                match = pattern.search(line)
                if match:
                    warnings.append(Finding(name, lineno, rule, match.group(0)))
                    break
    return errors, warnings


def scan(repo_root: Path) -> tuple[list[Finding], list[Finding]]:
    """返回 (errors, warnings)，覆盖全部已跟踪且可文本扫描的文件。"""
    errors: list[Finding] = []
    warnings: list[Finding] = []
    for name in tracked_files(repo_root):
        if _should_scan(repo_root, name):
            file_errors, file_warnings = scan_file(repo_root, name)
            errors.extend(file_errors)
            warnings.extend(file_warnings)
    return errors, warnings


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo-root", default=None,
                        help="仓库根目录，缺省为本脚本所在仓库")
    parser.add_argument("--list", action="store_true",
                        help="只列出会被检查的文件，不做扫描")
    parser.add_argument("--strict", action="store_true",
                        help="把**新增** warning 也算作失败（基线内的不算；CI 收紧时用）")
    parser.add_argument("--update-baseline", action="store_true",
                        help=f"把当前全部 warning 写进 {BASELINE_REL}（需人工 review 差异）")
    args = parser.parse_args(argv)

    repo_root = (Path(args.repo_root) if args.repo_root
                 else Path(__file__).resolve().parents[1])

    if args.list:
        for name in tracked_files(repo_root):
            if _should_scan(repo_root, name):
                print(name)
        return 0

    errors, warnings = scan(repo_root)

    if args.update_baseline:
        path = write_baseline(repo_root, warnings)
        print(f"[OK] 基线已写入 {path.relative_to(repo_root).as_posix()}"
              f"（{len({(f.path, f.text) for f in warnings})} 条）；请 review 差异后再提交。")
        return 0

    baseline = load_baseline(repo_root)
    new_warnings = [f for f in warnings if (f.path, f.text) not in baseline]
    accepted = len(warnings) - len(new_warnings)

    for finding in new_warnings:
        print(f"[warn] {finding}")
    for finding in errors:
        print(f"[error] {finding}", file=sys.stderr)

    failed = errors or (new_warnings and args.strict)
    if not failed:
        print(f"[OK] check-publishable: 0 errors, {len(new_warnings)} new warnings"
              f"（基线内已接受 {accepted} 条，见 {BASELINE_REL}）")
        return 0

    print(
        f"\n[FAIL] check-publishable: {len(errors)} errors, "
        f"{len(new_warnings)} new warnings（基线内已接受 {accepted} 条）"
        f"\n确需保留示例路径时：源码/散文行加行内标记 {ALLOW_MARKER!r}；"
        f"加不了标记的位置（如带续行的代码块）用 --update-baseline 并 review 差异；"
        "其余情况请改写成相对路径或占位符。", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
