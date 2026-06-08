#!/usr/bin/env python3
"""本地交互式前端服务（纯 Python 标准库，无需 pip / npm）。

启动：
    python scripts/serve.py            # 默认 http://127.0.0.1:8765
    python scripts/serve.py --port 9000

把全部 CLI 命令包进一个网页：导入 PDF、逐步跑命令（实时日志）、卡片式审批切分、
run-book、页内处理 Review-Queue、浏览 study-kb。长命令以子进程作业运行并轮询日志，
快操作直接调用 web_ops。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
WEBAPP_DIR = REPO_ROOT / "webapp"

sys.path.insert(0, str(SCRIPTS_DIR))
os.chdir(REPO_ROOT)  # 所有相对路径（books/...）以仓库根为基准

try:
    import web_ops  # noqa: E402
except ModuleNotFoundError as exc:
    sys.stderr.write(
        f"\n[serve] 缺少依赖 '{exc.name}'。当前 Python 解释器：\n  {sys.executable}\n\n"
        "几乎总是因为用了没装项目依赖的 Python。请改用安装了依赖的环境启动：\n"
        "  · 先安装依赖（全新克隆时）： pip install -r requirements.txt\n"
        "  · 用该环境的 python 运行本服务（serve.py 会用同一解释器跑流水线命令）。\n"
        "  例如 conda： conda activate <你的环境> 然后 python scripts/serve.py\n"
        "          或直接： <环境路径>/python.exe scripts/serve.py\n"
    )
    raise SystemExit(1)

# 允许以作业方式运行的命令白名单 → pipeline.py 子命令
_JOB_COMMANDS = {"profile-pdf", "plan-units", "validate-unit-plan", "run-book"}
# 作业附加参数的 flag 白名单：args 直接进 argv（无 shell 注入），但仍要挡掉 --book 改目标书、
# 或注入其它 pipeline flag（前端只用 --concurrency / --section / --force）。
_ALLOWED_JOB_FLAGS = {"--concurrency", "--section", "--force", "--dry-run", "--batch-size", "--max-revision-retry"}


def _validate_job_args(args: list[str]) -> None:
    for arg in args:
        if str(arg).startswith("-") and str(arg).split("=", 1)[0] not in _ALLOWED_JOB_FLAGS:
            raise web_ops.WebError(f"不允许的作业参数: {arg}")

_JOBS: dict[str, dict] = {}
_BOOK_LAST_JOB: dict[str, str] = {}
_JOBS_LOCK = threading.Lock()

# route() 返回它表示「已直接写出响应」，与「未匹配路由（None → 404）」区分开，
# 避免静态文件请求在已写出后又被补发一个 404。
_HANDLED = object()


# --------------------------------------------------------------------------- #
# 作业（子进程）运行器
# --------------------------------------------------------------------------- #

def _book_active_job(book_id: str) -> dict | None:
    with _JOBS_LOCK:
        job_id = _BOOK_LAST_JOB.get(book_id)
        job = _JOBS.get(job_id) if job_id else None
        if job and job["status"] == "running":
            return dict(job, log_tail=_tail(job["log_path"]))
    return None


def start_job(book_id: str, command: str, extra_args: list[str]) -> dict:
    book_id = web_ops._safe_book_id(book_id)
    if command not in _JOB_COMMANDS:
        raise web_ops.WebError(f"不支持的作业命令: {command}")
    _validate_job_args(extra_args)

    job_id = uuid.uuid4().hex[:12]
    log_dir = REPO_ROOT / "books" / book_id / "pipeline-workspace" / "reports" / "web-jobs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{job_id}-{command}.log"
    argv = [sys.executable, "-u", str(SCRIPTS_DIR / "pipeline.py"), command, "--book", book_id, *extra_args]
    job = {
        "job_id": job_id,
        "book_id": book_id,
        "command": command,
        "argv": argv,
        "status": "running",
        "returncode": None,
        "log_path": str(log_path),
        "started_at": time.time(),
        "finished_at": None,
    }
    # 「检查是否已有运行中的作业」与「登记新作业」必须在同一把锁内原子完成，
    # 否则两个并发请求可能都通过检查、同时对同一本书 spawn 子进程。
    with _JOBS_LOCK:
        active_id = _BOOK_LAST_JOB.get(book_id)
        active = _JOBS.get(active_id) if active_id else None
        if active and active["status"] == "running":
            raise web_ops.WebError("该书已有正在运行的作业，请等它结束")
        _JOBS[job_id] = job
        _BOOK_LAST_JOB[book_id] = job_id

    threading.Thread(target=_run_job, args=(job, log_path), daemon=True).start()
    return {"job_id": job_id, "command": command, "status": "running"}


def _run_job(job: dict, log_path: Path) -> None:
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    try:
        with open(log_path, "w", encoding="utf-8") as logf:
            logf.write(f"$ {' '.join(job['argv'])}\n\n")
            logf.flush()
            proc = subprocess.Popen(
                job["argv"], cwd=str(REPO_ROOT), env=env,
                stdout=logf, stderr=subprocess.STDOUT,
            )
            job["pid"] = proc.pid
            returncode = proc.wait()
    except Exception as exc:  # noqa: BLE001
        returncode = -1
        try:
            with open(log_path, "a", encoding="utf-8") as logf:
                logf.write(f"\n[serve] 作业启动失败: {exc}\n")
        except OSError:
            pass
    with _JOBS_LOCK:
        job["status"] = "done" if returncode == 0 else "failed"
        job["returncode"] = returncode
        job["finished_at"] = time.time()


def _tail(log_path: str, max_chars: int = 16000) -> str:
    try:
        text = Path(log_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[-max_chars:]


def job_status(job_id: str) -> dict:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            raise web_ops.WebError(f"未知作业: {job_id}")
        snapshot = dict(job)
    snapshot["log_tail"] = _tail(snapshot["log_path"])
    snapshot.pop("argv", None)
    return snapshot


# --------------------------------------------------------------------------- #
# HTTP 处理
# --------------------------------------------------------------------------- #

class Handler(BaseHTTPRequestHandler):
    server_version = "PdfStudyKB/1.0"

    def log_message(self, *args):  # 静默默认访问日志
        pass

    # ---- 工具 ----
    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str):
        if not path.exists():
            self._send_json({"error": "not found"}, 404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _read_json(self) -> dict:
        raw = self._read_body()
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise web_ops.WebError("请求体不是合法 JSON")

    def _handle(self, method: str):
        parsed = urlparse(self.path)
        path = parsed.path
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        try:
            result = route(method, path, query, self)
            if result is _HANDLED:
                return  # 静态文件等已直接写出响应
            if result is None:
                self._send_json({"error": "not found", "path": path}, 404)
            elif isinstance(result, tuple):
                data, status = result
                self._send_json(data, status)
            else:
                self._send_json(result)
        except web_ops.WebError as exc:
            self._send_json({"error": str(exc)}, 400)
        except BrokenPipeError:
            pass
        except Exception as exc:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            self._send_json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")


def _serve_static(path: str, handler: Handler):
    if path == "/" or path == "":
        handler._send_file(WEBAPP_DIR / "index.html", "text/html; charset=utf-8")
        return True
    if path.startswith("/static/"):
        rel = path[len("/static/"):]
        target = (WEBAPP_DIR / rel).resolve()
        # is_relative_to 按路径分量判断，避免 startswith 的兄弟目录前缀绕过
        if not target.is_relative_to(WEBAPP_DIR.resolve()):
            handler._send_json({"error": "forbidden"}, 403)
            return True
        ctype = {
            ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".html": "text/html; charset=utf-8",
            ".svg": "image/svg+xml",
        }.get(target.suffix, "application/octet-stream")
        handler._send_file(target, ctype)
        return True
    return False


def route(method: str, path: str, query: dict, handler: Handler):
    if method == "GET" and _serve_static(path, handler):
        return _HANDLED  # 已直接写出响应，勿再发 404
    parts = [unquote(p) for p in path.strip("/").split("/") if p]

    # /api/books
    if parts == ["api", "books"] and method == "GET":
        return {"books": web_ops.list_books()}

    if parts == ["api", "books", "import"] and method == "POST":
        book_id = query.get("book_id") or web_ops.slugify(query.get("title", ""))
        return web_ops.import_book(
            book_id=book_id,
            title=query.get("title", book_id),
            pdf_bytes=handler._read_body(),
            filename=query.get("filename", "source.pdf"),
            force=query.get("force") == "1",
        )

    if parts == ["api", "jobs"] and method == "GET":
        return {"jobs": list(_JOBS.keys())}
    if len(parts) == 3 and parts[:2] == ["api", "jobs"] and method == "GET":
        return job_status(parts[2])

    # /api/books/{id}/...
    if len(parts) >= 3 and parts[0] == "api" and parts[1] == "books":
        book_id = parts[2]
        rest = parts[3:]
        return route_book(method, book_id, rest, query, handler)
    return None


def route_book(method: str, book_id: str, rest: list[str], query: dict, handler: Handler):
    if rest == ["status"] and method == "GET":
        return web_ops.book_status(book_id)
    if rest == ["active-job"] and method == "GET":
        return {"job": _book_active_job(book_id)}

    if rest == ["jobs"] and method == "POST":
        body = handler._read_json()
        return start_job(book_id, body.get("command", ""), list(body.get("args", [])))

    # 切分候选审批
    if rest == ["candidates"] and method == "GET":
        return web_ops.get_candidates(book_id)
    if rest == ["candidates", "op"] and method == "POST":
        body = handler._read_json()
        return web_ops.apply_plan_op(book_id, body.get("op", ""), body.get("unit_id", ""), body.get("payload"))
    if rest == ["candidates", "auto-resolve"] and method == "POST":
        return web_ops.auto_resolve_candidates(book_id)
    if rest == ["candidates", "finalize"] and method == "POST":
        return web_ops.finalize_plan(book_id)

    # Review-Queue
    if rest == ["review-queue"] and method == "GET":
        return {"items": web_ops.list_review_queue(book_id)}
    if len(rest) == 3 and rest[0] == "review-queue" and rest[2] == "draft" and method == "GET":
        return web_ops.get_unit_draft(book_id, rest[1])
    if len(rest) == 3 and rest[0] == "review-queue" and rest[2] == "draft" and method == "POST":
        return web_ops.save_unit_draft(book_id, rest[1], handler._read_json().get("content", ""))
    if len(rest) == 3 and rest[0] == "review-queue" and rest[2] == "publish" and method == "POST":
        return web_ops.publish_unit(book_id, rest[1])

    if rest == ["rebuild-indexes"] and method == "POST":
        return web_ops.rebuild_indexes(book_id)

    # study-kb 浏览
    if rest == ["kb", "tree"] and method == "GET":
        return web_ops.kb_tree(book_id)
    if rest == ["kb", "file"] and method == "GET":
        return web_ops.kb_file(book_id, query.get("path", ""))
    return None


def main():
    parser = argparse.ArgumentParser(description="PDF to Study KB 本地前端")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}"
    print(f"PDF to Study KB 前端已启动：{url}")
    print("按 Ctrl+C 停止。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
        server.shutdown()


if __name__ == "__main__":
    main()
