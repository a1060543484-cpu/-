"""API 层汇总：把各模块的 Router 合并成一个总路由表。"""
from __future__ import annotations

import re

from .base import (HTTPError, Request, Response, Router, SSEStream, fail, ok, raw,
                   require, serve_static)
from . import auth, routes

ROUTER = Router("")
ROUTER.routes = auth.router.routes + routes.router.routes


def _is_business_message(msg: str) -> bool:
    """判断 ValueError 的文本是不是「我们自己写给用户的业务提示」。

    Python 原生报错（`invalid literal for int() with base 10: 'notanint'`、
    `could not convert string to float` 等）全部是 ASCII，且会泄露内部实现细节；
    业务提示都是短中文。只放行后者，其余一律换成通用文案。
    """
    if not msg or len(msg) > 60:
        return False
    if not re.search(r"[\u4e00-\u9fff]", msg):
        return False
    return not any(ch in msg for ch in ("\n", "\r", "'", '"', "/", "\\", "Traceback"))


def dispatch(req: Request) -> Response:
    """把请求分派到处理函数；统一异常 → JSON 错误响应（不回显内部细节）。"""
    matched = ROUTER.match(req.method, req.path)
    if not matched:
        allowed = ROUTER.allowed(req.path)
        if allowed:
            return fail(f"方法不允许：{req.method}，可用 {','.join(allowed)}", 405, 405)
        if not req.path.startswith("/api/"):
            static = serve_static(req.path)        # 非 API 路径 → 静态前端
            if static:
                return static
            return fail(f"资源不存在：{req.path}", 404, 404)
        return fail(f"接口不存在：{req.path}", 404, 404)
    fn, params = matched
    req.path_params = params
    try:
        result = fn(req)
    except HTTPError as exc:
        return fail(exc.msg, exc.code, exc.status)
    except ValueError as exc:
        # 🔴 原来直接 `fail(str(exc))`：畸形参数会把 Python 原生报错原文吐给前端。
        #    现在只放行我们自己写的中文业务提示，其余一律脱敏。
        msg = str(exc)
        return fail(msg if _is_business_message(msg) else "请求参数格式有误", 1, 400)
    except Exception as exc:
        import traceback
        traceback.print_exc()                       # 细节只留在服务端日志里
        return fail("服务内部错误，请查看服务端日志", 500, 500)
    if isinstance(result, Response):
        return result
    if isinstance(result, SSEStream):
        return result                            # 由 server 特殊处理
    return ok(result)
