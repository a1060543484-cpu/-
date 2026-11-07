"""卷宗 / 证据 / 亡魂 / 判决 / 服刑 / 轮回 / 审批 / 对话 的业务 API。

🔐 安全改造（2026-11-05）—— 对应评审 P0「写接口全线失守」：
- 新增 `_guard_case()`：**所有**对单个卷宗的读写（详情 / 启动流程 / 归档 / 删除 /
  批注 / 传证 / 删证据）统一走「鉴权 → 卷宗存在 → 行级可见」三步校验。
  以前只在「列表」做了行级过滤，详情与写接口各写各的，等于形同虚设。
- 新增 `_scoped_rows()`：列表类接口统一按可见卷宗过滤，避免统计/列表口径不一致。
- `delete_case` 改用 `case:delete` 权限（阴差不再能删卷宗），级联删除放进事务。
- 通知标已读 / 聊天历史 / 日志查询 / 审批裁决都补上归属校验。
- `/api/models` 不再回显 Key 片段，新 Key 走 `.run/secrets.env`。
- `/api/demo/reset` 收窄为「仅 admin + 二次确认 + 删除前自动备份」。
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

from .. import config, db, demo, security
from ..agents import chat as chat_agent
from ..agents import crew, graph, multimodal, rag, tools
from .base import HTTPError, Request, Router, ok, require

router = Router("/api")


# ================================================================== 统一权限护栏
def _guard_case(req: Request, cid: int, perm: str = "") -> tuple[dict, dict]:
    """单个卷宗操作的统一入口：登录 → 权限 → 存在 → 行级可见。

    返回 (user, case)。任何一步不过就抛 HTTPError，调用方不必再自己判。
    """
    user = require(req.user(), perm)
    case = db.get("case_file", cid)
    if not case:
        raise HTTPError("卷宗不存在", 404)
    if not security.can_see_case(user, case):
        raise HTTPError("无权操作该卷宗", 403)
    return user, case


def _scoped_rows(table: str, user: dict, extra: str = "", params: tuple = (),
                 order: str = "id DESC", limit: int = 0) -> list[dict]:
    """按行级权限查「挂在卷宗下」的表；admin/king 无过滤，其余按 case_scope 收窄。"""
    conds = []
    if extra:
        conds.append(extra)
    scope = security.case_scope(user)
    if scope:
        conds.append(f"case_id IN (SELECT id FROM case_file WHERE {scope})")
    sql = f"SELECT * FROM {table}"
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY " + order
    if limit:
        sql += f" LIMIT {int(limit)}"
    return db.query(sql, params)


def _clamp(value: int, default: int, lo: int, hi: int) -> int:
    """分页 / top_k 参数统一夹到合理范围，防止 `size=99999999` 拖垮服务。"""
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def _session_id(user: dict, raw: str) -> str:
    """聊天会话按用户加命名空间。

    🔴 原来 `/api/chat/history?session_id=xxx` 谁都能查任意会话内容；
       `chat_message` 表没有 user_id 列，所以用 `u<uid>-<原始id>` 做隔离，
       前后端仍传原始 session_id，服务端各自规范化，无需改表结构。
    """
    raw = str(raw or "default").strip()
    if raw.startswith(f"u{user['id']}-"):
        return raw
    return f"u{user['id']}-{raw}"


# ================================================================== 卷宗
@router.get("/cases")
def list_cases(req: Request):
    user = require(req.user(), "case:read")
    status = req.param("status", "")
    keyword = req.param("keyword", "")
    page = _clamp(req.param("page", 1, int), 1, 1, 100000)
    size = _clamp(req.param("size", 10, int), 10, 1, 100)
    where, params = [], []
    if status:
        where.append("status=?")
        params.append(status)
    if keyword:
        where.append("(title LIKE ? OR case_no LIKE ?)")
        params += [f"%{keyword}%", f"%{keyword}%"]
    scope = security.case_scope(user)      # 裸条件，需并入 WHERE 列表
    if scope:
        where.append(scope)
    sql = "SELECT * FROM case_file"
    if where:
        sql += " WHERE " + " AND ".join(where)
    total_sql = sql.replace("SELECT * FROM", "SELECT COUNT(*) AS c FROM", 1)
    total = (db.query_one(total_sql, tuple(params)) or {}).get("c", 0)
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    rows = db.query(sql, tuple(params) + (size, (page - 1) * size))
    for r in rows:
        dec = db.get("deceased", r["deceased_id"]) if r.get("deceased_id") else None
        r["deceased_name"] = (dec or {}).get("name", "")
        r["deceased_merit"] = (dec or {}).get("merit_score", 0)
        r["deceased_sin"] = (dec or {}).get("sin_score", 0)
        r["flow_state"] = db.jloads(r.get("flow_state"), {})
    return ok({"total": total, "page": page, "size": size, "list": rows})


@router.get("/cases/{case_id}")
def case_detail(req: Request):
    cid = req.param("case_id", 0, int)
    _guard_case(req, cid, "case:read")
    data = tools.call("case.get", {"case_id": cid}, case_id=cid)
    data["notes"] = db.all_rows("case_note", "case_id=?", (cid,), order="id ASC")
    data["agent_logs"] = db.all_rows("agent_log", "case_id=?", (cid,), order="id DESC", limit=50)
    data["tool_calls"] = db.all_rows("tool_call", "case_id=?", (cid,), order="id DESC", limit=50)
    data["approvals"] = db.all_rows("approval", "case_id=?", (cid,), order="id DESC")
    data["checkpoint"] = db.query_one("SELECT * FROM checkpoint WHERE case_id=?", (cid,))
    return ok(data)


@router.post("/cases")
def create_case(req: Request):
    user = require(req.user(), "case:create")
    body = req.json()
    # 🔴 原来 `int(body.get("deceased_id") or 0)` 遇到畸形值会把 Python 原生
    #    `invalid literal for int()` 回显给前端；这里改成明确的业务提示。
    try:
        dec_id = int(body.get("deceased_id") or 0)
    except (TypeError, ValueError):
        raise HTTPError("亡魂编号必须是数字", 400)
    case = demo.create_case(dec_id, body.get("title") or "", body.get("priority") or "normal",
                            judge_id=user["id"] if user["role_code"] == "judge" else 0,
                            clerk_id=user["id"] if user["role_code"] == "clerk" else 0,
                            source="manual")
    security.audit(user, "建卷宗", "case_file", case["id"], {"case_no": case["case_no"]},
                   ip=req.client_ip)
    security.agent_log(case["id"], "human", user["real_name"], "intake", "人工建卷",
                       f"为亡魂 {body.get('deceased_name', '')} 建立卷宗 {case['case_no']}")
    return ok(case, "卷宗已建立")


@router.post("/cases/{case_id}/run")
def run_case(req: Request):
    """启动 / 续跑办案流程。"""
    user, case = _guard_case(req, req.param("case_id", 0, int), "case:run")
    cid = case["id"]
    security.audit(user, "启动办案流程", "case_file", cid, {"case_no": case["case_no"]})
    if case["status"] in ("pending", ""):
        result = graph.start_case(cid)
    else:
        result = graph.continue_flow(cid, force=True)
    return ok(result, result.get("message") or "流程已推进")


@router.post("/cases/{case_id}/expire")
def expire_case(req: Request):
    """演示用：把刑期置为届满，触发轮回。"""
    user, case = _guard_case(req, req.param("case_id", 0, int), "case:run")
    cid = case["id"]
    security.audit(user, "模拟刑期届满", "case_file", cid, {})
    try:
        result = graph.simulate_expiry(cid)
    except ValueError as exc:
        raise HTTPError(str(exc))
    return ok(result, result.get("message") or "已流转")


@router.post("/cases/{case_id}/archive")
def archive_case(req: Request):
    user, case = _guard_case(req, req.param("case_id", 0, int), "case:update")
    cid = case["id"]
    out = tools.call("case.archive", {"case_id": cid}, case_id=cid)
    security.audit(user, "归档卷宗", "case_file", cid, {})
    return ok(out, "已归档")


@router.delete("/cases/{case_id}")
def delete_case(req: Request):
    """🔴 删卷宗：原来只校验 `case:update`，而阴差(clerk)正好有该权限 →
    任何阴差都能删掉不属于自己的卷宗。现在改为 `case:delete`（只有 admin/king/judge 有），
    外加行级可见校验，并把 7 张关联表的删除放进一个事务。
    """
    user, case = _guard_case(req, req.param("case_id", 0, int), "case:delete")
    cid = case["id"]
    # 日志三表（agent_log / tool_call / audit_log）刻意不删：它们是「已经发生过的事」，
    # 卷宗可以撤，留痕不能撤，否则删掉一份卷宗就能抹掉自己办过案的证据。
    with db.tx() as conn:
        for t in ("case_note", "evidence", "judgment", "execution", "reincarnation",
                  "approval", "checkpoint", "case_file"):
            col = "id" if t == "case_file" else "case_id"
            stmt = db.adapt(f"DELETE FROM {t} WHERE {col}=?")
            if db.engine() == "sqlite":
                conn.execute(stmt, (cid,))
            else:
                conn.cursor().execute(stmt, (cid,))
    security.audit(user, "删除卷宗", "case_file", cid, {"case_no": case["case_no"]})
    return ok(None, "已删除")


@router.post("/cases/{case_id}/note")
def add_note(req: Request):
    user, case = _guard_case(req, req.param("case_id", 0, int), "case:update")
    cid = case["id"]
    content = (req.json().get("content") or "").strip()
    if not content:
        raise HTTPError("批注内容不能为空")
    db.insert("case_note", {"case_id": cid, "node": "manual", "actor_type": "human",
                            "actor_name": user["real_name"], "content": content,
                            "created_at": db.now()})
    security.audit(user, "卷宗批注", "case_file", cid, {"content": content})
    return ok(None, "批注已记录")


# ================================================================== 亡魂
def _deceased_scope(user: dict) -> tuple[str, tuple]:
    """城隍只能看本辖区亡魂；其它角色全量。"""
    if user["role_code"] == "chenghuang" and user.get("jurisdiction_id"):
        return "jurisdiction_id=?", (user["jurisdiction_id"],)
    return "", ()


@router.get("/deceased")
def list_deceased(req: Request):
    user = require(req.user(), "deceased:read")
    kw = req.param("keyword", "")
    where, params = _deceased_scope(user)
    rows = db.query("SELECT * FROM deceased" + (f" WHERE {where}" if where else "") +
                    " ORDER BY id DESC", params)
    if kw:
        rows = [r for r in rows if kw in (r.get("name") or "") or kw in (r.get("hometown") or "")]
    return ok({"total": len(rows), "list": rows})


@router.get("/deceased/{dec_id}")
def deceased_detail(req: Request):
    user = require(req.user(), "deceased:read")
    did = req.param("dec_id", 0, int)
    row = db.get("deceased", did)
    if not row:
        raise HTTPError("亡魂档案不存在", 404)
    where, params = _deceased_scope(user)
    if where and row.get("jurisdiction_id") != params[0]:
        raise HTTPError("无权查看该亡魂档案", 403)
    # 注意：case_file 表没有 case_id 列，不能走 _scoped_rows，要用 visible_cases
    row["cases"] = security.visible_cases(user, "deceased_id=?", (did,))
    row["evidence"] = _scoped_rows("evidence", user, "deceased_id=?", (did,))
    return ok(row)


@router.post("/deceased")
def create_deceased(req: Request):
    user = require(req.user(), "deceased:create")
    b = req.json()
    if not b.get("name"):
        raise HTTPError("亡魂姓名不能为空")
    try:
        merit = float(b.get("merit_score") or 0)
        sin = float(b.get("sin_score") or 0)
        birth_year = int(b.get("birth_year") or 0)
        juri = int(b.get("jurisdiction_id") or 1)
    except (TypeError, ValueError):
        raise HTTPError("数值字段格式有误", 400)
    level = ("甲等" if merit >= 80 else "乙等" if merit >= 60 else "丙等" if merit >= 40
             else "丁等" if merit >= 20 else "戊等")
    did = db.insert("deceased", {
        "name": b["name"], "gender": b.get("gender", "男"), "birth_year": birth_year,
        "death_date": b.get("death_date", ""), "cause_of_death": b.get("cause_of_death", ""),
        "hometown": b.get("hometown", ""), "jurisdiction_id": juri,
        "merit_score": merit, "sin_score": sin, "life_summary": b.get("life_summary", ""),
        "merit_level": level, "status": "待审", "created_at": db.now(), "updated_at": db.now(),
    })
    security.audit(user, "新建亡魂档案", "deceased", did, {"name": b["name"]})
    return ok(db.get("deceased", did), "亡魂档案已建立")


@router.put("/deceased/{dec_id}")
def update_deceased(req: Request):
    user = require(req.user(), "deceased:update")
    did = req.param("dec_id", 0, int)
    old = db.get("deceased", did)
    if not old:
        raise HTTPError("亡魂档案不存在", 404)
    where, params = _deceased_scope(user)
    if where and old.get("jurisdiction_id") != params[0]:
        raise HTTPError("无权修改该亡魂档案", 403)
    b = req.json()
    fields = {k: b[k] for k in ("name", "gender", "birth_year", "death_date", "cause_of_death",
                                "hometown", "jurisdiction_id", "merit_score", "sin_score",
                                "life_summary", "status", "merit_level") if k in b}
    fields["updated_at"] = db.now()
    db.update("deceased", fields, "id=?", (did,))
    security.audit(user, "修改亡魂档案", "deceased", did, fields)
    return ok(db.get("deceased", did), "已更新")


# ================================================================== 证据
@router.get("/evidence")
def list_evidence(req: Request):
    user = require(req.user(), "evidence:read")
    cid = req.param("case_id", 0, int)
    if cid:
        _guard_case(req, cid, "evidence:read")
        return ok({"list": db.all_rows("evidence", "case_id=?", (cid,), order="id ASC")})
    return ok({"list": _scoped_rows("evidence", user, order="id DESC")})


@router.post("/evidence/upload")
def upload_evidence(req: Request):
    """multipart 上传 + 多模态解析（图片 OCR / 音频 ASR / 文档抽文）。"""
    cid = req.param("case_id", 0, int)
    user, case = _guard_case(req, cid, "evidence:create")
    files = req.files()
    if not files:
        raise HTTPError("请选择要上传的证据文件")
    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for f in files:
        data: bytes = f["data"]
        # 文件名只取 basename，防目录穿越；扩展名白名单化到安全字符集
        raw_name = os.path.basename(str(f["filename"] or "evidence.bin"))
        ext = os.path.splitext(raw_name)[1].lower() or ".bin"
        if not all(ch.isalnum() or ch in "._-" for ch in ext):
            ext = ".bin"
        safe_name = f"{int(time.time())}_{uuid.uuid4().hex[:8]}{ext}"
        path = os.path.join(config.UPLOAD_DIR, safe_name)
        with open(path, "wb") as fh:
            fh.write(data)
        parsed = multimodal.parse_file(Path(path), hint=raw_name)
        text = parsed.get("ocr_text") or ""
        eid = db.insert("evidence", {
            "case_id": cid, "deceased_id": case.get("deceased_id"),
            "evi_type": parsed.get("evi_type", "text"), "file_name": raw_name,
            "file_path": path, "mime": parsed.get("mime", ""), "size": len(data),
            "ocr_text": text, "summary": parsed.get("summary", ""),
            "chunk_id": 0, "status": "已解析", "created_at": db.now(),
        })
        # 证据正文回灌知识库，供后续检索比对
        if text:
            rag.ingest_text(f"{case['case_no']}·{raw_name}", "证据库",
                            text, source=f"evidence:{eid}")
        security.agent_log(cid, "multimodal", "多模态解析器", "intake", "证据解析",
                           parsed.get("summary", "")[:120], tool_name="parse_file",
                           tool_input={"file": raw_name},
                           tool_output={"type": parsed.get("evi_type"),
                                        "len": len(text)})
        out.append({"evidence_id": eid, "file_name": raw_name,
                    "type": parsed.get("evi_type"), "text_len": len(text),
                    "summary": parsed.get("summary", "")})
    security.audit(user, "上传证据", "evidence", out[0]["evidence_id"], {"case_id": cid, "n": len(out)})
    return ok(out, f"已上传 {len(out)} 件证据并完成解析")


@router.delete("/evidence/{eid}")
def delete_evidence(req: Request):
    eid = req.param("eid", 0, int)
    row = db.get("evidence", eid)
    if not row:
        raise HTTPError("证据不存在", 404)
    user, _ = _guard_case(req, row.get("case_id") or 0, "evidence:delete")
    if row.get("file_path") and os.path.exists(row["file_path"]):
        try:
            os.remove(row["file_path"])
        except OSError:
            pass
    db.delete("evidence", "id=?", (eid,))
    security.audit(user, "删除证据", "evidence", eid, {"file_name": row.get("file_name")})
    return ok(None, "已删除")


# ================================================================== 判决 / 服刑 / 轮回
@router.get("/judgments")
def list_judgments(req: Request):
    user = require(req.user(), "case:read")
    return ok({"list": _scoped_rows("judgment", user)})


@router.get("/executions")
def list_executions(req: Request):
    user = require(req.user(), "case:read")
    rows = _scoped_rows("execution", user)
    for r in rows:
        case = db.get("case_file", r["case_id"]) or {}
        r["case_no"] = case.get("case_no", "")
        r["title"] = case.get("title", "")
    return ok({"list": rows})


@router.get("/reincarnations")
def list_reincarnations(req: Request):
    user = require(req.user(), "case:read")
    rows = _scoped_rows("reincarnation", user)
    for r in rows:
        case = db.get("case_file", r["case_id"]) or {}
        r["case_no"] = case.get("case_no", "")
    return ok({"list": rows})


# ================================================================== 审批
@router.get("/approvals")
def list_approvals(req: Request):
    user = require(req.user(), "approval:read")
    status = req.param("status", "")
    rows = _scoped_rows("approval", user, "status=?", (status,)) if status else \
        _scoped_rows("approval", user)
    for r in rows:
        case = db.get("case_file", r["case_id"]) or {}
        r["case_no"] = case.get("case_no", "")
        r["case_title"] = case.get("title", "")
        r["can_decide"] = (r["status"] == "pending" and
                           security.has_perm(user["role_code"], "approval:decide"))
    return ok({"list": rows, "pending": sum(1 for r in rows if r.get("status") == "pending")})


@router.post("/approvals/{aid}/decide")
def decide_approval(req: Request):
    aid = req.param("aid", 0, int)
    appr = db.get("approval", aid)
    if not appr:
        raise HTTPError("审批单不存在", 404)
    user, _ = _guard_case(req, appr.get("case_id") or 0, "approval:decide")
    b = req.json()
    approve = bool(b.get("approve"))
    opinion = (b.get("opinion") or "").strip() or ("准予执行" if approve else "退回重审")
    try:
        res = graph.decide_approval(aid, approve, opinion, user)
    except ValueError as exc:
        raise HTTPError(str(exc))
    return ok(res, "已批准" if approve else "已驳回")


# ================================================================== 知识库
@router.get("/knowledge")
def list_knowledge(req: Request):
    require(req.user(), "knowledge:read")
    category = req.param("category", "")
    docs = db.all_rows("knowledge_doc", order="id DESC")
    if category:
        docs = [d for d in docs if d.get("category") == category]
    return ok({"list": docs, "stats": rag.stats()})


@router.post("/knowledge")
def create_knowledge(req: Request):
    user = require(req.user(), "knowledge:write")
    b = req.json()
    title = (b.get("title") or "").strip()
    content = (b.get("content") or "").strip()
    if not title or not content:
        raise HTTPError("标题与正文不能为空")
    out = rag.ingest_text(title, b.get("category") or "法典库", content, source=b.get("source") or "manual")
    security.audit(user, "新增知识", "knowledge_doc", out.get("doc_id", 0), {"title": title})
    return ok(out, "知识已入库并切片向量化")


@router.delete("/knowledge/{doc_id}")
def delete_knowledge(req: Request):
    user = require(req.user(), "knowledge:write")
    did = req.param("doc_id", 0, int)
    db.delete("knowledge_chunk", "doc_id=?", (did,))
    db.delete("knowledge_doc", "id=?", (did,))
    rag.rebuild()
    security.audit(user, "删除知识", "knowledge_doc", did, {})
    return ok(None, "已删除")


@router.get("/knowledge/search")
def search_knowledge(req: Request):
    require(req.user(), "knowledge:read")
    q = req.param("q", "")
    if not q:
        raise HTTPError("请输入检索词")
    top_k = _clamp(req.param("top_k", 5, int), 5, 1, 20)
    return ok({"query": q, "hits": rag.search(q, top_k=top_k,
                                              category=req.param("category") or None)})


# ================================================================== Agent / 流程
@router.get("/agents/graph")
def agent_graph(req: Request):
    require(req.user(), "case:read")
    return ok({**graph.graph_def(), "nodes_meta": graph.NODE_META,
               "progress": graph.NODE_PROGRESS,
               "engine_capability": graph.engine_capability(),
               "agents": [{"code": a.code, "name": a.name, "node": a.node, "duty": a.duty}
                          for a in crew.AGENTS.values()]})


@router.get("/agents/tools")
def agent_tools(req: Request):
    require(req.user(), "case:read")
    return ok({"list": tools.schema(), "count": len(tools.TOOLS)})


@router.get("/agents/logs")
def agent_logs(req: Request):
    user = require(req.user(), "log:read")
    cid = req.param("case_id", 0, int)
    agent = req.param("agent", "")
    limit = _clamp(req.param("limit", 50, int), 50, 1, 200)
    if cid:
        _guard_case(req, cid)                  # 指定卷宗时必须可见，防止按 id 枚举他人日志
    extra = "case_id=?" if cid else ""
    params = (cid,) if cid else ()
    if agent:
        extra = (extra + " AND " if extra else "") + "agent_code=?"
        params += (agent,)
    rows = _scoped_rows("agent_log", user, extra, params, limit=limit)
    for r in rows:
        r["tool_input"] = db.jloads(r.get("tool_input"), {})
        r["tool_output"] = db.jloads(r.get("tool_output"), {})
    return ok({"list": rows, "total": db.count("agent_log")})


@router.get("/audit/logs")
def audit_logs(req: Request):
    require(req.user(), "log:read")
    limit = _clamp(req.param("limit", 50, int), 50, 1, 200)
    source = req.param("source", "")
    where = ["source=?"] if source else []
    params = [source] if source else []
    rows = db.query("SELECT * FROM audit_log" +
                    (" WHERE " + " AND ".join(where) if where else "") +
                    " ORDER BY id DESC LIMIT ?", tuple(params) + (limit,))
    return ok({"list": rows, "total": db.count("audit_log")})


@router.get("/tools/calls")
def tool_calls(req: Request):
    user = require(req.user(), "log:read")
    limit = _clamp(req.param("limit", 50, int), 50, 1, 200)
    rows = _scoped_rows("tool_call", user, limit=limit)
    for r in rows:
        r["args"] = db.jloads(r.get("args"), {})
        r["result"] = db.jloads(r.get("result"), {})
    return ok({"list": rows, "total": db.count("tool_call")})


# ================================================================== 通知
@router.get("/notifications")
def list_notifications(req: Request):
    user = require(req.user())
    # 管理员看全量（含各岗位的补证通知 / 待办复核），其余人只看自己的 + 广播(user_id=0)。
    if user["role_code"] == "admin":
        rows = db.query("SELECT * FROM notification ORDER BY id DESC LIMIT 50")
    else:
        rows = db.query("SELECT * FROM notification WHERE user_id=? OR user_id=0 "
                        "ORDER BY id DESC LIMIT 50", (user["id"],))
    for r in rows:
        uid = r.get("user_id") or 0
        r["user_name"] = "全体" if uid == 0 else (
            (db.query_one("SELECT real_name FROM user WHERE id=?", (uid,)) or {}).get("real_name") or "")
    return ok({"list": rows, "unread": sum(1 for r in rows if not r.get("is_read"))})


@router.post("/notifications/{nid}/read")
def read_notification(req: Request):
    """🔴 原来 `UPDATE notification SET is_read=1 WHERE id=?` 没有任何归属校验 ——
    实测城隍能把阎王的待办通知改成已读（IDOR）。现在 SQL 直接带 user_id 条件。
    """
    user = require(req.user())
    nid = req.param("nid", 0, int)
    row = db.query_one("SELECT id, user_id FROM notification WHERE id=?", (nid,))
    if not row:
        raise HTTPError("通知不存在", 404)
    # 私有通知只能本人改；user_id=0 是广播，允许任何人标已读
    if row["user_id"] not in (0, user["id"]) and user["role_code"] != "admin":
        raise HTTPError("无权操作他人的通知", 403)
    db.update("notification", {"is_read": 1}, "id=?", (nid,))
    return ok(None, "已读")


# ================================================================== 字典 / 模型配置 / 用户
@router.get("/dict")
def list_dict(req: Request):
    require(req.user())
    dtype = req.param("type", "")
    rows = db.all_rows("sys_dict", order="dict_type ASC, sort ASC")
    if dtype:
        rows = [r for r in rows if r.get("dict_type") == dtype]
    groups: dict[str, list] = {}
    for r in rows:
        groups.setdefault(r["dict_type"], []).append(r)
    return ok({"list": rows, "groups": groups})


@router.post("/dict")
def create_dict(req: Request):
    user = require(req.user(), "dict:write")
    b = req.json()
    did = db.insert("sys_dict", {"dict_type": b.get("dict_type", ""), "dict_key": b.get("dict_key", ""),
                                 "dict_value": b.get("dict_value", ""), "sort": int(b.get("sort") or 0),
                                 "remark": b.get("remark", ""), "created_at": db.now()})
    security.audit(user, "新增字典", "sys_dict", did, b)
    return ok(db.get("sys_dict", did), "已新增")


@router.delete("/dict/{did}")
def delete_dict(req: Request):
    user = require(req.user(), "dict:write")
    did = req.param("did", 0, int)
    db.delete("sys_dict", "id=?", (did,))
    security.audit(user, "删除字典", "sys_dict", did, {})
    return ok(None, "已删除")


@router.get("/models")
def list_models(req: Request):
    require(req.user(), "model:read")
    rows = db.all_rows("model_config", order="id ASC")
    for r in rows:
        # 🔴 原来回显 `key[:6] + "****" + key[-4:]` —— 真 Key 的头尾都露出去了。
        #    现在只返回「有没有配置」，一个字符都不回显。
        r["api_key"] = ""
        r["has_key"] = security.has_api_key(r["purpose"])
    return ok({"list": rows})


@router.put("/models/{mid}")
def update_model(req: Request):
    """修改模型配置。Key 允许留空不写，避免前端回填的脱敏串把真 Key 覆盖掉。"""
    user = require(req.user(), "model:write")
    mid = req.param("mid", 0, int)
    row = db.get("model_config", mid)
    if not row:
        raise HTTPError("模型配置不存在", 404)
    b = req.json()
    data: dict = {}
    for f in ("name", "provider", "base_url", "model"):
        if f in b and b[f] is not None:
            data[f] = str(b[f]).strip()
    for f in ("temperature", "max_tokens"):
        if b.get(f) not in (None, ""):
            try:
                data[f] = float(b[f]) if f == "temperature" else int(b[f])
            except (TypeError, ValueError):
                raise HTTPError("数值字段格式有误", 400)
    if "is_active" in b and b["is_active"] is not None:
        data["is_active"] = 1 if b["is_active"] else 0
    key = str(b.get("api_key") or "").strip()
    if key and "****" not in key:                    # 脱敏占位串不算新 Key
        # 🔴 明文 Key 不再写进数据库：落到 .run/secrets.env + 当前进程环境
        security.store_api_key(row["purpose"], key)
    if data.get("base_url") and not data["base_url"].startswith(("http://", "https://")):
        raise HTTPError("Base URL 必须以 http:// 或 https:// 开头", 400)
    if not data and not key:
        raise HTTPError("没有要修改的字段", 400)
    if data:
        data["updated_at"] = db.now()
        if data.get("is_active") == 1:               # 启用某个配置就先停用同用途的其它配置
            db.update("model_config", {"is_active": 0},
                      "purpose=? AND id<>?", (row["purpose"], mid))
        db.update("model_config", data, "id=?", (mid,))
    safe = {k: ("****" if k == "api_key" else v) for k, v in data.items()}
    security.audit(user, "修改模型配置", "model_config", mid, safe)
    fresh = db.get("model_config", mid) or {}
    fresh["api_key"] = ""
    fresh["has_key"] = security.has_api_key(fresh.get("purpose", ""))
    return ok(fresh, "已保存")


@router.post("/models/{mid}/activate")
def activate_model(req: Request):
    user = require(req.user(), "model:write")
    mid = req.param("mid", 0, int)
    row = db.get("model_config", mid)
    if not row:
        raise HTTPError("模型配置不存在", 404)
    db.update("model_config", {"is_active": 0}, "purpose=?", (row["purpose"],))
    db.update("model_config", {"is_active": 1, "updated_at": db.now()}, "id=?", (mid,))
    security.audit(user, "启用模型", "model_config", mid, {"name": row["name"]})
    return ok(None, f"已启用「{row['name']}」")


@router.post("/models/{mid}/test")
def test_model(req: Request):
    require(req.user(), "model:write")
    mid = req.param("mid", 0, int)
    row = db.get("model_config", mid)
    if not row:
        raise HTTPError("模型配置不存在", 404)
    from ..agents import llm
    res = llm.test_connection(row)               # 内部已有环境变量回落
    db.update("model_config", {"status": "ok" if res.get("ok") else "fail",
                               "last_test": db.now()}, "id=?", (mid,))
    return ok(res, "连通性测试完成")


@router.get("/users")
def list_users(req: Request):
    require(req.user(), "user:read")
    rows = db.query("SELECT id,username,real_name,role_code,jurisdiction_id,status,created_at FROM user")
    for r in rows:
        r["role_name"] = security.ROLE_NAMES.get(r["role_code"], r["role_code"])
    return ok({"list": rows})


# ================================================================== 对话（SSE 流式）
@router.post("/chat/stream")
def chat_stream(req: Request):
    from .base import SSEStream
    user = require(req.user(), "chat:use")
    b = req.json()
    raw_sid = b.get("session_id") or f"s-{uuid.uuid4().hex[:8]}"
    session_id = _session_id(user, raw_sid)
    question = (b.get("question") or "").strip()
    if not question:
        raise HTTPError("请输入问题")
    case_id = int(b.get("case_id") or 0)
    if case_id:
        _guard_case(req, case_id)                 # 带卷宗上下文的提问也要看得见该卷宗
    security.audit(user, "对话提问", "chat", 0, {"question": question[:80]}, ip=req.client_ip)
    return SSEStream(chat_agent.answer(session_id, question, case_id))


@router.get("/chat/history")
def chat_history(req: Request):
    user = require(req.user(), "chat:use")
    sid = req.param("session_id", "")
    session_id = _session_id(user, sid)           # 命名空间隔离，读不到别人的会话
    rows = db.query("SELECT * FROM chat_message WHERE session_id=? ORDER BY id ASC", (session_id,))
    for r in rows:
        r["tool_calls"] = db.jloads(r.get("tool_calls"), [])
    return ok({"session_id": sid, "list": rows})


@router.post("/chat/case")
def chat_create_case(req: Request):
    user = require(req.user(), "case:create")
    b = req.json()
    sid = _session_id(user, b.get("session_id") or "default")
    out = chat_agent.create_case_from_chat(sid,
                                          int(b.get("deceased_id") or 0), b.get("title") or "", user)
    return ok(out, "对话建卷成功")


# ================================================================== 演示数据
@router.post("/demo/seed")
def demo_seed(req: Request):
    require(req.user(), "dict:write")
    from .. import seed
    return ok(seed.seed(force=False), "基础数据已就绪")


@router.post("/demo/build")
def demo_build(req: Request):
    user = require(req.user(), "dict:write")
    out = demo.build_demo()
    security.audit(user, "生成演示卷宗", "demo", 0, out)
    return ok(out, "演示卷宗已生成并跑完流程")


@router.post("/demo/reset")
def demo_reset(req: Request):
    """🔴 原来只要 `dict:write` 就能一键 DELETE 16 张表（还含审计日志），
    无备份、无事务、无二次确认，README 甚至把它写成体验第一步。
    现在：仅 admin + 请求体 confirm=RESET + 删除前自动在线备份 + 事务化删除。
    """
    user = require(req.user(), "dict:write")
    if user["role_code"] != "admin":
        raise HTTPError("仅管理员可以重置演示数据", 403)
    if str(req.json().get("confirm") or "").strip() != "RESET":
        raise HTTPError("请在请求体里带 confirm=RESET 完成二次确认", 400)
    backup_dir = config.BASE_DIR / "backups"
    backup_path = ""
    try:
        backup_path = db.backup(str(backup_dir / f"difu-{time.strftime('%Y%m%d-%H%M%S')}.db"))
    except Exception as exc:
        raise HTTPError(f"备份失败，已中止重置：{exc}", 500)
    out = demo.reset_and_build()
    security.audit(user, "重置演示数据", "demo", 0, {"backup": backup_path})
    return ok({**out, "backup": backup_path}, "数据已重置（已自动备份）")
