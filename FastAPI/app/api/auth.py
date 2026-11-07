"""登录 / 当前用户 / 仪表盘统计。"""
from __future__ import annotations

from .. import config, db, security
from ..agents import graph
from .base import HTTPError, Request, Router, ok, require

router = Router("/api")


@router.post("/auth/login")
def login(req: Request):
    body = req.json()
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    if not username or not password:
        raise HTTPError("请输入用户名与口令")
    user = db.query_one("SELECT * FROM user WHERE username=?", (username,))
    # 统一提示，不区分「账号不存在 / 口令错」，防用户名枚举
    if not user or not security.verify_password(password, user.get("password_hash", "")):
        security.audit({"real_name": username}, "登录失败", "user", 0, {"username": username},
                       ip=req.client_ip)
        raise HTTPError("用户名或口令有误", 401)
    if user.get("status") != "active":
        raise HTTPError("账号已停用，请联系管理员", 403)
    token = security.make_token(user["id"], user["role_code"])
    security.audit(user, "登录", "user", user["id"], {"role": user["role_code"]}, ip=req.client_ip)
    return ok({
        "token": token,
        "user": {
            "id": user["id"], "username": user["username"], "real_name": user["real_name"],
            "role_code": user["role_code"], "role_name": security.ROLE_NAMES.get(user["role_code"]),
            "jurisdiction_id": user.get("jurisdiction_id", 0),
            "permissions": security.permissions_of(user["role_code"]),
            "avatar": user.get("avatar", ""),
        },
    }, "登录成功")


@router.post("/auth/logout")
def logout(req: Request):
    """🔴 真登出：把 token 的 jti 写入服务端黑名单（落盘，重启后仍有效）。

    原来只记一条审计日志就返回「已登出」，同一个 token 之后照样能调接口 —— 假登出。
    """
    user = req.user()
    revoked = security.revoke_token(req.token) if req.token else False
    if user:
        security.audit(user, "登出", "user", user["id"], {"revoked": revoked}, ip=req.client_ip)
    return ok({"revoked": revoked}, "已登出")


@router.get("/auth/me")
def me(req: Request):
    user = require(req.user())
    return ok(user)


def _scoped_count(table: str, user: dict) -> int:
    """按行级权限统计「挂在卷宗下」的表。

    🔴 原来 dashboard 直接 `db.count(table)` / 全表 SELECT，城隍列表里只见 1 条卷宗，
       大殿统计却明明白白写着「卷宗 3」——行级过滤在统计接口整体失效。
    """
    scope = security.case_scope(user)
    if not scope:
        return db.count(table)
    return db.count(table, f"case_id IN (SELECT id FROM case_file WHERE {scope})")


@router.get("/dashboard/overview")
def overview(req: Request):
    user = require(req.user(), "dashboard:read")
    scope = security.case_scope(user)
    cases = db.query("SELECT * FROM case_file" + (f" WHERE {scope}" if scope else ""))
    by_status: dict[str, int] = {}
    by_realm: dict[str, int] = {}
    for c in cases:
        by_status[c["status"]] = by_status.get(c["status"], 0) + 1
    if scope:
        realms = db.query(
            "SELECT realm, COUNT(*) AS c FROM reincarnation "
            f"WHERE case_id IN (SELECT id FROM case_file WHERE {scope}) GROUP BY realm")
    else:
        realms = db.query("SELECT realm, COUNT(*) AS c FROM reincarnation GROUP BY realm")
    for r in realms:
        by_realm[r["realm"]] = r["c"]

    # 亡魂档案按辖区过滤（城隍只能看本辖区）
    dec_scope = ""
    dec_params: tuple = ()
    if user["role_code"] == "chenghuang" and user.get("jurisdiction_id"):
        dec_scope = " WHERE jurisdiction_id=?"
        dec_params = (user["jurisdiction_id"],)
    sin_rows = db.query("SELECT sin_score FROM deceased" + dec_scope, dec_params)
    total_sin = sum(float(c.get("sin_score") or 0) for c in sin_rows)

    return ok({
        "cases": len(cases),                       # ✅ 与列表口径一致
        "by_status": by_status,
        "deceased": db.count("deceased", "jurisdiction_id=?", dec_params) if dec_scope
                    else db.count("deceased"),
        "evidence": _scoped_count("evidence", user),
        "judgments": _scoped_count("judgment", user),
        "executions": _scoped_count("execution", user),
        "reincarnations": _scoped_count("reincarnation", user),
        "pending_approvals": db.count("approval", "status='pending'" +
                                      (f" AND case_id IN (SELECT id FROM case_file WHERE {scope})"
                                       if scope else "")),
        "agent_logs": _scoped_count("agent_log", user),
        "audit_logs": db.count("audit_log"),
        "chunks": db.count("knowledge_chunk"),
        "by_realm": by_realm,
        "total_sin": round(total_sin, 1),
        "engine": graph.engine_name(),
        "degrade": config.refresh_degrade(),
        "flow": graph.graph_def(),
    })


@router.get("/dashboard/series")
def series(req: Request):
    """近 7 天办案量趋势（按天聚合）——同样按行级权限过滤。"""
    user = require(req.user(), "dashboard:read")
    scope = security.case_scope(user)
    rows = db.query("SELECT created_at FROM case_file" +
                    (f" WHERE {scope}" if scope else "") + " ORDER BY id ASC")
    days: dict[str, int] = {}
    for r in rows:
        d = (r.get("created_at") or "")[:10]
        if d:
            days[d] = days.get(d, 0) + 1
    recent = sorted(days.items())[-7:]
    return ok({"dates": [d for d, _ in recent], "counts": [c for _, c in recent]})


@router.get("/dashboard/system")
def system(req: Request):
    require(req.user(), "dashboard:read")
    import sys
    return ok({
        "python": sys.version.split()[0],
        "engine": graph.engine_name(),
        "engine_note": graph.engine_capability()["note"],
        "engine_capability": graph.engine_capability(),
        "degrade": config.refresh_degrade(),
        "db": db.engine(),
        "scheduler": {"interval": config.SCHEDULER_INTERVAL, "enabled": config.SCHEDULER_ENABLED},
        "tools": len(__import__("app.agents.tools", fromlist=["TOOLS"]).TOOLS),
    })
