"""20 个业务工具（Function Calling 的工具层）。

Agent 不只是「生成文字」，而是通过这些工具**真的读写业务库**：
建卷宗、写判决书、建服刑记录、生成转世文书、挂待办审批单……
每个工具都是纯 Python 函数，既可直接被内置状态机调用，也可在装有
langchain 时包装成 `StructuredTool` 交给大模型做 Function Calling。
"""
from __future__ import annotations

import time
import re
from typing import Any, Callable

from .. import config, db, security
from . import rag

# name -> (description, parameters, fn)
TOOLS: dict[str, dict] = {}


def tool(name: str, description: str, parameters: dict[str, str]):
    def deco(fn: Callable[..., Any]):
        TOOLS[name] = {"name": name, "description": description, "parameters": parameters, "fn": fn}
        return fn
    return deco


def call(name: str, ctx: dict, **kwargs) -> Any:
    """统一调用入口：留痕到 tool_call 表，供前端溯源。"""
    if name not in TOOLS:
        raise ValueError(f"未注册的工具：{name}")
    started = time.time()
    ok, result = 1, None
    try:
        result = TOOLS[name]["fn"](ctx, **kwargs)
        return result
    except Exception as exc:                       # 工具失败不打断流程，留痕后抛出
        ok = 0
        result = {"error": str(exc)}
        raise
    finally:
        db.insert("tool_call", {
            "case_id": int(ctx.get("case_id") or 0), "agent_code": ctx.get("agent", ""),
            "tool_name": name, "args": db.jdumps(kwargs), "result": db.jdumps(result),
            "success": ok, "duration_ms": int((time.time() - started) * 1000),
            "created_at": db.now(),
        })


def schema() -> list[dict]:
    return [{"name": t["name"], "description": t["description"], "parameters": t["parameters"]}
            for t in TOOLS.values()]


# ------------------------------------------------------------------ 卷宗类
@tool("case.get", "查询案件卷宗的完整信息", {"case_id": "integer"})
def case_get(ctx: dict, case_id: int = 0) -> dict:
    cid = int(case_id or ctx.get("case_id") or 0)
    row = db.get("case_file", cid)
    if not row:
        raise ValueError(f"卷宗不存在：{cid}")
    row["evidence"] = db.all_rows("evidence", "case_id=?", (cid,), order="id ASC")
    row["deceased"] = db.get("deceased", row["deceased_id"]) if row.get("deceased_id") else None
    row["judgment"] = db.query_one("SELECT * FROM judgment WHERE case_id=? ORDER BY id DESC", (cid,))
    row["execution"] = db.query_one("SELECT * FROM execution WHERE case_id=? ORDER BY id DESC", (cid,))
    row["reincarnation"] = db.query_one("SELECT * FROM reincarnation WHERE case_id=? ORDER BY id DESC", (cid,))
    row["flow_state"] = db.jloads(row.get("flow_state"), {})
    return row


@tool("case.update", "更新卷宗状态、当前节点、进度等字段", {"case_id": "integer", "fields": "object"})
def case_update(ctx: dict, case_id: int = 0, fields: dict | None = None) -> dict:
    cid = int(case_id or ctx.get("case_id") or 0)
    fields = dict(fields or {})
    allowed = {"status", "current_node", "progress", "risk_level", "verdict", "summary",
               "judge_id", "clerk_id", "priority", "closed_at", "flow_state"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if "flow_state" in fields and not isinstance(fields["flow_state"], str):
        fields["flow_state"] = db.jdumps(fields["flow_state"])
    fields["updated_at"] = db.now()
    db.update("case_file", fields, "id=?", (cid,))
    return {"case_id": cid, "updated": list(fields.keys())}


@tool("case.note", "为卷宗追加一条流转批注", {"case_id": "integer", "node": "string",
                                           "content": "string", "actor_type": "string"})
def case_note(ctx: dict, case_id: int = 0, node: str = "", content: str = "",
              actor_type: str = "agent") -> dict:
    cid = int(case_id or ctx.get("case_id") or 0)
    rid = db.insert("case_note", {
        "case_id": cid, "node": node or ctx.get("node", ""), "actor_type": actor_type,
        "actor_name": ctx.get("agent_name", "系统"), "content": content,
        "created_at": db.now(),
    })
    return {"note_id": rid}


@tool("case.archive", "归档卷宗并把本案判例回灌知识库", {"case_id": "integer", "verdict": "string"})
def case_archive(ctx: dict, case_id: int = 0, verdict: str = "") -> dict:
    cid = int(case_id or ctx.get("case_id") or 0)
    case = db.get("case_file", cid)
    if not case:
        raise ValueError(f"卷宗不存在：{cid}")
    judgment = db.query_one("SELECT * FROM judgment WHERE case_id=? ORDER BY id DESC", (cid,))
    dec = db.get("deceased", case["deceased_id"]) if case.get("deceased_id") else {}
    db.update("case_file", {
        "status": "completed", "current_node": "archive", "progress": 100,
        "verdict": verdict or (judgment or {}).get("crime", ""),
        "closed_at": db.now(), "updated_at": db.now(),
    }, "id=?", (cid,))
    if judgment:
        rag.ingest_text(
            f"判例·{case['case_no']} {dec.get('name', '')}{judgment['crime']}案",
            "判例库",
            f"{dec.get('name', '无名')}，{judgment['crime']}。判决：{judgment['penalty_desc']}。"
            f"功德分 {judgment['merit_score']}，罪业 {judgment['sin_score']}，风险 {judgment['risk_level']}。"
            f"理由：{judgment['reason']}",
            source=f"case:{cid}",
        )
    return {"case_id": cid, "status": "completed", "archived_at": db.now()}


# ------------------------------------------------------------------ 证据类
@tool("evidence.list", "列出案件的全部证据（含 OCR/转写文本）", {"case_id": "integer"})
def evidence_list(ctx: dict, case_id: int = 0) -> list[dict]:
    cid = int(case_id or ctx.get("case_id") or 0)
    return db.all_rows("evidence", "case_id=?", (cid,), order="id ASC")


@tool("evidence.check_material", "校验卷宗材料是否齐全（身份、生平、证据、罪名线索）",
      {"case_id": "integer"})
def evidence_check_material(ctx: dict, case_id: int = 0) -> dict:
    cid = int(case_id or ctx.get("case_id") or 0)
    case = db.get("case_file", cid) or {}
    dec = db.get("deceased", case.get("deceased_id")) if case.get("deceased_id") else None
    evis = db.all_rows("evidence", "case_id=?", (cid,))
    missing = []
    if not dec:
        missing.append("亡魂档案")
    else:
        if not dec.get("life_summary"):
            missing.append("生平善恶记录")
        if not dec.get("hometown"):
            missing.append("籍贯信息")
        if float(dec.get("merit_score") or 0) <= 0 and float(dec.get("sin_score") or 0) <= 0:
            missing.append("功德/罪业记录")
    if not evis:
        missing.append("罪证材料")
    else:
        parsed = [e for e in evis if e.get("ocr_text")]
        if not parsed:
            missing.append("证据解析文本")
    types = {e.get("evi_type") for e in evis}
    if len(types) < 2 and len(evis) < 2:
        missing.append("证据链交叉印证（至少两类）")
    return {"complete": not missing, "missing": missing,
            "evidence_count": len(evis), "evidence_types": sorted(t for t in types if t)}


@tool("evidence.supplement_notice", "材料不全时生成补证通知并挂起案件",
      {"case_id": "integer", "missing": "array"})
def evidence_supplement_notice(ctx: dict, case_id: int = 0, missing: list | None = None) -> dict:
    cid = int(case_id or ctx.get("case_id") or 0)
    case = db.get("case_file", cid) or {}
    items = "、".join(missing or []) or "补充材料"
    title = f"补证通知·{case.get('case_no', cid)}"
    content = f"卷宗「{case.get('title', '')}」材料不全，缺少：{items}。请阴差于三日内补齐后重新提交。"
    # 同一卷宗反复点「推进」不该刷出一串重复通知，未读的就跳过
    for u in db.query("SELECT id FROM user WHERE role_code IN ('clerk','chenghuang') AND status='active'"):
        dup = db.query_one("SELECT id FROM notification WHERE user_id=? AND link=? AND is_read=0",
                           (u["id"], f"case:{cid}"))
        if dup:
            continue
        security.notify(u["id"], title, content, "supplement", f"case:{cid}")
    db.update("case_file", {"status": "suspended", "current_node": "intake",
                       "progress": 20, "updated_at": db.now()}, "id=?", (cid,))
    db.insert("case_note", {"case_id": cid, "node": "intake", "actor_type": "agent",
                            "actor_name": "阴差 Agent", "content": f"材料不全，已生成补证通知：{items}",
                            "created_at": db.now()})
    return {"notice": title, "missing": missing}


# ------------------------------------------------------------------ 亡魂类
@tool("deceased.get", "查询亡魂档案与生平善恶记录", {"deceased_id": "integer"})
def deceased_get(ctx: dict, deceased_id: int = 0) -> dict:
    row = db.get("deceased", int(deceased_id))
    if not row:
        raise ValueError(f"亡魂档案不存在：{deceased_id}")
    return row


@tool("deceased.update_merit", "按善恶行为核算并更新功德分/罪业分/等级",
      {"deceased_id": "integer", "merit_delta": "number", "sin_delta": "number",
       "merit_score": "number", "sin_score": "number"})
def deceased_update_merit(ctx: dict, deceased_id: int = 0, merit_delta: float = 0,
                          sin_delta: float = 0, merit_score: float | None = None,
                          sin_score: float | None = None) -> dict:
    dec = db.get("deceased", int(deceased_id))
    if not dec:
        raise ValueError(f"亡魂档案不存在：{deceased_id}")
    merit = float(merit_score if merit_score is not None else (dec.get("merit_score") or 0) + merit_delta)
    sin = float(sin_score if sin_score is not None else (dec.get("sin_score") or 0) + sin_delta)
    merit = max(0.0, min(100.0, merit))
    sin = max(0.0, min(100.0, sin))
    level = ("甲等" if merit >= 90 else "乙等" if merit >= 70 else "丙等" if merit >= 50
             else "丁等" if merit >= 30 else "戊等")
    db.update("deceased", {"merit_score": merit, "sin_score": sin, "merit_level": level,
                           "updated_at": db.now()}, "id=?", (int(deceased_id),))
    return {"deceased_id": int(deceased_id), "merit_score": merit, "sin_score": sin, "merit_level": level}


# ------------------------------------------------------------------ 知识 / 法典类
@tool("knowledge.search", "在法典库/判例库/善恶簿库/证据库中检索依据", {"query": "string",
                                                                  "category": "string"})
def knowledge_search(ctx: dict, query: str = "", category: str | None = None) -> list[dict]:
    return rag.search(query, top_k=4, category=category or None)


@tool("law.match_crime", "依《酆都法典》与字典匹配罪名", {"text": "string"})
def law_match_crime(ctx: dict, text: str = "") -> dict:
    """规则匹配：罪名关键词加权 + 案情别名加权，取最高分。"""
    crimes = db.all_rows("sys_dict", "dict_type='crime'", order="sort ASC")
    aliases: dict[str, dict[str, float]] = {
        "杀人": {"杀": 2.5, "毙": 2.0, "致死": 2.5, "性命": 2.0, "死亡": 1.5},
        "伤人": {"伤": 1.5, "斗殴": 2.0, "骨裂": 1.5, "殴打": 2.0, "推搡": 0.8},
        "盗窃": {"盗": 2.5, "窃": 2.0, "偷": 2.0, "赃": 2.0, "盗伐": 2.5},
        "贪污": {"贪": 2.5, "侵吞": 3.0, "赈灾": 1.5, "公帑": 2.0, "粮款": 2.0, "账册": 1.0},
        "欺诈": {"骗": 2.0, "诈": 2.0, "欺瞒": 2.0, "伪造": 1.0, "作假": 1.5},
        "忤逆": {"不孝": 3.0, "辱骂": 2.0, "老母": 1.5, "父母": 1.5, "忤逆": 2.5},
        "邪淫": {"淫": 2.0, "奸": 1.0},
        "诽谤": {"诽": 2.0, "污蔑": 2.0, "造谣": 2.0},
        "逃税": {"税": 2.0, "隐匿": 1.5},
        "纵火": {"火": 1.5, "焚": 2.0},
    }
    haystack = text or ""
    # 兜底 OCR / ASR 产出的是「模拟文本」（开头标【离线…模拟】），里面那些
    # 「收据、借据、账册」是模板词，不是真实案情，混进量刑会把好人判成贪官。
    # 因此这类段落整段剔除，只保留真实解析出来的证据正文。
    haystack = re.sub(r"【离线[^】]*模拟】[^。]*。", " ", haystack)
    best, score = crimes[0], 0.0
    for c in crimes:
        key = c["dict_key"]
        s = 0.0
        if key in haystack:                       # 罪名原词直接出现，强信号
            s += 4.0
        for alias, w in aliases.get(key, {}).items():
            if alias in haystack:
                s += w
        if s > score:
            best, score = c, s
    cands = sorted(
        [{"crime": c["dict_key"], "score": round(sum(
            [4.0 if c["dict_key"] in haystack else 0.0] +
            [w for a, w in aliases.get(c["dict_key"], {}).items() if a in haystack]), 2)}
         for c in crimes], key=lambda x: -x["score"])[:3]
    # 弱别名单独命中（如只出现「账册」1.0 分）不足以定罪，须达 2.0 才算显著
    if score < 2.0:
        return {"crime": "", "desc": "无显著罪名", "score": round(score, 2), "candidates": cands}
    return {"crime": best["dict_key"], "desc": best["dict_value"],
            "score": round(score, 2), "candidates": cands}


@tool("law.match_penalty", "匹配刑罚条款、刑期与服刑场所", {"crime": "string", "sin_score": "number"})
def law_match_penalty(ctx: dict, crime: str = "", sin_score: float = 0) -> dict:
    prisons = db.all_rows("sys_dict", "dict_type='punishment'", order="sort ASC")
    mapping = {"盗窃": "刀山狱", "杀人": "血池狱", "欺诈": "拔舌狱", "贪污": "油锅狱",
               "忤逆": "拔舌狱", "邪淫": "铜柱狱", "诽谤": "拔舌狱", "逃税": "磨盘狱",
               "伤人": "石压狱", "纵火": "油锅狱"}
    base_table = {"盗窃": 365, "杀人": 3285, "欺诈": 540, "贪污": 1825, "忤逆": 1825,
                  "邪淫": 720, "诽谤": 365, "逃税": 730, "伤人": 180, "纵火": 1095}
    if not crime or (sin_score or 0) <= 0:
        # 无显著罪名或罪业分为 0（善恶簿上无实罪）→ 免刑，直接入轮回
        return {"prison": "—", "sentence_days": 0, "penalty_desc": "免刑，功过相抵",
                "law_article": f"《酆都法典》·{crime or '无咎'}条", "matched": ""}
    # 模型常给出复合罪名（「伪造地契夺田产、逼死邻人一家三口」这种），
    # 直接按整串查字典必然落空、进而拿到默认值；落空后若再被判成 0 天，
    # 一个罪业 90 分的谋财害命案就会被判「免刑」——实测踩过。
    # 所以先在罪名串里找已知罪名：先按罪名原词，再按同义词别名（取最长匹配，
    # 避免「伤人」压过「杀人」、「盗」压过「盗伐」这类误判）。
    aliases = {
        "杀人": ("杀人", "害命", "毙命", "夺命", "命案", "谋杀", "故杀", "逼死", "致死"),
        "伤人": ("伤人", "殴打", "斗殴", "械斗", "致伤", "骨裂", "误杀", "误伤"),
        "盗窃": ("盗窃", "盗伐", "窃取", "偷盗", "行窃", "盗"),
        "贪污": ("贪污", "侵吞", "侵夺", "贪墨", "受贿", "夺产", "霸产", "挪用"),
        "欺诈": ("欺诈", "诈骗", "行骗", "骗"),
        "忤逆": ("忤逆", "不孝", "殴父", "弃养"),
        "邪淫": ("邪淫", "奸淫", "通奸", "诱奸"),
        "诽谤": ("诽谤", "造谣", "诬告", "辱骂"),
        "逃税": ("逃税", "漏税", "抗税"),
        "纵火": ("纵火", "放火", "焚"),
    }
    matched = crime if crime in base_table else ""
    if not matched:
        pairs = [(a, k) for k, al in aliases.items() for a in al if a in crime]
        if pairs:
            # 别名长的优先（「害命」比「命」具体），长度相同则按罪名表顺序
            matched = sorted(pairs, key=lambda p: len(p[0]), reverse=True)[0][1]
    prison = mapping.get(matched, "刀山狱")
    base_days = base_table.get(matched, 365)
    # 罪业分决定量刑轻重：60 分为基准线，0 分起免刑，上限 1.6 倍
    ratio = max(0.05, min(1.6, (sin_score or 0) / 60.0))
    days = int(base_days * ratio)
    desc = next((p["dict_value"] for p in prisons if p["dict_key"] == prison), "服刑涤罪")
    return {"prison": prison, "sentence_days": days, "penalty_desc": desc,
            "law_article": f"《酆都法典》·{crime}罪条", "matched": matched}


@tool("merit.calculate", "按善恶簿条目核算功德分与罪业分", {"life_summary": "string",
                                                          "evidence_text": "string",
                                                          "merit_base": "number",
                                                          "sin_base": "number"})
def merit_calculate(ctx: dict, life_summary: str = "", evidence_text: str = "",
                    merit_base: float = 0, sin_base: float = 0) -> dict:
    """按「善恶簿」条目逐项记分；同一关键词只记一次，避免重复计数虚高。

    自首、厚葬、赔偿等情节按《法典》减罪：sin *= 0.5（减罪二等）。
    """
    text = f"{life_summary} {evidence_text}"
    good = {"救": 20, "济贫": 10, "施粥": 8, "修桥": 5, "铺路": 5, "施药": 5,
            "孝": 30, "教化": 15, "赈灾": 8, "放生": 2, "拾金不昧": 3, "捐": 6,
            "厚葬": 5, "赔偿": 5, "自首": 8}
    bad = {"盗": 10, "杀": 100, "毙": 100, "致死": 60, "欺诈": 15, "骗": 12,
           "贪污": 40, "侵吞": 40, "忤逆": 60, "辱骂": 20, "伤": 30, "斗殴": 30,
           "纵火": 50, "焚": 50, "逃税": 20, "诽谤": 12, "淫": 25, "伪造": 20}
    merit = sum(v for k, v in good.items() if k in text)
    sin = sum(v for k, v in bad.items() if k in text)
    # 先与善恶簿基线取大，再减罪：顺序反了会让「自首减罪」被基线覆盖掉，
    # 成了自首也不减刑。
    merit = max(merit, float(merit_base or 0))
    sin = max(sin, float(sin_base or 0))
    if "自首" in text:                       # 自首减罪二等
        sin *= 0.5
    return {"merit_score": round(min(100.0, merit), 1), "sin_score": round(min(100.0, sin), 1),
            "good_hits": [k for k in good if k in text], "bad_hits": [k for k in bad if k in text],
            "self_surrender": "自首" in text}


# ------------------------------------------------------------------ 判决 / 执行
@tool("judgment.create", "生成判决书（罪名、法条、量刑、功德分、风险分级）",
      {"case_id": "integer", "crime": "string", "law_article": "string",
       "merit_score": "number", "sin_score": "number", "sentence_type": "string",
       "sentence_years": "number", "sentence_days": "integer",
       "penalty_desc": "string", "risk_level": "string",
       "reason": "string", "evidence_refs": "array"})
def judgment_create(ctx: dict, case_id: int = 0, **fields) -> dict:
    cid = int(case_id or ctx.get("case_id") or 0)
    payload = {
        "case_id": cid, "crime": fields.get("crime", ""),
        "law_article": fields.get("law_article", ""),
        "merit_score": float(fields.get("merit_score") or 0),
        "sin_score": float(fields.get("sin_score") or 0),
        "sentence_type": fields.get("sentence_type", "有期徒刑"),
        "sentence_years": float(fields.get("sentence_years") or 0),
        # 天数单独存一份：years 只有两位小数，180 天往返换算会变成 178 天
        "sentence_days": int(fields.get("sentence_days") or 0),
        "penalty_desc": fields.get("penalty_desc", ""),
        "risk_level": fields.get("risk_level", "中"),
        "reason": fields.get("reason", ""),
        "evidence_refs": db.jdumps(fields.get("evidence_refs") or []),
        "judge_agent": ctx.get("agent_name", "判官 Agent"),
        "approved_by": "", "approved_at": "", "created_at": db.now(),
    }
    rid = db.insert("judgment", payload)
    return {"judgment_id": rid, **{k: payload[k] for k in ("crime", "risk_level", "penalty_desc")}}


@tool("execution.create", "创建服刑记录并计算刑期、剩余天数", {"case_id": "integer",
                                                            "judgment_id": "integer",
                                                            "prison": "string",
                                                            "sentence_days": "integer",
                                                            "punishment_desc": "string"})
def execution_create(ctx: dict, case_id: int = 0, judgment_id: int = 0, prison: str = "",
                     sentence_days: int = 0, punishment_desc: str = "") -> dict:
    cid = int(case_id or ctx.get("case_id") or 0)
    days = max(0, int(sentence_days))
    start = db.now()
    end = db.days_later(days)
    rid = db.insert("execution", {
        "case_id": cid, "judgment_id": int(judgment_id), "prison": prison,
        "sentence_days": days, "start_date": start, "end_date": end,
        "remaining_days": days, "status": "服刑中" if days else "免刑",
        "punishment_desc": punishment_desc, "created_at": db.now(), "updated_at": db.now(),
    })
    db.update("case_file", {"status": "executing" if days else "reincarnating",
                       "current_node": "execution", "progress": 70, "updated_at": db.now()},
              "id=?", (cid,))
    return {"execution_id": rid, "prison": prison, "sentence_days": days,
            "end_date": end, "remaining_days": days}


@tool("execution.progress", "刷新服刑进度（定时巡检调用）", {"case_id": "integer"})
def execution_progress(ctx: dict, case_id: int = 0) -> dict:
    cid = int(case_id or ctx.get("case_id") or 0)
    ex = db.query_one("SELECT * FROM execution WHERE case_id=? ORDER BY id DESC", (cid,))
    if not ex:
        return {"found": False}
    total = max(1, int(ex["sentence_days"]))
    remain = max(0, (db.parse_ts(ex["end_date"]) - db.parse_ts(db.now())).days)
    status = "已期满" if remain <= 0 else "服刑中"
    db.update("execution", {"remaining_days": remain, "status": status,
                            "updated_at": db.now()}, "id=?", (ex["id"],))
    progress = 70 + int(20 * (1 - remain / total))
    db.update("case_file", {"progress": min(90, progress), "updated_at": db.now()}, "id=?", (cid,))
    return {"found": True, "execution_id": ex["id"], "remaining_days": remain,
            "status": status, "expired": remain <= 0}


# ------------------------------------------------------------------ 轮回 / 孟婆
@tool("realm.match", "按功德分匹配六道轮回", {"merit_score": "number", "sin_score": "number"})
def realm_match(ctx: dict, merit_score: float = 0, sin_score: float = 0) -> dict:
    realms = db.all_rows("sys_dict", "dict_type='realm'", order="sort ASC")
    if merit_score >= 90:
        code = "天道"
    elif merit_score >= 70:
        code = "人道"
    elif merit_score >= 50:
        code = "阿修罗道" if sin_score >= 50 else "人道"
    elif merit_score >= 30:
        code = "畜生道"
    elif sin_score >= 90:
        code = "地狱道"
    else:
        code = "饿鬼道"
    desc = next((r["dict_value"] for r in realms if r["dict_key"] == code), "")
    reason = (f"功德分 {merit_score}、罪业分 {sin_score}，依《功德核算通则》"
              f"判定：{code}——{desc}")
    return {"realm": code, "realm_reason": reason}


@tool("reincarnation.create", "生成转世文书（六道归属 + 来世安排）",
      {"case_id": "integer", "merit_score": "number", "realm": "string",
       "realm_reason": "string", "next_life": "string"})
def reincarnation_create(ctx: dict, case_id: int = 0, merit_score: float = 0,
                         realm: str = "", realm_reason: str = "",
                         next_life: str = "") -> dict:
    cid = int(case_id or ctx.get("case_id") or 0)
    case = db.get("case_file", cid) or {}
    dec = db.get("deceased", case.get("deceased_id")) if case.get("deceased_id") else {}
    doc = (f"【六道转世文书】\n案号：{case.get('case_no', '')}\n亡魂：{dec.get('name', '')}"
           f"（{dec.get('hometown', '')}）\n功德分：{merit_score}\n判定：{realm_reason}\n"
           f"来世：{next_life}\n签发：轮回司 Agent\n日期：{db.now()}")
    rid = db.insert("reincarnation", {
        "case_id": cid, "merit_score": float(merit_score), "realm": realm,
        "realm_reason": realm_reason, "next_life": next_life, "document": doc,
        "status": "待登记", "mengpo_registered": 0, "created_at": db.now(),
    })
    db.update("case_file", {"status": "reincarnating", "current_node": "reincarnation",
                       "progress": 85, "updated_at": db.now()}, "id=?", (cid,))
    return {"reincarnation_id": rid, "realm": realm, "document": doc}


@tool("mengpo.register", "孟婆完成忘川登记，亡魂饮汤忘前尘", {"case_id": "integer"})
def mengpo_register(ctx: dict, case_id: int = 0) -> dict:
    cid = int(case_id or ctx.get("case_id") or 0)
    rec = db.query_one("SELECT * FROM reincarnation WHERE case_id=? ORDER BY id DESC", (cid,))
    if not rec:
        raise ValueError("尚未生成转世文书，无法登记")
    db.update("reincarnation", {"mengpo_registered": 1, "status": "已登记"}, "id=?", (rec["id"],))
    db.insert("case_note", {"case_id": cid, "node": "mengpo", "actor_type": "agent",
                            "actor_name": "孟婆 Agent",
                            "content": "忘川登记完成，孟婆汤已饮，前尘尽忘。",
                            "created_at": db.now()})
    return {"registered": True, "reincarnation_id": rec["id"]}


# ------------------------------------------------------------------ 审批 / 通知
@tool("approval.create", "重大疑难案件生成待办审批单并挂起流程",
      {"case_id": "integer", "title": "string", "reason": "string", "risk_level": "string"})
def approval_create(ctx: dict, case_id: int = 0, title: str = "", reason: str = "",
                    risk_level: str = "高") -> dict:
    cid = int(case_id or ctx.get("case_id") or 0)
    case = db.get("case_file", cid) or {}
    no = f"SP-{case.get('case_no', cid)}-{int(time.time()) % 100000}"
    rid = db.insert("approval", {
        "case_id": cid, "approval_no": no, "appr_type": "case_review",
        "title": title or f"疑难案件复核·{case.get('case_no', '')}",
        "reason": reason, "status": "pending", "applicant": ctx.get("agent_name", "判官 Agent"),
        "approver_id": 0, "approver_name": "", "opinion": "",
        "created_at": db.now(), "decided_at": "",
    })
    db.update("case_file", {"status": "reviewing", "current_node": "review",
                       "progress": 60, "risk_level": risk_level, "updated_at": db.now()},
              "id=?", (cid,))
    security.notify_role("king", "待办复核", f"{no}：{title or case.get('case_no')}", "approval",
                         f"case:{cid}")
    return {"approval_id": rid, "approval_no": no}


@tool("notification.send", "向指定角色发送站内消息通知", {"title": "string", "content": "string",
                                                        "role": "string"})
def notification_send(ctx: dict, title: str = "", content: str = "", role: str = "king") -> dict:
    security.notify_role(role, title, content, "system")
    return {"sent": True, "role": role}
