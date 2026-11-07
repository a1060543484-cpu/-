#!/usr/bin/env python3
"""启动入口：python3 run.py [--port 8899] [--host 127.0.0.1]

不依赖 uvicorn / fastapi —— 直接用标准库 HTTP 服务承载 API + 前端静态资源。

🔐 安全改造（2026-11-05）：默认绑定与端口统一由 config.py 提供
（127.0.0.1:8899），不再默认 0.0.0.0:8000；要对外监听必须显式传 --host，
并会打印醒目警告。
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main() -> None:
    from app import config, server

    parser = argparse.ArgumentParser(description="地府阴差管理系统后端")
    parser.add_argument("--host", default=config.DEFAULT_HOST,
                        help="监听地址，默认 127.0.0.1（仅本机可访问）")
    parser.add_argument("--port", type=int, default=config.DEFAULT_PORT,
                        help=f"监听端口，默认 {config.DEFAULT_PORT}")
    args = parser.parse_args()

    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(f"[安全警告] 正在以 {args.host} 对外开放监听，局域网内任何人都能访问本服务；"
              "确认已改掉默认口令并配好防火墙再这么做。", flush=True)
    server.serve(args.host, args.port)


if __name__ == "__main__":
    main()
