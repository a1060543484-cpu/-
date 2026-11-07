"""多模态对话审案：SSE 流式输出 + 边聊边建卷宗。

有模型 Key 走真实大模型流式；无 Key 走本地规则引擎（检索知识库 + 办案规程模板），
前端体验完全一致。
"""
from __future__ import annotations

import json
import time
import uuid

from .. import db, security
from . import graph, llm, rag, tools

SYSTEM_PROMPT = (
    "你是「地府阴司办案助手」，通晓《酆都法典》、判例与办案规程。"
    "回答要简洁、有依据，涉及罪名/量刑时引用法条与判例；"
    "用户要求办案时，可提示其调用工具：建卷宗、受理、审判、复核、执行、轮回。"
)


def _history(session_id: str, limit: int = 8) -> list[dict]:
    rows = db.query("SELECT * FROM chat_message WHERE session_id=? ORDER BY id DESC LIMIT ?",
                    (session_id, limit))
    rows.reverse()
    return [{"role": r["role"], "content": r["content"]} for r in rows if r["role"] in ("user", "assistant")]


def save_message(session_id: str, role: str, content: str, case_id: int = 0,
                 tool_calls: list | None = None) -> int:
    return db.insert("chat_message", {
        "session_id": session_id, "role": role, "content": content,
        "case_id": int(case_id or 0), "tool_calls": db.jdumps(tool_calls or []),
        "created_at": db.now(),
    })


def answer(session_id: str, question: str, case_id: int = 0):
    """生成器：逐段 yield (event, data)，供 SSE 推送。"""
    save_message(session_id, "user", question, case_id)
    started = time.time()
    full: list[str] = []

    # ① 先做一次知识库检索，把命中依据作为上下文（多模态 RAG）
    hits = rag.search(question, top_k=3)
    context = "\n".join(f"{h['title']}：{h['content'][:200]}" for h in hits)
    yield ("meta", {"rag_hits": hits, "mode": "llm" if llm.active_config("text") else "rule-engine"})

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if context:
        messages.append({"role": "system", "content": f"【知识库检索结果】\n{context}"})
    if case_id:
        case = db.get("case_file", case_id)
        if case:
            messages.append({"role": "system",
                             "content": f"【当前卷宗】{case['case_no']} {case['title']}，"
                                        f"状态 {case['status']}，进度 {case['progress']}%"})
    messages += _history(session_id, limit=6)
    if not messages or messages[-1]["content"] != question:
        messages.append({"role": "user", "content": question})

    for piece in llm.chat_stream(messages):
        full.append(piece)
        yield ("delta", {"text": piece})

    text = "".join(full)
    save_message(session_id, "assistant", text, case_id)
    cfg = llm.active_config("text")
    security.agent_log(case_id, "chat", "对话审案助手", "chat", "对话答复", text[:200],
                       model=(cfg or {}).get("model") or "本地规则引擎",
                       latency_ms=int((time.time() - started) * 1000))
    yield ("done", {"latency_ms": int((time.time() - started) * 1000), "length": len(text)})


def create_case_from_chat(session_id: str, deceased_id: int, title: str,
                          user: dict | None = None) -> dict:
    """边聊边建卷宗：对话里直接开案。"""
    dec = db.get("deceased", deceased_id)
    if not dec:
        raise ValueError("亡魂档案不存在")
    case_no = f"DF-{time.strftime('%Y%m%d')}-{db.count('case_file') + 1:04d}"
    cid = db.insert("case_file", {
        "case_no": case_no, "title": title or f"{dec['name']}亡魂案",
        "deceased_id": deceased_id, "jurisdiction_id": dec.get("jurisdiction_id") or 1,
        "status": "pending", "priority": "normal", "risk_level": "待定",
        "current_node": "intake", "progress": 0, "judge_id": 0, "clerk_id": (user or {}).get("id", 0),
        "flow_state": "{}", "summary": dec.get("life_summary", ""), "verdict": "",
        "source": "chat", "created_at": db.now(), "updated_at": db.now(), "closed_at": "",
    })
    save_message(session_id, "assistant", f"已为您建立卷宗 {case_no}，可直接启动办案流程。", cid)
    security.audit(user, "对话建卷宗", "case_file", cid, {"case_no": case_no})
    return {"case_id": cid, "case_no": case_no}
