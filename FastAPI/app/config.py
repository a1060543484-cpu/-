"""全局配置：全部走环境变量，缺省即「零依赖可跑」的演示模式。

🔐 安全改造（2026-11-05）：
- 删掉源码里写死的 `APP_SECRET="difu-secret-key-please-change"`。
  那个值公开在仓库里，任何人都能离线签一个 uid=1 的 token 直接变管理员。
  现在改成：环境变量 > `.run/app_secret`（首次随机生成、权限 600、不进 git）。
- 大模型 Key 不再明文存数据库，统一放 `.run/secrets.env`（权限 600、不进 git），
  启动时由 `_load_local_secrets()` 注入环境变量；`llm.active_config()` 本来就有
  「库里没 Key 就回落环境变量」的逻辑，所以功能不受影响。
- 绑定地址、CORS 白名单、演示模式开关也在这里集中管理。
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent          # FastAPI/
APP_DIR = BASE_DIR / "app"
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = BASE_DIR / "uploads"
FRONT_DIR = BASE_DIR.parent / "frontVue3"                   # 前端目录（由后端静态托管）

# 运行时私有文件目录：pid / 日志 / 密钥 / 凭据都放这里，整目录不进 git。
# 可用 DIFU_RUN_DIR 覆盖（自动化用临时库起隔离实例时，避免污染真实运行目录）。
RUN_DIR = Path(os.environ.get("DIFU_RUN_DIR", "") or (BASE_DIR.parent / ".run")).resolve()
SECRETS_FILE = RUN_DIR / "secrets.env"                     # 明文大模型 Key（权限 600）
SECRET_KEY_FILE = RUN_DIR / "app_secret"                   # token 签名密钥（权限 600）
CREDENTIALS_FILE = RUN_DIR / "credentials.txt"             # 首次启动生成的随机演示口令


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _env_bool(key: str, default: bool = False) -> bool:
    v = _env(key)
    if not v:
        return default
    return v.lower() in ("1", "true", "yes", "on")


def _ensure_private_dir() -> None:
    """创建运行目录，权限 700（仅本机当前用户可读写）。"""
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(RUN_DIR, 0o700)
    except OSError:
        pass                                             # 某些文件系统不支持 chmod，不阻断启动


def _write_private(path: Path, text: str) -> None:
    """写私密文件：先写内容再收权限到 600，避免短暂窗口里是 644。"""
    _ensure_private_dir()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(path)


def _load_local_secrets() -> None:
    """把 `.run/secrets.env` 注入进程环境（只补空缺，绝不覆盖显式传入的值）。

    这样不管用哪种方式启动（./start.sh、PyCharm 直接跑 run.py、python -m ...），
    Key 都能被读到。格式就是最简单的 `KEY=VALUE`，支持 `# 注释` 与引号。
    """
    if not SECRETS_FILE.is_file():
        return
    try:
        lines = SECRETS_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        # 显式环境变量优先：方便临时用别的 Key 测试，也方便 CI 注入
        if key and key not in os.environ:
            os.environ[key] = value


def _resolve_app_secret() -> str:
    """签名密钥：环境变量 > 本地随机密钥文件 > 首次生成随机密钥。

    关键点：**源码里不再有任何可用默认值**。拿到仓库源码 ≠ 能伪造 token。
    密钥落盘是为了重启后旧 token 依然有效（否则每次重启都把所有人踢下线）。
    """
    explicit = os.environ.get("APP_SECRET", "").strip()
    if explicit:
        return explicit
    if SECRET_KEY_FILE.is_file():
        saved = SECRET_KEY_FILE.read_text(encoding="utf-8").strip()
        if saved:
            return saved
    generated = secrets.token_hex(32)                     # 256 bit 随机
    _write_private(SECRET_KEY_FILE, generated + "\n")
    print(f"[安全] 已生成随机签名密钥：{SECRET_KEY_FILE}（权限 600，请勿提交 git）", flush=True)
    return generated


# ⚠️ 必须在下面所有 `_env()` 取值之前执行，否则环境变量回落拿不到 Key
_load_local_secrets()
APP_SECRET = _resolve_app_secret()

# ------- 运行方式 -------
# 流程引擎选择：auto（装了 langgraph 就用）/ langgraph / builtin（强制纯标准库降级）。
# 教学或排错时用 DIFU_FLOW_ENGINE=builtin ./start.sh 可一键切回旧状态机做对比。
FLOW_ENGINE = _env("DIFU_FLOW_ENGINE", "auto").lower()
DEFAULT_HOST = _env("DIFU_HOST", "127.0.0.1")             # 默认只监听本机，不再 0.0.0.0
DEFAULT_PORT = int(_env("DIFU_PORT", "8899"))              # 与 start.sh / stop.sh / README 统一
# 演示模式：显式打开才允许弱口令。默认关闭 → 首次启动生成随机强口令。
DEMO_MODE = _env_bool("DIFU_DEMO_MODE", False)
# 逗号分隔的白名单；同源部署不需要 CORS，留空即可
CORS_ALLOWED_ORIGINS = [o.strip() for o in _env("CORS_ALLOWED_ORIGINS").split(",") if o.strip()]
MAX_UPLOAD_BYTES = int(_env("MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)))   # 单请求上传上限 10MB

DB_ENGINE = _env("DB_ENGINE", "sqlite")            # sqlite | mysql（未装 aiomysql 时自动回落 sqlite）
SQLITE_PATH = _env("SQLITE_PATH", str(DATA_DIR / "difu.db"))
MYSQL_HOST = _env("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = int(_env("MYSQL_PORT", "3306"))
MYSQL_USER = _env("MYSQL_USER", "root")
MYSQL_PASSWORD = _env("MYSQL_PASSWORD", "")
MYSQL_DB = _env("MYSQL_DB", "difu")

# ------- 大模型 -------
LLM_API_KEY = _env("LLM_API_KEY")                  # 兼容 OpenAI 协议：DeepSeek / 通义 / OpenAI ...
LLM_BASE_URL = _env("LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_MODEL = _env("LLM_MODEL", "deepseek-chat")
LLM_TIMEOUT = float(_env("LLM_TIMEOUT", "60"))
VISION_API_KEY = _env("VISION_API_KEY")
VISION_BASE_URL = _env("VISION_BASE_URL", "")
VISION_MODEL = _env("VISION_MODEL", "qwen-vl-max")
ASR_API_KEY = _env("ASR_API_KEY")
ASR_BASE_URL = _env("ASR_BASE_URL", "")

# ------- 向量库 -------
EMBEDDING_API_KEY = _env("EMBEDDING_API_KEY")
EMBEDDING_BASE_URL = _env("EMBEDDING_BASE_URL", "")
EMBEDDING_MODEL = _env("EMBEDDING_MODEL", "text-embedding-3-small")
VECTOR_STORE = _env("VECTOR_STORE", "local")       # local | pgvector（未装依赖时自动回落 local）
PGVECTOR_DSN = _env("PGVECTOR_DSN")
VECTOR_JSON = DATA_DIR / "vector_store.json"

# ------- 调度与安全 -------
SCHEDULER_ENABLED = _env_bool("SCHEDULER_ENABLED", True)
LOG_VERBOSE = _env_bool("LOG_VERBOSE", False)          # 是否打印每条 HTTP 访问日志
SCHEDULER_INTERVAL = int(_env("SCHEDULER_INTERVAL", "60"))   # 秒
TOKEN_TTL = int(_env("TOKEN_TTL", "86400"))
CHECKPOINT_TTL = int(_env("CHECKPOINT_TTL", "86400"))        # 检查点内存版保留 24h

# ------- 降级矩阵（每一项都是「有真件用真件，没有就用替身」）-------
DEGRADE = {
    "llm": not bool(LLM_API_KEY),
    "vision": not bool(VISION_API_KEY),
    "asr": not bool(ASR_API_KEY),
    "embedding": not bool(EMBEDDING_API_KEY),
    "vector_pg": True,
    "langgraph": True,
    "apscheduler": True,
    "tortoise": True,
    "fastapi": True,
}


def env_key_var(purpose: str) -> str:
    """按用途映射到环境变量名（写入 / 读取 secrets.env 时统一走这里）。"""
    return {
        "text": "LLM_API_KEY", "vision": "VISION_API_KEY",
        "asr": "ASR_API_KEY", "embedding": "EMBEDDING_API_KEY",
    }.get(purpose, "LLM_API_KEY")


def refresh_degrade() -> dict:
    """启动后探测真实依赖，把降级矩阵刷新为真实值。

    ⚠️ 语义说明（2026-11-05 修正）：矩阵里的 `langgraph` 只表示「装没装包」，
    **不表示实际在用**。执行路径一直是内置状态机，所以额外返回
    `engine` / `langgraph_used` / `langgraph_installed` 三个字段如实报告，
    避免顶栏「显示 langgraph」被误读成「跑了 langgraph」。
    """
    import importlib.util

    def has(mod: str) -> bool:
        return importlib.util.find_spec(mod) is not None

    DEGRADE["langgraph"] = not has("langgraph")
    DEGRADE["apscheduler"] = not has("apscheduler")
    DEGRADE["tortoise"] = not has("tortoise")
    DEGRADE["fastapi"] = not has("fastapi")
    DEGRADE["vector_pg"] = not (bool(PGVECTOR_DSN) and has("psycopg2"))

    env_keys = {
        "llm": LLM_API_KEY,
        "vision": VISION_API_KEY,
        "asr": ASR_API_KEY,
        "embedding": EMBEDDING_API_KEY,
    }
    # 降级矩阵的键与 model_config.purpose 不是同一套命名：
    # 矩阵里叫 llm，表里叫 text（llm.active_config 默认 purpose="text"）
    purpose_of = {"llm": "text"}
    # 延迟导入：db 反过来 import 本模块，模块级导入会成环
    try:
        from . import db

        rows = db.query("SELECT purpose, api_key FROM model_config "
                        "WHERE is_active=1 AND api_key IS NOT NULL AND api_key<>''")
        db_keys = {r["purpose"] for r in rows}
    except Exception:
        db_keys = set()
    for purpose, env_key in env_keys.items():
        DEGRADE[purpose] = not (bool(env_key) or purpose_of.get(purpose, purpose) in db_keys)

    # 如实报告：装了包 ≠ 用了它。
    # 2026-11-05 起 LangGraph 真的接管了 run_flow，所以从执行引擎动态取，
    # 不再写死 False（否则前端会一直显示"未使用"，与事实相反）。
    try:
        from .agents import graph as _graph          # 延迟导入，避免 config ↔ graph 循环
        cap = _graph.engine_capability()
        DEGRADE["engine"] = cap["engine"]
        DEGRADE["langgraph_used"] = cap["langgraph_used"]
        DEGRADE["langgraph_installed"] = cap["langgraph_installed"]
        DEGRADE["flow_engine"] = cap["flow_engine"]
        DEGRADE["checkpointer"] = cap["checkpointer"]
    except Exception:
        DEGRADE["engine"] = "builtin-state-machine"
        DEGRADE["langgraph_used"] = False
        DEGRADE["langgraph_installed"] = not DEGRADE["langgraph"]
    return DEGRADE
