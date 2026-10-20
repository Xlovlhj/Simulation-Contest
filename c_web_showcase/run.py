"""Serve the C-problem showcase locally and run its original Q1 model on demand.

Usage: python3 c_web_showcase/run.py --port 8765 --no-open
"""
from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from urllib.parse import unquote, urlsplit
import uuid
import webbrowser

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / ".runtime"
PUBLIC_DIRS = {"js", "css", "fonts", "vendor", "data", "assets"}
PUBLIC_ROOT_FILES = {"index.html", "LICENSE", "LICENSE-CONTENT.md", "THIRD_PARTY_NOTICES.md"}
PUBLIC_EXTENSIONS = {
    ".js", ".css", ".json", ".svg", ".png", ".jpg", ".jpeg", ".webp",
    ".woff2", ".woff", ".ttf", ".ico", ".csv",
}
sys.dont_write_bytecode = True


def worker_environment() -> dict:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    env["TMPDIR"] = str(RUNTIME / "tmp")
    env.pop("PYTHONPATH", None)
    return env


class RunManager:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.child: subprocess.Popen | None = None
        self.started = 0.0
        self.state = {"status": "idle", "run_id": None, "computed_now": False}
        (RUNTIME / "tmp").mkdir(parents=True, exist_ok=True)
        try:
            check = subprocess.run(
                [sys.executable, "-B", str(ROOT / "compute.py"), "--probe"],
                cwd=ROOT, env=worker_environment(), capture_output=True,
                text=True, encoding="utf-8", timeout=30,
            )
            if check.returncode:
                raise RuntimeError("Dependency check did not finish successfully")
            available = json.loads(check.stdout)
            self.state.update(available)
        except (OSError, ValueError, subprocess.SubprocessError, RuntimeError):
            self.state.update(
                status="unavailable",
                error="真实复算环境检查失败；仍可浏览论文归档结果。",
            )

    def snapshot(self) -> dict:
        with self.lock:
            result = dict(self.state)
            if result["status"] == "running":
                result["elapsed_s"] = time.monotonic() - self.started
            return result

    def start(self) -> tuple[int, dict]:
        with self.lock:
            if self.state["status"] == "unavailable":
                return HTTPStatus.SERVICE_UNAVAILABLE, dict(self.state)
            if self.state["status"] == "running":
                return HTTPStatus.CONFLICT, dict(self.state)
            run_id = uuid.uuid4().hex
            run_dir = RUNTIME / "runs" / run_id
            run_dir.mkdir(parents=True)
            output = run_dir / "result.json"
            self.started = time.monotonic()
            self.state = {
                "status": "running", "run_id": run_id,
                "elapsed_s": 0.0, "computed_now": False,
                "label": "正在调用原始 Q1 求解器",
            }
            try:
                with (run_dir / "worker.log").open("wb") as log:
                    self.child = subprocess.Popen(
                        [sys.executable, "-B", str(ROOT / "compute.py"),
                         "--output", str(output), "--run-id", run_id],
                        cwd=ROOT, env=worker_environment(),
                        stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                    )
            except OSError:
                self.state.update(status="failed", error="无法启动工作区中的 Q1 计算进程。")
                return HTTPStatus.INTERNAL_SERVER_ERROR, dict(self.state)
            threading.Thread(
                target=self._finish, args=(self.child, output, run_id), daemon=True,
            ).start()
            return HTTPStatus.ACCEPTED, dict(self.state)

    def _finish(self, child: subprocess.Popen, output: Path, run_id: str) -> None:
        try:
            child.wait(timeout=120)
            if output.is_file():
                result = json.loads(output.read_text(encoding="utf-8"))
            else:
                result = {"status": "failed", "run_id": run_id, "error": "求解器未生成结果。"}
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
            result = {"status": "failed", "run_id": run_id, "error": "Q1 计算超过 120 秒，已停止该计算。"}
        except (OSError, ValueError):
            result = {"status": "failed", "run_id": run_id, "error": "无法读取本次计算结果。"}
        result.setdefault("elapsed_s", time.monotonic() - self.started)
        with self.lock:
            if self.state.get("run_id") == run_id:
                self.state = result
            if self.child is child:
                self.child = None

    def close(self) -> None:
        with self.lock:
            child = self.child
        if child and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


class ShowcaseServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, manager: RunManager) -> None:
        self.manager = manager
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = "CShowcase/1.0"
    sys_version = ""

    def log_message(self, _format, *_args) -> None:
        # Avoid clutter and do not put arbitrary request strings in log files.
        return

    def _headers(self, status: int, content_type: str, length: int) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()

    def _json(self, status: int, value: dict, head: bool = False) -> None:
        body = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self._headers(status, "application/json; charset=utf-8", len(body))
        if not head:
            self.wfile.write(body)

    def _local_host(self) -> bool:
        port = self.server.server_port
        return self.headers.get("Host", "") in {f"127.0.0.1:{port}", f"localhost:{port}"}

    def _same_origin(self) -> bool:
        if not self._local_host():
            return False
        origin = self.headers.get("Origin")
        if origin and origin != f"http://{self.headers.get('Host')}":
            return False
        return self.headers.get("Sec-Fetch-Site", "same-origin") in {"same-origin", "none"}

    def do_GET(self) -> None:
        self._get()

    def do_HEAD(self) -> None:
        self._get(head=True)

    def _get(self, head: bool = False) -> None:
        if not self._local_host():
            self._json(HTTPStatus.FORBIDDEN, {"error": "仅接受本机访问。"}, head)
            return
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        if path == "/api/run-status":
            if not self._same_origin():
                self._json(HTTPStatus.FORBIDDEN, {"error": "仅接受同源访问。"}, head)
            else:
                self._json(HTTPStatus.OK, self.server.manager.snapshot(), head)
            return
        if path == "/":
            path = "/index.html"
        relative = path.lstrip("/")
        raw_parts = relative.split("/")
        if (
            not path.startswith("/") or "\\" in path or "\x00" in path
            or "//" in path
            or any(p in {"", ".", ".."} or p.startswith(".") for p in raw_parts)
            or ".local." in path
        ):
            self._json(HTTPStatus.NOT_FOUND, {"error": "文件不存在。"}, head)
            return
        candidate = ROOT / relative
        allowed = (
            relative in PUBLIC_ROOT_FILES
            or (
                raw_parts[0] in PUBLIC_DIRS
                and candidate.suffix.lower() in PUBLIC_EXTENSIONS
                and len(raw_parts) > 1
            )
        )
        if not allowed or not candidate.resolve().is_relative_to(ROOT):
            self._json(HTTPStatus.NOT_FOUND, {"error": "文件不存在。"}, head)
            return
        # Do not serve symlinks, even if their target happens to be inside ROOT.
        ancestor = candidate
        while ancestor != ROOT:
            if ancestor.is_symlink():
                self._json(HTTPStatus.NOT_FOUND, {"error": "文件不存在。"}, head)
                return
            ancestor = ancestor.parent
        try:
            if not candidate.is_file():
                raise FileNotFoundError
            body = candidate.read_bytes()
        except OSError:
            self._json(HTTPStatus.NOT_FOUND, {"error": "文件不存在。"}, head)
            return
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if candidate.suffix.lower() == ".js":
            content_type = "text/javascript"
        if content_type.startswith("text/") or content_type == "application/json":
            content_type += "; charset=utf-8"
        self._headers(HTTPStatus.OK, content_type, len(body))
        if not head:
            self.wfile.write(body)

    def do_POST(self) -> None:
        if not self._same_origin():
            self._json(HTTPStatus.FORBIDDEN, {"error": "仅接受当前网页的同源请求。"})
            return
        if urlsplit(self.path).path != "/api/run-baseline":
            self._json(HTTPStatus.NOT_FOUND, {"error": "接口不存在。"})
            return
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "请求体必须是 JSON 空对象。"})
            return
        if self.headers.get("Transfer-Encoding"):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "不接受分块请求体。"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 128:
                raise ValueError
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict) or body:
                raise ValueError
        except (ValueError, UnicodeError):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "此接口仅复算固定 Q1 基准；请发送空对象 {}。"})
            return
        code, state = self.server.manager.start()
        self._json(code, state)

    def do_OPTIONS(self) -> None:
        self._json(HTTPStatus.FORBIDDEN, {"error": "不允许跨域调用。"})


def main() -> None:
    parser = argparse.ArgumentParser(description="启动 C 题网页与原模型 Q1 复算接口。")
    parser.add_argument("--port", type=int, default=8765, help="本机端口，默认 8765")
    parser.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("port must be between 0 and 65535")
    manager = RunManager()
    try:
        server = ShowcaseServer(("127.0.0.1", args.port), manager)
    except OSError as exc:
        manager.close()
        parser.exit(1, f"无法监听端口 {args.port}：{exc}\n")
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"C 题演示网页：{url}", flush=True)
    print("按 Ctrl+C 停止网页服务与本服务启动的计算。", flush=True)
    print(f"可选 Q1 原模型复算：{manager.snapshot()['status']}（不影响网页展示）。", flush=True)
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\n网页服务已停止。", flush=True)
    finally:
        server.server_close()
        manager.close()


if __name__ == "__main__":
    main()
