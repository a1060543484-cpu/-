#!/usr/bin/env bash
# 地府阴差管理系统 · 停止（一个命令搞定）
#
#   ./stop.sh            停掉本脚本管理的全部实例
#   ./stop.sh 8899       只停指定端口
#
# 内部转给 difu.sh：先确认 pid/端口确实是本项目（绝不误杀无关进程），
# 再发 SIGTERM 让后端走 finally 做 SQLite WAL checkpoint，超时才 SIGKILL。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$ROOT/difu.sh" stop "$@"
