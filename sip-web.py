#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sip-web —— sip（RSS 阅读器）的本地 Web 界面与 HTTP 翻译层
============================================================

把浏览器的 Web 请求翻译成 sip CLI 调用（`sip <命令> --json`），
再把 sip 的 JSON 输出原样返回给前端。

部署方式：把本文件（连同 index.html）放到 sip 可执行文件（sip.exe / sip）
所在的文件夹里，运行：

    python sip-web.py            # 默认 http://127.0.0.1:8777
    python sip-web.py --port 9000
    python sip-web.py --sip /path/to/sip

零第三方依赖，仅用 Python 标准库；Windows / macOS / Linux 通用。

安全：
- 默认只监听 127.0.0.1（本地优先，符合 sip 的产品理念）
- 所有 sip 参数以列表形式传给子进程（不经 shell），并对命令做白名单校验
- 超时保护：所有 CLI 调用都有超时上限，避免卡死
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

VERSION = "1.0.0"
DEFAULT_PORT = 8777
CLI_TIMEOUT = 300          # 单次 CLI 调用超时（秒）；--search 跨源、--update-all 可能较慢
HOST = "127.0.0.1"         # 本地优先：只监听本机回环地址

# Windows 控制台默认 GBK，强制 UTF-8 输出（emoji / 中文日志）
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# ---------------------------------------------------------------------------
# sip 命令白名单：command 名称 -> 构造 CLI 参数列表的函数。
# 这里就是「把 web 请求翻译成 http 调用」的翻译表。
# ---------------------------------------------------------------------------

def _ok(code: int = 0, msg: str = "OK") -> str:
    return json.dumps({"success": True, "code": code, "message": msg}, ensure_ascii=False)


def _err(code: int, message: str, suggestion: str = "", details: str = "") -> str:
    return json.dumps({
        "success": False,
        "error": {"code": code, "message": message, "suggestion": suggestion, "details": details},
    }, ensure_ascii=False)


class SipTranslator:
    """把 HTTP 请求翻译成 sip CLI 子进程调用。"""

    def __init__(self, sip_path: str, timeout: int = CLI_TIMEOUT):
        self.sip_path = sip_path
        self.sip_dir = os.path.dirname(os.path.abspath(sip_path)) or "."
        self.timeout = timeout

    def run(self, args: list[str], timeout: int | None = None) -> dict:
        """执行 sip CLI，返回 {exit, stdout, stderr, raw}。"""
        timeout = timeout or self.timeout
        if not os.path.isfile(self.sip_path):
            raise RuntimeError(f"找不到 sip 可执行文件：{self.sip_path}")
        cmd = [self.sip_path] + args
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                cwd=self.sip_dir,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"sip 调用超时（>{timeout}s）：{' '.join(args[:4])}…")
        except OSError as e:
            raise RuntimeError(f"sip 启动失败：{e}")
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        return {"exit": proc.returncode, "stdout": out, "stderr": err}

    def call_json(self, args: list[str], timeout: int | None = None) -> dict:
        """执行并解析 JSON 输出；返回可直接回给前端的 dict。"""
        r = self.run(args, timeout)
        body = r["stdout"] or r["stderr"]
        try:
            data = json.loads(body)
            # 透传 sip 的结构化输出（success/data/error），并附上退出码
            if isinstance(data, dict):
                data.setdefault("exit", r["exit"])
                return data
        except json.JSONDecodeError:
            pass
        # 非 JSON 输出：包一层，避免前端解析失败
        if r["exit"] == 0:
            return {"success": True, "exit": 0, "data": {"text": body}, "raw": body}
        return {
            "success": False,
            "exit": r["exit"],
            "error": {"code": r["exit"], "message": body or "sip 调用失败", "raw": body},
        }

    def call_text(self, args: list[str], timeout: int | None = None) -> dict:
        """执行并原样返回文本输出。"""
        r = self.run(args, timeout)
        return {"exit": r["exit"], "text": r["stdout"] or r["stderr"], "raw": r}


def find_sip(explicit: str | None) -> str:
    """查找 sip 可执行文件：优先 --sip 参数，其次本脚本同目录。"""
    if explicit:
        p = os.path.abspath(explicit)
        if os.path.isfile(p):
            return p
        raise SystemExit(f"错误：--sip 指定的文件不存在：{p}")

    here = os.path.dirname(os.path.abspath(__file__))
    for name in ("sip.exe", "sip", "sip.cmd", "sip.bat"):
        p = os.path.join(here, name)
        if os.path.isfile(p):
            return p

    # 兜底：PATH 里的 sip
    from shutil import which
    w = which("sip")
    if w:
        return w

    raise SystemExit(
        "错误：在脚本同目录找不到 sip 可执行文件（sip.exe / sip）。\n"
        f"请把本文件（连同 index.html）复制到 sip 所在的文件夹后再运行，\n"
        f"或用 --sip 指定 sip 的完整路径。当前脚本目录：{here}"
    )


# ---------------------------------------------------------------------------
# HTTP 处理
# ---------------------------------------------------------------------------

def _int_param(q: dict, name: str, default: int | None = None) -> int | None:
    v = q.get(name, [None])[0]
    if v is None or v == "":
        return default
    try:
        return int(v)
    except ValueError:
        return None


class SipWebHandler(BaseHTTPRequestHandler):
    server_version = f"sip-web/{VERSION}"
    translator: SipTranslator = None  # type: ignore[assignment]  # 由 server 注入

    # ---- 工具 ----
    def _send_json(self, obj: dict, status: int = 200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text: str, status: int = 200, ctype: str = "text/plain; charset=utf-8"):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, path: str):
        """提供前端静态文件（index.html）。"""
        base = os.path.dirname(os.path.abspath(__file__))
        rel = path.lstrip("/")
        # 只允许服务本目录内的静态文件，防目录穿越
        full = os.path.normpath(os.path.join(base, rel))
        if not full.startswith(base):
            self._send_text("forbidden", 403)
            return
        if os.path.isdir(full):
            full = os.path.join(full, "index.html")
        if os.path.isfile(full):
            ext = os.path.splitext(full)[1].lower()
            ctype = {
                ".html": "text/html; charset=utf-8",
                ".js": "application/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8",
                ".json": "application/json; charset=utf-8",
                ".svg": "image/svg+xml",
                ".png": "image/png",
                ".ico": "image/x-icon",
            }.get(ext, "application/octet-stream")
            with open(full, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self._send_text("404 not found: " + path, 404)

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    # ---- 请求分发 ----
    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_DELETE(self):
        self._route("DELETE")

    def _route(self, method: str):
        parsed = urlparse(self.path)
        path = parsed.path
        q = parse_qs(parsed.query)
        t = self.translator

        try:
            # ---------- 静态资源 ----------
            if method == "GET" and (path == "/" or not path.startswith("/api/")):
                if path == "/" or path == "/index.html":
                    self._serve_static("index.html")
                else:
                    self._serve_static(path)
                return

            # ---------- API：状态与元信息 ----------
            if method == "GET" and path == "/api/status":
                r = t.run(["--version"])
                ver = (r["stdout"] or r["stderr"] or "?").strip()
                self._send_json({
                    "success": True,
                    "data": {
                        "sipWeb": VERSION,
                        "sip": ver,
                        "sipPath": t.sip_path,
                        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    },
                })
                return

            # ---------- API：订阅源 ----------
            if method == "GET" and path == "/api/feeds":
                self._send_json(t.call_json(["-l", "--json", "--ignoresafeannouncement"]))
                return

            if method == "POST" and path == "/api/feeds":
                body = self._read_json_body()
                url = (body.get("url") or "").strip()
                if not url:
                    self._send_json(_err(400, "缺少 url 参数", "请提供 RSS 订阅地址"), 400)
                    return
                self._send_json(t.call_json(["-d", url, "--ignoresafeannouncement"]))
                return

            if method == "POST" and path == "/api/feeds/sync":
                self._send_json(t.call_json(["--sync", "--json", "--ignoresafeannouncement"]))
                return

            if method == "POST" and path == "/api/feeds/update-all":
                self._send_json(t.call_json(["--update-all", "--ignoresafeannouncement"]))
                return

            # ---------- API：单个订阅源 ----------
            m = self._match(path, r"^/api/feeds/(\d+)(/.*)?$")
            if m:
                fid = m.group(1)
                sub = m.group(2) or ""

                if method == "GET" and sub == "":
                    args = ["-l", fid, "--json", "--ignoresafeannouncement"]
                    limit = _int_param(q, "limit")
                    if limit is not None:
                        args += ["--limit", str(limit)]
                    self._send_json(t.call_json(args))
                    return

                if method == "GET" and sub == "/info":
                    self._send_json(t.call_json(["--feed-info", fid, "--json", "--ignoresafeannouncement"]))
                    return

                if method == "POST" and sub == "/update":
                    self._send_json(t.call_json(["-u", fid, "--ignoresafeannouncement"]))
                    return

                if method == "POST" and sub == "/archive":
                    self._send_json(t.call_json(["-a", fid, "--ignoresafeannouncement"]))
                    return

                if method == "POST" and sub == "/unarchive":
                    self._send_json(t.call_json(["-una", fid, "--ignoresafeannouncement"]))
                    return

                if method == "DELETE" and sub == "":
                    self._send_json(t.call_json(["-r", fid, "--yes", "--ignoresafeannouncement"]))
                    return

                if method == "POST" and sub == "/schedule":
                    body = self._read_json_body()
                    expr = (body.get("expr") or "").strip()
                    if not expr:
                        self._send_json(_err(400, "缺少 expr 参数", "例如 1h / 7d / daily@10:00"), 400)
                        return
                    self._send_json(t.call_json(["--schedule", fid, expr, "--ignoresafeannouncement"]))
                    return

            # ---------- API：文章 ----------
            m = self._match(path, r"^/api/articles/(\d+)(/.*)?$")
            if m:
                aid = m.group(1)
                sub = m.group(2) or ""

                if method == "GET" and sub == "":
                    self._send_json(t.call_json(["--show", aid, "--json", "--ignoresafeannouncement"]))
                    return

                if method == "GET" and sub == "/versions":
                    self._send_json(t.call_json(["--versions", aid, "--json", "--ignoresafeannouncement"]))
                    return

                if method == "GET" and sub == "/diff":
                    args = ["--diff", aid, "--json", "--ignoresafeannouncement"]
                    frm = q.get("from", [None])[0]
                    to = q.get("to", [None])[0]
                    if frm and to:
                        args = ["--diff", aid, frm, to, "--json", "--ignoresafeannouncement"]
                    self._send_json(t.call_json(args))
                    return

                if method == "POST" and sub == "/fulltext":
                    self._send_json(t.call_json(
                        ["--fulltext", aid, "--yes", "--json", "--ignoresafeannouncement"]))
                    return

                if method == "DELETE" and sub == "/fulltext":
                    self._send_json(t.call_json(["--purge-fulltext", aid, "--ignoresafeannouncement"]))
                    return

                if method == "POST" and sub == "/like":
                    self._send_json(t.call_json(["--like", aid, "--ignoresafeannouncement"]))
                    return

                if method == "POST" and sub == "/summary":
                    self._send_json(t.call_json(["--summary", aid, "--json", "--ignoresafeannouncement"]))
                    return

            # ---------- API：点赞列表 ----------
            if method == "GET" and path == "/api/likes":
                self._send_json(t.call_json(["--likes", "--json", "--ignoresafeannouncement"]))
                return

            # ---------- API：搜索 ----------
            if method == "GET" and path == "/api/search/grep":
                kw = q.get("q", [""])[0].strip()
                if not kw:
                    self._send_json(_err(400, "缺少 q 参数", "请输入搜索关键词"), 400)
                    return
                args = ["--grep", kw, "--json", "--ignoresafeannouncement"]
                feed = q.get("feed", [None])[0]
                limit = _int_param(q, "limit")
                if feed and feed.isdigit():
                    args += ["--feed", feed]
                if limit is not None:
                    args += ["--limit", str(limit)]
                self._send_json(t.call_json(args))
                return

            if method == "GET" and path == "/api/search/semantic":
                kw = q.get("q", [""])[0].strip()
                if not kw:
                    self._send_json(_err(400, "缺少 q 参数", "请输入搜索关键词"), 400)
                    return
                args = ["--search", kw, "--json", "--ignoresafeannouncement"]
                feed = q.get("feed", [None])[0]
                threshold = q.get("threshold", [None])[0]
                if feed and feed.isdigit():
                    args += ["--feed", feed]
                if threshold:
                    args += ["--threshold", threshold]
                self._send_json(t.call_json(args))
                return

            # ---------- API：今日哈汤 ----------
            if method == "GET" and path == "/api/today":
                args = ["--today", "--json", "--ignoresafeannouncement"]
                if q.get("refresh", ["0"])[0] in ("1", "true"):
                    args.insert(1, "--refresh")
                self._send_json(t.call_json(args))
                return

            # ---------- API：AI 配置 ----------
            if method == "GET" and path == "/api/config":
                self._send_json(t.call_json(["--config", "--ignoresafeannouncement"]))
                return

            # ---------- 兜底 ----------
            self._send_json(_err(404, f"未知端点：{method} {path}", "请检查 API 路径"), 404)
        except RuntimeError as e:
            self._send_json(_err(500, str(e), "请确认 sip 可执行文件存在且可运行"), 500)
        except Exception as e:  # noqa: BLE001 —— 服务器不应因单个请求崩溃
            self._send_json(_err(500, f"内部错误：{e}"), 500)

    @staticmethod
    def _match(path: str, pattern: str):
        import re
        return re.match(pattern, path)

    def log_message(self, fmt, *args):  # 简洁日志
        sys.stderr.write("[sip-web] %s - %s\n" % (time.strftime("%H:%M:%S"), fmt % args))


def main():
    ap = argparse.ArgumentParser(description="sip 的本地 Web 界面与 HTTP 翻译层")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"监听端口（默认 {DEFAULT_PORT}）")
    ap.add_argument("--host", default=HOST, help=f"监听地址（默认 {HOST}，本地优先）")
    ap.add_argument("--sip", default=None, help="sip 可执行文件路径（默认找脚本同目录）")
    ap.add_argument("--timeout", type=int, default=CLI_TIMEOUT, help=f"CLI 调用超时秒数（默认 {CLI_TIMEOUT}）")
    args = ap.parse_args()

    sip_path = find_sip(args.sip)
    translator = SipTranslator(sip_path, timeout=args.timeout)

    handler = SipWebHandler
    handler.translator = translator  # type: ignore[attr-defined]

    try:
        server = ThreadingHTTPServer((args.host, args.port), handler)
    except OSError as e:
        sys.exit(f"错误：无法监听 {args.host}:{args.port} —— {e}")

    print("=" * 56)
    print("  🍲 sip-web — 品，你细品。")
    print(f"  Web 界面 : http://{args.host}:{args.port}")
    print(f"  sip 程序 : {sip_path}")
    print(f"  数据目录 : {os.path.join(translator.sip_dir, 'readwithhotsoup')}")
    print(f"  按 Ctrl+C 退出")
    print("=" * 56)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n再见，慢慢来，不着急。🍲")
        server.shutdown()


if __name__ == "__main__":
    main()
