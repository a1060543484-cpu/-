"""零依赖 HTTP 服务：标准库 ThreadingHTTPServer 承载 API + 静态前端 + SSE。

替代 uvicorn 的角色：同一套路由既能在 FastAPI/uvicorn 下跑，也能在零依赖环境里跑。
默认走本文件，功能对齐（多线程 + SSE 流式 + multipart 上传 + 静态托管）。

🔐 安全改造（2026-11-05）：
- 默认只绑 127.0.0.1（不再 0.0.0.0 暴露到局域网）；
- CORS 从 `*` 改成显式白名单；加基础安全响应头；
- 兜底异常不再把 Python 原生报错回显给前端；访问日志打码敏感 query；
- 启动横幅不再打印弱口令；
- SIGTERM 优雅退出 → 真正走 finally → SQLite WAL checkpoint。
"""
from __future__ import annotations

import json
import re
import signal
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config, db
from .api import dispatch
from .api.base import Request, Response, SSEStream, fail, serve_static


class Handler(BaseHTTPRequestHandler):
    server_version = "DifuServer/1.0"
    protocol_version = "HTTP/1.1"

    # ---- 各方法统一入口
    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PUT(self):
        self._handle("PUT")

    def do_DELETE(self):
        self._handle("DELETE")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self._security_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ---- 核心
    def _handle(self, method: str) -> None:
        started = time.time()
        length = int(self.headers.get("Content-Length") or 0)
        # 🔴 上传大小上限：原来完全不设限，一个请求就能把磁盘写满
        if length > config.MAX_UPLOAD_BYTES:
            self.close_connection = True
            self._send(fail("请求体过大，已拒绝", 413, 413))
            return
        body = self.rfile.read(length) if length else b""
        ip = self.client_address[0] if self.client_address else "127.0.0.1"
        req = Request(method, self.path, dict(self.headers), body, ip)
        try:
            result = dispatch(req)
        except Exception:
            traceback.print_exc()
            result = fail("服务内部错误，请查看服务端日志", 500, 500)

        if isinstance(result, SSEStream):
            self._stream(result)
            return
        self._send(result)

        if method != "OPTIONS":
            cost = int((time.time() - started) * 1000)
            if cost > 500:                              # 慢请求留痕，便于性能排查
                db.insert("tool_call", {"case_id": 0, "agent_code": "http", "tool_name": req.path,
                                        "args": db.jdumps({"method": method}),
                                        "result": db.jdumps({"ms": cost, "status": result.status}),
                                        "success": 1 if result.status < 400 else 0,
                                        "duration_ms": cost, "created_at": db.now()})

    def _cors(self) -> None:
        """同源部署不需要 CORS；确需跨域时用 CORS_ALLOWED_ORIGINS 显式放行。

        🔴 原来无脑 `Access-Control-Allow-Origin: *`：任何网站都能带着
           浏览器里的请求访问本机接口。现在只回显白名单里的 Origin。
        """
        origin = self.headers.get("Origin", "")
        if origin and origin in config.CORS_ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers", "Content-Type,Authorization")
            self.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,DELETE,OPTIONS")

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        if self.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-store")

    def _send(self, resp: Response) -> None:
        self.send_response(resp.status)
        self._cors()
        self._security_headers()
        self.send_header("Content-Type", resp.ctype)
        self.send_header("Content-Length", str(len(resp.body)))
        for k, v in (resp.headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(resp.body)

    def _stream(self, stream: SSEStream) -> None:
        self.send_response(stream.status)
        self._cors()
        self._security_headers()
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            for event, data in stream.generator:
                payload = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
                self.wfile.write(payload.encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass                                     # 前端断开不报错
        except Exception:
            traceback.print_exc()               # 细节只留服务端日志，不回显给前端
            try:
                payload = json.dumps({"msg": "流式输出中断"}, ensure_ascii=False)
                self.wfile.write(f"event: error\ndata: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
            except Exception:
                pass

    def log_message(self, fmt: str, *args) -> None:
        if not config.LOG_VERBOSE:
            return
        # 🔴 访问日志不能出现 token / key：把 query 里的敏感值打码
        text = fmt % args
        text = re.sub(r'((?:token|api_key|password|secret)=)[^&\s"\']+', r'\1***', text,
                      flags=re.IGNORECASE)
        print(f"[{time.strftime('%H:%M:%S')}] {self.address_string()} {text}")


# ------------------------------------------------------------------ 定时巡检
def _start_scheduler() -> threading.Thread | None:
    if not config.SCHEDULER_ENABLED:
        return None
    from .agents.scheduler import SCHEDULER

    def loop():
        while True:
            time.sleep(config.SCHEDULER_INTERVAL)
            try:
                summary = SCHEDULER.tick()
                if summary.get("expired"):
                    print(f"[巡检] 期满 {summary['expired']} → 已流转 {len(summary['flowed'])} 件",
                          flush=True)
            except Exception as exc:
                print(f"[巡检] 异常：{exc}", flush=True)

    t = threading.Thread(target=loop, name="difu-scheduler", daemon=True)
    t.start()
    SCHEDULER.running = True
    return t


def serve(host: str | None = None, port: int | None = None) -> None:
    host = host or config.DEFAULT_HOST
    port = port or config.DEFAULT_PORT
    db.init()
    from . import demo, seed, security
    # 1) 把老库里遗留的明文大模型 Key 搬到 .run/secrets.env，并清空数据库字段
    security.migrate_plaintext_keys()
    # 2) 建基础数据（空库会用随机强口令建 6 个账号）
    seed.seed()
    # 3) 老库里若还是历史弱口令，自动轮换；凭据统一写 .run/credentials.txt
    security.rotate_initial_passwords()
    # 首次启动顺带把三条演示卷宗建好（build_demo 幂等，已有卷宗就跳过），
    # 这样打开页面立刻能看到「服刑中 / 待复核 / 已归档」三种典型状态
    try:
        demo.build_demo()
    except Exception as exc:                     # 演示数据失败不该拦住服务启动
        print(f"[warn] 演示数据生成失败：{exc}", flush=True)
    _start_scheduler()
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    from .agents import graph
    # flush=True：重定向到日志文件时 stdout 默认全缓冲，横幅会迟迟不出现
    banner = "\n".join([
        "=" * 62,
        "  地府阴差管理系统 · 后端已启动",
        f"  监听地址 : {host}:{port}" + ("（仅本机）" if host in ("127.0.0.1", "localhost") else "  ⚠️ 对外开放"),
        f"  服务地址 : http://127.0.0.1:{port}/",
        f"  前端页面 : http://127.0.0.1:{port}/index.html",
        f"  接口前缀 : http://127.0.0.1:{port}/api/",
        f"  数据库   : {db.engine()} → {config.SQLITE_PATH}",
        f"  流程引擎 : {graph.engine_name()}"
        f"（{graph.engine_capability()['checkpointer']}，DIFU_FLOW_ENGINE={config.FLOW_ENGINE}）",
        f"  定时巡检 : 每 {config.SCHEDULER_INTERVAL}s 一次",
        f"  登录口令 : {config.CREDENTIALS_FILE}" if not config.DEMO_MODE else
        "  登录口令 : 演示模式（DIFU_DEMO_MODE=1）；仅全新空库用弱口令，已有库看 .run/credentials.txt",
        "  安全提示 : 密钥 / 口令 / API Key 都在 .run/ 下，已加 .gitignore",
        "=" * 62,
    ])
    print(banner, flush=True)

    # 🔴 SIGTERM 优雅退出：原来 `stop.sh` 发 TERM 后 Python 直接被杀，
    #    `finally` 根本不执行 → SQLite WAL 不 checkpoint，日志缓冲也丢。
    #    shutdown() 必须在**另一个线程**里调，否则和 serve_forever 互等死锁。
    def _graceful(signum, frame):
        print(f"\n[停止] 收到信号 {signum}，正在收尾…", flush=True)
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _graceful)
    signal.signal(signal.SIGINT, _graceful)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[停止] 收到 Ctrl+C", flush=True)
    finally:
        httpd.server_close()
        try:
            db.shutdown()                        # WAL checkpoint + 收尾
        except Exception as exc:
            print(f"[停止] 数据库收尾异常：{exc}", flush=True)
        print("[停止] 端口已释放，WAL 已落盘。", flush=True)
