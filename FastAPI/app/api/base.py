"""零依赖 HTTP 框架：路由、请求解析、JSON 响应、鉴权、静态文件、SSE。

设计目标：在没有 fastapi/starlette 的环境里，提供与 FastAPI 等价的开发体验：
  @router.get("/api/cases")  → 自动解析 query / path 参数 / body
  @router.post("/api/evidence/upload") → 自动解析 multipart 文件
  依赖注入：处理函数声明 user=Depends(login) 即自动鉴权
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
from typing import Any, Callable

from .. import config, security


# ------------------------------------------------------------------ 请求对象
class Request:
    def __init__(self, method: str, raw_path: str, headers: dict, body: bytes, client_ip: str):
        parsed = urllib.parse.urlsplit(raw_path)
        self.method = method.upper()
        self.path = urllib.parse.unquote(parsed.path)
        self.raw_query = parsed.query
        self.query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
        self.headers = {k.lower(): v for k, v in headers.items()}
        self.body = body or b""
        self.client_ip = client_ip
        self.path_params: dict[str, str] = {}
        self._json: dict | None = None
        self._form: dict | None = None
        self._files: list[dict] | None = None

    # ---- 鉴权
    @property
    def token(self) -> str:
        """只认 Authorization: Bearer 头。

        🔴 原来还回落 `?token=xxx`：token 会进访问日志、浏览器历史、Referer，
           而且任何看图的人都能直接复制登录。SSE / 上传走的都是 fetch + header，
           前端不依赖 query 传 token，所以这里直接砍掉。
        """
        auth = self.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return ""

    def user(self) -> dict | None:
        data = security.parse_token(self.token) if self.token else None
        if not data:
            return None
        from .. import db
        try:
            uid = int(data["uid"])
        except (TypeError, ValueError, KeyError):
            return None
        row = db.get("user", uid)
        if not row or row.get("status") != "active":
            return None
        row["role_name"] = security.ROLE_NAMES.get(row["role_code"], row["role_code"])
        row["permissions"] = security.permissions_of(row["role_code"])
        row.pop("password_hash", None)          # 口令摘要绝不出接口
        return row

    # ---- 请求体
    def json(self) -> dict:
        if self._json is None:
            try:
                self._json = json.loads(self.body.decode("utf-8") or "{}")
            except Exception:
                self._json = {}
        return self._json if isinstance(self._json, dict) else {}

    def content_type(self) -> str:
        return self.headers.get("content-type", "").split(";")[0].strip().lower()

    def form(self) -> dict:
        if self._form is None:
            self._parse_multipart()
        return self._form or {}

    def files(self) -> list[dict]:
        if self._files is None:
            self._parse_multipart()
        return self._files or []

    def _parse_multipart(self) -> None:
        self._form, self._files = {}, []
        ctype = self.headers.get("content-type", "")
        if "multipart/form-data" not in ctype:
            # 普通表单
            if self.content_type() == "application/x-www-form-urlencoded":
                self._form = dict(urllib.parse.parse_qsl(self.body.decode("utf-8", "ignore")))
            return
        m = re.search(r'boundary=(?:"([^"]+)"|([^;]+))', ctype)
        if not m:
            return
        boundary = (m.group(1) or m.group(2)).strip().encode()
        parts = self.body.split(b"--" + boundary)
        for part in parts:
            if b"\r\n\r\n" not in part:
                continue
            head, _, payload = part.partition(b"\r\n\r\n")
            # 🔴 原来是 `payload.rstrip(b"\r\n")`：会把二进制文件**尾部合法的换行
            #    字节**一起吃掉（PNG/JPEG/文本证据都可能损坏）。multipart 规范里
            #    分隔符前的那个 CRLF 是协议附加的，只去掉最后一对即可。
            if payload.endswith(b"\r\n"):
                payload = payload[:-2]
            if payload.endswith(b"--"):
                payload = payload[:-2]
            name = filename = None
            disp = ""
            for line in head.decode("utf-8", "ignore").splitlines():
                low = line.lower()
                if low.startswith("content-disposition:"):
                    disp = line
            if not disp:
                continue
            n = re.search(r'name="([^"]*)"', disp)
            f = re.search(r'filename="([^"]*)"', disp)
            if n:
                name = n.group(1)
            if f:
                filename = f.group(1)
            if filename:
                self._files.append({"field": name, "filename": filename, "data": payload})
            elif name:
                self._form[name] = payload.decode("utf-8", "ignore")

    def param(self, name: str, default: Any = None, cast: Callable | None = None) -> Any:
        """按 path → query → json body → form 的顺序取参数。"""
        raw = self.path_params.get(name)
        if raw is None:
            raw = self.query.get(name)
        if raw is None:
            raw = self.json().get(name)
        if raw is None:
            raw = self.form().get(name)
        if raw is None:
            return default
        if cast is int:
            try:
                return int(raw)
            except (TypeError, ValueError):
                return default
        if cast is float:
            try:
                return float(raw)
            except (TypeError, ValueError):
                return default
        if cast is bool:
            return str(raw).lower() in ("1", "true", "yes", "on")
        if cast is list:
            return raw if isinstance(raw, list) else [raw]
        return raw


# ------------------------------------------------------------------ 响应对象
class Response:
    def __init__(self, body: bytes = b"", status: int = 200, ctype: str = "application/json; charset=utf-8",
                 headers: dict | None = None):
        self.body = body
        self.status = status
        self.ctype = ctype
        self.headers = headers or {}


def ok(data: Any = None, msg: str = "ok") -> Response:
    return Response(json.dumps({"code": 0, "msg": msg, "data": data}, ensure_ascii=False,
                               default=str).encode("utf-8"))


def fail(msg: str, code: int = 1, status: int = 400) -> Response:
    return Response(json.dumps({"code": code, "msg": msg, "data": None}, ensure_ascii=False).encode(),
                    status=status)


def raw(data: bytes, ctype: str = "application/octet-stream", status: int = 200,
        headers: dict | None = None) -> Response:
    return Response(data, status, ctype, headers)


class SSEStream:
    """SSE 响应：处理函数返回它，server 会逐条 flush。"""

    def __init__(self, generator, status: int = 200):
        self.generator = generator
        self.status = status


# ------------------------------------------------------------------ 异常
class HTTPError(Exception):
    def __init__(self, msg: str, status: int = 400, code: int = 1):
        super().__init__(msg)
        self.msg = msg
        self.status = status
        self.code = code


def require(user: dict | None, perm: str = "") -> dict:
    if not user:
        raise HTTPError("登录已过期，请重新登录", 401, 401)
    if perm and not security.has_perm(user["role_code"], perm):
        raise HTTPError(f"无权限：{perm}", 403, 403)
    return user


# ------------------------------------------------------------------ 路由注册
class Router:
    def __init__(self, prefix: str = ""):
        self.prefix = prefix
        self.routes: list[tuple[str, re.Pattern, Callable]] = []

    def _add(self, method: str, path: str, fn: Callable) -> Callable:
        full = self.prefix + path
        pattern = re.compile("^" + re.sub(r"\{([a-zA-Z_]+)\}", r"(?P<\1>[^/]+)", full) + "$")
        self.routes.append((method.upper(), pattern, fn))
        return fn

    def get(self, path: str):
        return lambda fn: self._add("GET", path, fn)

    def post(self, path: str):
        return lambda fn: self._add("POST", path, fn)

    def put(self, path: str):
        return lambda fn: self._add("PUT", path, fn)

    def delete(self, path: str):
        return lambda fn: self._add("DELETE", path, fn)

    def match(self, method: str, path: str) -> tuple[Callable, dict] | None:
        for m, pattern, fn in self.routes:
            if m != method:
                continue
            mt = pattern.match(path)
            if mt:
                return fn, mt.groupdict()
        return None

    def allowed(self, path: str) -> list[str]:
        return [m for m, p, _ in self.routes if p.match(path)]


# ------------------------------------------------------------------ 静态文件
STATIC_MIME = {
    ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8", ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".ico": "image/x-icon",
    ".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf",
    ".txt": "text/plain; charset=utf-8", ".md": "text/markdown; charset=utf-8",
}


def serve_static(path: str) -> Response | None:
    """从 frontVue3/ 目录取文件；目录或 / 时返回 index.html。"""
    import mimetypes
    from pathlib import Path
    root = Path(config.FRONT_DIR)
    if not root.exists():
        return None
    rel = path.lstrip("/") or "index.html"
    if rel.startswith("api/"):
        return None
    target = (root / rel).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return None                                  # 防目录穿越
    if target.is_dir():
        target = target / "index.html"
    if not target.is_file():
        return None
    mime = STATIC_MIME.get(target.suffix.lower()) or mimetypes.guess_type(str(target))[0] or "text/plain"
    return raw(target.read_bytes(), mime, headers={"Cache-Control": "no-cache"})


def now_ms() -> int:
    return int(time.time() * 1000)
