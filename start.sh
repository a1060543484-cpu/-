#!/usr/bin/env bash
# 地府阴差管理系统 · 启动（一个命令搞定）
#
#   ./start.sh                 后台启动，默认 127.0.0.1:8899
#   ./start.sh 8900            指定端口
#   PORT=8900 ./start.sh       同上
#   ./start.sh --foreground    前台运行（Ctrl+C 停止，调试时用）
#   PYTHON=/path/to/python ./start.sh   指定解释器
#   DIFU_FLOW_ENGINE=builtin ./start.sh 强制纯标准库降级
#
# 对应的停止命令就是 ./stop.sh。
# 内部转给 difu.sh 做后台托管（端口锁、pid 校验、HTTP 就绪等待、日志轮转）。
# 登录口令见 .run/credentials.txt；课堂演示可 DIFU_DEMO_MODE=1 ./start.sh。
#
# 🔴 解释器自动挑选（2026-11-05）：
#    项目装了 langgraph 就用 LangGraph 引擎，没装就降级内置状态机。
#    但本机默认 `python3` 常是没装 langgraph 的那个解释器 —— 如果不管，
#    `./start.sh` 会静默降级，等于白接框架。所以这里按优先级探测，
#    挑第一个 `import langgraph` 成功的；全都失败才用 python3 并明确提示。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$ROOT/.run"

# 探测顺序：显式 PYTHON > 项目自带 .venv > 工作区 .venv > 系统 python3
pick_python() {
  local candidate
  if [ -n "${PYTHON:-}" ]; then
    printf '%s\n' "$PYTHON"
    return 0
  fi
  for candidate in \
    "$ROOT/.venv/bin/python" \
    "$ROOT/../../.venv/bin/python" \
    "$(command -v python3 2>/dev/null || true)"; do
    [ -n "$candidate" ] || continue
    [ -x "$candidate" ] || continue
    if "$candidate" -c 'import langgraph' >/dev/null 2>&1; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  # 都不行：回落到 python3，让后端自动降级（不再报错拦住启动）
  printf '%s\n' "${PYTHON:-python3}"
}

export PYTHON="$(pick_python)"
if "$PYTHON" -c 'import langgraph' >/dev/null 2>&1; then
  echo "流程引擎: LangGraph（${PYTHON}）" >&2
else
  echo "流程引擎: 内置状态机（未找到装 langgraph 的解释器，已自动降级）" >&2
  echo "          想启用 LangGraph：PYTHON=/你的/venv/bin/python ./start.sh" >&2
fi

# 前台模式保留原来的使用习惯，但绑定地址默认改成仅本机
if [ "${1:-}" = "--foreground" ] || [ "${1:-}" = "-f" ]; then
  shift
  PORT="${1:-${PORT:-${DIFU_PORT:-8899}}}"
  PY="$PYTHON"
  HOST="${HOST:-127.0.0.1}"
  if ! command -v "$PY" >/dev/null 2>&1; then
    echo "找不到 $PY，请先安装 Python 3.9+ 或用 PYTHON=/path/to/python3 ./start.sh" >&2
    exit 1
  fi
  echo "Python : $("$PY" -V 2>&1)"
  echo "端口   : $PORT"
  echo "监听   : $HOST"
  echo "停止   : Ctrl+C（或另开终端 ./stop.sh $PORT）"
  echo "口令   : .run/credentials.txt"
  echo
  cd "$ROOT/FastAPI"
  exec "$PY" run.py --host "$HOST" --port "$PORT"
fi

exec "$ROOT/difu.sh" start "$@"
