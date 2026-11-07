#!/usr/bin/env bash
# 地府阴差管理系统 · 后台托管（start / stop / restart / status / log）
#
# 日常只需要两个命令（薄封装，内部转到这里）：
#   ./start.sh  [端口]   启动（后台）
#   ./stop.sh   [端口]   停止（不给端口 = 停掉全部受管实例）
#
# 高级用法（本脚本）：
#   ./difu.sh start   [端口]    后台启动（默认 8899）
#   ./difu.sh stop    [端口]    停止
#   ./difu.sh restart [端口]    重启（不给端口时自动跟随正在运行的实例）
#   ./difu.sh status  [端口]    查看状态
#   ./difu.sh log     [端口] [-f]   查看日志（-f 持续跟随）
#
# 端口优先级：命令行参数 > PORT/DIFU_PORT 环境变量 > 8899（全项目统一）
# 监听地址默认 127.0.0.1（仅本机），要对外必须显式 HOST=0.0.0.0。
# 大模型 Key 放 .run/secrets.env，Python 端会自动读；也可临时透传：
#   LLM_API_KEY=sk-xxx ./start.sh
#
# 每个端口各自一份 pid / 日志（.run/difu-<端口>.pid / .run/difu-<端口>.log）。
set -uo pipefail          # 不加 -e：探活失败在启停流程里是正常分支，不该中断

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$ROOT/FastAPI"
RUN_DIR="$ROOT/.run"
# 🔴 解释器自动挑选（与 start.sh 保持一致，2026-11-05）：
#    直接 `./difu.sh restart 8899` 时若不探测，PY 会回落到系统 python3
#    （那个没装 langgraph），把刚接好的框架静默降级掉。
if [ -n "${PYTHON:-}" ]; then
  PY="$PYTHON"
else
  PY=python3
  for _c in "$ROOT/.venv/bin/python" "$ROOT/../../.venv/bin/python" \
            "$(command -v python3 2>/dev/null || true)"; do
    [ -n "$_c" ] && [ -x "$_c" ] || continue
    if "$_c" -c 'import langgraph' >/dev/null 2>&1; then PY="$_c"; break; fi
  done
fi
HOST="${HOST:-127.0.0.1}"                # 🔴 原来是 0.0.0.0：局域网内谁都能访问
# 🔴 原来脚本默认 8000，README 主推 8899、横幅写 8000，`restart` 不带端口
#    永远只动 8000 → 实际部署的实例根本管不到。现在全项目统一 8899。
DEFAULT_PORT="${PORT:-${DIFU_PORT:-8899}}"
READY_TIMEOUT="${READY_TIMEOUT:-20}"     # 等 HTTP 就绪的秒数
STOP_TIMEOUT="${STOP_TIMEOUT:-10}"       # 等进程退出的秒数
LOG_MAX_BYTES=$((5 * 1024 * 1024))       # 单个日志超过 5MB 时，启动前轮转一次

if [ -t 1 ]; then
  C_R=$'\033[31m'; C_G=$'\033[32m'; C_Y=$'\033[33m'; C_C=$'\033[36m'; C_D=$'\033[2m'; C_0=$'\033[0m'
else
  C_R=; C_G=; C_Y=; C_C=; C_D=; C_0=
fi

# 中文按 2 列算宽度：printf 的 %-Ns 数的是字节，中英混排会错位
disp_width() {
  local s="$1" w=0 i ch
  for ((i = 0; i < ${#s}; i++)); do
    ch="${s:i:1}"
    case "$ch" in
      [!\ -~]) w=$((w + 2)) ;;      # 非 ASCII 一律按全角算
      *) w=$((w + 1)) ;;
    esac
  done
  printf '%s' "$w"
}

# row <标签> <值>：标签按 8 显示列左对齐
row() {
  local label="$1" value="$2" pad
  pad=$((8 - $(disp_width "$label")))
  [ "$pad" -lt 1 ] && pad=1
  printf '  %s%*s%s\n' "$label" "$pad" "" "$value"
}

info() { printf '%s\n' "$*"; }
ok()   { printf '%s✓%s %s\n' "$C_G" "$C_0" "$*"; }
warn() { printf '%s!%s %s\n' "$C_Y" "$C_0" "$*"; }
err()  { printf '%s✗%s %s\n' "$C_R" "$C_0" "$*" >&2; }
dim()  { printf '%s%s%s\n' "$C_D" "$*" "$C_0"; }

usage() {
  cat <<'TXT'
地府阴差管理系统 · 一键启停

  日常用这两个就够了：
    ./start.sh [端口]             启动（后台，默认 8899）
    ./stop.sh  [端口]             停止（不给端口 = 停掉全部受管实例）

  高级命令：
    ./difu.sh start   [端口]      后台启动
    ./difu.sh stop    [端口]      停止
    ./difu.sh restart [端口]      重启（自动跟随正在运行的端口）
    ./difu.sh status  [端口]      查看状态
    ./difu.sh log     [端口] [-f] 查看日志

端口优先级：命令行参数 > PORT/DIFU_PORT > 8899
监听地址：默认 127.0.0.1；HOST=0.0.0.0 才会对外（会打印警告）
登录口令：见 .run/credentials.txt；课堂演示可 DIFU_DEMO_MODE=1
每个端口独立 pid/日志：.run/difu-<端口>.pid、.run/difu-<端口>.log
TXT
  exit "${1:-0}"
}

# ------------------------------------------------------------------ 路径与探测
pid_file_of() { printf '%s/difu-%s.pid' "$RUN_DIR" "$1"; }
log_file_of() { printf '%s/difu-%s.log' "$RUN_DIR" "$1"; }

# 端口参数：给了就用，没给回落到 $PORT，再没有就用 $2 指定的默认值（空串表示「全部」）。
# 结果写进全局 RESOLVED_PORT —— 不能用 `p=$(arg_port ...)`，
# 那样校验失败里的 exit 只退出命令替换的子 shell，主流程会拿着空端口继续往下跑。
RESOLVED_PORT=""
resolve_port() {
  local p="${1:-}"
  if [ -z "$p" ]; then p="${PORT:-}"; fi
  if [ -z "$p" ]; then p="${2:-}"; fi
  case "$p" in
    '') RESOLVED_PORT=""; return 0 ;;
    *[!0-9]*) err "端口必须是数字：$p"; exit 2 ;;
  esac
  if [ "$p" -lt 1 ] || [ "$p" -gt 65535 ]; then err "端口超出范围：$p"; exit 2; fi
  RESOLVED_PORT="$p"
}

pid_alive() {
  local pid="${1:-}"
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

# 判断 pid 是不是「本项目的后端」：cwd 必须是 FastAPI/（本脚本启动的），
# 或者项目根 + 解释器是 Python（有人用 `python3 FastAPI/run.py` 从根目录起）。
# 光看 cwd/解释器不够：`python3 -m http.server` 从项目根起同样满足这两条，
# 所以凡是能连上端口的场合，还要再过一道 server_fingerprint。
# ps 在本机沙箱里不可用，lsof 最可靠。
is_ours() {
  local pid="${1:-}" cwd exe
  [ -z "$pid" ] && return 1
  cwd=$(lsof -p "$pid" -a -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1)
  [ "$cwd" = "$APP_DIR" ] && return 0
  if [ "$cwd" = "$ROOT" ]; then
    exe=$(lsof -p "$pid" -a -d txt -Fn 2>/dev/null | sed -n 's/^n//p' | head -1)
    case "$(basename "${exe:-}")" in
      [Pp]ython*) return 0 ;;
    esac
  fi
  return 1
}

# 端口上跑的是不是「我们的后端」：看 Server 头。
# 返回 0=确认是本项目；1=能连上但不是本项目；2=连不上（进程可能还在初始化或已僵死）
# 后端 server.py 的 version_string 是 "DifuServer/1.0 Python/3.14.5"。
server_fingerprint() {
  local port="$1" hdr
  # 🔴 --noproxy '*' 必须加：这台机器 shell 里常驻 http_proxy，
  #    curl 访问 127.0.0.1 会被代理截走返回 502，导致端口指纹判断全错。
  hdr=$(curl --noproxy '*' -s -D - -o /dev/null -m 2 "http://127.0.0.1:${port}/" 2>/dev/null | head -5)
  if [ -z "$hdr" ]; then return 2; fi
  case "$hdr" in
    *DifuServer*) return 0 ;;
    *) return 1 ;;
  esac
}

# 端口被「别的程序」占着吗（确认不是本项目才返回 0，连不上不算）
foreign_on_port() {
  server_fingerprint "$1"
  [ $? -eq 1 ]
}

pid_on_port() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null | head -1
}

http_code() {
  curl --noproxy '*' -s -o /dev/null -w '%{http_code}' -m 3 "http://127.0.0.1:$1/" 2>/dev/null
}

# 读 pid 文件：输出 "PID TS"（TS=启动时间戳，0 表示未知）
read_pidfile() {
  local f pid ts
  f=$(pid_file_of "$1")
  [ -f "$f" ] || return 1
  read -r pid ts <"$f" || return 1
  [ -n "${pid:-}" ] || return 1
  printf '%s %s' "$pid" "${ts:-0}"
}

write_pidfile() {                # $1=端口 $2=pid
  mkdir -p "$RUN_DIR"
  printf '%s %s\n' "$2" "$(date +%s)" >"$(pid_file_of "$1")"
}

drop_pidfile() { rm -f "$(pid_file_of "$1")"; }

# 只删「自己写的」那份 pid 文件。
# 并发的两次 start 里，抢端口失败的那次退出时会走到清理分支，
# 如果无脑 rm，就会把成功那次的记录删掉 —— 之后 status 找不到 pid、stop 停不掉。
drop_pidfile_if_own() {          # $1=端口 $2=自己的 pid
  local f pid _
  f=$(pid_file_of "$1")
  [ -f "$f" ] || return 0
  if read -r pid _ <"$f" 2>/dev/null && [ "${pid:-}" = "${2:-}" ]; then
    rm -f "$f"
  fi
  return 0
}

# 端口锁：mkdir 是原子操作，用它保证同一端口同时只有一个 start 在跑。
# 持锁者把自己的 pid 写进锁目录的 owner，进程意外退出留下的锁能被后来者识别并清掉。
LOCK_HELD=""
lock_dir_of() { printf '%s/difu-%s.lock' "$RUN_DIR" "$1"; }

lock_acquire() {
  local port="$1" d owner waited=0 max=$(((READY_TIMEOUT + 5) * 4))
  mkdir -p "$RUN_DIR"
  d=$(lock_dir_of "$port")
  while :; do
    if mkdir "$d" 2>/dev/null; then
      printf '%s\n' "$$" >"$d/owner"
      LOCK_HELD="$d"
      return 0
    fi
    owner=$(cat "$d/owner" 2>/dev/null || true)
    if [ -n "$owner" ] && ! pid_alive "$owner"; then
      dim "  清理残留的端口锁（持有者 ${owner} 已退出）"
      rm -rf "$d"
      continue
    fi
    if [ "$waited" -ge "$max" ]; then
      err "端口 ${port} 的启动锁被 pid ${owner:-?} 占着超过 $((READY_TIMEOUT + 5))s，放弃"
      return 1
    fi
    sleep 0.25
    waited=$((waited + 1))
  done
}

lock_release() {
  [ -n "${LOCK_HELD:-}" ] && rm -rf "$LOCK_HELD"
  LOCK_HELD=""
}

managed_ports() {                # 列出 .run/difu-<端口>.pid 里出现过的端口
  local f p
  for f in "$RUN_DIR"/difu-*.pid; do
    [ -f "$f" ] || continue
    p=$(basename "$f" .pid)
    printf '%s\n' "${p#difu-}"
  done
}

human_duration() {
  local s="${1:-0}"
  [ "$s" -lt 0 ] && s=0
  if   [ "$s" -ge 86400 ]; then printf '%d天%d时' $((s / 86400)) $(((s % 86400) / 3600))
  elif [ "$s" -ge 3600 ];  then printf '%d时%d分' $((s / 3600)) $(((s % 3600) / 60))
  elif [ "$s" -ge 60 ];    then printf '%d分%d秒' $((s / 60)) $((s % 60))
  else printf '%d秒' "$s"
  fi
}

# 端口归属：ours（本项目后端）/ foreign（别的程序）/ none（没人监听）
# 判断顺序：lsof 找到监听进程 → 看 Server 头 → 连不上再退回进程身份（cwd + 解释器）。
port_state() {
  local port="$1" pid
  pid=$(pid_on_port "$port")
  [ -z "$pid" ] && { printf 'none'; return; }
  server_fingerprint "$port"
  case $? in
    0) printf 'ours'; return ;;
    1) printf 'foreign'; return ;;
  esac
  # 连不上（正在初始化，或进程僵死）：只能靠进程身份兜底
  if is_ours "$pid"; then printf 'ours'; else printf 'foreign'; fi
}

# 解析某端口当前的实际持有者：输出 "PID PORT"，没有则输出空
who_runs() {
  local port="$1" pid="" ts="" rec
  [ "$(port_state "$port")" = "ours" ] || return 1
  if rec=$(read_pidfile "$port"); then
    read -r pid ts <<<"$rec"
    if pid_alive "$pid" && is_ours "$pid"; then
      printf '%s %s' "$pid" "$port"; return 0
    fi
  fi
  pid=$(pid_on_port "$port")
  [ -n "$pid" ] || return 1
  printf '%s %s' "$pid" "$port"
}

banner() {                       # $1=端口 $2=pid $3=1 表示「本来就在跑」
  local port="$1" pid="$2" already="${3:-0}"
  printf '\n'
  if [ "$already" = "1" ]; then ok "服务在运行"; else ok "服务已启动"; fi
  info "  页面  ${C_C}http://127.0.0.1:${port}/${C_0}"
  info "  接口  ${C_C}http://127.0.0.1:${port}/api/${C_0}"
  info "  日志  $(log_file_of "$port")"
  info "  pid   ${pid}"
  # 🔴 原来这里直接打印三组弱口令，横幅 + 日志文件 + README 到处都是。
  #    现在只指路，口令本体在 600 权限的 credentials.txt 里。
  #    位置跟随 DIFU_RUN_DIR（测试时可用独立目录，别写死 $ROOT/.run）。
  local cred_dir="${DIFU_RUN_DIR:-$ROOT/.run}"
  if [ -f "$cred_dir/credentials.txt" ]; then
    dim   "  口令  ${cred_dir}/credentials.txt（600 权限，首次启动自动生成）"
  else
    dim   "  口令  课堂演示模式 DIFU_DEMO_MODE=1，或看服务端启动横幅"
  fi
  dim   "  停止  ./stop.sh ${port}"
}

# ------------------------------------------------------------------ start
cmd_start() {
  local port="$1" rec pid ts occ logf rc

  # 0) 先拿端口锁：两个 start 同时进来时只有一个能往下走。
  #    没锁的话两边都能通过「端口没人占」的检查、一起 nohup，
  #    后起的那个 bind 失败（OSError: [Errno 48] Address already in use），
  #    退出时还会把成功那次的 pid 文件删掉。
  lock_acquire "$port" || return 1
  # 🔴 trap：脚本中途被 Ctrl+C / 异常退出时也要释放端口锁，
  #    否则下次 start 会一直等到超时才发现「持有者已退出」。
  trap 'lock_release' EXIT INT TERM
  start_body "$port"
  rc=$?
  lock_release
  trap - EXIT INT TERM
  return "$rc"
}

start_body() {
  local port="$1" rec pid ts occ logf

  # 1) 该端口已有本项目实例 → 直接报地址，不重复启动
  if rec=$(read_pidfile "$port"); then
    read -r pid ts <<<"$rec"
    if pid_alive "$pid" && is_ours "$pid" && [ "$(port_state "$port")" != "foreign" ]; then
      warn "端口 ${port} 已在运行（pid ${pid}），无需重复启动"
      banner "$port" "$pid" 1
      return 0
    fi
    if pid_alive "$pid"; then
      warn "pid 文件里的 ${pid} 还活着但不是本项目进程，清理 pid 文件"
    else
      dim "  清理上次残留的 pid 文件（进程 ${pid} 已退出）"
    fi
    drop_pidfile "$port"
  fi

  # 2) 端口被占：是本项目的就收编，是别人的就拒绝（绝不抢端口）
  # 先看 Server 头（光看进程身份会把同目录下的 http.server 认成自己）
  local st
  st=$(port_state "$port")
  case "$st" in
    ours)
      occ=$(pid_on_port "$port")
      warn "端口 ${port} 上已有本项目实例（pid ${occ}），收编为受管进程"
      write_pidfile "$port" "$occ"
      banner "$port" "$occ" 1
      return 0
      ;;
    foreign)
      occ=$(pid_on_port "$port")
      err "端口 ${port} 已被其他程序占用（pid ${occ}），换个端口：./start.sh 8900"
      return 1
      ;;
  esac

  # 3) 前置检查
  if ! command -v "$PY" >/dev/null 2>&1; then
    err "找不到 ${PY}，请先安装 Python 3.9+，或用 PYTHON=/path/to/python3 ./start.sh"
    return 1
  fi
  if [ ! -f "$APP_DIR/run.py" ]; then
    err "找不到 ${APP_DIR}/run.py，脚本可能被移动过"
    return 1
  fi

  mkdir -p "$RUN_DIR"
  logf=$(log_file_of "$port")
  if [ -f "$logf" ] && [ "$(wc -c <"$logf" | tr -d ' ')" -gt "$LOG_MAX_BYTES" ]; then
    mv -f "$logf" "$logf.1"
    dim "  日志超过 5MB，已轮转为 $(basename "$logf").1"
  fi

  info "${C_C}启动中${C_0}  Python $("$PY" -V 2>&1 | awk '{print $2}') · 端口 ${port}"
  printf '%s\n' "--- $(date '+%Y-%m-%d %H:%M:%S') 启动 ---" >>"$logf"

  # 后台启动：先 cd 到 FastAPI/ 再 nohup，这样 $! 拿到的是 python 本身。
  # 不能写成 `( cd ... && nohup ... ) &` —— 那样 $! 是子 shell 的 pid，
  # stop 时只杀掉子 shell，python 会变成孤儿继续占着端口。
  cd "$APP_DIR" || { err "无法进入 ${APP_DIR}"; return 1; }
  nohup "$PY" run.py --host "$HOST" --port "$port" >>"$logf" 2>&1 &
  pid=$!
  cd "$ROOT" || true
  write_pidfile "$port" "$pid"

  # 4) 等 HTTP 就绪（只看进程活着不够，db.init/seed 还要跑几秒）
  local ticks=0 max=$((READY_TIMEOUT * 4)) code
  while :; do
    if ! pid_alive "$pid"; then
      err "启动失败，进程已退出。最近日志："
      tail -n 20 "$logf" | sed 's/^/    /'
      drop_pidfile_if_own "$port" "$pid"
      return 1
    fi
    code=$(http_code "$port")
    [ "$code" = "200" ] && break
    if [ "$ticks" -ge "$max" ]; then
      err "等待 ${READY_TIMEOUT}s 仍未就绪（最后一次 HTTP=${code:-无响应}），进程 ${pid} 仍在后台"
      info "  继续排查：./difu.sh status ${port}   /   ./difu.sh log ${port} -f"
      return 1
    fi
    sleep 0.25
    ticks=$((ticks + 1))
  done

  banner "$port" "$pid"
  printf '\n'
  dim "  停止：./stop.sh ${port}      日志：./difu.sh log ${port} -f"
  return 0
}

# ------------------------------------------------------------------ stop
stop_one() {
  local port="$1" rec pid ts waited=0 max=$((STOP_TIMEOUT * 4))

  if ! rec=$(who_runs "$port"); then
    if [ "$(port_state "$port")" = "foreign" ]; then
      err "端口 ${port} 被其他程序占用（pid $(pid_on_port "$port")），不是本项目，不动它"
      return 1
    fi
    warn "端口 ${port} 未在运行"
    drop_pidfile "$port"
    return 0
  fi
  read -r pid _ <<<"$rec"

  info "${C_C}停止中${C_0}  pid ${pid} · 端口 ${port}"
  kill -TERM "$pid" 2>/dev/null
  while pid_alive "$pid"; do
    if [ "$waited" -ge "$max" ]; then
      warn "TERM 后 ${STOP_TIMEOUT}s 仍未退出，改用 SIGKILL"
      kill -9 "$pid" 2>/dev/null
      sleep 1
      break
    fi
    sleep 0.25
    waited=$((waited + 1))
  done

  if pid_alive "$pid"; then
    err "无法终止 pid ${pid}"
    return 1
  fi

  drop_pidfile "$port"
  printf '%s\n' "--- $(date '+%Y-%m-%d %H:%M:%S') 已停止 ---" >>"$(log_file_of "$port")" 2>/dev/null
  ok "端口 ${port} 已停止（pid ${pid}）"
  return 0
}

cmd_stop() {
  local want="${1:-}" port rc=0
  if [ -n "$want" ]; then
    stop_one "$want" || rc=1
    return "$rc"
  fi
  # 没指定端口：停掉所有受管实例
  local any=0
  for port in $(managed_ports); do
    any=1
    stop_one "$port" || rc=1
  done
  if [ "$any" -eq 0 ]; then
    warn "没有本脚本管理的实例在运行（看全部端口：./difu.sh status）"
  fi
  return "$rc"
}

# ------------------------------------------------------------------ status
status_one() {
  local port="$1" rec pid ts=0 code running=0 occ
  if rec=$(who_runs "$port"); then
    read -r pid _ <<<"$rec"
    ts=$(read_pidfile "$port" 2>/dev/null | awk '{print $2}')
    [ -z "${ts:-}" ] && ts=0
    running=1
  fi

  if [ "$running" -eq 1 ]; then
    row "端口" "$port"
    row "状态" "${C_G}运行中${C_0}  pid ${pid}"
    if [ "$ts" -gt 0 ] 2>/dev/null; then
      row "时长" "$(human_duration $(($(date +%s) - ts)))"
    fi
    code=$(http_code "$port")
    if [ "$code" = "200" ]; then
      row "HTTP" "${C_G}200${C_0}  http://127.0.0.1:${port}/"
    else
      row "HTTP" "${C_R}${code:-无响应}${C_0}  （进程在但接口不通，看日志）"
    fi
    if [ -f "$(log_file_of "$port")" ]; then
      row "日志" "$(log_file_of "$port")"
    else
      row "日志" "${C_D}无（不是本脚本启动的，看不到输出）${C_0}"
    fi
    return 0
  fi

  row "端口" "$port"
  row "状态" "${C_Y}未运行${C_0}"
  occ=$(pid_on_port "$port")
  if [ -n "$occ" ]; then
    row "占用" "${C_R}pid ${occ}（不是本项目）${C_0}"
  fi
  row "启动" "./start.sh ${port}"
  return 1
}

cmd_status() {
  local want="${1:-}" port
  printf '%s\n' "地府阴差管理系统 · 运行状态"
  row "目录" "$ROOT"
  printf '\n'

  if [ -n "$want" ]; then
    status_one "$want"
    local rc=$?
    if [ "$rc" -eq 0 ] && [ -f "$(log_file_of "$want")" ]; then
      printf '\n%s最近 8 行日志%s\n' "$C_D" "$C_0"
      tail -n 8 "$(log_file_of "$want")" | sed 's/^/  /'
    fi
    return "$rc"
  fi

  local any=0 rc=0
  for port in $(managed_ports); do
    any=1
    status_one "$port" || rc=1
    printf '\n'
  done
  if [ "$any" -eq 0 ]; then
    printf '  %s没有任何受管实例%s\n\n' "$C_Y" "$C_0"
    printf '  %s启动：./start.sh%s\n' "$C_D" "$C_0"
    return 1
  fi
  return "$rc"
}

# ------------------------------------------------------------------ log
cmd_log() {
  local want="" follow=0 a port logf
  for a in "$@"; do
    case "$a" in
      -f|--follow) follow=1 ;;
      ''|*[!0-9]*) err "日志只接受一个端口参数：$a"; return 2 ;;
      *) want="$a" ;;
    esac
  done

  if [ -z "$want" ]; then
    # 没给端口：只有一个受管实例就用它，有多个就提示
    local ports
    ports=$(managed_ports)
    if [ -z "$ports" ]; then
      err "还没有受管实例，先启动：./start.sh"
      return 1
    fi
    if [ "$(printf '%s\n' "$ports" | wc -l | tr -d ' ')" -gt 1 ]; then
      err "有多个实例在运行，请指定端口：$(echo $ports)"
      return 2
    fi
    want="$ports"
  fi

  logf=$(log_file_of "$want")
  if [ ! -f "$logf" ]; then
    warn "端口 ${want} 还没有日志（没通过本脚本启动过）：${logf}"
    return 1
  fi
  if [ "$follow" -eq 1 ]; then
    info "${C_D}跟随 ${logf}（Ctrl+C 退出，不会停服务）${C_0}"
    tail -n 20 -f "$logf"
  else
    tail -n "${LOG_LINES:-40}" "$logf"
  fi
}

# ------------------------------------------------------------------ 分发
ACTION="${1:-}"
case "$ACTION" in
  start)
    shift
    resolve_port "${1:-}" "$DEFAULT_PORT"
    cmd_start "$RESOLVED_PORT"
    ;;
  stop)
    shift
    resolve_port "${1:-}" ""
    cmd_stop "$RESOLVED_PORT"
    ;;
  restart)
    shift
    # 🔴 原来写死 8000：实际部署在 8899 时 `restart` 不带端口会去停一个
    #    根本没在跑的实例，然后又在 8000 起一个。现在自动跟随正在运行的端口。
    if [ -n "${1:-}" ] || [ -n "${PORT:-}" ] || [ -n "${DIFU_PORT:-}" ]; then
      resolve_port "${1:-}" "$DEFAULT_PORT"
    else
      running=""
      while IFS= read -r p; do
        [ -n "$p" ] || continue
        if [ "$(port_state "$p")" = "ours" ]; then running="$p"; break; fi
      done < <(managed_ports)
      resolve_port "$running" "$DEFAULT_PORT"
    fi
    cmd_stop "$RESOLVED_PORT"
    printf '\n'
    cmd_start "$RESOLVED_PORT"
    ;;
  status)
    shift
    resolve_port "${1:-}" ""
    cmd_status "$RESOLVED_PORT"
    ;;
  log)
    shift
    cmd_log "$@"
    ;;
  -h|--help|help|'')
    usage 0
    ;;
  *)
    err "未知命令：${ACTION}"
    printf '\n'
    usage 2
    ;;
esac
