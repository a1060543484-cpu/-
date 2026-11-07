"""鉴权 / RBAC / 行级数据过滤 / 双日志（audit_log + agent_log）。

🔐 安全改造（2026-11-05），逐条对应评审里的 P0：
1. 口令：固定全局盐 `sha256(b"difu-salt"+pwd)` → **每账号随机盐 + PBKDF2-HMAC-SHA256**。
   旧格式仍可验证（平滑迁移），但首次启动会自动把弱口令轮换掉。
2. token：加 `jti`，支持**服务端吊销**，logout 不再是「假登出」。
3. 行级权限：`can_see_case` 与 `case_scope` 对齐，补上 `judge_id=0`（未指派卷宗）。
4. 大模型 Key：不再明文存库，统一搬到 `.run/secrets.env`（600，不进 git）。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from typing import Any

from . import config, db

ROLE_PERMISSIONS = {
    "admin": ["*"],
    "king": ["case:*", "approval:*", "deceased:*", "knowledge:*", "user:*",
             "dict:*", "model:*", "log:*", "dashboard:*", "chat:*", "evidence:*",
             "judgment:*", "reincarnation:*"],
    "judge": ["case:read", "case:update", "case:run", "case:create", "case:delete",
              "deceased:*", "evidence:*", "judgment:*", "knowledge:read",
              "approval:read", "approval:decide", "dashboard:read", "chat:use", "log:read"],
    # 阴差能建卷、批注、传证，但**不能删卷宗**（没有 case:delete）。
    # 以前 delete 接口只校验 case:update，等于把删除权白送给了 clerk。
    "clerk": ["case:read", "case:create", "case:update", "case:run",
              "deceased:read", "deceased:create", "deceased:update",
              "evidence:read", "evidence:create", "evidence:delete",
              "knowledge:read", "approval:read", "chat:use", "dashboard:read"],
    "reincarnation": ["case:read", "case:run", "deceased:read", "judgment:read",
                      "reincarnation:*", "knowledge:read", "approval:read",
                      "dashboard:read", "chat:use"],
    "chenghuang": ["case:read", "case:create", "deceased:read", "deceased:create",
                   "deceased:update", "evidence:read", "approval:read",
                   "dashboard:read", "chat:use"],
}

ROLE_NAMES = {
    "admin": "管理员", "king": "阎王", "judge": "判官", "clerk": "阴差",
    "reincarnation": "轮回官", "chenghuang": "城隍",
}

# 课堂演示用的六组历史弱口令（只在 DIFU_DEMO_MODE=1 时保留）
LEGACY_WEAK_PASSWORDS = ("admin123", "123456")
PBKDF2_ITERATIONS = 120_000
OLD_SALT = b"difu-salt"                     # 仅为兼容已存在的旧库，新口令绝不再用它

_LOCK = threading.Lock()
REVOKED_FILE = config.RUN_DIR / "revoked_tokens.json"
_REVOKED: dict[str, int] = {}              # jti -> exp（Unix 秒）
# seed.py 在空库建账号时生成的随机口令，先暂存，等轮换流程一起写凭据文件
NEW_CREDENTIALS: list[tuple[str, str, str]] = []


# ------------------------------------------------------------------ 口令
def hash_password(raw: str, salt: bytes | None = None) -> str:
    """每账号随机盐 + PBKDF2。格式：pbkdf2_sha256$<迭代数>$<盐hex>$<摘要hex>。

    固定全局盐的致命处不在「盐被知道」，而在于**同口令 → 同哈希**：
    库里 6 个账号有 5 个哈希一模一样，一眼就能看出大家共用 123456。
    """
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", raw.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def _legacy_hash(raw: str) -> str:
    return hashlib.sha256(OLD_SALT + raw.encode("utf-8")).hexdigest()


def verify_password(raw: str, hashed: str) -> bool:
    """新格式走 PBKDF2；64 位裸 hex 视为旧库遗留，临时兼容（轮换后即消失）。"""
    if not hashed:
        return False
    if hashed.startswith("pbkdf2_sha256$"):
        try:
            _, iters, salt_hex, want = hashed.split("$", 3)
            got = hashlib.pbkdf2_hmac(
                "sha256", raw.encode("utf-8"), bytes.fromhex(salt_hex), int(iters)
            ).hex()
            return hmac.compare_digest(got, want)
        except (ValueError, TypeError):
            return False
    return hmac.compare_digest(_legacy_hash(raw), hashed)


def _random_password(n: int = 14) -> str:
    """随机口令：去掉容易看错的 0/O/1/l，纯字母数字便于手敲。"""
    alphabet = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(n))


# 历史演示口令（只在 DIFU_DEMO_MODE=1 时使用）
DEMO_PASSWORD_OF = {
    "admin": "admin123", "yanwang": "123456", "panguan": "123456",
    "yinchai": "123456", "lunhui": "123456", "chenghuang": "123456",
}


def initial_password(username: str, real_name: str = "") -> str:
    """给 seed.py 建账号用：演示模式给历史弱口令，正常模式给随机强口令。"""
    if config.DEMO_MODE:
        return DEMO_PASSWORD_OF.get(username, "123456")
    pwd = _random_password()
    NEW_CREDENTIALS.append((username, real_name, pwd))
    return pwd


def rotate_initial_passwords() -> dict:
    """首次启动把库里的弱口令轮换为随机强口令，凭据写到 `.run/credentials.txt`。

    - `DIFU_DEMO_MODE=1`：课堂演示模式，保留弱口令，只打印醒目警告。
    - 轮换过的账号（新格式哈希）不再重复轮换，避免每次重启口令都变。
    - 凭据文件权限 600，且在 .gitignore 里，不会被提交。
    """
    if config.DEMO_MODE:
        print("[安全警告] 当前是演示模式（DIFU_DEMO_MODE=1），已保留弱口令；"
              "仅限本机 127.0.0.1 使用，切勿开公网。", flush=True)
        return {"rotated": 0, "demo_mode": True}

    try:
        users = db.query("SELECT id, username, real_name, password_hash FROM user ORDER BY id")
    except Exception as exc:
        print(f"[安全警告] 读取账号失败，跳出口令轮换：{exc}", flush=True)
        return {"rotated": 0, "error": str(exc)}

    changed: list[tuple[str, str, str]] = []
    for u in users:
        old = u.get("password_hash") or ""
        # 只处理「旧格式且确实是弱口令」的账号；用户自己改过的强口令不动
        if old.startswith("pbkdf2_sha256$"):
            continue
        if not any(verify_password(p, old) for p in LEGACY_WEAK_PASSWORDS):
            continue
        pwd = _random_password()
        db.update("user", {"password_hash": hash_password(pwd), "updated_at": db.now()},
                  "id=?", (u["id"],))
        changed.append((u["username"], u.get("real_name") or "", pwd))

    # 空库首次启动时 seed.py 已经用 initial_password() 生成了随机口令，
    # 一起写进凭据文件；否则用户新建库后会完全不知道登录口令。
    all_creds = list(NEW_CREDENTIALS) + changed
    NEW_CREDENTIALS.clear()
    if not all_creds:
        return {"rotated": 0, "demo_mode": False}

    lines = [
        "# 地府阴差管理系统 · 本机演示账号口令",
        f"# 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        "# 本文件权限 600 且已被 .gitignore 忽略，请勿外传 / 提交。",
        "# DIFU_DEMO_MODE=1 只对全新空库给固定弱口令；已有库不会改回弱口令，仍用下面的口令。",
        "",
    ]
    lines += [f"{u:14} {p}    # {name}" for u, name, p in all_creds]
    lines.append("")
    config._write_private(config.CREDENTIALS_FILE, "\n".join(lines))
    print(f"[安全] 已为 {len(all_creds)} 个账号生成随机口令，见：{config.CREDENTIALS_FILE}",
          flush=True)
    return {"rotated": len(changed), "created": len(all_creds), "demo_mode": False,
            "usernames": [u for u, _, _ in all_creds]}


# ------------------------------------------------------------------ 令牌
def _sign(payload: str) -> str:
    return hmac.new(config.APP_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]


def make_token(user_id: int, role: str) -> str:
    body = base64.urlsafe_b64encode(
        json.dumps({
            "uid": user_id, "role": role,
            "iat": int(time.time()),
            "exp": int(time.time()) + config.TOKEN_TTL,
            "jti": secrets.token_urlsafe(16),          # 唯一 id，供服务端吊销
        }).encode()
    ).decode()
    return f"{body}.{_sign(body)}"


def _load_revoked() -> None:
    global _REVOKED
    if _REVOKED:
        return
    try:
        if REVOKED_FILE.is_file():
            data = json.loads(REVOKED_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                _REVOKED = {str(k): int(v) for k, v in data.items()}
    except (OSError, ValueError, TypeError):
        _REVOKED = {}


def _save_revoked() -> None:
    # 顺手清掉已过期的条目，文件不会无限增长
    now = int(time.time())
    fresh = {k: v for k, v in _REVOKED.items() if v > now}
    _REVOKED.clear()
    _REVOKED.update(fresh)
    config._write_private(REVOKED_FILE, json.dumps(_REVOKED, ensure_ascii=False, indent=2))


def revoke_token(token: str) -> bool:
    """把 token 加入服务端黑名单（落盘），返回是否成功吊销。"""
    data = parse_token(token, check_revoked=False)
    if not data or not data.get("jti"):
        return False
    with _LOCK:
        _load_revoked()
        _REVOKED[data["jti"]] = int(data.get("exp") or 0)
        _save_revoked()
    return True


def parse_token(token: str, check_revoked: bool = True) -> dict | None:
    try:
        body, sig = token.split(".")
        if not hmac.compare_digest(_sign(body), sig):
            return None
        data = json.loads(base64.urlsafe_b64decode(body.encode()).decode())
        if data.get("exp", 0) < int(time.time()):
            return None
        if check_revoked:
            with _LOCK:
                _load_revoked()
                if data.get("jti") and data["jti"] in _REVOKED:
                    return None
        return data
    except Exception:
        return None


# ------------------------------------------------------------------ 大模型 Key（不入库）
def _read_secrets() -> dict[str, str]:
    out: dict[str, str] = {}
    if not config.SECRETS_FILE.is_file():
        return out
    try:
        for line in config.SECRETS_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def _write_secrets(data: dict[str, str]) -> None:
    lines = [
        "# 地府阴差管理系统 · 本地密钥（权限 600，已被 .gitignore 忽略）",
        "# 明文只放这里，数据库 model_config.api_key 永远留空。",
        "",
    ]
    lines += [f"{k}={v}" for k, v in sorted(data.items()) if v]
    config._write_private(config.SECRETS_FILE, "\n".join(lines) + "\n")


def _apply_env_key(var: str, value: str) -> None:
    """写进程环境 + 同步 config 模块里的快照变量（refresh_degrade 要用）。"""
    os.environ[var] = value
    mapping = {
        "LLM_API_KEY": "LLM_API_KEY", "VISION_API_KEY": "VISION_API_KEY",
        "ASR_API_KEY": "ASR_API_KEY", "EMBEDDING_API_KEY": "EMBEDDING_API_KEY",
    }
    if var in mapping:
        setattr(config, mapping[var], value)


def store_api_key(purpose: str, key: str) -> None:
    """把新 Key 写进 secrets.env + 当前进程环境，并把库里对应字段清空。"""
    var = config.env_key_var(purpose)
    with _LOCK:
        data = _read_secrets()
        data[var] = key
        _write_secrets(data)
    _apply_env_key(var, key)
    db.update("model_config", {"api_key": "", "status": "ready", "updated_at": db.now()},
              "purpose=?", (purpose,))


def has_api_key(purpose: str) -> bool:
    var = config.env_key_var(purpose)
    return bool(os.environ.get(var) or getattr(config, var, ""))


def migrate_plaintext_keys() -> int:
    """启动时把库里遗留的明文 Key 搬到 secrets.env，然后清空数据库字段。

    返回迁移条数。这一步让「数据库文件被提交/外传」不再等于「Key 泄露」。
    """
    try:
        rows = db.query("SELECT id, purpose, api_key FROM model_config "
                        "WHERE api_key IS NOT NULL AND api_key<>''")
    except Exception:
        return 0
    if not rows:
        return 0
    with _LOCK:
        data = _read_secrets()
        for r in rows:
            var = config.env_key_var(r["purpose"])
            data.setdefault(var, r["api_key"])
        _write_secrets(data)
    for r in rows:
        var = config.env_key_var(r["purpose"])
        _apply_env_key(var, data[var])
        db.update("model_config", {"api_key": "", "status": "ready"}, "id=?", (r["id"],))
    print(f"[安全] 已把 {len(rows)} 条明文 API Key 从数据库迁移到 "
          f"{config.SECRETS_FILE}，数据库字段已清空。", flush=True)
    print("[安全] ⚠️ 这个 Key 曾以明文出现过，建议到模型平台**轮换**一次。", flush=True)
    return len(rows)


# ------------------------------------------------------------------ 权限判定
def permissions_of(role: str) -> list[str]:
    return ROLE_PERMISSIONS.get(role, [])


def has_perm(role: str, perm: str) -> bool:
    perms = permissions_of(role)
    if "*" in perms:
        return True
    for p in perms:
        if p == perm:
            return True
        if p.endswith(":*") and perm.split(":")[0] == p.split(":")[0]:
            return True
        if p.endswith(":read") and perm.endswith(":read") and p.split(":")[0] == perm.split(":")[0]:
            return True
    return False


def case_scope(user: dict) -> str:
    """行级数据过滤：返回**裸条件**（不带 WHERE / AND 前缀），无过滤时返回空串。

    调用方负责拼 "WHERE"：有裸条件才拼，否则裸拼会得到 `FROM case_file AND ...`
    这种非法 SQL（曾经因此让城隍账号访问卷宗列表 500）。
    """
    role = user.get("role_code")
    if role in ("admin", "king"):
        return ""
    if role == "judge":
        # judge_id=0 是「尚未指派承办人」（schema 里该列默认 0 而非 NULL），
        # 这类新立案卷宗必须对所有判官可见，否则阴差/管理员立的卷子没人能接。
        return f"(judge_id={int(user['id'])} OR judge_id=0 OR judge_id IS NULL)"
    if role == "chenghuang" and user.get("jurisdiction_id"):
        return f"jurisdiction_id={int(user['jurisdiction_id'])}"
    return ""


def visible_cases(user: dict, where: str = "", params: tuple = ()) -> list[dict]:
    conds = [where] if where else []
    scope = case_scope(user)
    if scope:
        conds.append(scope)
    sql = "SELECT * FROM case_file"
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY id DESC"
    return db.query(sql, params)


def can_see_case(user: dict, case: dict) -> bool:
    """与 case_scope 保持同一套规则。

    🔴 修过一次 bug：这里原来只认 `judge_id in (None, user.id)`，但列表 SQL
    明确放行 `judge_id=0`（未指派），导致「列表看得见、点进详情 403」。
    """
    role = user.get("role_code")
    if role in ("admin", "king"):
        return True
    if role == "judge":
        judge_id = case.get("judge_id")
        return judge_id in (None, 0, user["id"])
    if role == "chenghuang":
        return case.get("jurisdiction_id") == user.get("jurisdiction_id")
    return True                                        # clerk / reincarnation：全量可见（岗位需要）


# ------------------------------------------------------------------ 双日志
def audit(user: dict | None, action: str, target_type: str = "", target_id: int = 0,
          detail: Any = "", ip: str = "127.0.0.1", source: str = "human") -> None:
    """人工操作审计日志（source=human）与 Agent 代操作（source=agent）分来源记录。"""
    db.insert("audit_log", {
        "user_id": (user or {}).get("id", 0), "user_name": (user or {}).get("real_name", "系统"),
        "action": action, "target_type": target_type, "target_id": int(target_id or 0),
        "detail": detail if isinstance(detail, str) else db.jdumps(detail),
        "ip": ip, "source": source, "created_at": db.now(),
    })


def agent_log(case_id: int, agent_code: str, agent_name: str, node: str, action: str,
              thought: str = "", tool_name: str = "", tool_input: Any = None,
              tool_output: Any = None, model: str = "", latency_ms: int = 0,
              tokens: int = 0) -> None:
    """Agent 推理日志：每一步思考 + 工具调用都可回放。"""
    db.insert("agent_log", {
        "case_id": int(case_id or 0), "agent_code": agent_code, "agent_name": agent_name,
        "node": node, "action": action, "thought": thought, "tool_name": tool_name,
        "tool_input": db.jdumps(tool_input) if tool_input is not None else "",
        "tool_output": db.jdumps(tool_output) if tool_output is not None else "",
        "model": model, "latency_ms": int(latency_ms), "tokens": int(tokens),
        "created_at": db.now(),
    })


def notify(user_id: int, title: str, content: str, ntype: str = "system",
           link: str = "") -> None:
    db.insert("notification", {
        "user_id": int(user_id or 0), "title": title, "content": content,
        "ntype": ntype, "is_read": 0, "link": link, "created_at": db.now(),
    })


def notify_role(role_code: str, title: str, content: str, ntype: str = "approval",
                link: str = "") -> None:
    for u in db.query("SELECT id FROM user WHERE role_code=? AND status='active'", (role_code,)):
        notify(u["id"], title, content, ntype, link)
